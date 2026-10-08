"""한 번 쓰는 업로드 표 — MCP 가 파일을 **토큰으로 나르지 않게**(ADR 0012).

MCP 도구는 표를 받아 `curl` 명령만 돌려주고, AI 의 셸이 파일을 직접 올린다. 표 자체가 자격이라
짧게(5분) · 한 번만 · 자리를 정해서 낸다. 낼 때 자리를 보고, 쓸 때 **다시** 본다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEntry
from app.modules.files import services
from app.modules.files.models import AttachmentTicket
from tests.api.conftest import Signed
from tests.api.test_attachment_images import _png, _world


def _ticket(
    client: TestClient,
    who: Signed | dict[str, str],
    owner_id: str,
    *,
    field: str | None = "photo",
    table: str = "objects",
) -> Response:
    headers = who.headers if isinstance(who, Signed) else who
    response: Response = client.post(
        "/api/attachments/tickets",
        json={
            "owner_table": table,
            "owner_id": owner_id,
            "owner_field": field,
            "filename": "현장.png",
        },
        headers=headers,
    )
    return response


def _put(client: TestClient, ticket: str, content: bytes, name: str = "") -> Response:
    """curl `-T <파일> -H 'X-Upload-Ticket: …'` 와 같다 — 토큰이 없다."""
    response: Response = client.put(
        "/api/attachments/upload",
        params={"filename": name} if name else None,
        content=content,
        headers={"X-Upload-Ticket": ticket},
    )
    return response


def test_표로_올리면_붙고_한_번만_쓴다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    issued = _ticket(client, admin, w["id"])
    assert issued.status_code == 201, issued.text
    body = issued.json()
    assert body["upload_path"].endswith("/api/attachments/upload")
    assert body["ticket"] not in body["upload_path"]  # 표는 주소가 아니라 머리로
    assert body["expires_in_seconds"] == 300

    done = _put(client, body["ticket"], _png())
    assert done.status_code == 201, done.text
    made = done.json()
    assert made["is_image"] is True and made["original_name"] == "현장.png"
    assert made["owner_field"] == "photo"

    again = _put(client, body["ticket"], _png())
    assert again.status_code == 401
    assert again.json()["error"]["code"].endswith("FILES-0010")


def test_거절되면_같은_표로_다시_올린다(client: TestClient, admin: Signed) -> None:
    """이미지만 받는 칸에 PDF 를 올려 거절되면 — 표는 성공했을 때만 쓴 것이 된다."""
    w = _world(client, admin, accept="image")
    issued = _ticket(client, admin, w["id"]).json()
    assert issued["accept"] == "image"
    refused = _put(client, issued["ticket"], b"%PDF-1.7", name="성적서.pdf")
    assert refused.status_code == 415
    assert _put(client, issued["ticket"], _png(), name="사진.png").status_code == 201


def test_만료된_표는_못_쓴다(client: TestClient, admin: Signed, db: Session) -> None:
    w = _world(client, admin)
    issued = _ticket(client, admin, w["id"]).json()
    db.execute(
        update(AttachmentTicket)
        .where(AttachmentTicket.token_hash == services._ticket_hash(issued["ticket"]))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db.commit()
    assert _put(client, issued["ticket"], _png()).status_code == 401
    assert _put(client, "missing-ticket-" + "x" * 30, _png()).status_code == 401


def test_표를_낼_때_자리를_본다(client: TestClient, admin: Signed, member: Signed) -> None:
    w = _world(client, admin)
    wrong_field = _ticket(client, admin, w["id"], field="memo")
    assert wrong_field.status_code == 409
    assert wrong_field.json()["error"]["code"].endswith("OBJECTS-0097")
    assert _ticket(client, member, w["id"]).status_code == 403
    other_table = _ticket(client, admin, str(uuid.uuid4()), table="parts")
    assert other_table.status_code == 409
    assert other_table.json()["error"]["code"].endswith("FILES-0009")


def test_쓸_때_다시_본다(client: TestClient, admin: Signed) -> None:
    """표를 받은 뒤 객체가 지워지면 — 그 사이 바뀐 것은 쓸 때 걸린다."""
    w = _world(client, admin)
    issued = _ticket(client, admin, w["id"]).json()
    gone = client.delete(f"/api/objects/{w['type']}/{w['id']}", headers=admin.headers)
    assert gone.status_code == 204, gone.text
    assert _put(client, issued["ticket"], _png()).status_code == 404


def _asgi_put(ticket: str, chunks: int) -> tuple[int, int]:
    """앱을 ASGI 로 직접 불러 (응답 상태, **받아 간 본문 조각 수**) 를 낸다. TestClient 는
    본문을 한 번에 넘겨서 「얼마나 받고 나서 거절했나」 가 안 보인다. 조각은 1KB 씩."""
    import anyio
    from starlette.types import Message

    from app.main import app

    pulled = 0
    statuses: list[int] = []

    async def receive() -> Message:
        nonlocal pulled
        if pulled >= chunks:
            return {"type": "http.disconnect"}
        pulled += 1
        return {"type": "http.request", "body": b"x" * 1024, "more_body": pulled < chunks}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            statuses.append(int(message["status"]))

    path = "/api/attachments/upload"
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PUT",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"x-upload-ticket", ticket.encode()),
            (b"content-length", str(chunks * 1024).encode()),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    anyio.run(app, scope, receive, send)
    return statuses[0], pulled


def test_표부터_보고_본문을_받는다(client: TestClient, admin: Signed) -> None:
    """**표가 자격이다** — 표 없는 PUT 이 본문을 끝까지 보내고 나서야 거절되면, 아무나 50MB 를
    거듭 보내 서버 디스크(임시 파일)를 쓰게 할 수 있다. 본문을 다 받은 뒤에 표를 봤다
    (2026-10-08)."""
    w = _world(client, admin)
    status, pulled = _asgi_put("missing-ticket-" + "x" * 30, chunks=64)
    assert status == 401
    assert pulled == 0
    # 쓴 표도 같다 — 한 번 쓴 표를 다시 들고 오면 본문을 받지 않는다.
    issued = _ticket(client, admin, w["id"]).json()
    status, pulled = _asgi_put(issued["ticket"], chunks=4)
    assert (status, pulled) == (201, 4)
    status, pulled = _asgi_put(issued["ticket"], chunks=64)
    assert (status, pulled) == (401, 0)


def test_크기를_미리_밝히면_받기_전에_끊는다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Content-Length` 가 상한을 넘으면 한 바이트도 받지 않는다 — 받다가 끊는 것은 그 값을 안
    밝히거나 속인 요청의 몫이다. 표는 쓰지 않은 채로 남는다(작게 고쳐 다시 올린다)."""
    monkeypatch.setattr(services, "MAX_BYTES", 2048)
    w = _world(client, admin)
    issued = _ticket(client, admin, w["id"]).json()
    status, pulled = _asgi_put(issued["ticket"], chunks=8)
    assert (status, pulled) == (413, 0)
    assert _put(client, issued["ticket"], b"x" * 100).status_code == 201


