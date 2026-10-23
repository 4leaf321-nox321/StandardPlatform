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
import importlib.util
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import server

TOOLS = {
    "get_guide",
    "whoami",
    "search",
    "ontology_schema",
    "ontology_import",
    "table_infer",
    "table_build",
    "ontology_delete",
    "ontology_retype",
    "ontology_rename_option",
    "ontology_promote",
    "ontology_restore",
    "objects_list",
    "object_resolve",
    "objects_resolve_many",
    "aliases_pending",
    "aliases_review",
    "objects_summary",
    "object_fields",
    "object_get",
    "objects_similar",
    "object_create",
    "object_update",
    "objects_delete",
    "object_restore",
    "object_merge",
    "attachment_upload_prepare",
    "attachment_remove",
    "relation_add",
    "objects_import",
    "bundle_import",
    "bundle_runs",
    "bundle_undo",
    "relations_import",
    "object_history",
    "object_references",
    "object_rollup",
    "object_tree",
    "graph_neighbors",
    "graph_path",
    "graph_overview",
    "bulk_edit",
    "bulk_edit_undo",
    "audit_recent",
    "relation_update",
    "relation_remove",
    "quality_report",
    "datasources_list",
    "datasource_sync",
    "rdf_schema",
    "rdf_query",
    "job_status",
    "job_apply",
    "jobs_list",
    "job_cancel",
    "filestore_gc",
    "datasource_runs",
    "datasource_preview",
    "server_maintenance",
    "notifications",
    "metric_recompute",
    "metric_runs",
    "metric_home",
    "extensions_schema",
    "extension_call",
    "metric_list",
    "metric_query",
    "metric_analyze",
    "metric_alerts",
    "insights",
    "metric_define",
    "platform_profile",
    "platform_profile_update",
    "fill_priorities",
    "alias_candidates",
    "alias_candidate_apply",
    "review_list",
    "review_decide",
    "metric_alert_save",
    "config_delete",
    "datasource_save",
    "core_status",
    "workspaces",
    "views",
    "view_save",
    "object_watch",
    "object_years",
    "webhooks",
    "notices",
    "review_history",
    "review_undo",
    "server_status",
    "jobs_purge",
    "export_start",
}


def _ctx(authorization: str | None) -> Any:
    """들어온 MCP HTTP 요청 흉내 — `_forward_headers` 가 보는 것은 헤더뿐이다."""
    headers = {"authorization": authorization} if authorization else {}
    request = SimpleNamespace(headers=headers)
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


def _serve(handler: Any, *, admin: bool | None = True) -> list[httpx.Request]:
    """가짜 백엔드. 받은 요청을 모아 두고 handler 의 응답을 돌려준다.

    위험 도구는 부를 때마다 `GET /api/auth/me` 로 시스템 관리자인지 먼저 묻는다 — 그
    물음은 `admin` 으로 답하고 `seen` 에 넣지 않는다(시험마다 첫 요청이 그것이면 시험이
    도구가 하는 일을 못 본다). `admin=None` 이면 그 물음도 handler 가 받는다.
    """
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if admin is not None and request.url.path == "/api/auth/me":
            return httpx.Response(200, json={"is_system_admin": admin})
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
    with pytest.raises(server.BackendUnauthorized):
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

    # 여러 행 넣기는 **작업**이다 — 계획만 세우는 작업을 만들고, 적용은 job_apply 로만.
    seen = _serve(
        lambda _r: httpx.Response(
            202, json={"id": "j1", "kind": "objects_import", "status": "done"}
        )
    )
    got = asyncio.run(server.objects_import(_ctx("Bearer t"), "mach", rows=[{"key": "M-1"}]))
    assert seen[0].url.path == "/api/jobs"
    assert b'name="kind"' in seen[0].content and b"objects_import" in seen[0].content
    assert got["job_id"] == "j1" and "job_apply" in got["next"]


def test_지우기와_값까지_바꾸는_수정도_미리_보기가_기본이다() -> None:
    """지우기 · 종류 변경 · 이름 변경 · 승격 · 복원 · 합치기 — **안 바꾸는 쪽이 기본**이다.
    기계의 실수는 기계 속도로 반영되고, 지운 것은 대개 되돌리기 어렵다."""
    ctx = _ctx("Bearer t")
    seen = _serve(lambda _r: httpx.Response(200, json={"applied": False, "object": {}}))

    asyncio.run(server.ontology_delete(ctx, "property", "part", key="w"))
    assert seen[-1].method == "GET" and seen[-1].url.path == "/api/ontology/delete-plan"
    assert dict(seen[-1].url.params) == {"kind": "property", "slug": "part", "key": "w"}

    asyncio.run(server.ontology_retype(ctx, "equip", "maker", "enum", owner="interface"))
    assert seen[-1].url.path == "/api/ontology/interfaces/equip/properties/maker/retype"
    assert json.loads(seen[-1].content)["apply"] is False

    asyncio.run(server.ontology_rename_option(ctx, "part", "grade", "상", "A"))
    assert json.loads(seen[-1].content) == {"from": "상", "to": "A", "apply": False}

    asyncio.run(server.ontology_promote(ctx, "part", "grade", new_slug="grade"))
    assert json.loads(seen[-1].content)["apply"] is False

    asyncio.run(server.ontology_restore(ctx, "s1"))
    assert seen[-1].url.path == "/api/ontology/snapshots/s1/restore"
    assert seen[-1].url.params["dry_run"] == "true"

    asyncio.run(server.objects_delete(ctx, "part", ["o1"]))
    assert json.loads(seen[-1].content) == {"ids": ["o1"], "mode": "block", "apply": False}

    before = len(seen)
    asyncio.run(server.object_merge(ctx, "part", "o1", "o2"))
    assert [one.method for one in seen[before:]] == ["GET", "GET", "GET"], "읽기만 한다"

    # 적용은 **의도를 적어야** 일어난다 — 그때 각자의 자리로 간다.
    seen = _serve(lambda _r: httpx.Response(204))
    got = asyncio.run(
        server.ontology_delete(ctx, "property", "part", key="w", apply=True, accept_core=True)
    )
    assert seen[-1].method == "DELETE"
    assert seen[-1].url.path == "/api/ontology/types/part/properties/w"
    assert seen[-1].url.params["accept_core"] == "true"
    assert got["ok"] is True and "part.w" in got["message"]

    asyncio.run(server.ontology_delete(ctx, "relation_type", "uses", apply=True))
    assert seen[-1].url.path == "/api/ontology/relation-types/uses"
    assert "accept_core" not in seen[-1].url.params

    # 영구 삭제는 **타입에만**, 확인을 적었을 때만 실린다(ADR 0008).
    asyncio.run(server.ontology_delete(ctx, "type", "part", apply=True))
    assert "purge_deleted" not in seen[-1].url.params
    asyncio.run(server.ontology_delete(ctx, "type", "part", apply=True, purge_deleted=True))
    assert seen[-1].url.params["purge_deleted"] == "true"
    asyncio.run(server.ontology_delete(ctx, "group", "g", apply=True, purge_deleted=True))
    assert "purge_deleted" not in seen[-1].url.params

    # 모르는 것은 보내지 않는다 — 짐작해서 다른 자리로 보내면 엉뚱한 것이 지워진다.
    count = len(seen)
    assert "error" in asyncio.run(server.ontology_delete(ctx, "table", "part", apply=True))
    assert "error" in asyncio.run(server.ontology_delete(ctx, "property", "part", apply=True))
    assert "error" in asyncio.run(server.ontology_retype(ctx, "p", "k", "text", owner="x"))
    assert "error" in asyncio.run(server.objects_delete(ctx, "p", ["o1"], mode="force"))
    assert len(seen) == count


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


