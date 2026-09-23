"""웹훅 관리 — 시스템 관리자만. 만들고 고치고 「보내 보기」 와 최근 기록."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.webhooks import services
from app.modules.webhooks.models import Webhook, WebhookDelivery
from app.modules.webhooks.schemas import (
    DeliveryOut,
    WebhookOut,
    WebhookPatchRequest,
    WebhookWriteRequest,
)
from app.shared import audit
from app.shared.auth import require_system_admin
from app.shared.errors import AppError, NotFound, code

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _out(row: Webhook) -> WebhookOut:
    return WebhookOut(
        id=row.id,
        name=row.name,
        url=row.url,
        has_secret=bool(row.secret),
        events=list(row.events or []),
        type_slugs=list(row.type_slugs) if row.type_slugs else None,
        core_types_only=row.core_types_only,
        is_active=row.is_active,
        last_status=row.last_status,
        last_at=row.last_at,
        created_at=row.created_at,
    )


def _hook(db: Session, webhook_id: uuid.UUID) -> Webhook:
    row = db.get(Webhook, webhook_id)
    if row is None:
        raise NotFound(code("WEBHOOKS", 1), "웹훅을 찾을 수 없습니다.")
    return row


def _require_url(url: str) -> str:
    """http(s) 만. 다른 스킴은 받는 쪽이 없다 — 저장은 되는데 영영 실패하는 웹훅이 된다."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AppError(
            code("WEBHOOKS", 2), "주소는 http:// 또는 https:// 로 시작해야 합니다.", status=422
        )
    return url.strip()


@router.get("", response_model=list[WebhookOut])
def list_webhooks(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> list[WebhookOut]:
    return [_out(row) for row in db.scalars(select(Webhook).order_by(Webhook.created_at))]


@router.post("", response_model=WebhookOut, status_code=201)
def create_webhook(
    payload: WebhookWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> WebhookOut:
    row = Webhook(
        name=payload.name.strip(),
        url=_require_url(payload.url),
        secret=payload.secret,
        events=[one.strip() for one in payload.events if one.strip()],
        type_slugs=None if payload.core_types_only else (payload.type_slugs or None),
        core_types_only=payload.core_types_only,
        is_active=payload.is_active,
        created_by_id=user.id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="webhook.create",
        actor=user,
        target_table="webhooks",
        target_id=row.id,
        target_label=row.name,
        changes={"url": row.url, "events": row.events},
    )
    db.commit()
    db.refresh(row)
    return _out(row)


@router.patch("/{webhook_id}", response_model=WebhookOut)
def update_webhook(
    webhook_id: uuid.UUID,
    payload: WebhookPatchRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> WebhookOut:
    row = _hook(db, webhook_id)
    sent = payload.model_fields_set
    before = {"url": row.url, "events": list(row.events or []), "is_active": row.is_active}
    if "name" in sent and payload.name is not None:
        row.name = payload.name.strip()
    if "url" in sent and payload.url is not None:
        row.url = _require_url(payload.url)
    if "secret" in sent and payload.secret is not None:
        row.secret = payload.secret
    if "events" in sent and payload.events is not None:
        row.events = [one.strip() for one in payload.events if one.strip()]
    if "core_types_only" in sent and payload.core_types_only is not None:
        row.core_types_only = payload.core_types_only
        # **둘을 함께 켜 두지 않는다.** 무엇이 이기는지 화면만 보고는 알 수 없다.
        if payload.core_types_only:
            row.type_slugs = None
    if "type_slugs" in sent and not row.core_types_only:
        row.type_slugs = payload.type_slugs or None
    if "is_active" in sent and payload.is_active is not None:
        row.is_active = payload.is_active
    after = {"url": row.url, "events": list(row.events or []), "is_active": row.is_active}
    audit.record(
        db,
        action="webhook.update",
        actor=user,
        target_table="webhooks",
        target_id=row.id,
        target_label=row.name,
        changes=audit.diff(before, after),
    )
    db.commit()
    db.refresh(row)
    return _out(row)


@router.delete("/{webhook_id}", status_code=204)
def delete_webhook(
    webhook_id: uuid.UUID,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> None:
    row = _hook(db, webhook_id)
    audit.record(
        db,
        action="webhook.delete",
        actor=user,
        target_table="webhooks",
        target_id=row.id,
        target_label=row.name,
    )
    db.delete(row)
    db.commit()


@router.post("/{webhook_id}/test", response_model=DeliveryOut)
def test_webhook(
    webhook_id: uuid.UUID,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> DeliveryOut:
    """「보내 보기」 — 지금 이 자리에서 한 번 쏘고 결과를 돌려준다. 저장한 뒤 「왜 안 오지」 를
    바깥 시스템 로그에서 찾게 하지 않는다."""
    row = _hook(db, webhook_id)
    delivery = WebhookDelivery(
        webhook_id=row.id,
        event="webhook.test",
        payload={
            "event": "webhook.test",
            "at": datetime.now(UTC).isoformat(),
            "actor": {"label": user.display_name or user.email, "client": None, "token": None},
            "target": {
                "table": "webhooks",
                "id": str(row.id),
                "label": row.name,
                "type_slug": None,
            },
            "workspace_id": None,
            "changes": {},
            "reason": "보내 보기",
            "request_id": None,
        },
    )
    db.add(delivery)
    db.flush()
    result = services.dispatcher.send(row, delivery)
    now = datetime.now(UTC)
    delivery.attempts = 1
    delivery.response_code = result.code
    delivery.last_error = result.error
    delivery.status = "ok" if result.ok else "failed"
    delivery.delivered_at = now if result.ok else None
    row.last_status = delivery.status
    row.last_at = now
    db.commit()
    db.refresh(delivery)
    return DeliveryOut.model_validate(delivery)


@router.get("/{webhook_id}/deliveries", response_model=list[DeliveryOut])
def list_deliveries(
    webhook_id: uuid.UUID,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[DeliveryOut]:
    row = _hook(db, webhook_id)
    rows = db.scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == row.id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(services.RECENT)
    )
    return [DeliveryOut.model_validate(one) for one in rows]


@router.post("/{webhook_id}/deliveries/{delivery_id}/retry", response_model=DeliveryOut)
def retry_delivery(
    webhook_id: uuid.UUID,
    delivery_id: uuid.UUID,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> DeliveryOut:
    row = _hook(db, webhook_id)
    delivery = db.get(WebhookDelivery, delivery_id)
    if delivery is None or delivery.webhook_id != row.id:
        raise NotFound(code("WEBHOOKS", 3), "보낸 기록을 찾을 수 없습니다.")
    services.resend(db, delivery)
    db.refresh(delivery)
    return DeliveryOut.model_validate(delivery)
