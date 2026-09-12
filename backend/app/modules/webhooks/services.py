"""웹훅 보내기 — **기록을 먼저 남기고, 보내는 것은 스레드가.**

`shared/events.py` 가 커밋 뒤에 `on_events` 를 부른다. 여기서는 (1) 어느 웹훅이 이
이벤트를 원하는지 골라 (2) 보낼 기록(`webhook_deliveries`, pending)을 **먼저 표에 남기고**
(3) 보내는 스레드를 깨운다. 표에 먼저 남기는 이유: 앱이 그 순간 죽어도 다음 기동에
pending 이 남아 있고, 다시 보낼 수 있다. 세 번 실패하면 `failed` 로 두고 사람이 화면에서
다시 보낸다.

**요청을 처리하는 스레드에서 밖으로 HTTP 를 쏘지 않는다.** 받는 쪽이 느리면 저장이 느려
보이고, 그 이유는 화면 어디에도 안 뜬다.

시험은 `dispatcher.sync = True` 와 `dispatcher.transport` 로 스레드 없이·소켓 없이 본다.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.modules.accounts.models import User
from app.modules.notifications import services as notifications
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import ObjectType
from app.modules.webhooks.models import Webhook, WebhookDelivery
from app.shared import events, extensions

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
TIMEOUT_SECONDS = 10.0
#: 실패한 뒤 다시 보내기까지. 받는 쪽이 잠깐 죽은 것이 대부분이라 길게 기다린다.
RETRY_AFTER_SECONDS = 60.0
#: 화면에 보여 주는 최근 기록 수.
RECENT = 50


def wants(hook: Webhook, action: str, type_slug: str | None) -> bool:
    """이 웹훅이 이 이벤트를 원하나 — 패턴과 타입으로."""
    if not hook.is_active:
        return False
    if not any(fnmatch.fnmatchcase(action, pattern) for pattern in hook.events or []):
        return False
    # 타입을 정해 뒀으면 그 타입의 객체 이벤트만. 객체가 아닌 이벤트(정의 변경)는 타입이
    # 없으므로 통과한다 — 타입 거르기는 객체에만 뜻이 있다.
    return not (hook.type_slugs and type_slug is not None and type_slug not in hook.type_slugs)


def _type_slug_of(db: Session, one: events.ChangeEvent) -> str | None:
    """객체 이벤트면 그 객체의 타입 — 타입으로 거르는 근거. 관계·링크면 출발 객체의 타입."""
    object_id: uuid.UUID | None = None
    if one.target_table == "objects":
        object_id = one.target_id
    elif one.target_table in ("object_relations", "object_links"):
        raw = one.changes.get("src")
        try:
            object_id = uuid.UUID(str(raw)) if raw else None
        except ValueError:
            object_id = None
    if object_id is None:
        return None
    row = db.execute(
        select(ObjectType.slug)
        .join(ObjectInstance, ObjectInstance.type_id == ObjectType.id)
        .where(ObjectInstance.id == object_id)
    ).first()
    return str(row[0]) if row else None


def payload_of(one: events.ChangeEvent, type_slug: str | None) -> dict[str, Any]:
    return {
        "event": one.action,
        "at": (one.at or datetime.now(UTC)).isoformat(),
        "actor": {
            "label": one.actor_label,
            "client": one.actor_client,
            "token": one.actor_token,
        },
        "target": {
            "table": one.target_table,
            "id": str(one.target_id) if one.target_id else None,
            "label": one.target_label,
            "type_slug": type_slug,
        },
        "workspace_id": str(one.workspace_id) if one.workspace_id else None,
        "changes": one.changes,
        "reason": one.reason,
        "request_id": one.request_id,
    }


def on_events(staged: list[events.ChangeEvent]) -> None:
    """커밋 뒤에 불린다. 보낼 기록을 남기고 스레드를 깨운다."""
    with SessionLocal() as db:
        hooks = list(db.scalars(select(Webhook).where(Webhook.is_active.is_(True))))
        if not hooks:
            return
        made = 0
        for one in staged:
            type_slug = _type_slug_of(db, one)
            for hook in hooks:
                if not wants(hook, one.action, type_slug):
                    continue
                db.add(
                    WebhookDelivery(
                        webhook_id=hook.id,
                        event=one.action,
                        payload=payload_of(one, type_slug),
                    )
                )
                made += 1
        if made:
            db.commit()
    if made:
        dispatcher.kick()


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@dataclass
class SendResult:
    ok: bool
    code: int | None
    error: str | None


class Dispatcher:
    """pending 을 보낸다 — 한 번에 한 스레드만. 시험에서는 `sync` 로 그 자리에서."""

    def __init__(self) -> None:
        self.sync = False
        self.transport: httpx.BaseTransport | None = None
        self._lock = threading.Lock()
        self._running = False
        self._retry_timer: threading.Timer | None = None

    def kick(self) -> None:
        if self.sync:
            self.deliver_pending()
            return
        with self._lock:
            if self._running:
                return
            self._running = True
        thread = threading.Thread(target=self._run, name="webhooks", daemon=True)
        thread.start()

    def _run(self) -> None:
        try:
            left = self.deliver_pending()
        finally:
            with self._lock:
                self._running = False
        if left:
            self._schedule_retry()

    def _schedule_retry(self) -> None:
        with self._lock:
            if self._retry_timer is not None and self._retry_timer.is_alive():
                return
            self._retry_timer = threading.Timer(RETRY_AFTER_SECONDS, self.kick)
            self._retry_timer.daemon = True
            self._retry_timer.start()

    def send(self, hook: Webhook, delivery: WebhookDelivery) -> SendResult:
        body = json.dumps(
            {"id": str(delivery.id), **delivery.payload}, ensure_ascii=False, default=str
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Event": delivery.event,
            "X-Delivery-Id": str(delivery.id),
            "User-Agent": "standard-platform-webhook",
        }
        if hook.secret:
            headers["X-Signature-256"] = sign(hook.secret, body)
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS, transport=self.transport) as client:
                response = client.post(hook.url, content=body, headers=headers)
        except httpx.HTTPError as caught:
            return SendResult(ok=False, code=None, error=str(caught)[:500])
        if 200 <= response.status_code < 300:
            return SendResult(ok=True, code=response.status_code, error=None)
        return SendResult(
            ok=False, code=response.status_code, error=response.text[:500] or None
        )

    def deliver_pending(self) -> int:
        """pending 을 전부 한 번씩. **아직 남은(실패해서 다시 보낼) 수**를 돌려준다."""
        left = 0
        gave_up: set[uuid.UUID] = set()
        with SessionLocal() as db:
            rows = list(
                db.scalars(
                    select(WebhookDelivery)
                    .where(
                        WebhookDelivery.status == "pending",
                        WebhookDelivery.attempts < MAX_ATTEMPTS,
                    )
                    .order_by(WebhookDelivery.created_at)
                )
            )
            hooks = {
                hook.id: hook
                for hook in db.scalars(
                    select(Webhook).where(Webhook.id.in_({row.webhook_id for row in rows}))
                )
            }
            for delivery in rows:
                hook = hooks.get(delivery.webhook_id)
                if hook is None:
                    delivery.status = "failed"
                    delivery.last_error = "웹훅이 지워졌습니다"
                    continue
                result = self.send(hook, delivery)
                now = datetime.now(UTC)
                delivery.attempts += 1
                delivery.response_code = result.code
                delivery.last_error = result.error
                if result.ok:
                    delivery.status = "ok"
                    delivery.delivered_at = now
                elif delivery.attempts >= MAX_ATTEMPTS:
                    delivery.status = "failed"
                    gave_up.add(hook.id)
                else:
                    left += 1
                hook.last_status = "ok" if result.ok else "failed"
                hook.last_at = now
                db.commit()
            # **웹훅 하나에 한 번만 알린다.** 받는 쪽이 죽으면 밀린 전송이 한꺼번에
            # 포기하는데, 그때 건마다 알리면 종에 같은 말이 수십 개 쌓이고 사람은
            # 그것을 통째로 지운다 — 정작 다른 웹훅의 알림도 함께 지워진다.
            for hook_id in gave_up:
                hook = hooks.get(hook_id)
                if hook is None:
                    continue
                notifications.notify_system_admins(
                    db,
                    kind=notifications.WEBHOOK_FAILED,
                    title=f"웹훅 「{hook.name}」 보내기를 포기했습니다",
                    body=f"{MAX_ATTEMPTS}번 다 실패했습니다. 받는 쪽이 지금 못 받고 "
                    "있습니다 — 고친 뒤 웹훅 화면에서 「다시 보내기」 를 누르세요.",
                    link="/admin/webhooks",
                )
            if gave_up:
                db.commit()
        return left


dispatcher = Dispatcher()


def resend(db: Session, delivery: WebhookDelivery) -> None:
    """사람이 「다시 보내기」 를 눌렀다 — 실패 횟수를 되돌리고 pending 으로."""
    delivery.status = "pending"
    delivery.attempts = 0
    delivery.last_error = None
    db.commit()
    dispatcher.kick()


def stats(db: Session) -> list[extensions.StatItem]:
    total = db.scalar(select(func.count()).select_from(Webhook)) or 0
    failed = (
        db.scalar(
            select(func.count())
            .select_from(WebhookDelivery)
            .where(WebhookDelivery.status == "failed")
        )
        or 0
    )
    return [
        extensions.StatItem(label="웹훅", count=int(total)),
        extensions.StatItem(label="웹훅 실패", count=int(failed)),
    ]


def maintenance(db: Session, viewer: User) -> list[extensions.MaintenanceItem]:
    """**포기한 전송이 있으면 홈이 말한다.**

    받는 쪽이 죽으면 이 표에 failed 가 쌓이는데, 웹훅 화면을 여는 사람만 그것을
    본다 — 그리고 잘 가는 동안에는 아무도 그 화면을 안 연다. 그러면 저쪽 시스템은
    몇 주째 못 받고 있고, 그 사실을 양쪽 다 모른다.

    시스템 관리자에게만. 「다시 보내기」 를 그들만 누를 수 있다.
    """
    if not viewer.is_system_admin:
        return []
    failed = (
        db.scalar(
            select(func.count())
            .select_from(WebhookDelivery)
            .where(WebhookDelivery.status == "failed")
        )
        or 0
    )
    return [
        extensions.MaintenanceItem(
            key="webhook_failed",
            label="보내기를 포기한 웹훅 전송",
            count=int(failed),
            link="/admin/webhooks",
            severity="warning",
        )
    ]