def test_분석은_레시피와_options_를_경로와_질의로_건넨다() -> None:
    """레시피는 경로, options 는 질의, filters 는 `d.<기준>` — 모르는 레시피 · options 는
    보내기 전에 거절한다(백엔드는 모르는 질의를 조용히 버린다)."""
    seen = _serve(lambda _r: httpx.Response(200, json={"recipe": "sprt"}))
    got = asyncio.run(
        server.metric_analyze(
            _ctx("Bearer t"),
            "m1",
            "SPRT",
            options={"target": "x", "reference": "y", "rho": 1.5, "beta": None},
            filters={"region": "KR", "empty": None},
        )
    )
    assert got == {"recipe": "sprt"}
    request = seen[0]
    params = request.url.params
    assert request.url.path == "/api/metrics/m1/analysis/sprt"
    assert params["target"] == "x" and params["reference"] == "y" and params["rho"] == "1.5"
    assert "beta" not in params and params["compact"] == "true"
    assert params["d.region"] == "KR" and params["d.empty"] == ""
    seen = _serve(lambda _r: httpx.Response(200, json={}))
    asyncio.run(
        server.metric_analyze(
            _ctx("Bearer t"),
            "m1",
            "pareto",
            options={"dim": "part", "by_period": True},
            compact=False,
        )
    )
    assert seen[0].url.params["by_period"] == "true"
    assert seen[0].url.params["compact"] == "false"
    # 목록 options 는 쉼표로 — 위험 요인의 factors.
    seen = _serve(lambda _r: httpx.Response(200, json={}))
    asyncio.run(
        server.metric_analyze(
            _ctx("Bearer t"), "m1", "logit", options={"factors": ["factory", "symptom"]}
        )
    )
    assert seen[0].url.params["factors"] == "factory,symptom"
    seen = _serve(lambda _r: httpx.Response(200, json={}))
    wrong = asyncio.run(server.metric_analyze(_ctx("Bearer t"), "m1", "anova"))
    assert "recipe" in wrong["error"]
    stray = asyncio.run(
        server.metric_analyze(_ctx("Bearer t"), "m1", "life", options={"dim": "x"})
    )
    assert "dim" in stray["error"] and "max_age" in stray["error"]
    assert seen == []


def test_경보는_내_발생을_읽고_지표를_주면_그_지표만() -> None:
    """만들기는 화면에서 — 이 도구는 읽기만 한다. 지표를 주면 그 지표의 경보와 발생만."""
    events = [{"metric": "m1", "title": "a"}, {"metric": "m2", "title": "b"}]

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/metrics/alerts/events":
            return httpx.Response(200, json=events)
        return httpx.Response(200, json=[{"id": "x", "name": "경보"}])

    seen = _serve(respond)
    got = asyncio.run(server.metric_alerts(_ctx("Bearer t"), limit=999))
    assert got == {"events": events}
    assert seen[0].url.params["limit"] == "200" and seen[0].method == "GET"
    seen = _serve(respond)
    one = asyncio.run(server.metric_alerts(_ctx("Bearer t"), "m1"))
    assert one == {"alerts": [{"id": "x", "name": "경보"}], "events": [events[0]]}
    assert [request.url.path for request in seen] == [
        "/api/metrics/alerts/events",
        "/api/metrics/m1/alerts",
    ]


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
    assert set(overview["more_topics"]) >= {
        "modeling",
        "schema",
        "find",
        "objects",
        "bulk",
        "relations",
    }
    # 규약은 **공통 코어를 가리킨다** — AI 가 무엇부터 쓸지 여기서 안다.
    modeling = asyncio.run(server.get_guide(_ctx(None), topic="modeling"))
    assert "plm-core.json" in modeling["content"] and "work-core.json" in modeling["content"]

    # 인터페이스(ADR 0006) — 같은 개념을 여러 타입이 따로 적지 않게, 정의를 바꾸는 AI 가 안다.
    schema = asyncio.run(server.get_guide(_ctx(None), topic="schema"))
    assert "interface_slugs" in schema["content"] and "parent_slug" in schema["content"]

    bulk = asyncio.run(server.get_guide(_ctx(None), topic="bulk"))
    assert bulk["topic"] == "bulk" and "upsert" in bulk["content"]
    # 지우는 모드와 검수는 **가이드에 적혀 있어야** AI 가 묻기 전에 안다.
    assert "aliases_mode" in bulk["content"] and "aliases_review" in bulk["content"]

    relations = asyncio.run(server.get_guide(_ctx(None), topic="relations"))
    assert "replace_type" in relations["content"] and "unlink" in relations["content"]

    # 분석(ADR 0014) — 전할 때의 규칙이 **가이드에 있어야** 「이르지 않음」 을 외삽으로,
    # 「아직」 을 「문제없음」 으로 옮기지 않는다.
    metrics = asyncio.run(server.get_guide(_ctx(None), topic="metrics"))
    assert "metric_analyze" in metrics["content"] and "unreachable" in metrics["content"]
    assert "metric_analyze" in overview["content"]

    # 보고서 기록(ADR 0018) — 잘린 본문을 끝까지 읽는 길과 「원본에서 내려감」 을 전하는 법.
    reports = asyncio.run(server.get_guide(_ctx(None), topic="reports"))
    assert "text_from" in reports["content"] and "원본에서 내려감" in reports["content"]
    assert 'topic="reports"' in overview["content"]

    missing = asyncio.run(server.get_guide(_ctx(None), topic="없는주제"))
    assert "error" in missing and "bulk" in missing["topics"]


def test_질의는_그대로_넘기고_본문을_돌려준다() -> None:
    """SPARQL 은 서버가 검사한다 — MCP 는 고쳐 쓰지 않고 그대로 넘긴다."""
    seen = _serve(lambda _r: httpx.Response(200, json={"columns": ["x"], "rows": []}))
    got = asyncio.run(
        server.rdf_query(_ctx("Bearer t"), "SELECT ?x WHERE { ?x ?y ?z }", types=["part"])
    )
    assert got == {"columns": ["x"], "rows": []}
    assert seen[0].url.path == "/api/rdf/query"
    body = json.loads(seen[0].content)
    assert body["query"].startswith("SELECT") and body["types"] == ["part"]
    assert body["infer"] is False and body["limit"] == 200

    # 정의는 Turtle 본문 그대로 — JSON 이 아니다.
    seen = _serve(lambda _r: httpx.Response(200, text="sp:part a owl:Class ."))
    assert asyncio.run(server.rdf_schema(_ctx("Bearer t"))) == "sp:part a owl:Class ."
    assert seen[0].url.path == "/api/rdf/schema"


def test_자취는_값을_안_남기고_모양만_남긴다() -> None:
    """자취는 「AI 가 어디서 헤맸나」 를 보려는 것이지 데이터를 모으려는 게 아니다 —
    객체 이름·속성 값이 파일에 쌓이면 그 파일 자체가 유출 경로가 된다."""
    assert server._signal({"total": 0, "diagnosis": {"reason": "empty_type"}}) == {
        "total": 0,
        "outcome": "empty",
        "reason": "empty_type",
    }
    assert server._signal({"match": "candidates", "candidates": [{"label": "한국소재"}]}) == {
        "outcome": "ok",
        "match": "candidates",
    }
    # 오류는 **코드만** — 문구에는 객체 이름이 들어간다.
    assert server._signal({"error": "[APP-OBJ-0007] 출력은 500 kW 이하여야 합니다: 9000"}) == {
        "outcome": "error",
        "code": "APP-OBJ-0007",
    }


def test_자취를_켜도_도구는_그대로_선다(tmp_path: Any) -> None:
    """감싸면 `FastMCP` 가 인자 모양을 못 읽을 수 있다 — 그러면 도구는 서는데
    **인자가 없는 도구**가 되고, 그 사실은 붙여 보기 전까지 안 보인다."""
    path = tmp_path / "trace.jsonl"
    os.environ["MCP_TRACE_FILE"] = str(path)
    try:
        spec = importlib.util.spec_from_file_location(
            "server_traced", Path(server.__file__).resolve()
        )
        assert spec is not None and spec.loader is not None
        traced = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(traced)
    finally:
        del os.environ["MCP_TRACE_FILE"]

    listed = asyncio.run(traced.mcp.list_tools())
    assert {one.name for one in listed} == TOOLS
    resolve = next(one for one in listed if one.name == "object_resolve")
    assert {"type_slug", "name"} <= set(resolve.inputSchema["properties"])

    traced._TRANSPORT = httpx.MockTransport(
        lambda _r: httpx.Response(200, json={"match": "none", "hint": "없습니다"})
    )
    asyncio.run(traced.object_resolve(_ctx("Bearer t"), "vendor", "없는회사"))
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert line["tool"] == "object_resolve" and line["match"] == "none"
    assert "없는회사" not in path.read_text(encoding="utf-8")


