"""AI 도구마다(ADR 0078) — MCP 가 실은 도구 이름 · 부름 id 를 요청 시간 기록이 남기고, 서버
화면이 도구마다 센다.

여기서 보는 것: 도구 이름 · 부름 id 가 남나(모양이 아니면 버린다) · MCP 가 부른 성공 응답이
0건이었나 · 실패한 응답의 오류 코드(사람의 요청도) · 사람의 성공 응답은 읽지 않음 · 도구마다
부른 수(부름 id 로) · 0건 · 거절 · 오류 코드 · 도구 하나의 경로 틀과 원 경로 · 시스템 관리자만.

시험 DB 를 스위트가 함께 쓰므로 도구 이름은 시험마다 새로 짓는다.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import RequestTiming
from app.shared.request_timing import writer
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_type


def _tool() -> str:
    return f"t_{uuid.uuid4().hex[:10]}"


def _as(who: Signed, tool: str | None, call: str | None = None) -> dict[str, str]:
    headers = dict(who.headers)
    if tool is not None:
        headers["X-SP-Tool"] = tool
    if call is not None:
        headers["X-SP-Tool-Call"] = call
    return headers


def _row(db: Session, response: Any) -> RequestTiming:
    writer.flush()
    found = db.scalar(
        select(RequestTiming).where(
            RequestTiming.request_id == response.headers["x-request-id"]
        )
    )
    assert found is not None, "요청 시간 기록이 남지 않았다"
    return found


def test_도구_이름과_0건_오류_코드가_남는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    tool = _tool()
    part = _make_type(client, admin, label="부품")
    _make_object(client, admin, part, label="후면 커버")

    found = client.get(f"/api/objects/{part}", headers=_as(admin, tool, "a1b2c3"))
    assert found.status_code == 200 and found.json()["total"] == 1
    row = _row(db, found)
    assert (row.tool, row.tool_call, row.empty, row.error_code) == (
        tool,
        "a1b2c3",
        False,
        None,
    )

    none = client.get(
        f"/api/objects/{part}", params={"q": "없는 말 zzz"}, headers=_as(admin, tool, "d4e5f6")
    )
    assert none.status_code == 200 and none.json()["total"] == 0
    assert _row(db, none).empty is True

    # 실패한 응답은 오류 코드 — 0건인지는 보지 않는다.
    missing = client.get("/api/objects/no-such-type-zz", headers=_as(admin, tool, "d4e5f6"))
    assert missing.status_code == 404
    row = _row(db, missing)
    assert row.error_code == missing.json()["error"]["code"] and row.empty is None

    # 모양이 아닌 머리는 버린다 — 아무나 적는 머리다.
    odd = client.get(f"/api/objects/{part}", headers=_as(admin, "Bad Tool!", "zz"))
    row = _row(db, odd)
    assert (row.tool, row.tool_call, row.empty) == (None, None, None)


def test_사람의_요청은_성공_응답을_읽지_않고_실패만_코드를_남긴다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """사람의 화면은 대부분의 줄이다 — 성공 응답까지 읽으면 그 값이 든다. 실패한 응답(오류 봉투
    — 몇 백 바이트)의 코드만 남긴다."""
    part = _make_type(client, admin, label="부품")
    listed = client.get(f"/api/objects/{part}", params={"q": "zzz"}, headers=admin.headers)
    assert listed.json()["total"] == 0
    row = _row(db, listed)
    assert (row.tool, row.empty, row.error_code) == (None, None, None)

    refused = client.post(f"/api/objects/{part}", json={}, headers=admin.headers)
    assert refused.status_code == 422
    assert _row(db, refused).error_code == refused.json()["error"]["code"]


def test_도구마다_부른_수_0건_거절과_도구_하나의_경로(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    tool = _tool()
    part = _make_type(client, admin, label="부품")
    obj = _make_object(client, admin, part, label="후면 커버")
    # 부름 셋: 하나는 백엔드를 두 번 부른다(같은 id) — 부른 수는 셋, 요청은 넷.
    client.get(f"/api/objects/{part}", headers=_as(admin, tool, "aaa111"))
    client.get(f"/api/objects/{part}/{obj['id']}", headers=_as(admin, tool, "aaa111"))
    client.get(
        f"/api/objects/{part}", params={"q": "zzz"}, headers=_as(member, tool, "bbb222")
    )
    missing = client.get("/api/objects/no-such-type-zz", headers=_as(member, tool, "ccc333"))
    writer.flush()

    assert client.get("/api/server/performance/ai", headers=member.headers).status_code == 403
    got = client.get(
        "/api/server/performance/ai", params={"period": "1h"}, headers=admin.headers
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["first_recorded_at"] is not None
    (mine,) = [one for one in body["items"] if one["tool"] == tool]
    assert (mine["calls"], mine["requests"], mine["people"]) == (3, 4, 2)
    assert (mine["empty"], mine["rejected"], mine["failed"]) == (1, 1, 0)
    assert mine["codes"] == [{"code": missing.json()["error"]["code"], "count": 1}]

    detail = client.get(
        f"/api/server/performance/ai/{tool}", params={"period": "1h"}, headers=admin.headers
    )
    assert detail.status_code == 200, detail.text
    routes = {one["route"]: one for one in detail.json()["routes"]}
    listed = routes["/api/objects/{type_slug}"]
    assert listed["requests"] == 3 and listed["empty"] == 1 and listed["rejected"] == 1
    # 원 경로(타입이 든다) — 많이 부른 것부터.
    assert listed["paths"][0] == {"path": f"/api/objects/{part}", "requests": 2}
    assert routes["/api/objects/{type_slug}/{object_id}"]["requests"] == 1
    assert [one["count"] for one in detail.json()["codes"]] == [1]

    # 30일도 고를 수 있다(기록의 기본 보관 기간).
    month = client.get(
        "/api/server/performance/ai", params={"period": "30d"}, headers=admin.headers
    )
    assert month.status_code == 200
