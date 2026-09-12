"""웹훅 — **커밋된 변경만, 기록을 먼저 남기고, 실패는 남아서 다시 보낸다.**"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.modules.webhooks import services
from app.modules.webhooks.models import Webhook
from tests.api.conftest import Signed
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


def test_실패는_남아서_다시_보낸다(
    client: TestClient, admin: Signed, inbox: list[httpx.Request]
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

    # 남은 것은 다음 기회에 — 세 번까지. 그 뒤엔 failed 로 두고 사람이 다시 보낸다.
    services.dispatcher.deliver_pending()
    services.dispatcher.deliver_pending()
    assert len(inbox) == 3
    again = client.get(f"/api/webhooks/{hook['id']}/deliveries", headers=admin.headers).json()[
        0
    ]
    assert again["status"] == "failed" and again["attempts"] == 3

    # 받는 쪽을 고친 뒤 「다시 보내기」.
    client.patch(
        f"/api/webhooks/{hook['id']}",
        json={"url": "http://receiver.local/hook"},
        headers=admin.headers,
    )
    retried = client.post(
        f"/api/webhooks/{hook['id']}/deliveries/{one['id']}/retry", headers=admin.headers
    ).json()
    assert retried["status"] == "ok" and retried["attempts"] == 1


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