def test_확장_호출은_그_확장_뿌리로만_간다() -> None:
    """경로를 글자로 받는 도구다 — **뿌리 밖으로 나가는 길이 있으면** 이 도구가 확장과
    무관한 자리를 부르는 문이 된다."""
    seen = _serve(lambda _r: httpx.Response(200, json={"rows": []}))
    got = asyncio.run(
        server.extension_call(
            _ctx("Bearer t"), "caegroup", "dt/pairs", query={"workspace": "cae"}
        )
    )
    assert got == {"rows": []}
    assert seen[0].url.path == "/api/ext/caegroup/dt/pairs"
    assert seen[0].url.params["workspace"] == "cae"
    assert seen[0].headers["authorization"] == "Bearer t"

    for bad in ("../../objects/mach", "https://elsewhere/x", "dt/pairs?workspace=cae"):
        got = asyncio.run(server.extension_call(_ctx("Bearer t"), "caegroup", bad))
        assert "error" in got, bad
    # 이름도 경로를 짓는 값이다.
    assert "error" in asyncio.run(server.extension_call(_ctx("Bearer t"), "cae/group", "dt"))
    # 메서드는 표에 있는 것만.
    assert "error" in asyncio.run(
        server.extension_call(_ctx("Bearer t"), "caegroup", "dt/pairs", method="OPTIONS")
    )
    # 막힌 것은 **부르기 전에** 막는다 — 위 다섯 번에 요청이 나가지 않았다.
    assert len(seen) == 1


def test_본문은_본문_있는_메서드에만_실린다() -> None:
    """GET 에 본문을 실으면 프록시가 자르거나 서버가 거절한다 — 부르는 쪽이 그것을 모른다."""
    seen = _serve(lambda _r: httpx.Response(200, json={"ok": True}))
    asyncio.run(
        server.extension_call(
            _ctx("Bearer t"),
            "caegroup",
            "dt/assessments/bulk",
            method="PUT",
            body={"axis": "a"},
        )
    )
    assert json.loads(seen[0].content) == {"axis": "a"}
    asyncio.run(
        server.extension_call(_ctx("Bearer t"), "caegroup", "dt/defs", body={"버릴": "것"})
    )
    assert not seen[1].content


def test_파일을_주는_자리는_바이트를_안_흘린다() -> None:
    """엑셀을 주는 자리다. 바이트를 도구 결과로 흘리면 대화가 쓰레기로 찬다."""
    _serve(
        lambda _r: httpx.Response(
            200, content=b"a,b\n1,2\n", headers={"content-type": "text/csv; charset=utf-8"}
        )
    )
    got = asyncio.run(
        server.extension_call(_ctx("Bearer t"), "caegroup", "dt/assessments/sheet/export")
    )
    assert got["ok"] is True and "파일 응답" in got["message"]


def test_적재가_사람이_고친_칸을_기본으로_지킨다() -> None:
    """**적재가 사람의 수정을 조용히 되돌리면 사람은 고치기를 그만둔다.** 기본이 지키는
    쪽이어야 하고, 덮는 것은 사용자가 그렇게 정했을 때만이다."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "job-1", "status": "done", "result": {}})

    seen = _serve(handler)
    asyncio.run(server.objects_import(_ctx("Bearer t"), "part", rows=[{"key": "P-1"}]))
    asyncio.run(
        server.objects_import(
            _ctx("Bearer t"), "part", rows=[{"key": "P-1"}], human_edits="overwrite"
        )
    )
    bodies = [one.read() for one in seen if one.url.path == "/api/jobs"]
    assert b'"human_edits":"keep"' in bodies[0].replace(b" ", b""), bodies[0][:400]
    assert b'"human_edits":"overwrite"' in bodies[1].replace(b" ", b""), bodies[1][:400]


def test_적재_도구가_맞춤과_검수를_보낸다() -> None:
    """**새 옵션이 도구에 없으면 MCP 로 넣는 사람은 그 길을 못 쓴다.**

    별칭을 파일대로 맞추기 · 사라진 관계 끊기 · 이름 여럿 풀기 · 별칭 검수 — 넷 다 API 에는
    있었지만 도구에 안 붙어 있어서, AI 로 적재하면 기본값으로만 돌았다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/resolve-many"):
            return httpx.Response(200, json={"items": [], "counts": {}})
        if request.url.path.endswith("/aliases/pending"):
            return httpx.Response(200, json={"items": [], "total": 0})
        if request.url.path.endswith("/aliases/review"):
            return httpx.Response(200, json={"done": 1, "refused": []})
        return httpx.Response(202, json={"id": "j1", "kind": "x", "status": "done"})

    seen = _serve(handler)

    asyncio.run(
        server.objects_import(
            _ctx("Bearer t"), "mach", rows=[{"key": "M-1"}], aliases_mode="replace"
        )
    )
    assert b'"aliases_mode": "replace"' in seen[-1].content.replace(b",", b", ")

    asyncio.run(
        server.relations_import(
            _ctx("Bearer t"),
            "mach",
            rows=[{"src": "a", "relation": "r", "dst": "b"}],
            mode="replace_type",
        )
    )
    assert b"replace_type" in seen[-1].content

    asyncio.run(server.objects_resolve_many(_ctx("Bearer t"), "mach", ["가", "나"]))
    assert seen[-1].url.path.endswith("/mach/resolve-many")
    assert b'"names"' in seen[-1].content

    asyncio.run(server.aliases_pending(_ctx("Bearer t"), "mach", limit=5))
    assert seen[-1].url.path.endswith("/mach/aliases/pending")

    asyncio.run(server.aliases_review(_ctx("Bearer t"), "mach", ["a1"], action="remove"))
    assert b'"action":"remove"' in seen[-1].content


def test_채울_곳과_별칭_후보가_그대로_건너간다() -> None:
    """「어디부터 채우나」 와 못 찾은 말 — **붙이기 · 무시는 미리 보기가 기본이다.** 별칭은 그
    뒤로 이름 풀이 · 검색 · 적재가 전부 쓰므로, 틀리게 붙인 별칭은 틀린 객체로 풀린다."""
    seen = _serve(lambda _r: httpx.Response(200, json={"items": [], "total": 0}))
    asyncio.run(server.fill_priorities(_ctx("Bearer t"), type_slug="part", limit=5))
    assert seen[-1].url.path == "/api/objects/fill-priorities"
    assert seen[-1].url.params["type"] == "part" and seen[-1].url.params["limit"] == "5"

    # 빈 글자는 「통합 검색에서 못 찾은 것」 — None(전부)과 달라서 그대로 건너가야 한다.
    asyncio.run(server.alias_candidates(_ctx("Bearer t"), type_slug=""))
    assert seen[-1].url.path == "/api/objects/alias-candidates"
    assert seen[-1].url.params["type"] == "" and seen[-1].url.params["status"] == "pending"
    asyncio.run(server.alias_candidates(_ctx("Bearer t")))
    assert "type" not in seen[-1].url.params

    # 어느 객체인지 안 정했으면 부르지도 않는다.
    before = len(seen)
    got = asyncio.run(server.alias_candidate_apply(_ctx("Bearer t"), "c1"))
    assert "object_id" in got["error"] and len(seen) == before

    asyncio.run(server.alias_candidate_apply(_ctx("Bearer t"), "c1", object_id="o1"))
    assert seen[-1].url.path == "/api/objects/alias-candidates/c1/attach"
    assert json.loads(seen[-1].content) == {"object_id": "o1", "apply": False}

    preview = asyncio.run(
        server.alias_candidate_apply(_ctx("Bearer t"), "c1", action="ignore")
    )
    assert preview["applied"] is False and seen[-1].method == "GET"
    assert seen[-1].url.path == "/api/objects/alias-candidates/c1"

    _serve(lambda _r: httpx.Response(200, json={"done": 1, "refused": []}))
    done = asyncio.run(
        server.alias_candidate_apply(_ctx("Bearer t"), "c1", action="ignore", apply=True)
    )
    assert done == {"applied": True, "action": "ignore", "done": 1, "refused": []}
    assert "error" in asyncio.run(
        server.alias_candidate_apply(_ctx("Bearer t"), "c1", action="drop", apply=True)
    )


