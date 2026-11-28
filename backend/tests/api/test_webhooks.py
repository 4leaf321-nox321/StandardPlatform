"""웹훅 — **커밋된 변경만, 기록을 먼저 남기고, 실패는 남아서 다시 보낸다.**"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.modules.webhooks import services
from app.modules.webhooks.models import Webhook, WebhookDelivery
from tests.api.conftest import (
    Signed,
    clock_settled,
    maintenance_counts,
    notifications_of,
    work_until,
)
from tests.api.test_ontology import _make_object, _make_type


@pytest.fixture
def inbox(db: Session) -> Iterator[list[httpx.Request]]:
    """받는 쪽 흉내 — 스레드 없이, 소켓 없이. 주소가 /fail 이면 500 을 돌려준다.

    **시험 DB 는 스위트가 함께 쓴다.** 앞 시험이 남긴 웹훅이 이 시험의 변경을 받으면 수가
    안 맞으므로, 시작할 때 웹훅을 전부 비운다(끝나서도 비워 다른 시험을 안 건드린다)."""
    seen: list[httpx.Request] = []
    db.execute(delete(Webhook))
    db.commit()

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/fail"):
            return httpx.Response(500, text="boom")
        return httpx.Response(204)

    services.dispatcher.sync = True
    services.dispatcher.transport = httpx.MockTransport(respond)
    try:
        yield seen
    finally:
        services.dispatcher.sync = False
        services.dispatcher.transport = None
        db.execute(delete(Webhook))
        db.commit()


def _hook(client: TestClient, admin: Signed, **kw: Any) -> dict[str, Any]:
    body = {
        "name": "테스트",
        "url": "http://receiver.local/hook",
        "secret": "s3cret",
        "events": ["object.*"],
        **kw,
    }
    made = client.post("/api/webhooks", json=body, headers=admin.headers)
    assert made.status_code == 201, made.text
    return dict(made.json())


def test_시스템_관리자만_만든다(client: TestClient, member: Signed) -> None:
    denied = client.post(
        "/api/webhooks",
        json={"name": "x", "url": "http://a/b", "events": ["*"]},
        headers=member.headers,
    )
    assert denied.status_code == 403


def test_주소는_http_여야_한다(client: TestClient, admin: Signed) -> None:
    bad = client.post(
        "/api/webhooks",
        json={"name": "x", "url": "ftp://a/b", "events": ["*"]},
        headers=admin.headers,
    )
    assert bad.status_code == 422


def test_객체를_만들면_서명된_알림이_간다(
    client: TestClient, admin: Signed, inbox: list[httpx.Request]
) -> None:
    hook = _hook(client, admin)
    assert hook["has_secret"] is True
    part = _make_type(client, admin, label="부품")
    inbox.clear()  # 타입 만들기(ontology.*)는 object.* 에 안 걸린다 — 비어 있어야 한다

    bolt = _make_object(client, admin, part, label="볼트")
    assert len(inbox) == 1
    request = inbox[0]
    body = json.loads(request.content)
    assert body["event"] == "object.create"
    assert body["target"] == {
        "table": "objects",
        "id": bolt["id"],
        "label": body["target"]["label"],
        "type_slug": part,
    }
    assert request.headers["X-Event"] == "object.create"
    expected = "sha256=" + hmac.new(b"s3cret", request.content, hashlib.sha256).hexdigest()
    assert request.headers["X-Signature-256"] == expected

    deliveries = client.get(
        f"/api/webhooks/{hook['id']}/deliveries", headers=admin.headers
    ).json()
    assert [d["status"] for d in deliveries] == ["ok"]
    listed = client.get("/api/webhooks", headers=admin.headers).json()
    assert next(h for h in listed if h["id"] == hook["id"])["last_status"] == "ok"


def test_타입으로_거른다(
    client: TestClient, admin: Signed, inbox: list[httpx.Request]
) -> None:
    part = _make_type(client, admin, label="부품")
    other = _make_type(client, admin, label="공급사")
    _hook(client, admin, type_slugs=[part])
    inbox.clear()
    _make_object(client, admin, other, label="ACME")
    assert inbox == []
    _make_object(client, admin, part, label="볼트")
    assert len(inbox) == 1


def test_거절된_저장은_알리지_않는다(
    client: TestClient, admin: Signed, inbox: list[httpx.Request]
) -> None:
    """트랜잭션이 롤백되면 이벤트도 버린다 — 나가지 않은 변경을 알리면 안 된다."""
    _hook(client, admin)
    part = _make_type(client, admin, label="부품", key_policy="required")
    inbox.clear()
    refused = client.post(
        f"/api/objects/{part}",
        json={"label": "식별자 없음", "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert refused.status_code == 422
    assert inbox == []


def _later(db: Session, hook: dict[str, Any]) -> None:
    """시계를 돌리는 대신 — 그 웹훅의 마지막 시도를 재시도 간격보다 앞으로 민다.

    민 뒤에 **시계가 그 「지금」 을 지날 때까지** 기다린다(`clock_settled`) — 이 기계의
    벽시계는 뒤로 튄다(ADR 0024). 여유가 1초뿐이라 그 사이 시계가 2초 뒤로 가면 재시도가
    「아직 이르다」 가 되어 포기 알림이 안 났다(-n 8 전체 시험에서 가끔, 2026-10-31)."""
    now = datetime.now(UTC)
    db.execute(
        update(Webhook)
        .where(Webhook.id == uuid.UUID(hook["id"]))
        .values(last_at=now - timedelta(seconds=services.RETRY_AFTER_SECONDS + 1))
    )
    db.commit()
    clock_settled(now)


def test_실패는_남아서_다시_보낸다(
    client: TestClient, admin: Signed, inbox: list[httpx.Request], db: Session
) -> None:
    hook = _hook(client, admin, url="http://receiver.local/fail")
    part = _make_type(client, admin, label="부품")
    inbox.clear()
    _make_object(client, admin, part, label="볼트")
    assert len(inbox) == 1

    deliveries = client.get(
        f"/api/webhooks/{hook['id']}/deliveries", headers=admin.headers
    ).json()
    one = deliveries[0]
    assert one["status"] == "pending" and one["attempts"] == 1
    assert one["response_code"] == 500 and one["last_error"] == "boom"

    # **곧바로는 다시 안 보낸다** — 새 이벤트가 보내기를 깨울 때마다 다시 보내면 세 번을 몇
    # 초 안에 다 쓰고 포기했다(2026-10-08).
    services.dispatcher.deliver_pending()
    assert len(inbox) == 1

    # 간격이 지나면 다음 기회에 — 세 번까지. 그 뒤엔 failed 로 두고 사람이 다시 보낸다.
    _later(db, hook)
    services.dispatcher.deliver_pending()
    _later(db, hook)
    services.dispatcher.deliver_pending()
    assert len(inbox) == 3
    again = client.get(f"/api/webhooks/{hook['id']}/deliveries", headers=admin.headers).json()[
        0
    ]
    assert again["status"] == "failed" and again["attempts"] == 3

    # 받는 쪽을 고친 뒤 「다시 보내기」 — 사람이 누른 것은 간격을 안 기다린다(방금 실패했어도).
    client.patch(
        f"/api/webhooks/{hook['id']}",
        json={"url": "http://receiver.local/hook"},
        headers=admin.headers,
    )
    retried = client.post(
        f"/api/webhooks/{hook['id']}/deliveries/{one['id']}/retry", headers=admin.headers
    ).json()
    assert retried["status"] == "ok" and retried["attempts"] == 1


def test_포기하면_홈과_종이_말한다(
    client: TestClient,
    admin: Signed,
    member: Signed,
    inbox: list[httpx.Request],
    db: Session,
) -> None:
    """**받는 쪽이 조용히 못 받고 있는 상태**를 아무도 모르면 안 된다 — 잘 가는 동안에는
    웹훅 화면을 아무도 안 연다."""
    hook = _hook(client, admin, url="http://receiver.local/fail")
    part = _make_type(client, admin, label="부품")
    quiet = maintenance_counts(client, admin).get("webhook_failed", 0)
    before = len(notifications_of(client, admin, "webhook.failed"))

    _make_object(client, admin, part, label="너트")
    _later(db, hook)
    services.dispatcher.deliver_pending()
    # 아직 포기 전(세 번째에 포기) — 알리지 않는다.
    assert len(notifications_of(client, admin, "webhook.failed")) == before

    _later(db, hook)
    services.dispatcher.deliver_pending()
    after = notifications_of(client, admin, "webhook.failed")
    assert len(after) == before + 1
    assert hook["name"] in after[0]["title"]
    assert maintenance_counts(client, admin)["webhook_failed"] == quiet + 1
    assert "webhook_failed" not in maintenance_counts(client, member)


def test_보내_보기는_그_자리에서_결과를_준다(
    client: TestClient, admin: Signed, inbox: list[httpx.Request]
) -> None:
    hook = _hook(client, admin, secret="")
    tried = client.post(f"/api/webhooks/{hook['id']}/test", headers=admin.headers)
    assert tried.status_code == 200, tried.text
    assert tried.json()["status"] == "ok" and tried.json()["event"] == "webhook.test"
    assert "X-Signature-256" not in inbox[-1].headers

    broken = _hook(client, admin, url="http://receiver.local/fail")
    failed = client.post(f"/api/webhooks/{broken['id']}/test", headers=admin.headers).json()
    assert failed["status"] == "failed" and failed["response_code"] == 500


def test_지우면_기록도_함께_사라지고_보내지_않는다(
    client: TestClient, admin: Signed, inbox: list[httpx.Request]
) -> None:
    hook = _hook(client, admin)
    part = _make_type(client, admin, label="부품")
    assert (
        client.delete(f"/api/webhooks/{hook['id']}", headers=admin.headers).status_code == 204
    )
    inbox.clear()
    _make_object(client, admin, part, label="볼트")
    assert inbox == []
    assert (
        client.get(f"/api/webhooks/{hook['id']}/deliveries", headers=admin.headers).status_code
        == 404
    )


def test_보내는_것은_워커의_작업이다(client: TestClient, admin: Signed, db: Session) -> None:
    """전에는 앱 안의 스레드가 보내고 `threading.Timer` 로 재시도했다 — 앱을 재시작하면 그
    타이머가 사라져 밀린 것이 다음 이벤트까지 안 나갔다. 이제 표에 남는다."""
    from app.modules.jobs import services as job_services
    from app.modules.jobs.models import Job
    from app.modules.webhooks import services as webhook_services

    _hook(client, admin, events=["object.*"])
    part = _make_type(client, admin, label="부품")
    services.dispatcher.sync = False  # 진짜 길 — 스레드가 아니라 작업을 넣는다
    try:
        _make_object(client, admin, part, label="볼트")
        waiting = job_services.pending_for(db, "webhook_dispatch")
        assert waiting is not None, "웹훅을 보낼 작업이 안 생겼다"
        # **이벤트가 더 와도 작업은 한 줄이다** — 백 건을 고친 날 작업이 백 줄이 되면 안 된다.
        _make_object(client, admin, part, label="너트")
        assert (
            len(
                list(
                    db.scalars(
                        select(Job).where(
                            Job.kind == "webhook_dispatch",
                            Job.status.in_(("queued", "running")),
                        )
                    )
                )
            )
            == 1
        )
        # 워커가 집어 돌리면 보낸다.
        assert webhook_services.pending_count(db) >= 1
        services.dispatcher.sync = True
        work_until(db, waiting.id)
        db.expire_all()
        done = db.scalar(select(Job).where(Job.id == waiting.id))
        assert done is not None and done.status == "done"
    finally:
        services.dispatcher.sync = True


def test_밀린_것은_워커가_다시_집는다(client: TestClient, admin: Signed, db: Session) -> None:
    """전에는 재시도가 프로세스 안의 `threading.Timer` 였다 — 앱을 재시작하면 그 타이머가
    사라져 밀린 것이 **다음 이벤트가 올 때까지** 안 나갔다. 이제 워커가 표를 보고 다시
    넣는다."""
    from app.modules.jobs import services as job_services
    from app.worker import Worker

    hook = _hook(client, admin)
    db.add(
        WebhookDelivery(
            webhook_id=uuid.UUID(hook["id"]), event="object.create", payload={"x": 1}
        )
    )
    db.commit()
    assert services.pending_count(db) >= 1
    # 작업이 없는 상태에서 — 워커가 살림살이를 한 바퀴 돌면 다시 넣는다.
    assert job_services.pending_for(db, services.DISPATCH_KIND) is None
    Worker()._tick_housekeeping()
    assert job_services.pending_for(db, services.DISPATCH_KIND) is not None


def test_코어_타입만_받는_웹훅은_목록이_아니라_규칙을_따라간다(
    client: TestClient, admin: Signed, inbox: list[httpx.Request]
) -> None:
    """코어를 새로 열 때마다 slug 를 손으로 더해야 하면 언젠가 빠뜨리고, 빠뜨린 타입은
    **조용히** 알림이 안 간다 — 그 침묵은 받는 쪽에서 「안 바뀌었나 보다」 로 읽힌다."""
    opened = _make_type(client, admin, label=f"코어{uuid.uuid4().hex[:6]}")
    closed = _make_type(client, admin, label=f"안연것{uuid.uuid4().hex[:6]}")
    patched = client.patch(
        f"/api/ontology/types/{opened}", json={"core": True}, headers=admin.headers
    )
    assert patched.status_code == 200, patched.text

    _hook(client, admin, core_types_only=True)

    _make_object(client, admin, closed, label="안 가는 것")
    assert inbox == []

    _make_object(client, admin, opened, label="가는 것")
    assert len(inbox) == 1
    assert json.loads(inbox[0].content)["target"]["type_slug"] == opened

    # **나중에 연 타입도 설정을 안 고치고 따라온다.**
    client.patch(f"/api/ontology/types/{closed}", json={"core": True}, headers=admin.headers)
    _make_object(client, admin, closed, label="이제 가는 것")
    assert len(inbox) == 2

    # 닫으면 그날로 멎는다.
    client.patch(f"/api/ontology/types/{opened}", json={"core": False}, headers=admin.headers)
    _make_object(client, admin, opened, label="다시 안 가는 것")
    assert len(inbox) == 2


def test_새_이벤트가_깨워도_방금_실패한_전송은_간격을_지킨다(
    client: TestClient, admin: Signed, inbox: list[httpx.Request]
) -> None:
    """**받는 쪽이 1분 죽은 사이에 포기하던 자리다**(2026-10-08). 이벤트마다 보내기가 깨는데,
    그때마다 실패한 것까지 곧바로 다시 보내 세 번을 몇 초 안에 다 썼다. 새 것은 곧장 가고,
    방금 실패한 것은 `RETRY_AFTER_SECONDS` 를 기다린다."""
    hook = _hook(client, admin, url="http://receiver.local/fail")
    part = _make_type(client, admin, label="부품")
    inbox.clear()
    for label in ("볼트", "너트", "와셔"):
        _make_object(client, admin, part, label=label)
    assert len(inbox) == 3  # 새 것마다 첫 시도 한 번씩 — 다시 보낸 것은 없다
    rows = client.get(f"/api/webhooks/{hook['id']}/deliveries", headers=admin.headers).json()
    assert [(one["status"], one["attempts"]) for one in rows] == [("pending", 1)] * 3


def test_꺼_둔_웹훅의_밀린_전송은_기다렸다가_다시_켜면_나간다(
    client: TestClient, admin: Signed, inbox: list[httpx.Request], db: Session
) -> None:
    """끄면 새 이벤트를 안 쌓는데 이미 쌓인 것은 그대로 나갔다 — 「사용 안 함」 이 반만
    들었다(2026-10-08). 켜 있을 때 생긴 이벤트이므로 지우지 않고 기다렸다가 다시 켜면 낸다."""
    hook = _hook(client, admin)
    off = client.patch(
        f"/api/webhooks/{hook['id']}", json={"is_active": False}, headers=admin.headers
    )
    assert off.status_code == 200, off.text
    # 끄기 전에 쌓여 아직 못 나간 전송 하나.
    db.add(
        WebhookDelivery(
            webhook_id=uuid.UUID(hook["id"]), event="object.create", payload={"x": 1}
        )
    )
    db.commit()
    inbox.clear()

    services.dispatcher.deliver_pending()
    assert inbox == []
    # 워커도 그것 때문에 보내기 작업을 1분마다 넣지 않는다 — 기다리는 것이지 밀린 것이 아니다.
    assert services.pending_count(db) == 0

    on = client.patch(
        f"/api/webhooks/{hook['id']}", json={"is_active": True}, headers=admin.headers
    )
    assert on.status_code == 200, on.text
    assert len(inbox) == 1
    rows = client.get(f"/api/webhooks/{hook['id']}/deliveries", headers=admin.headers).json()
    assert [one["status"] for one in rows] == ["ok"]


# --- 죽은 받는 쪽이 워커를 붙잡지 않는다 · 알림이 쌓이지 않는다(2026-11-28 점검)
# ----------------
#
# 받는 쪽이 막히면(응답 없음) 한 건이 `TIMEOUT_SECONDS` 를 다 쓰는데, 예전에는 첫 시도는 식는
# 중이어도 다 보냈고 한 바퀴에 상한이 없었다 — 하루 1,000건이면 워커 하나가 몇 시간을 기다림에
# 썼다. 그리고 포기 알림은 한 바퀴 안에서만 하나로 묶여, 1분마다 시스템 관리자마다 하나씩
# 쌓였다.


def _pending(db: Session, hook: dict[str, Any], count: int, *, attempts: int = 0) -> None:
    for n in range(count):
        db.add(
            WebhookDelivery(
                webhook_id=uuid.UUID(hook["id"]),
                event="object.create",
                payload={"n": n},
                attempts=attempts,
            )
        )
    db.commit()


def _attempts(db: Session, hook: dict[str, Any]) -> list[tuple[str, int]]:
    db.expire_all()
    rows = db.scalars(
        select(WebhookDelivery).where(WebhookDelivery.webhook_id == uuid.UUID(hook["id"]))
    )
    return sorted((one.status, one.attempts) for one in rows)


def test_잇달아_실패한_웹훅의_나머지는_이번_바퀴에_안_보낸다(
    client: TestClient, admin: Signed, inbox: list[httpx.Request], db: Session
) -> None:
    dead = _hook(client, admin, name="죽은 곳", url="http://receiver.local/fail")
    alive = _hook(client, admin, name="사는 곳")
    _pending(db, dead, 6)
    _pending(db, alive, 2)
    inbox.clear()

    left = services.dispatcher.deliver_pending()

    sent = [one.url.path for one in inbox]
    assert sent.count("/fail") == services.BREAKER_FAILURES  # 여섯이 아니라 셋만 기다린다
    assert sent.count("/hook") == 2  # 다른 웹훅은 그대로 간다
    # 나머지는 pending 그대로 — 시도도 안 썼다(다음 바퀴가 보낸다).
    assert _attempts(db, dead) == [("pending", 0)] * 3 + [("pending", 1)] * 3
    assert _attempts(db, alive) == [("ok", 1)] * 2
    assert left == 6


def test_이미_실패하던_웹훅은_한_건만_떠_본다(
    client: TestClient, admin: Signed, inbox: list[httpx.Request], db: Session
) -> None:
    """1분마다 도는 바퀴가 그때마다 세 건씩 기다리지 않게 — 지난 시도가 실패였으면 한 건으로
    받는 쪽이 살았는지 본다."""
    dead = _hook(client, admin, url="http://receiver.local/fail")
    db.execute(
        update(Webhook)
        .where(Webhook.id == uuid.UUID(dead["id"]))
        .values(last_status="failed", last_at=datetime.now(UTC) - timedelta(hours=1))
    )
    db.commit()
    _pending(db, dead, 4)
    inbox.clear()

    services.dispatcher.deliver_pending()

    assert len(inbox) == services.PROBE_FAILURES == 1
    assert _attempts(db, dead) == [("pending", 0)] * 3 + [("pending", 1)]


def test_보내기_한_바퀴는_시간_상한에서_멈추고_나머지는_다음_바퀴에(
    client: TestClient,
    admin: Signed,
    inbox: list[httpx.Request],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slow = _hook(client, admin)
    _pending(db, slow, 5)
    inbox.clear()
    clock = {"now": 0.0}

    def respond(request: httpx.Request) -> httpx.Response:
        inbox.append(request)
        clock["now"] += 50.0  # 받는 쪽이 느리다 — 한 건에 50초
        return httpx.Response(204)

    monkeypatch.setattr(services.dispatcher, "transport", httpx.MockTransport(respond))
    monkeypatch.setattr(services.dispatcher, "clock", lambda: clock["now"])

    left = services.dispatcher.deliver_pending()

    # 0 · 50 · 100초에 하나씩 — 150초에서 상한(120초)을 넘겨 멈춘다.
    assert len(inbox) == 3
    assert _attempts(db, slow) == [("ok", 1)] * 3 + [("pending", 0)] * 2
    assert left == 2


def test_보내기는_보낸_수를_진행으로_적는다(
    client: TestClient, admin: Signed, inbox: list[httpx.Request], db: Session
) -> None:
    """작업 화면이 「보내기 0/0」 만 보이면 2분 걸리는 바퀴가 멎은 것과 구별되지 않는다."""
    hook = _hook(client, admin)
    _pending(db, hook, 2)
    seen: list[tuple[str, int, int]] = []

    services.dispatcher.deliver_pending(
        progress=lambda stage, done, total: seen.append((stage, done, total))
    )

    assert seen and seen[-1] == ("보내기", 2, 2)


def test_포기_알림은_웹훅마다_하루에_한_번만(
    client: TestClient, admin: Signed, inbox: list[httpx.Request], db: Session
) -> None:
    """받는 쪽이 죽으면 밀린 전송이 1분마다 하나씩 포기한다 — 그때마다 알리면 시스템 관리자마다
    1분에 하나씩 쌓였다. 다른 웹훅의 포기는 따로 알린다."""
    dead = _hook(client, admin, name="죽은 곳", url="http://receiver.local/fail")
    other = _hook(client, admin, name="다른 죽은 곳", url="http://receiver.local/b/fail")
    before = len(notifications_of(client, admin, "webhook.failed"))

    _pending(db, dead, 1, attempts=services.MAX_ATTEMPTS - 1)
    services.dispatcher.deliver_pending()
    assert len(notifications_of(client, admin, "webhook.failed")) == before + 1

    # 다음 바퀴(1분 뒤)에 또 하나가 포기한다 — 같은 웹훅이면 알리지 않는다.
    _later(db, dead)
    _pending(db, dead, 1, attempts=services.MAX_ATTEMPTS - 1)
    services.dispatcher.deliver_pending()
    assert len(notifications_of(client, admin, "webhook.failed")) == before + 1

    # 다른 웹훅은 따로 — 이름이 같아도 웹훅이 다르면(링크에 웹훅 id) 따로 센다.
    _pending(db, other, 1, attempts=services.MAX_ATTEMPTS - 1)
    services.dispatcher.deliver_pending()
    shown = notifications_of(client, admin, "webhook.failed")
    assert len(shown) == before + 2
    assert shown[0]["link"] == f"/admin/webhooks?hook={other['id']}"
    assert _attempts(db, dead) == [("failed", services.MAX_ATTEMPTS)] * 2


def test_떠_본_것이_되면_그_바퀴의_나머지는_보통의_끊개로(
    client: TestClient,
    admin: Signed,
    inbox: list[httpx.Request],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """지난번에 실패하던 웹훅은 한 건만 떠 본다 — 그것이 되면 받는 쪽이 살아난 것이다. 그
    뒤에도 한 번의 실패로 바퀴를 접으면, 잠깐의 오류 하나에 밀린 것이 1분씩 더
    밀렸다(2026-11-28 검토)."""
    flaky = _hook(client, admin)
    db.execute(
        update(Webhook)
        .where(Webhook.id == uuid.UUID(flaky["id"]))
        .values(last_status="failed", last_at=datetime.now(UTC) - timedelta(hours=1))
    )
    db.commit()
    _pending(db, flaky, 6)
    inbox.clear()

    def respond(request: httpx.Request) -> httpx.Response:
        inbox.append(request)
        return httpx.Response(204 if len(inbox) == 1 else 500, text="boom")

    monkeypatch.setattr(services.dispatcher, "transport", httpx.MockTransport(respond))
    services.dispatcher.deliver_pending()

    # 떠 본 1건이 되고, 그 뒤로는 3번 잇달아 실패해야 접는다 — 2건이 아니라 4건.
    assert len(inbox) == 1 + services.BREAKER_FAILURES
    assert _attempts(db, flaky) == [("ok", 1)] + [("pending", 0)] * 2 + [("pending", 1)] * 3


def test_보내기를_도중에_취소해도_이미_포기한_전송은_알린다(
    client: TestClient,
    admin: Signed,
    inbox: list[httpx.Request],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """예전에는 포기 알림이 바퀴 끝에만 있어, 취소된 바퀴에서 「실패」 가 된 전송은 홈의 수로만
    남고 알림은 안 갔다(2026-11-28 검토)."""
    from app.modules.jobs.services import Cancelled

    dead = _hook(client, admin, name="취소된 바퀴", url="http://receiver.local/fail")
    _pending(db, dead, 2, attempts=services.MAX_ATTEMPTS - 1)
    before = len(notifications_of(client, admin, "webhook.failed"))
    clock = {"now": 0.0}

    def tick() -> float:
        clock["now"] += 5.0  # 부를 때마다 5초 — 첫 건 뒤에 진행을 적는다
        return clock["now"]

    def cancel(_stage: str, _done: int, _total: int) -> None:
        raise Cancelled()

    monkeypatch.setattr(services.dispatcher, "clock", tick)
    with pytest.raises(Cancelled):
        services.dispatcher.deliver_pending(progress=cancel)

    assert len(inbox) == 1  # 첫 건을 보내고 진행을 적다 취소됐다
    shown = notifications_of(client, admin, "webhook.failed")
    assert len(shown) == before + 1 and "취소된 바퀴" in shown[0]["title"]
    assert _attempts(db, dead) == [("failed", services.MAX_ATTEMPTS), ("pending", 2)]