def test_크기_상한을_넘으면_받다가_끊는다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(services, "MAX_BYTES", 1000)
    w = _world(client, admin)
    issued = _ticket(client, admin, w["id"]).json()
    # 크기를 안 밝히고(chunked) 보낸다 — 밝힌 요청은 받기 전에 끊긴다(위 시험).
    refused = client.put(
        "/api/attachments/upload",
        content=iter([b"x" * 5000]),
        headers={"X-Upload-Ticket": issued["ticket"]},
    )
    assert "content-length" not in refused.request.headers
    assert refused.status_code == 413
    assert refused.json()["error"]["code"].endswith("FILES-0003")


def test_감사에_표를_낸_토큰이_남는다(client: TestClient, admin: Signed, db: Session) -> None:
    w = _world(client, admin)
    name = f"사진봇_{uuid.uuid4().hex[:6]}"
    token = client.post(
        "/api/auth/tokens",
        json={"name": name, "scopes": ["read", "objects:write"]},
        headers=admin.headers,
    ).json()["token"]
    machine = {"Authorization": f"Bearer {token}"}
    issued = _ticket(client, machine, w["id"])
    assert issued.status_code == 201, issued.text
    assert _put(client, issued.json()["ticket"], _png()).status_code == 201
    entry: Any = db.scalar(
        select(AuditEntry).where(
            AuditEntry.action == "object.attachment.add",
            AuditEntry.target_id == uuid.UUID(w["id"]),
        )
    )
    assert entry is not None and entry.actor_token == name