def test_검토함은_미리_보기_먼저이고_지문이_같을_때만_적용한다() -> None:
    """`review_decide` 는 **늘 미리 보기 먼저** — 적용은 사람이 본 지문(`fingerprint`)과 지금
    다시 본 계획의 지문이 같을 때만. 다르면 백엔드에 적용을 보내지도 않는다."""
    seen = _serve(lambda _r: httpx.Response(200, json={"items": [], "kinds": []}))
    asyncio.run(
        server.review_list(
            _ctx("Bearer t"), kind="alias_review", type_slug="vendor", min_confidence=0.9
        )
    )
    assert seen[-1].url.path == "/api/review/items"
    params = seen[-1].url.params
    assert params["kind"] == "alias_review" and params["type"] == "vendor"
    assert params["min_confidence"] == "0.9" and "workspace" not in params

    # 무엇을 정할지 없으면 부르지도 않는다 — 스스로 고르지 않는다.
    before = len(seen)
    assert "error" in asyncio.run(server.review_decide(_ctx("Bearer t")))
    assert len(seen) == before

    planned = {
        "fingerprint": "fp1",
        "decisions": [{"kind": "alias_review", "item_id": "a1", "action": "accept"}],
        "counts": {"apply": 1, "blocked": 0},
    }

    def backend(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/review/plan":
            return httpx.Response(200, json=planned)
        return httpx.Response(200, json={"applied": True, "counts": {"done": 1}})

    seen = _serve(backend)
    preview = asyncio.run(
        server.review_decide(_ctx("Bearer t"), recommended=True, kind="alias_review")
    )
    assert preview["applied"] is False and "fp1" in preview["next"]
    body = json.loads(seen[-1].content)
    assert body["mode"] == "recommended" and body["threshold"] == 0.9
    assert body["filters"] == {"kind": "alias_review"}
    assert all(one.url.path != "/api/review/apply" for one in seen)

    # 지문 없이는 적용하지 않는다.
    assert "error" in asyncio.run(
        server.review_decide(
            _ctx("Bearer t"), recommended=True, kind="alias_review", apply=True
        )
    )
    # 그 사이 바뀌었다(지문이 다르다) — 적용을 보내지 않는다.
    stale = asyncio.run(
        server.review_decide(
            _ctx("Bearer t"),
            recommended=True,
            kind="alias_review",
            apply=True,
            fingerprint="old",
        )
    )
    assert "error" in stale and all(one.url.path != "/api/review/apply" for one in seen)

    done = asyncio.run(
        server.review_decide(
            _ctx("Bearer t"),
            recommended=True,
            kind="alias_review",
            apply=True,
            fingerprint="fp1",
        )
    )
    assert done["applied"] is True
    assert seen[-1].url.path == "/api/review/apply"
    sent = json.loads(seen[-1].content)
    assert sent["decisions"] == planned["decisions"] and sent["fingerprint"] == "fp1"
    assert sent["mode"] == "recommended" and sent["threshold"] == 0.9


def test_기동_블록_뒤에_도구가_없다() -> None:
    """`mcp.run()` 은 블로킹이다 — 그 뒤에 적힌 `@tool()` 은 운영(`python server.py`)에서
    등록되지 않는다. 확장 도구 둘이 그렇게 빠져 있었고, 이 시험은 모듈을 import 해 보므로
    `list_tools` 로는 못 잡는다 — 그래서 **소스의 순서**를 본다."""
    source = (Path(server.__file__)).read_text(encoding="utf-8")
    boot = source.index('if __name__ == "__main__":')
    assert "@tool()" not in source[boot:], (
        "기동 블록 뒤에 도구가 있습니다 — 블록을 파일 끝으로"
    )


def test_통계의_기간_단위와_시간순이_그대로_건너간다() -> None:
    """「월별 추이」 는 `grain="month", order="key"` 다 — 서버가 월로 묶고 시간순으로 세운다.
    도구가 그 둘을 떨어뜨리면 해 단위 · 건수 순이 되어 추이가 아니다."""
    seen = _serve(lambda _r: httpx.Response(200, json={"buckets": []}))
    asyncio.run(
        server.objects_summary(
            _ctx("Bearer abc"),
            "svc_case",
            group_by="properties.made",
            grain="month",
            order="key",
        )
    )
    params = seen[0].url.params
    assert params["grain"] == "month" and params["order"] == "key"
    assert params["group_by"] == "properties.made"


def test_긴_글은_잘라_주고_어디까지인지_말한다() -> None:
    """보고서 본문 같은 긴 글 — 목록은 앞부분만, 상세는 한 번에 일정 길이씩 이어 읽는다.

    목록 50건이 본문째 오면 답 하나가 문맥을 채운다. 잘랐으면 잘랐다고 적는다 — 앞부분을
    전부로 읽고 「본문에 없다」 고 답하지 않게."""
    body = "가" * (server.GET_TEXT + 500)
    one = {"id": "o1", "label": "보고서", "properties": {"body": body, "author": "홍길동"}}
    _serve(
        lambda r: httpx.Response(
            200,
            json={"items": [one], "total": 1} if r.url.path.endswith("/ra_report") else one,
        )
    )
    listed = asyncio.run(server.objects_list(_ctx("Bearer t"), "ra_report"))
    row = listed["items"][0]
    assert len(row["properties"]["body"]) == server.LIST_TEXT
    assert row["properties"]["author"] == "홍길동" and "author" not in row["clipped"]
    assert row["clipped"]["body"] == {"from": 0, "to": server.LIST_TEXT, "length": len(body)}

    first = asyncio.run(server.object_get(_ctx("Bearer t"), "ra_report", "o1"))
    assert first["clipped"]["body"]["to"] == server.GET_TEXT
    rest = asyncio.run(
        server.object_get(_ctx("Bearer t"), "ra_report", "o1", text_from=server.GET_TEXT)
    )
    assert rest["properties"]["body"] == "가" * 500
    assert rest["clipped"]["body"] == {
        "from": server.GET_TEXT,
        "to": len(body),
        "length": len(body),
    }


PROFILE = {
    "slug": "caedatahub",
    "name": "CAE 그룹 Datahub",
    "tagline": "해석 데이터의 자리",
    "summary": "CAE 그룹의 보고서 · 해석 기록과 개발모델",
    "notes": "개발모델 · 과제의 정본은 허브다.",
    "updated_at": "2026-10-04T10:00:00Z",
}
LIVE = {
    **PROFILE,
    "facts": [
        {
            "key": "types",
            "label": "담긴 것",
            "lines": ["기록 — 보고서 1.2만", "축 — 개발모델 6,000"],
        },
        {
            "key": "datasources",
            "label": "들어오는 곳",
            "lines": ["CAE 보고서(RA 보고서, 조직 cae) → 보고서"],
        },
    ],
    "stale": ["생김: 타입 「보고서」"],
}


def test_안내문_첫머리는_사람의_소개_지금_담긴_것_낡음을_함께_싣는다() -> None:
    text = server.identity(LIVE)
    assert text.startswith("**이 서버는 「CAE 그룹 Datahub」(`caedatahub`)의 것이다.**")
    assert "담는 것(사람이 쓴 소개, 2026-10-04): CAE 그룹의 보고서" in text
    assert "  - 담긴 것: 기록 — 보고서 1.2만" in text and "  - 들어오는 곳: CAE 보고서" in text
    assert (
        "⚠ 사람이 쓴 소개는 그 뒤 달라진 것을 반영하지 않았다 — 생김: 타입 「보고서」" in text
    )
    assert server.SAME_TOOLS_RULE in text
    # 사람의 소개가 없으면 낡음이 아니라 「없다」 — 자동 요약이 대신 말한다.
    blank = server.identity({**LIVE, "summary": "", "stale": ["자기소개를 아직 안 적었다"]})
    assert "사람이 쓴 소개가 아직 없다 — 아래 「지금 담긴 것」" in blank and "⚠" not in blank
    anonymous = server.identity({**PROFILE, "summary": ""})  # 토큰 없이 — 요약이 없다
    assert "`whoami` 의 `platform.facts`" in anonymous
    assert "자기소개를 읽지 못했다" in server.identity(None)


def test_접속하는_사람의_토큰으로_지금_담긴_것을_세어_안내문에_싣는다() -> None:
    """진짜 MCP 앱에 초기화를 보낸다 — 접속 요청의 토큰이 백엔드 `live` 에 실려야 한다.
    토큰이 없으면 로그인 없는 소개(사람의 문장)만."""
    from starlette.testclient import TestClient

    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/server/profile/live":
            return httpx.Response(200, json=LIVE)
        return httpx.Response(200, json=PROFILE)

    server._SYNC_TRANSPORT = httpx.MockTransport(respond)
    server._profile_cache.clear()
    hello = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
    accept = {"Accept": "application/json, text/event-stream"}

    def instructions(got: httpx.Response) -> str:
        assert got.status_code == 200, got.text
        body = got.text
        if body.lstrip().startswith("{"):
            return str(json.loads(body)["result"]["instructions"])
        data = next(line[5:] for line in body.splitlines() if line.startswith("data:"))
        return str(json.loads(data)["result"]["instructions"])

    try:
        with TestClient(server.http_app(), base_url="http://127.0.0.1:8042") as client:
            signed = client.post(
                "/mcp", json=hello, headers={**accept, "Authorization": "Bearer t1"}
            )
            text = instructions(signed)
            assert "  - 담긴 것: 기록 — 보고서 1.2만" in text and "⚠" in text
            live = next(one for one in seen if one.url.path == "/api/server/profile/live")
            assert live.headers["authorization"] == "Bearer t1"
            seen.clear()
            anonymous = instructions(client.post("/mcp", json=hello, headers=accept))
            assert "담는 것(사람이 쓴 소개" in anonymous and "지금 담긴 것" not in anonymous
            assert [one.url.path for one in seen] == ["/api/server/profile"]
    finally:
        server._SYNC_TRANSPORT = None
        server._profile_cache.clear()


def test_토큰마다_짧게_붙들고_백엔드가_안_닿으면_붙들던_것으로() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=LIVE)

    server._SYNC_TRANSPORT = httpx.MockTransport(respond)
    server._profile_cache.clear()
    try:
        assert server.profile_now("Bearer a")["facts"]
        server.profile_now("Bearer a")
        assert len(seen) == 1  # 붙들었다
        server.profile_now("Bearer b")
        assert len(seen) == 2  # 사람마다 따로
        # 붙들 시간이 지났는데 백엔드가 안 닿는다 — 붙들던 것으로 선다.
        key = next(iter(server._profile_cache))
        server._profile_cache[key] = (0.0, server._profile_cache[key][1])

        def down(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        server._SYNC_TRANSPORT = httpx.MockTransport(down)
        assert server.profile_now("Bearer a")["name"] == LIVE["name"]
    finally:
        server._SYNC_TRANSPORT = None
        server._profile_cache.clear()


def test_whoami_와_안내서는_지금의_플랫폼을_싣는다() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/me":
            return httpx.Response(200, json={"name": "홍", "home_workspace_slug": "cae"})
        assert request.url.path == "/api/server/profile/live"
        return httpx.Response(200, json=LIVE)

    _serve(respond, admin=None)
    me = asyncio.run(server.whoami(_ctx("Bearer t")))
    assert me["home_workspace_slug"] == "cae" and me["platform"]["facts"]
    guide = asyncio.run(server.get_guide(_ctx("Bearer t")))
    assert guide["platform"]["stale"] == LIVE["stale"]
    assert "platforms" in guide["more_topics"]
    got = asyncio.run(server.platform_profile(_ctx("Bearer t")))
    assert got["facts"] == LIVE["facts"]


def test_자기소개_고치기는_미리_보기가_먼저고_안_보낸_칸은_그대로다() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={**PROFILE, **json.loads(request.content)})
        return httpx.Response(200, json=LIVE)

    seen = _serve(respond)
    server._profile_cache["x"] = (0.0, LIVE)
    plan = asyncio.run(
        server.platform_profile_update(_ctx("Bearer t"), summary="  보고서 쌍둥이  ")
    )
    assert plan["applied"] is False and all(one.method == "GET" for one in seen)
    assert plan["after"] == {"summary": "보고서 쌍둥이", "notes": PROFILE["notes"]}
    preview = plan["instructions_preview"]
    assert "보고서 쌍둥이" in preview and "지금 담긴 것" in preview and "⚠" not in preview
    too_long = asyncio.run(
        server.platform_profile_update(_ctx("Bearer t"), notes="가" * 2001, apply=True)
    )
    assert "2,000자" in too_long["error"]
    done = asyncio.run(
        server.platform_profile_update(_ctx("Bearer t"), summary="보고서 쌍둥이", apply=True)
    )
    assert done["applied"] is True
    put = next(one for one in seen if one.method == "PUT")
    assert json.loads(put.content) == {"summary": "보고서 쌍둥이", "notes": PROFILE["notes"]}
    assert server._profile_cache == {}  # 다음 접속이 새로 읽는다
    assert "error" in asyncio.run(server.platform_profile_update(_ctx("Bearer t")))


