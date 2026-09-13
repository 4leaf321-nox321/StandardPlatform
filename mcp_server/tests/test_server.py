"""어댑터가 서는가 — **`mcp` 가 API 를 바꾸면 여기서 걸린다.**

이 시험만 따로 도는 이유: `mcp` 를 백엔드 개발 환경에 깔면 `starlette.testclient`
가 쓰는 HTTP 스택이 바뀌어 그쪽 시험의 타입이 흔들린다 — 실측으로 겪었다. 그래서
**환경을 가른다.**

    cd mcp_server && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt pytest
    cd .. && mcp_server/venv/bin/python -m pytest mcp_server/tests

진짜 앱에 붙여 보는 시험은 백엔드 쪽에 있다(`backend/tests/api/test_mcp_tools.py`).
여기서는 **헤더가 그대로 건너가는가, 오류 봉투가 서버의 말을 잃지 않는가, 가이드가
읽히는가**만 본다 — 백엔드 없이 확인할 수 있는 전부다.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx

import server

TOOLS = {
    "get_guide",
    "ontology_schema",
    "ontology_import",
    "objects_list",
    "objects_summary",
    "object_fields",
    "object_get",
    "object_create",
    "object_update",
    "relation_add",
    "objects_import",
    "bundle_import",
    "relations_import",
    "object_history",
    "object_references",
    "object_rollup",
    "quality_report",
    "datasources_list",
    "datasource_sync",
}


def _ctx(authorization: str | None) -> Any:
    """들어온 MCP HTTP 요청 흉내 — `_forward_headers` 가 보는 것은 헤더뿐이다."""
    headers = {"authorization": authorization} if authorization else {}
    request = SimpleNamespace(headers=headers)
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


def _serve(handler: Any) -> list[httpx.Request]:
    """가짜 백엔드. 받은 요청을 모아 두고 handler 의 응답을 돌려준다."""
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    server._TRANSPORT = httpx.MockTransport(respond)
    return seen


def test_도구가_그대로_선다() -> None:
    """도구 이름은 README·가이드·스킬이 함께 부르는 이름이다 — 하나 빠지면 안내가
    없는 도구를 가리킨다."""
    listed = asyncio.run(server.mcp.list_tools())
    assert {one.name for one in listed} == TOOLS


def test_토큰을_그대로_건넨다() -> None:
    """**만능 토큰이 없다.** 들어온 Authorization 을 그대로 백엔드에 넘기므로 같은
    서버를 여러 사람이 각자 권한으로 쓴다."""
    seen = _serve(lambda _r: httpx.Response(200, json={"types": []}))
    got = asyncio.run(server.ontology_schema(_ctx("Bearer abc")))
    assert got == {"types": []}
    assert seen[0].headers["authorization"] == "Bearer abc"
    assert seen[0].url.path == "/api/ontology/schema"

    seen = _serve(
        lambda _r: httpx.Response(401, json={"error": {"code": "X", "message": "m"}})
    )
    asyncio.run(server.ontology_schema(_ctx(None)))
    assert "authorization" not in seen[0].headers


def test_서버의_말을_그대로_전한다() -> None:
    """오류 문구에 무엇을 고쳐야 하는지가 적혀 있다 — 여기서 고쳐 쓰면 그것을 잃는다."""
    _serve(
        lambda _r: httpx.Response(
            422,
            json={
                "error": {
                    "code": "APP-OBJ-0007",
                    "message": "출력은 500 kW 이하여야 합니다: 9000",
                    "details": {"key": "power"},
                }
            },
        )
    )
    got = asyncio.run(
        server.object_create(
            _ctx("Bearer t"), "mach", label="터빈", properties={"power": 9000}
        )
    )
    assert got == {
        "error": "[APP-OBJ-0007] 출력은 500 kW 이하여야 합니다: 9000",
        "details": {"key": "power"},
    }


def test_빈_성공은_확인_신호가_된다() -> None:
    """돌려줄 게 없는 성공을 그대로 흘리면 도구 결과가 빈 문자열이라 **조용한
    무동작과 구분이 안 된다.**"""
    _serve(lambda _r: httpx.Response(204))
    got = asyncio.run(server.object_update(_ctx("Bearer t"), "mach", "id", label="x"))
    assert got["ok"] is True


def test_미리_보기가_기본이다() -> None:
    """`apply` 를 안 주면 dry_run — 에이전트의 실수가 기계 속도로 반영되지 않게."""
    seen = _serve(lambda _r: httpx.Response(200, json={"applied": False}))
    asyncio.run(server.ontology_import(_ctx("Bearer t"), {"types": []}))
    assert seen[0].url.params["dry_run"] == "true"
    asyncio.run(server.ontology_import(_ctx("Bearer t"), {"types": []}, apply=True))
    assert seen[1].url.params["dry_run"] == "false"

    seen = _serve(lambda _r: httpx.Response(200, json={"applied": False}))
    asyncio.run(server.objects_import(_ctx("Bearer t"), "mach", rows=[{"key": "M-1"}]))
    assert json.loads(seen[0].content)["apply"] is False


def test_조건은_화면과_같은_모양으로_건너간다() -> None:
    """`conditions` 가 `f.<칸>.<연산>=<값>` 로 — 같은 칸 둘은 둘 다(OR)."""
    seen = _serve(lambda _r: httpx.Response(200, json={"items": [], "total": 0}))
    asyncio.run(
        server.objects_list(
            _ctx("Bearer t"),
            "mach",
            conditions=[
                {"field": "power", "op": "gte", "value": "10"},
                {"field": "power", "op": "lte", "value": "50"},
                {"field": "license", "op": "in", "value": "A|B"},
            ],
            status="active",
        )
    )
    params = seen[0].url.params
    assert params["f.power.gte"] == "10" and params["f.power.lte"] == "50"
    assert params["f.license.in"] == "A|B" and params["status"] == "active"


def test_통계는_목록과_같은_거르기로_건너간다() -> None:
    """목록과 통계가 거르기를 따로 만들면 「목록은 12건인데 통계는 15건」 이 된다."""
    seen = _serve(lambda _r: httpx.Response(200, json={"buckets": [], "total": 0}))
    asyncio.run(
        server.objects_summary(
            _ctx("Bearer t"),
            "tool",
            group_by="ref.vendor.country",
            split_by="status",
            q="해석",
            properties={"grade": "A"},
            conditions=[{"field": "ref.vendor.country", "op": "eq", "value": "미국"}],
        )
    )
    request = seen[0]
    params = request.url.params
    assert request.url.path == "/api/objects/tool/summary"
    assert params["group_by"] == "ref.vendor.country" and params["split_by"] == "status"
    assert params["f.ref.vendor.country.eq"] == "미국"
    assert params["p.grade"] == "A" and params["q"] == "해석"
    # 건수로 셀 때는 숫자 칸을 안 보낸다.
    assert "metric_field" not in params


def test_이어진_칸의_주소는_서버에_묻는다() -> None:
    seen = _serve(lambda _r: httpx.Response(200, json=[]))
    asyncio.run(server.object_fields(_ctx("Bearer t"), "tool"))
    assert seen[0].url.path == "/api/objects/tool/fields"


def test_묶음은_미리_보기가_기본이다() -> None:
    """넣는 것은 사람이 미리 보기를 확인한 뒤다 — 기본으로 넣으면 그 자리가 없다."""
    seen = _serve(lambda _r: httpx.Response(200, json={"ok": True, "applied": False}))
    asyncio.run(server.bundle_import(_ctx("Bearer t"), {"objects": []}))
    assert seen[0].url.path == "/api/bundles/import"
    assert json.loads(seen[0].content)["apply"] is False


def test_가이드는_서버가_쥔다() -> None:
    """로컬 스킬에 본문을 두면 사람마다 복사 시점이 달라 낡는다."""
    overview = asyncio.run(server.get_guide(_ctx(None)))
    assert overview["topic"] == "overview"
    assert "ontology_schema" in overview["content"]
    assert set(overview["more_topics"]) >= {"schema", "find", "objects", "bulk", "relations"}

    bulk = asyncio.run(server.get_guide(_ctx(None), topic="bulk"))
    assert bulk["topic"] == "bulk" and "upsert" in bulk["content"]

    missing = asyncio.run(server.get_guide(_ctx(None), topic="없는주제"))
    assert "error" in missing and "bulk" in missing["topics"]
