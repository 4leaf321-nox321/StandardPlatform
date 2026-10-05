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
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

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
    "extensions_schema",
    "extension_call",
    "metric_list",
    "metric_query",
    "metric_analyze",
    "metric_alerts",
    "metric_define",
    "platform_profile",
    "platform_profile_update",
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

    _serve(respond)
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