def test_값마다_훑기는_by_를_주면_훑기_경로로_간다() -> None:
    """「전작보다 빨리 늘고 있는 증상은?」 — 같은 레시피에 `by` 만 더한다(응답 모양이 달라
    경로가 따로다)."""
    seen = _serve(lambda _r: httpx.Response(200, json={"items": []}))
    asyncio.run(
        server.metric_analyze(
            _ctx("Bearer t"),
            "cases",
            "sprt",
            options={"target": "S", "reference": "A", "by": "symptom", "top": 10},
        )
    )
    asyncio.run(server.metric_analyze(_ctx("Bearer t"), "cases", "changes", {"by": "symptom"}))
    asyncio.run(server.metric_analyze(_ctx("Bearer t"), "cases", "changes", {"window": 3}))
    paths = [one.url.path for one in seen]
    assert paths == [
        "/api/metrics/cases/analysis/sprt/scan",
        "/api/metrics/cases/analysis/changes/scan",
        "/api/metrics/cases/analysis/changes",
    ]
    assert seen[0].url.params["by"] == "symptom" and seen[0].url.params["top"] == "10"


def test_작업의_다음_할_일은_종류마다_다르다() -> None:
    """계획을 `job_apply` 로 적용하는 종류 · 도구를 `apply=true` 로 다시 부르는 종류 · 파일을
    내는 종류 · 그냥 끝나는 종류 — 하나로 말하면 AI 가 없는 길(내보내기를 job_apply)을 탄다."""
    view = server._job_view
    plan = {"applied": False, "counts": {"create": 1}}
    assert (
        "job_apply"
        in view({"id": "a", "kind": "objects_import", "status": "done", "result": plan})[
            "next"
        ]
    )
    sync = view({"id": "b", "kind": "datasource_sync", "status": "done", "result": plan})
    assert "datasource_sync(slug, apply=true)" in sync["next"] and "안 된다" in sync["next"]
    gc = view(
        {
            "id": "c",
            "kind": "filestore_gc",
            "status": "done",
            "result": {"applied": False, "ok": True},
        }
    )
    assert "job_apply" in gc["next"]
    nothing = view(
        {
            "id": "c2",
            "kind": "filestore_gc",
            "status": "done",
            "result": {"applied": False, "ok": False, "summary": "지울 것이 없습니다."},
        }
    )
    assert "적용할 것이 없는" in nothing["next"] and "job_apply" not in nothing["next"]
    export = view(
        {
            "id": "d",
            "kind": "objects_export",
            "status": "done",
            "result": {"format": "xlsx"},
            "has_output": True,
        }
    )
    assert "job_apply" not in export["next"] and "파일" in export["next"]
    metrics = view(
        {"id": "e", "kind": "metrics_recompute", "status": "done", "result": {"applied": True}}
    )
    assert metrics["next"].startswith("적용됐다")
    done = view(
        {
            "id": "f",
            "kind": "objects_import",
            "status": "done",
            "result": plan,
            "applied_by": "g",
        }
    )
    assert done["applied_by"] == "g" and "이미 적용" in done["next"]


def test_작업_목록은_거르기를_그대로_건네고_취소는_그_작업으로() -> None:
    seen = _serve(lambda _r: httpx.Response(200, json={"items": [], "total": 0}))
    asyncio.run(
        server.jobs_list(_ctx("Bearer t"), status="failed", kind="datasource_sync", mine=True)
    )
    assert dict(seen[0].url.params) == {
        "limit": "20",
        "status": "failed",
        "kind": "datasource_sync",
        "mine": "true",
    }
    seen = _serve(
        lambda _r: httpx.Response(200, json={"id": "j1", "kind": "x", "status": "cancelled"})
    )
    got = asyncio.run(server.job_cancel(_ctx("Bearer t"), "j1"))
    assert seen[0].method == "POST" and seen[0].url.path == "/api/jobs/j1/cancel"
    assert got["status"] == "cancelled"


def test_고아_파일_정리는_계획_작업만_넣는다() -> None:
    seen = _serve(
        lambda _r: httpx.Response(
            202,
            json={
                "id": "g1",
                "kind": "filestore_gc",
                "status": "done",
                "result": {"applied": False, "ok": True},
            },
        )
    )
    got = asyncio.run(server.filestore_gc(_ctx("Bearer t")))
    assert seen[0].url.path == "/api/jobs" and b"filestore_gc" in seen[0].content
    assert "job_apply" in got["next"]


def test_지표_도구는_주기를_안_주면_안_보내고_홈과_기록은_그_경로로() -> None:
    """고칠 때 주기를 안 주면 **그대로 둔다** — 예전에는 늘 24 를 보내 손으로 정한 주기를
    덮었다."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/metrics/m1":
            return httpx.Response(200, json={"slug": "m1"})
        return httpx.Response(200, json={"metric": {"slug": "m1"}, "job": None})

    seen = _serve(respond)
    asyncio.run(
        server.metric_define(
            _ctx("Bearer t"), "m1", "지표", "case", {"measure": "count"}, apply=True
        )
    )
    body = json.loads(seen[-1].content)
    assert (
        seen[-1].method == "PATCH" and "interval_hours" not in body and "is_active" not in body
    )
    asyncio.run(
        server.metric_define(
            _ctx("Bearer t"),
            "m1",
            "지표",
            "case",
            {"measure": "count"},
            apply=True,
            interval_hours=6,
            is_active=False,
        )
    )
    body = json.loads(seen[-1].content)
    assert body["interval_hours"] == 6 and body["is_active"] is False

    seen = _serve(lambda _r: httpx.Response(200, json=[]))
    ctx = _ctx("Bearer t")
    asyncio.run(server.metric_runs(ctx, "m1"))
    asyncio.run(server.metric_home(ctx, "m1"))
    asyncio.run(server.metric_home(ctx, "m1", "rnd", split="symptom"))
    asyncio.run(server.metric_home(ctx, "m1", "rnd", remove=True))
    asyncio.run(
        server.metric_query(ctx, "m1", shape="dim_values", dims=["symptom"], search="소")
    )
    assert [(one.method, one.url.path) for one in seen] == [
        ("GET", "/api/metrics/m1/runs"),
        ("GET", "/api/metrics/m1/home"),
        ("PUT", "/api/metrics/m1/home"),
        ("DELETE", "/api/metrics/m1/home"),
        ("GET", "/api/metrics/m1/dims"),
    ]
    assert json.loads(seen[2].content) == {"workspace_slug": "rnd", "split": "symptom"}
    assert dict(seen[3].url.params) == {"workspace": "rnd"}
    assert dict(seen[4].url.params) == {"name": "symptom", "q": "소"}
    wrong = asyncio.run(server.metric_query(ctx, "m1", shape="dim_values", dims=["a", "b"]))
    assert "기준 하나" in wrong["error"]


def test_제외_조건과_구성비와_규칙_기준이_그대로_건너간다() -> None:
    """ADR 0015 — 지표 건 보기의 `nf<번호>.` 키를 `objects_list` 의 `exclude` 로 되돌려 보내고,
    구성비는 표(기준 이름) · 추이(true)로, 규칙 기준은 정의 그대로 계획에 간다."""
    seen = _serve(lambda _r: httpx.Response(200, json={"items": [], "total": 3}))
    ctx = _ctx("Bearer t")
    asyncio.run(
        server.objects_list(
            ctx,
            "svc_case",
            conditions=[
                {"field": "parts", "op": "notempty", "value": ""},
                {"field": "action", "op": "starts", "value": "SW", "exclude": 1},
                {"field": "diag", "op": "in", "value": "C01|C02", "exclude": "2"},
                {"field": "center", "op": "eq", "value": "서울", "exclude": 0},
            ],
        )
    )
    params = seen[0].url.params
    assert params["f.parts.notempty"] == "" and params["nf1.action.starts"] == "SW"
    assert params["nf2.diag.in"] == "C01|C02" and params["f.center.eq"] == "서울"

    seen = _serve(lambda _r: httpx.Response(200, json={"cells": [], "total_count": 0}))
    asyncio.run(server.metric_query(ctx, "m1", dims=["nature"], composition="nature"))
    asyncio.run(
        server.metric_query(ctx, "m1", shape="series", split="nature", composition=True)
    )
    asyncio.run(server.metric_query(ctx, "m1", dims=["nature"]))
    assert seen[0].url.params["composition"] == "nature"
    assert seen[1].url.path == "/api/metrics/m1/series"
    assert seen[1].url.params["composition"] == "true"
    assert "composition" not in seen[2].url.params

    rules = {
        "name": "nature",
        "rules": [
            {"label": "SW", "when": [{"field": "action", "op": "starts", "value": "SW"}]}
        ],
        "else": "기타",
    }
    seen = _serve(lambda _r: httpx.Response(200, json={"ok": True, "rules": []}))
    asyncio.run(
        server.metric_define(
            ctx, "m1", "성격", "svc_case", {"measure": "count", "dimensions": [rules]}
        )
    )
    assert seen[0].url.path == "/api/metrics/plan"
    assert json.loads(seen[0].content)["spec"]["dimensions"] == [rules]


# --- HWAX 게이트웨이 (ADR 0028) ---------------------------------------------------

#: 게이트웨이가 위임 토큰을 **다시 받는** 판정 — HWAXMcpGateway `gateway.py` 의
#: `_TOKEN_REJECTED` 를 글자 그대로 옮겼다. 이것에 안 맞으면 회수된 토큰으로 12시간 동안 401 만
#: 받는다.
GATEWAY_TOKEN_REJECTED = re.compile(
    r"^Error executing tool \S+: (?:GET|POST|PUT|PATCH|DELETE) \S+ → HTTP 401\b"
)


def _through_fastmcp(name: str, arguments: dict[str, Any], authorization: str) -> Any:
    """게이트웨이가 보는 그대로 — FastMCP 의 도구 호출 처리기(`tools/call`)를 거친 결과.
    도구에서 난 예외를 FastMCP 가 어떻게 감싸는지까지 이 길에서 돈다."""
    from mcp import types
    from mcp.server.lowlevel.server import request_ctx
    from mcp.shared.context import RequestContext

    handler = server.mcp._mcp_server.request_handlers[types.CallToolRequest]
    request = SimpleNamespace(headers={"authorization": authorization})
    held = request_ctx.set(
        RequestContext(
            request_id=1, meta=None, session=None, lifespan_context=None, request=request
        )
    )
    try:
        got = asyncio.run(
            handler(
                types.CallToolRequest(
                    method="tools/call",
                    params=types.CallToolRequestParams(name=name, arguments=arguments),
                )
            )
        )
    finally:
        request_ctx.reset(held)
    return got.root


def _text(result: Any) -> str:
    """게이트웨이의 `_token_rejected` 가 읽는 글 — 내용 조각을 이어 붙여 앞뒤 공백을 뗀 것."""
    return "".join(getattr(one, "text", "") or "" for one in result.content).strip()


def test_백엔드가_토큰을_거절하면_게이트웨이가_다시_받는_모양으로_실패한다() -> None:
    """401 은 결과(`{"error": …}`)가 아니라 **오류**로 — 문구가 게이트웨이의 정규식에 맞아야
    게이트웨이가 위임 토큰을 새로 받아 한 번 다시 부른다."""
    seen = _serve(
        lambda _r: httpx.Response(
            401,
            json={"error": {"code": "APP-AUTH-0101", "message": "토큰이 유효하지 않습니다."}},
        )
    )
    got = _through_fastmcp("objects_list", {"type_slug": "model", "q": "x y"}, "Bearer old")
    assert got.isError is True
    text = _text(got)
    assert GATEWAY_TOKEN_REJECTED.match(text), text
    # 경로는 물음표 앞까지 — 질의에 공백이 있어도 `\S+` 가 끊기지 않는다. 서버의 말은 그대로다.
    assert text == (
        "Error executing tool objects_list: GET /api/objects/model → HTTP 401: "
        "[APP-AUTH-0101] 토큰이 유효하지 않습니다."
    )
    assert seen[0].headers["authorization"] == "Bearer old"

    # 쓰는 도구도 같다(POST) · 본문이 JSON 이 아니어도 같다.
    _serve(lambda _r: httpx.Response(401, text="Unauthorized"))
    got = _through_fastmcp("object_create", {"type_slug": "part", "label": "볼트"}, "Bearer t")
    assert got.isError is True and GATEWAY_TOKEN_REJECTED.match(_text(got)), _text(got)
    assert _text(got).startswith("Error executing tool object_create: POST /api/objects/part")

    # 결과를 직접 다루는 도구(텍스트를 주는 rdf_schema)도 같은 모양이다.
    _serve(lambda _r: httpx.Response(401, json={"error": {"code": "X", "message": "m"}}))
    got = _through_fastmcp("rdf_schema", {}, "Bearer t")
    assert got.isError is True and GATEWAY_TOKEN_REJECTED.match(_text(got)), _text(got)


def test_401_이_아닌_오류는_지금처럼_결과로_전한다() -> None:
    """권한 없음(403) · 검증(422) 은 토큰 문제가 아니다 — 게이트웨이가 다시 받으면 같은 일이 두
    번 돈다. 결과로 그대로 전해 Claude 가 서버의 말을 읽는다."""
    for status in (403, 404, 422, 500):
        _serve(
            lambda _r, status=status: httpx.Response(
                status, json={"error": {"code": "APP-X-0001", "message": "안 됩니다"}}
            )
        )
        got = _through_fastmcp("objects_list", {"type_slug": "model"}, "Bearer t")
        assert got.isError is False, status
        assert not GATEWAY_TOKEN_REJECTED.match(_text(got))
        assert "[APP-X-0001] 안 됩니다" in _text(got)


#: 이름만 보고도 지움 · 되돌리기 · 멈춤인 도구 — 새로 생기면 위험 목록에 들어 있어야 한다.
_DESTRUCTIVE_WORDS = ("delete", "remove", "cancel", "undo", "merge", "restore", "_gc")
REQUEST_TEMPLATE = (
    Path(__file__).resolve().parents[2] / "docs" / "[참고] HWAX포탈_연동_요청서.md"
)


def test_위험_도구는_실제_도구이고_이름이_그런_것은_빠짐없다() -> None:
    """HWAX 게이트웨이에 범용 실행기 차단(`_INVOKE_DENY_EXACT`)을 요청하는 목록 — 없는 이름을
    적으면 요청이 헛돌고, 새 지우기 도구를 빠뜨리면 그 도구는 게이트웨이의 이름 규칙(`delete_`
    로 **시작**)에도 안 걸려 환각 한 번에 불린다."""
    assert server.DESTRUCTIVE_TOOLS <= TOOLS
    named = {one for one in TOOLS if any(word in one for word in _DESTRUCTIVE_WORDS)}
    assert named <= server.DESTRUCTIVE_TOOLS, named - server.DESTRUCTIVE_TOOLS


def test_요청서_틀의_위험_도구_목록이_서버와_같다() -> None:
    """요청서 틀(`deploy.sh hwax-request` 가 채운다)이 포털 담당에게 넘기는 목록은 코드의
    목록과 **같아야** 한다 — 두 벌이 갈리면 늘 느슨한 쪽이 게이트웨이에 들어간다."""
    text = REQUEST_TEMPLATE.read_text(encoding="utf-8")
    start = text.index("<!-- 위험 도구: 시작 -->")
    end = text.index("<!-- 위험 도구: 끝 -->")
    listed = set(re.findall(r'"([a-z_]+)"', text[start:end]))
    assert listed == set(server.DESTRUCTIVE_TOOLS)


# --- 운영 · 설정(2026-10-15) ---------------------------------------------------------


def _route(table: dict[tuple[str, str], Any]) -> list[httpx.Request]:
    """경로마다 다른 응답 — (메서드, 경로) → JSON. 없는 것은 404 봉투."""

    def respond(request: httpx.Request) -> httpx.Response:
        found = table.get((request.method, request.url.path))
        if found is None:
            return httpx.Response(404, json={"error": {"code": "X", "message": "없음"}})
        return httpx.Response(204) if found == 204 else httpx.Response(200, json=found)

    return _serve(respond)


def test_설정_지우기는_미리_보기가_기본이고_종류마다_제_자리를_지운다() -> None:
    ctx = _ctx("Bearer t")
    seen = _route(
        {
            ("GET", "/api/datasources/erp"): {"slug": "erp", "name": "ERP"},
            ("DELETE", "/api/datasources/erp"): 204,
        }
    )
    got = asyncio.run(server.config_delete(ctx, "datasource", slug="erp"))
    assert got["applied"] is False and [one.method for one in seen] == ["GET"]
    got = asyncio.run(server.config_delete(ctx, "datasource", slug="erp", apply=True))
    assert seen[-1].method == "DELETE" and seen[-1].url.path == "/api/datasources/erp"
    assert got == {
        "applied": True,
        "deleted": {"kind": "datasource", "slug": "erp", "id": None},
    }

    # 관리 설정(부서 · 공지)은 MCP 로 안 지운다 — 토큰에 안 열린 자리다.
    for kind in ("account", "workspace", "notice"):
        assert "error" in asyncio.run(server.config_delete(ctx, kind, slug="x", id="y"))
    assert "error" in asyncio.run(server.config_delete(ctx, "view", slug="part"))


def test_데이터_소스_저장은_비밀을_안_받고_있으면_고치고_없으면_만든다() -> None:
    ctx = _ctx("Bearer t")
    refused = asyncio.run(
        server.datasource_save(ctx, "erp", {"auth_secret": "s3cret"}, apply=True)
    )
    assert "error" in refused and "화면" in refused["error"]

    seen = _route(
        {
            ("GET", "/api/datasources"): [{"slug": "erp"}],
            ("GET", "/api/datasources/erp"): {"slug": "erp", "interval_minutes": 0},
            ("PATCH", "/api/datasources/erp"): {"slug": "erp", "interval_minutes": 60},
            ("POST", "/api/datasources"): {"slug": "mes"},
        }
    )
    got = asyncio.run(server.datasource_save(ctx, "erp", {"interval_minutes": 60}))
    assert got["applied"] is False and got["current"] == {"interval_minutes": 0}
    asyncio.run(server.datasource_save(ctx, "erp", {"interval_minutes": 60}, apply=True))
    assert seen[-1].method == "PATCH" and json.loads(seen[-1].content) == {
        "interval_minutes": 60
    }
    asyncio.run(
        server.datasource_save(
            ctx, "mes", {"name": "MES", "entity_set": "Lots", "type_slug": "lot"}, apply=True
        )
    )
    assert seen[-1].method == "POST" and json.loads(seen[-1].content)["slug"] == "mes"


def test_경보는_미리_보기에서_지금_분석을_돌리고_인자는_못_고친다() -> None:
    ctx = _ctx("Bearer t")
    seen = _route(
        {
            ("GET", "/api/metrics/svc/analysis/sprt"): {"decision": "continue"},
            ("POST", "/api/metrics/svc/alerts"): {"id": "a1"},
            ("POST", "/api/metrics/svc/alerts/a1/check"): {"findings": []},
            ("GET", "/api/metrics/svc/alerts/a1/events"): [],
        }
    )
    got = asyncio.run(
        server.metric_alert_save(
            ctx, "svc", name="A60", recipe="sprt", params={"d.base_model": "A60"}
        )
    )
    assert got["applied"] is False and got["now"] == {"decision": "continue"}
    assert (
        seen[0].url.params["d.base_model"] == "A60" and seen[0].url.params["compact"] == "true"
    )
    asyncio.run(
        server.metric_alert_save(ctx, "svc", name="A60", recipe="sprt", params={}, apply=True)
    )
    assert seen[-1].method == "POST" and json.loads(seen[-1].content)["recipe"] == "sprt"
    assert "error" in asyncio.run(
        server.metric_alert_save(ctx, "svc", alert_id="a1", recipe="control")
    )
    asyncio.run(server.metric_alerts(ctx, "svc", alert_id="a1", check=True))
    assert seen[-1].url.path == "/api/metrics/svc/alerts/a1/check"
    asyncio.run(server.metric_alerts(ctx, "svc", alert_id="a1"))
    assert seen[-1].url.path == "/api/metrics/svc/alerts/a1/events"


def test_내보내기는_목록과_같은_거르기로_작업만_시작한다() -> None:
    seen = _serve(
        lambda _r: httpx.Response(
            202,
            json={"id": "j2", "kind": "objects_export", "status": "done", "has_output": True},
        )
    )
    got = asyncio.run(
        server.export_start(_ctx("Bearer t"), "part", q="볼트", properties={"grade": "A"})
    )
    assert seen[0].method == "POST" and seen[0].url.path == "/api/objects/part/export"
    assert seen[0].url.params["format"] == "csv" and seen[0].url.params["q"] == "볼트"
    assert "화면" in got["next"]
    assert "error" in asyncio.run(server.export_start(_ctx("Bearer t"), "part", kind="photos"))


def test_작업은_여럿을_한_번에_적용하고_멈춘다() -> None:
    seen = _serve(lambda _r: httpx.Response(200, json=[{"id": "j1", "status": "ok"}]))
    asyncio.run(server.job_apply(_ctx("Bearer t"), job_ids=["j1", "j2"]))
    assert seen[-1].url.path == "/api/jobs/apply"
    assert json.loads(seen[-1].content) == {"ids": ["j1", "j2"]}
    asyncio.run(server.job_cancel(_ctx("Bearer t"), job_ids=["j3"]))
    assert seen[-1].url.path == "/api/jobs/cancel"


def test_코어_현황은_칸_목록을_수로_줄여_준다() -> None:
    _route(
        {
            ("GET", "/api/ontology/core-status"): {
                "types": [{"slug": "part", "properties": [{"key": "a"}, {"key": "b"}]}],
                "withheld_relations": [{"slug": "supplies", "reason": "closed_types"}],
            }
        }
    )
    got = asyncio.run(server.core_status(_ctx("Bearer t")))
    assert got["types"][0]["property_count"] == 2 and "properties" not in got["types"][0]
    assert got["withheld_relations"][0]["reason"] == "closed_types"


def test_서버의_느린_곳은_종류마다_제_자리로() -> None:
    seen = _serve(lambda _r: httpx.Response(200, json={}))
    asyncio.run(
        server.server_status(
            _ctx("Bearer t"), performance="cases", route="/api/x", method="GET"
        )
    )
    assert seen[-1].url.path == "/api/server/performance/requests/cases"
    assert seen[-1].url.params["route"] == "/api/x"
    # 끊긴 질문(ADR 0031) — 지표로 미리 세어 둘 후보.
    asyncio.run(server.server_status(_ctx("Bearer t"), performance="cut", period="7d"))
    assert seen[-1].url.path == "/api/server/performance/cut"
    assert seen[-1].url.params["period"] == "7d"
    assert "suggest" not in seen[-1].url.params
    asyncio.run(server.server_status(_ctx("Bearer t"), performance="cut", suggest=True))
    assert seen[-1].url.params["suggest"] == "true"  # 지표 정의 초안(ADR 0032)
    assert "error" in asyncio.run(server.server_status(_ctx("Bearer t"), performance="disk"))


def test_위험_도구는_시스템_관리자만_쓴다() -> None:
    """2026-10-15 사용자 결정 — 서버의 권한(부서 관리자도 자기 부서 객체를 지운다)보다
    좁힌다. 막을 때는 **백엔드에 아무것도 안 보낸다**(미리 보기도) — 관리자인지 묻는 한
    번뿐이다. 확장의 읽기(GET)는 위험 도구 이름이어도 그대로 열린다."""
    ctx = _ctx("Bearer t")
    seen = _serve(lambda _r: httpx.Response(200, json={"rows": []}), admin=False)
    for call in (
        server.objects_delete(ctx, "part", ["1"]),
        server.config_delete(ctx, "datasource", slug="erp"),
        server.jobs_purge(ctx),
        server.job_cancel(ctx, "j1"),
        server.extension_call(ctx, "caegroup", "dt/pairs", method="POST", body={}),
    ):
        got = asyncio.run(call)
        assert got["admin_only"] is True and "시스템 관리자" in got["error"], got
    assert seen == []

    got = asyncio.run(server.extension_call(ctx, "caegroup", "dt/pairs"))
    assert got == {"rows": []} and [r.method for r in seen] == ["GET"]
    seen.clear()
    assert asyncio.run(server.objects_summary(ctx, "part")) == {"rows": []}
    assert len(seen) == 1  # 위험 도구가 아니면 묻지도 않는다

    # 관리자인지 못 물으면 그 오류 그대로 — 열지 않는다. 401 은 다른 도구처럼 올린다
    # (게이트웨이가 토큰을 다시 받는 신호).
    seen = _serve(
        lambda _r: httpx.Response(503, json={"error": {"code": "X", "message": "점검"}}),
        admin=None,
    )
    got = asyncio.run(server.objects_delete(ctx, "part", ["1"], apply=True))
    assert "error" in got and "admin_only" not in got
    assert [r.url.path for r in seen] == ["/api/auth/me"]
    server._TRANSPORT = httpx.MockTransport(
        lambda _r: httpx.Response(401, json={"error": {"code": "X", "message": "만료"}})
    )
    with pytest.raises(server.BackendUnauthorized):
        asyncio.run(server.objects_delete(ctx, "part", ["1"], apply=True))

    # 시스템 관리자면 그대로 — 이름이 남아 FastMCP 가 같은 인자표를 본다.
    seen = _serve(lambda _r: httpx.Response(200, json={"rows": []}), admin=True)
    assert asyncio.run(server.jobs_purge(ctx))["applied"] is False
    assert server.objects_delete.__name__ == "objects_delete"


def test_눈여겨볼_것은_거르기를_그대로_넘긴다() -> None:
    seen = _serve(lambda _r: httpx.Response(200, json={"items": [], "total": 0}))
    asyncio.run(
        server.insights(
            _ctx("Bearer t"), metric="svc", kind="change", days=30, include_past=True
        )
    )
    request = seen[-1]
    assert request.url.path == "/api/insights"
    assert dict(request.url.params) == {
        "limit": "20",
        "metric": "svc",
        "kind": "change",
        "days": "30",
        "include_past": "true",
    }
