"""MCP 도구를 **진짜 앱에 붙여** 본다 — 붙여 보기 전에 도는지 아는 유일한 방법.

`mcp_server/server.py` 는 한 파일이고 `mcp` 패키지를 쓴다. 그 패키지를 백엔드
환경에 깔면 `starlette.testclient` 의 HTTP 스택이 바뀌어 다른 시험이 흔들리므로
(실측), 여기서는 **가짜 `mcp` 를 끼워** 도구 함수만 꺼낸다. 도구는 백엔드에
`httpx` 로 말하고, 그 자리를 이 프로세스 안의 앱(ASGI)으로 돌린다 — **라우터·
권한·검증이 통째로 돈다**(서비스 함수를 직접 부르면 그것이 전부 빠진다).

**규칙이 두 벌이 아닌지도 여기서 본다.** 서버가 막는 것을 MCP 가 통과시키면
「MCP 로는 되는데 화면에서는 안 되는」 상태가 되고, 그때 어느 쪽이 맞는지 알
방법이 없다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import uuid
from collections.abc import Callable, Coroutine, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app as fastapi_app
from tests.api.conftest import Signed
from tests.api.test_delete_plan import _only

SERVER_PY = Path(__file__).resolve().parents[3] / "mcp_server" / "server.py"


class _FakeFastMCP:
    """`FastMCP` 의 자리 — 도구 등록을 **그냥 통과**시킨다. 여기서 보는 것은 등록이
    아니라 도구가 백엔드에 무엇을 보내고 무엇을 돌려주는가다."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.settings = SimpleNamespace()

    def tool(self) -> Callable[[Any], Any]:
        return lambda function: function


def _load_server() -> Any:
    fake = types.ModuleType("mcp.server.fastmcp")
    fake.FastMCP = _FakeFastMCP  # type: ignore[attr-defined]
    fake.Context = object  # type: ignore[attr-defined]
    for name in ("mcp", "mcp.server"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["mcp.server.fastmcp"] = fake
    spec = importlib.util.spec_from_file_location("mcp_server_under_test", SERVER_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = _load_server()


def _ctx(token: str) -> Any:
    """들어온 MCP HTTP 요청 흉내 — 서버는 그 헤더를 백엔드로 그대로 넘긴다."""
    request = SimpleNamespace(headers={"authorization": f"Bearer {token}"})
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


class Bot:
    """기계 자격 하나로 도구를 부르는 손. **사람 세션이 아니라 PAT 로 붙는다.**"""

    def __init__(self, token: str) -> None:
        self.ctx = _ctx(token)

    def call(
        self, tool: Callable[..., Coroutine[Any, Any, Any]], *args: Any, **kw: Any
    ) -> Any:
        got = asyncio.run(tool(self.ctx, *args, **kw))
        if isinstance(got, dict) and "error" in got:
            raise ToolError(str(got["error"]))
        return got


class ToolError(RuntimeError):
    """도구가 돌려준 `{"error": ...}` — 서버의 말 그대로."""


def _uniq(base: str) -> str:
    return f"{base}_{uuid.uuid4().hex[:6]}"


@pytest.fixture
def bot(client: TestClient, admin: Signed) -> Iterator[Bot]:
    made = client.post(
        "/api/auth/tokens",
        json={"name": _uniq("bot"), "scopes": ["read", "objects:write", "ontology:write"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    # 도구의 httpx 를 이 프로세스 안의 앱으로 돌린다. 진짜 HTTP 스택을 타되 소켓은 없다.
    server._TRANSPORT = httpx.ASGITransport(app=fastapi_app)
    # 여러 행 넣기 · 묶음은 **작업**이라 도구가 `GET /api/jobs/{id}` 를 되풀이한다 — 시험에는
    # 워커 프로세스가 없으니 물을 때마다 한 바퀴 돌린다(그것이 곧 워커다).
    from app.modules.jobs import services as job_services

    original_get = server._get

    async def get_with_worker(ctx: Any, path: str, params: Any = None) -> Any:
        if path.startswith("/api/jobs/") and not path.endswith("/workers"):
            job_services.process_one("test-worker")
        return await original_get(ctx, path, params)

    server._get = get_with_worker
    try:
        yield Bot(made.json()["token"])
    finally:
        server._get = original_get
        server._TRANSPORT = None


def test_토큰이_없으면_서버의_말이_그대로_온다(client: TestClient) -> None:
    """**「인증 실패」 만 뜨면 무엇을 해야 하는지 알 수 없다** — 백엔드의 오류
    코드와 문구가 그대로 와야 README 의 표로 찾을 수 있다."""
    server._TRANSPORT = httpx.ASGITransport(app=fastapi_app)
    try:
        got = asyncio.run(server.ontology_schema(_ctx("")))
    finally:
        server._TRANSPORT = None
    assert got["error"].startswith("[APP-AUTH-0100]")


def test_스키마부터_읽는다(bot: Bot) -> None:
    """**다른 도구를 부르기 전에 이것부터.** 무엇을 만들 수 있고 각 타입이 어떤
    값을 받는지가 여기 다 있다."""
    got = bot.call(server.ontology_schema)
    assert {"groups", "types", "relation_types", "data_types"} <= set(got)


def test_가이드가_먼저다(bot: Bot) -> None:
    """도구 설명이 「먼저 부르라」 고 하는 것이 실제로 읽혀야 한다 — 빠지면 AI 는
    도구 설명만으로 헤맨다."""
    got = bot.call(server.get_guide)
    assert got["topic"] == "overview" and "ontology_import" in got["content"]


def test_정의를_넣고_객체를_채운다(bot: Bot) -> None:
    slug = _uniq("mach")
    schema = {
        "types": [
            {
                "slug": slug,
                "label": "설비",
                "key_policy": "required",
                "properties": [
                    {
                        "key": "power",
                        "label": "출력",
                        "data_type": "number",
                        "unit": "kW",
                        "min_value": 0,
                        "max_value": 500,
                    },
                ],
            }
        ]
    }

    # **기본은 미리 보기다.** 아무것도 안 바뀐다.
    plan = bot.call(server.ontology_import, schema)
    assert plan["applied"] is False
    assert all(t["slug"] != slug for t in bot.call(server.ontology_schema)["types"])

    done = bot.call(server.ontology_import, schema, apply=True)
    assert done["applied"] is True
    assert done["snapshot_id"], "되돌릴 자리가 남아야 한다"

    made = bot.call(
        server.object_create, slug, label="1호기", key="M-001", properties={"power": 15}
    )
    assert made["properties"]["power"] == 15

    listed = bot.call(server.objects_list, slug, q="1호기")
    assert [one["label"] for one in listed["items"]] == ["1호기"]

    got = bot.call(server.object_get, slug, made["id"])
    assert got["object"]["key"] == "M-001"
    assert [p["key"] for p in got["properties_schema"]] == ["power"]


def test_서버가_막는_것을_그대로_전한다(bot: Bot) -> None:
    """**규칙이 두 벌이 아니다.** MCP 가 통과시키면 「MCP 로는 되는데 화면에서는
    안 되는」 상태가 되고, 그때 어느 쪽이 맞는지 알 방법이 없다.

    그리고 **서버의 말을 그대로 전한다** — 오류 문구에 무엇을 고쳐야 하는지가
    적혀 있고, 여기서 고쳐 쓰면 그것을 잃는다.
    """
    slug = _uniq("mach")
    bot.call(
        server.ontology_import,
        {
            "types": [
                {
                    "slug": slug,
                    "label": "설비",
                    "properties": [
                        {
                            "key": "power",
                            "label": "출력",
                            "data_type": "number",
                            "unit": "kW",
                            "min_value": 0,
                            "max_value": 500,
                        }
                    ],
                }
            ]
        },
        apply=True,
    )

    with pytest.raises(ToolError) as over:
        bot.call(server.object_create, slug, label="터빈", properties={"power": 9000})
    assert "kW" in str(over.value) and "9000" in str(over.value)

    with pytest.raises(ToolError) as unknown:
        bot.call(server.object_create, slug, label="터빈", properties={"없는키": 1})
    assert "정의되지 않은 속성" in str(unknown.value)


def test_부분_수정은_다른_속성을_안_건드린다(bot: Bot) -> None:
    slug = _uniq("mach")
    bot.call(
        server.ontology_import,
        {
            "types": [
                {
                    "slug": slug,
                    "label": "설비",
                    "properties": [
                        {"key": "power", "label": "출력", "data_type": "number"},
                        {"key": "memo", "label": "메모", "data_type": "text"},
                    ],
                }
            ]
        },
        apply=True,
    )
    made = bot.call(
        server.object_create, slug, label="1호기", properties={"power": 10, "memo": "점검함"}
    )
    patched = bot.call(server.object_update, slug, made["id"], properties={"power": 20})
    assert patched["properties"] == {"power": 20, "memo": "점검함"}


def test_이을_때_근거를_남긴다(bot: Bot) -> None:
    """**근거 없는 연결은 시간이 지나면 아무도 못 믿는다** — 기계가 이은 것이면
    더 그렇다."""
    machine, site = _uniq("mach"), _uniq("site")
    relation = _uniq("installed")
    bot.call(
        server.ontology_import,
        {
            "types": [{"slug": machine, "label": "설비"}, {"slug": site, "label": "현장"}],
            "relation_types": [
                {
                    "slug": relation,
                    "label": "설치",
                    "src_type_slugs": [machine],
                    "dst_type_slugs": [site],
                }
            ],
        },
        apply=True,
    )
    m = bot.call(server.object_create, machine, label="1호기")
    s = bot.call(server.object_create, site, label="부산")
    bot.call(
        server.relation_add, machine, m["id"], relation, s["id"], evidence_note="설치 대장 3쪽"
    )

    got = bot.call(server.object_get, machine, m["id"])
    assert [(r["label"], r["object_label"], r["evidence_note"]) for r in got["related"]] == [
        ("설치", "부산", "설치 대장 3쪽")
    ]


def test_여러_행은_계획이_먼저고_전부_아니면_무다(bot: Bot) -> None:
    """**한 행이라도 오류면 아무것도 안 넣는다.** 절반만 들어간 파일은 어느 절반인지
    아무도 모른다."""
    slug = _uniq("mach")
    bot.call(
        server.ontology_import,
        {
            "types": [
                {
                    "slug": slug,
                    "label": "설비",
                    "key_policy": "required",
                    "properties": [
                        {
                            "key": "power",
                            "label": "출력",
                            "data_type": "number",
                            "max_value": 500,
                        }
                    ],
                }
            ]
        },
        apply=True,
    )
    rows: list[dict[str, Any]] = [
        {"key": "M-001", "label": "1호기", "power": 10},
        {"key": "M-002", "label": "2호기", "power": 9000},
    ]
    # 작업이 된다 — 도구는 끝나기를 기다렸다가 계획을 돌려준다. 아직 아무것도 안 들어갔다.
    seen = bot.call(server.objects_import, slug, rows)
    assert seen["status"] == "done" and "job_apply" not in seen["next"]
    plan = seen["result"]
    assert plan["applied"] is False
    assert [r["action"] for r in plan["rows"]] == ["create", "error"]

    # 오류가 있는 계획은 적용 작업 자체가 안 만들어진다.
    with pytest.raises(ToolError, match="JOBS-0011"):
        bot.call(server.job_apply, seen["job_id"])
    assert bot.call(server.objects_list, slug)["items"] == []

    rows[1]["power"] = 20
    planned = bot.call(server.objects_import, slug, rows)
    assert "job_apply" in planned["next"]
    done = bot.call(server.job_apply, planned["job_id"])
    assert done["status"] == "done" and done["result"]["applied"] is True
    assert {o["key"] for o in bot.call(server.objects_list, slug)["items"]} == {
        "M-001",
        "M-002",
    }
    assert bot.call(server.job_status, planned["job_id"])["status"] == "done"
    listed = bot.call(server.jobs_list)
    assert any(one["job_id"] == done["job_id"] for one in listed["items"])


def test_읽기_토큰으로는_못_쓴다(client: TestClient, admin: Signed) -> None:
    """범위를 셋으로 가른 이유 — 한 범위로 묶으면 「객체만 넣게」 하려던 토큰이
    **타입까지 지울 수 있다.**"""
    made = client.post(
        "/api/auth/tokens",
        json={"name": _uniq("reader"), "scopes": ["read"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    server._TRANSPORT = httpx.ASGITransport(app=fastapi_app)
    try:
        reader = Bot(made.json()["token"])
        assert "types" in reader.call(server.ontology_schema)
        with pytest.raises(ToolError) as denied:
            reader.call(server.ontology_import, {"types": []}, apply=True)
        assert "ontology:write" in str(denied.value)
    finally:
        server._TRANSPORT = None


def test_읽기가_화면과_대칭이다(bot: Bot) -> None:
    """조건 거르기·이력·가리키는 것·품질 — 화면이 보는 것을 MCP 도 본다. 한쪽만 보이면
    「화면에서는 보이는데 도구로는 못 찾는」 상태가 되고, 모델은 없다고 답한다."""
    slug = _uniq("mach")
    bot.call(
        server.ontology_import,
        {
            "types": [
                {
                    "slug": slug,
                    "label": "설비",
                    "key_policy": "required",
                    "properties": [{"key": "power", "label": "출력", "data_type": "number"}],
                }
            ]
        },
        apply=True,
    )
    small = bot.call(
        server.object_create, slug, label="1호기", key="M-1", properties={"power": 5}
    )
    bot.call(server.object_create, slug, label="2호기", key="M-2", properties={"power": 50})

    strong = bot.call(
        server.objects_list,
        slug,
        conditions=[{"field": "power", "op": "gte", "value": "10"}],
    )
    assert [one["key"] for one in strong["items"]] == ["M-2"]

    bot.call(server.object_update, slug, small["id"], properties={"power": 7})
    history = bot.call(server.object_history, slug, small["id"])
    assert history[0]["kind"] == "object" and "properties.power" in history[0]["changes"]

    refs = bot.call(server.object_references, slug, small["id"])
    assert refs["property_refs"] == [] and refs["relations"] == []

    report = bot.call(server.quality_report, kind="orphan")
    assert "findings" in report


def test_통계와_다른_타입의_칸을_도구로도_센다(bot: Bot) -> None:
    """「개발사 국가별 툴 수」 — 목록을 전부 받아 세게 두면 쪽 상한에서 틀린다. 화면의
    통계와 **같은 서버의 셈**을 도구가 받아야 모델의 답과 화면의 숫자가 같다."""
    company, tool = _uniq("company"), _uniq("tool")
    bot.call(
        server.ontology_import,
        {
            "types": [
                {
                    "slug": company,
                    "label": "기업",
                    "properties": [
                        {
                            "key": "country",
                            "label": "국가",
                            "data_type": "enum",
                            "enum_options": ["미국", "한국"],
                        }
                    ],
                },
                {
                    "slug": tool,
                    "label": "툴",
                    "properties": [
                        {
                            "key": "vendor",
                            "label": "개발사",
                            "data_type": "object_ref",
                            "ref_type_slug": company,
                        }
                    ],
                },
            ]
        },
        apply=True,
    )
    us = bot.call(
        server.object_create, company, label="미국사", properties={"country": "미국"}
    )
    kr = bot.call(
        server.object_create, company, label="한국사", properties={"country": "한국"}
    )
    for label, vendor in (("툴1", us), ("툴2", us), ("툴3", kr)):
        bot.call(server.object_create, tool, label=label, properties={"vendor": vendor["id"]})

    fields = {one["field"]: one for one in bot.call(server.object_fields, tool)}
    assert fields["ref.vendor.country"]["label"] == "개발사 › 국가"

    found = bot.call(server.objects_summary, tool, group_by="ref.vendor.country")
    assert {one["label"]: one["count"] for one in found["buckets"]} == {"미국": 2, "한국": 1}
    assert found["total"] == 3 and found["overlap"] is False

    # **거르기가 목록과 같다** — 같은 조건이면 total 이 같다.
    american = [{"field": "ref.vendor.country", "op": "eq", "value": "미국"}]
    narrowed = bot.call(server.objects_summary, tool, group_by="status", conditions=american)
    listed = bot.call(server.objects_list, tool, conditions=american)
    assert narrowed["total"] == listed["total"] == 2


def test_인터페이스로_여러_타입을_한_번에_읽고_쓰기는_타입으로_한다(bot: Bot) -> None:
    """「설비 중 한국산」 — 시험장비 · 계측기를 따로 묻고 더하게 두면 하나를 빠뜨린다. 화면의
    인터페이스 목록과 **같은 서버의 범위**를 도구가 받는다(ADR 0006)."""
    equip, tester, meter = _uniq("equip"), _uniq("tester"), _uniq("meter")
    bot.call(
        server.ontology_import,
        {
            "interfaces": [
                {
                    "slug": equip,
                    "label": "설비",
                    "properties": [
                        {
                            "key": "country",
                            "label": "국가",
                            "data_type": "enum",
                            "enum_options": ["KR", "US"],
                        }
                    ],
                }
            ],
            "types": [
                {"slug": tester, "label": "시험장비", "interface_slugs": [equip]},
                {"slug": meter, "label": "계측기", "interface_slugs": [equip]},
            ],
        },
        apply=True,
    )
    bot.call(server.object_create, tester, label="시험기", properties={"country": "KR"})
    bot.call(server.object_create, meter, label="계측기", properties={"country": "KR"})
    bot.call(server.object_create, meter, label="수입 계측기", properties={"country": "US"})

    korean = [{"field": "country", "op": "eq", "value": "KR"}]
    listed = bot.call(server.objects_list, equip, conditions=korean)
    assert {(one["label"], one["type_slug"]) for one in listed["items"]} == {
        ("시험기", tester),
        ("계측기", meter),
    }
    by_type = bot.call(server.objects_summary, equip, group_by="type")
    assert {one["label"]: one["count"] for one in by_type["buckets"]} == {
        "시험장비": 1,
        "계측기": 2,
    }
    # **쓰기는 타입으로** — 어느 타입에 넣을지 서버가 짐작하지 않는다.
    with pytest.raises(ToolError, match="OBJECTS-0092"):
        bot.call(server.object_create, equip, label="새 설비")


def test_묶음을_도구로_미리_본다(bot: Bot) -> None:
    """AI 가 만든 묶음이 어떻게 들어갈지 **스스로** 확인하는 자리 — 아무것도 안 남는다."""
    slug = _uniq("memo")
    bundle = {
        "ontology": {"types": [{"slug": slug, "label": "메모", "properties": []}]},
        "objects": [{"type_slug": slug, "rows": [{"label": "첫 메모"}]}],
    }
    seen = bot.call(server.bundle_import, bundle)
    assert seen["status"] == "done", seen
    result = seen["result"]
    assert result["ok"] is True and result["applied"] is False
    assert result["counts"]["objects_create"] == 1
    with pytest.raises(ToolError):
        bot.call(server.objects_list, slug)


def test_이름은_해소하고_쓴다(bot: Bot) -> None:
    """**AI 는 첫 줄을 집는다 — 틀린 줄도 첫 줄이면 집는다.** 그래서 이름으로 가리키는
    자리에는 목록이 아니라 판정을 준다."""
    slug = _uniq("vendor")
    bot.call(
        server.ontology_import,
        {"types": [{"slug": slug, "label": "공급사", "key_policy": "optional"}]},
        apply=True,
    )
    ansys = bot.call(server.object_create, slug, label="Ansys", key="V-001")
    bot.call(server.object_create, slug, label="Ansys Korea")

    exact = bot.call(server.object_resolve, slug, "V-001")
    assert exact["match"] == "exact" and exact["object"]["id"] == ansys["id"]

    # 포함으로 둘이 걸린다 — 하나를 고르지 않는다.
    many = bot.call(server.object_resolve, slug, "Ans")
    assert many["match"] == "candidates" and many["object"] is None
    assert {one["label"] for one in many["candidates"]} == {"Ansys", "Ansys Korea"}

    assert bot.call(server.object_resolve, slug, "없는회사")["match"] == "none"


def test_빈_목록에는_이유가_붙는다(bot: Bot) -> None:
    """0건을 「없다」 로 읽으면 사람은 없는 것을 새로 만든다. 무엇 때문에 0건인지
    **목록과 같은 응답에** 붙어야 모델이 그것을 읽는다."""
    slug = _uniq("part")
    bot.call(
        server.ontology_import,
        {
            "types": [
                {
                    "slug": slug,
                    "label": "부품",
                    "key_policy": "optional",
                    "properties": [{"key": "grade", "label": "등급", "data_type": "text"}],
                }
            ]
        },
        apply=True,
    )

    empty = bot.call(server.objects_list, slug)
    assert empty["total"] == 0
    assert empty["diagnosis"]["reason"] == "empty_type"

    bot.call(server.object_create, slug, label="볼트", properties={"grade": "A"})
    bot.call(server.object_create, slug, label="너트")  # 등급이 비어 있다

    narrow = bot.call(
        server.objects_list,
        slug,
        conditions=[{"field": "grade", "op": "eq", "value": "Z"}],
    )
    assert narrow["total"] == 0
    found = narrow["diagnosis"]
    assert found["reason"] == "filters" and found["type_total"] == 2
    # 「조건에 안 맞음」 과 「값이 없음」 을 가른다.
    assert found["filters"][0]["remaining"] == 2
    assert found["filters"][0]["unknown"] == 1

    # 있으면 진단은 안 붙는다 — 덤이 답을 가리지 않는다.
    assert "diagnosis" not in bot.call(server.objects_list, slug)


def test_타입을_모르면_search_부서를_모르면_whoami(bot: Bot) -> None:
    """`objects_list` 도 `object_resolve` 도 타입이 필수다 — 타입을 모르는 물음의 첫 걸음이
    없으면 모델은 스키마를 읽고 타입마다 돈다. 부서도 같다 — 짐작해 넣으면 거절되거나
    엉뚱한 부서 것이 된다."""
    vendor = _uniq("vendor")
    bot.call(
        server.ontology_import,
        {"types": [{"slug": vendor, "label": "공급사", "key_policy": "optional"}]},
        apply=True,
    )
    me = bot.call(server.whoami)
    assert me["home_workspace_slug"] and isinstance(me["memberships"], list)

    # **고유한 말로 찾는다.** 「ansys」 로 찾으면 다른 시험들이 남긴 「ANSYS Inc.」 들이 이름순
    # 상한(20줄) 안을 먼저 채워 이 줄이 밀려난다 — 시험 DB 를 스위트가 함께 쓰므로 수가 변한다.
    label = f"Ansys {vendor}"
    bot.call(
        server.object_create,
        vendor,
        label=label,
        workspace_slug=me["home_workspace_slug"],
    )
    found = bot.call(server.search, vendor)
    assert any(t["type_slug"] == vendor and t["count"] == 1 for t in found["types"])
    assert any(one["label"] == label for one in found["items"])


def test_잘못_이은_관계는_고치고_끊는다(bot: Bot) -> None:
    """잇기만 되고 고치지도 끊지도 못하면, 틀리게 이은 것을 되돌리려고 사람이 화면으로
    가야 한다 — 그 사이 그 선은 「맞는 선」 으로 읽힌다."""
    machine, site = _uniq("mach"), _uniq("site")
    relation = _uniq("installed")
    bot.call(
        server.ontology_import,
        {
            "types": [{"slug": machine, "label": "설비"}, {"slug": site, "label": "현장"}],
            "relation_types": [
                {
                    "slug": relation,
                    "label": "설치",
                    "src_type_slugs": [machine],
                    "dst_type_slugs": [site],
                }
            ],
        },
        apply=True,
    )
    m = bot.call(server.object_create, machine, label="1호기")
    s = bot.call(server.object_create, site, label="부산")
    bot.call(server.relation_add, machine, m["id"], relation, s["id"], evidence_note="추정")
    rid = bot.call(server.object_get, machine, m["id"])["related"][0]["relation_id"]

    fixed = bot.call(
        server.relation_update, machine, m["id"], rid, evidence_note="설치 대장 3쪽"
    )
    assert fixed["evidence_note"] == "설치 대장 3쪽"

    cut = bot.call(server.relation_remove, machine, m["id"], rid)
    assert cut["ok"] is True  # 204 를 빈 문자열로 흘리지 않는다
    assert bot.call(server.object_get, machine, m["id"])["related"] == []


def test_한_칸_일괄_수정은_계획_먼저고_묶음으로_되돌린다(bot: Bot) -> None:
    part = _uniq("part")
    bot.call(
        server.ontology_import,
        {
            "types": [
                {
                    "slug": part,
                    "label": "부품",
                    "key_policy": "optional",
                    "properties": [{"key": "grade", "label": "등급", "data_type": "text"}],
                }
            ]
        },
        apply=True,
    )
    ids = [
        bot.call(server.object_create, part, label=name, properties={"grade": "A"})["id"]
        for name in ("볼트", "너트")
    ]

    plan = bot.call(server.bulk_edit, part, ids, "properties.grade", "B")
    assert plan["applied"] is False
    assert {row["action"] for row in plan["rows"]} == {"change"}

    done = bot.call(server.bulk_edit, part, ids, "properties.grade", "B", apply=True)
    assert done["applied"] is True and done["batch_id"]
    assert bot.call(server.object_get, part, ids[0])["object"]["properties"]["grade"] == "B"

    back = bot.call(server.bulk_edit_undo, part, done["batch_id"], apply=True)
    assert back["applied"] is True
    assert bot.call(server.object_get, part, ids[0])["object"]["properties"]["grade"] == "A"


def test_트리와_감사_기록을_도구로도_본다(bot: Bot) -> None:
    part = _uniq("part")
    bot.call(
        server.ontology_import,
        {"types": [{"slug": part, "label": "부품", "key_policy": "optional"}]},
        apply=True,
    )
    # 트리 관계가 안 정해진 타입은 빈 트리 — 오류가 아니다.
    assert bot.call(server.object_tree, part) == {"nodes": [], "orphan_count": 0}

    bot.call(server.object_create, part, label="볼트")
    recent = bot.call(server.audit_recent, action="object.create", limit=5)
    assert recent["total"] >= 1
    assert recent["items"][0]["action"] == "object.create"


def test_확장_기능도_도구_둘로_닿는다(client: TestClient, admin: Signed, bot: Bot) -> None:
    """**확장마다 도구를 만들지 않는다.** 도구 목록이 길어질수록 그것을 읽는 쪽은 엉뚱한
    것을 고른다 — 부를 수 있는 것은 목록 하나가 말하고(`extensions_schema`), 부르는 일은
    한 도구가 한다(`extension_call`).

    꺼진 확장은 목록에 없고 불러도 404 다. 쓰기는 그 확장의 범위를 가진 토큰만 한다.
    """
    client.patch(
        "/api/server/extensions/caegroup", json={"enabled": True}, headers=admin.headers
    )
    try:
        listed = bot.call(server.extensions_schema)
        mine = next(one for one in listed if one["name"] == "caegroup")
        paths = {(one["method"], one["path"]) for one in mine["endpoints"]}
        assert ("GET", "dt/pairs") in paths and ("PUT", "dt/assessments/bulk") in paths
        # 한 줄 설명은 **독스트링 첫 줄**이다(자동 생성된 summary 가 아니다).
        defs_line = next(
            one
            for one in mine["endpoints"]
            if one["path"] == "dt/defs" and one["method"] == "GET"
        )
        assert "정의" in defs_line["summary"]
        # 받는 칸도 함께 온다 — 무엇을 보내야 하는지 짐작하지 않게.
        bulk = next(one for one in mine["endpoints"] if one["path"] == "dt/assessments/bulk")
        assert "axis*" in bulk["body"] and "rows*" in bulk["body"]
        pairs = next(
            one
            for one in mine["endpoints"]
            if one["path"] == "dt/pairs" and one["method"] == "GET"
        )
        assert pairs["query"] == ["workspace", "kind"]

        # 읽기는 `read` 로 된다.
        body = bot.call(server.extension_call, "caegroup", "dt/defs")
        assert body["sector"] == "simulation"

        # **쓰기는 그 확장의 범위가 있어야 한다** — 등록하지 않은 경로는 아무 범위로도 못
        # 고친다(모르는 것은 막는다).
        with pytest.raises(ToolError) as denied:
            bot.call(server.extension_call, "caegroup", "dt/setup", method="POST", body={})
        assert "caegroup:write" in str(denied.value)

        made = client.post(
            "/api/auth/tokens",
            json={"name": _uniq("dt"), "scopes": ["read", "caegroup:write"]},
            headers=admin.headers,
        )
        assert made.status_code == 201, made.text
        writer = Bot(made.json()["token"])
        ready = writer.call(server.extension_call, "caegroup", "dt/setup", method="POST")
        assert ready["ready"] is True

        # 꺼면 목록에서도 사라지고, 불러도 문이 404 로 답한다.
        client.patch(
            "/api/server/extensions/caegroup", json={"enabled": False}, headers=admin.headers
        )
        assert all(one["name"] != "caegroup" for one in bot.call(server.extensions_schema))
        with pytest.raises(ToolError) as gone:
            bot.call(server.extension_call, "caegroup", "dt/defs")
        assert "404" in str(gone.value) or "없" in str(gone.value)
    finally:
        client.patch(
            "/api/server/extensions/caegroup", json={"enabled": False}, headers=admin.headers
        )


def test_확장_호출은_뿌리_밖으로_안_나간다(bot: Bot) -> None:
    """경로를 글자로 받는 도구다 — `..` 나 절대 주소가 통하면 이 도구가 확장과 무관한
    자리를 부르는 문이 된다(코어의 쓰기까지 닿는다)."""
    for bad in ("../../ontology/schema", "https://example.test/x", "dt/pairs?workspace=x"):
        got = asyncio.run(server.extension_call(bot.ctx, "caegroup", bad))
        assert "error" in got, bad


def test_이어진_것을_질의어_없이_훑는다(bot: Bot) -> None:
    """**「이것과 이어진 것들」 은 목록으로는 한 걸음까지다.** 그보다 멀면 질의어를 써야
    했고, 질의문을 틀리면 0건이 나와 「없다」 로 잘못 읽힌다.

    화면의 지식 그래프가 쓰는 길을 도구로도 연다 — 답이 화면과 같다.
    """
    part, relation = _uniq("part"), _uniq("uses")
    bot.call(
        server.ontology_import,
        {
            "types": [{"slug": part, "label": "부품", "key_policy": "required"}],
            "relation_types": [{"slug": relation, "label": "사용", "inverse_label": "쓰임"}],
        },
        apply=True,
    )
    # 이름(「사용」)으로 찾지 않는다 — 시험 DB 는 스위트가 함께 써서 같은 이름이 여럿이다.
    schema = bot.call(server.ontology_schema)
    assert relation in {one["slug"] for one in schema["relation_types"]}
    top = bot.call(server.object_create, part, label="상위", key="P-TOP")
    middle = bot.call(server.object_create, part, label="중간", key="P-MID")
    leaf = bot.call(server.object_create, part, label="말단", key="P-LEAF")
    bot.call(
        server.relation_add, part, top["id"], relation, middle["id"], evidence_note="도면"
    )
    bot.call(
        server.relation_add, part, middle["id"], relation, leaf["id"], evidence_note="도면"
    )

    # 한 걸음 — 바로 이웃만.
    near = bot.call(server.graph_neighbors, top["id"], depth=1)
    assert {one["label"] for one in near["nodes"]} == {"상위", "중간"}

    # 두 걸음 — 목록 도구로는 닿지 않던 자리.
    far = bot.call(server.graph_neighbors, top["id"], depth=2)
    assert {one["label"] for one in far["nodes"]} == {"상위", "중간", "말단"}
    assert all(one["relation"] == relation for one in far["edges"])
    # **잘렸으면 잘렸다고 말한다** — 「이게 전부」 로 읽지 않게.
    assert far["truncated"] is False

    # 관계로 좁히면 그 관계만 — 이름을 틀리면 이웃이 안 온다(짐작하지 말라는 뜻).
    only = bot.call(server.graph_neighbors, top["id"], depth=2, relations=[relation])
    assert len(only["nodes"]) == 3
    none = bot.call(server.graph_neighbors, top["id"], depth=2, relations=["없는관계"])
    assert [one["label"] for one in none["nodes"]] == ["상위"]

    # 타입 지형 — 질의를 쓰기 전에 어디로 갈 수 있는지 본다.
    shape = bot.call(server.graph_overview)
    assert any(one["slug"] == part for one in shape["nodes"])


# --- 지우기 · 되돌리기 · 값까지 바꾸는 수정 ------------------------------------------
#
# 시스템 관리자는 MCP 로도 지우고 고친다 — **화면과 같은 규칙 · 같은 권한**으로. 미리 보기가
# 기본이고, 서버가 막는 것은 MCP 도 막는다(서버의 말 그대로).


def _part_type(bot: Bot, **extra: Any) -> str:
    slug = _uniq("part")
    bot.call(
        server.ontology_import,
        {
            "types": [
                {
                    "slug": slug,
                    "label": "부품",
                    "properties": [
                        {"key": "w", "label": "무게", "data_type": "text"},
                        {
                            "key": "grade",
                            "label": "등급",
                            "data_type": "enum",
                            "enum_options": ["상", "하"],
                        },
                    ],
                    **extra,
                }
            ]
        },
        apply=True,
    )
    return slug


def test_정의_지우기는_미리_보기가_먼저고_지운_것은_스냅샷이_되살린다(
    bot: Bot, db: Session
) -> None:
    kind = _part_type(bot)
    bolt = bot.call(server.object_create, kind, label="볼트", properties={"w": "3"})

    # 객체가 든 타입은 계획이 막고, 적용해도 서버의 말 그대로 거절된다.
    plan = bot.call(server.ontology_delete, "type", kind)
    assert plan["allowed"] is False and plan["blocking"][0]["code"].endswith("ONTOLOGY-0038")
    with pytest.raises(ToolError, match="ONTOLOGY-0038"):
        bot.call(server.ontology_delete, "type", kind, apply=True)

    # 속성은 지울 수 있다 — 저장값이 남는다는 것을 미리 말한다. 미리 보기는 아무것도 안 바꾼다.
    planned = bot.call(server.ontology_delete, "property", kind, key="w")
    assert planned["allowed"] is True and planned["keeps"][0].startswith("저장값 1개")
    assert "w" in {
        p["key"] for p in bot.call(server.object_get, kind, bolt["id"])["properties_schema"]
    }
    done = bot.call(server.ontology_delete, "property", kind, key="w", apply=True)
    assert done["ok"] is True
    assert "w" not in {
        p["key"] for p in bot.call(server.object_get, kind, bolt["id"])["properties_schema"]
    }

    # 되살릴 자리 — 지우기 직전 스냅샷. 미리 보기 → 적용. (시험 DB 는 스위트가 함께 써서
    # 통째 스냅샷은 남의 정의에 걸린다 — 그 스냅샷에서 이 타입만 덜어 되돌린다.)
    snapshots = bot.call(server.ontology_restore)["snapshots"]
    assert snapshots[0]["reason"] == f"삭제 직전: 속성 {kind}.w"
    only = _only(db, snapshots[0]["id"], kind)
    preview = bot.call(server.ontology_restore, only)
    assert preview["applied"] is False
    back = bot.call(server.ontology_restore, only, apply=True)
    assert back["applied"] is True
    got = bot.call(server.object_get, kind, bolt["id"])
    assert got["object"]["properties"]["w"] == "3", "되살린 정의로 남아 있던 값이 다시 보인다"

    # 지운 객체만 남은 타입 — 계획이 수를 말하고, 확인을 적어야 영구 삭제하며 지운다.
    bot.call(server.objects_delete, kind, [bolt["id"]], apply=True)
    plan = bot.call(server.ontology_delete, "type", kind)
    assert plan["allowed"] is True and plan["purge_deleted"] == 1
    with pytest.raises(ToolError, match="ONTOLOGY-0084"):
        bot.call(server.ontology_delete, "type", kind, apply=True)
    gone = bot.call(server.ontology_delete, "type", kind, apply=True, purge_deleted=True)
    assert gone["ok"] is True
    assert kind not in {one["slug"] for one in bot.call(server.ontology_schema)["types"]}

    with pytest.raises(ToolError, match="key"):
        bot.call(server.ontology_delete, "property", kind)
    with pytest.raises(ToolError, match="kind"):
        bot.call(server.ontology_delete, "table", kind)


def test_종류_변경은_사람이_정한_대체_값으로_적용된다(bot: Bot) -> None:
    kind = _part_type(bot)
    light = bot.call(server.object_create, kind, label="볼트", properties={"w": "1,200"})
    odd = bot.call(server.object_create, kind, label="너트", properties={"w": "12 kg"})

    plan = bot.call(server.ontology_retype, kind, "w", "number")
    assert plan["applied"] is False
    assert [one["value"] for one in plan["failures"]] == ["12 kg"]
    # 대체 값 없이 적용하면 아무것도 안 바뀐다.
    refused = bot.call(server.ontology_retype, kind, "w", "number", apply=True)
    assert refused["applied"] is False and refused["errors"]

    done = bot.call(
        server.ontology_retype, kind, "w", "number", mapping={"12 kg": "12"}, apply=True
    )
    assert done["applied"] is True and done["snapshot_id"]
    assert bot.call(server.object_get, kind, light["id"])["object"]["properties"]["w"] == 1200
    assert bot.call(server.object_get, kind, odd["id"])["object"]["properties"]["w"] == 12

    with pytest.raises(ToolError, match="owner"):
        bot.call(server.ontology_retype, kind, "w", "text", owner="relation")


def test_고를_값_이름은_저장값과_함께_바뀐다(bot: Bot) -> None:
    kind = _part_type(bot)
    bolt = bot.call(server.object_create, kind, label="볼트", properties={"grade": "상"})

    plan = bot.call(server.ontology_rename_option, kind, "grade", "상", "A")
    assert plan["applied"] is False and plan["objects_with_value"] == 1
    assert (
        bot.call(server.object_get, kind, bolt["id"])["object"]["properties"]["grade"] == "상"
    )

    done = bot.call(server.ontology_rename_option, kind, "grade", "상", "A", apply=True)
    assert done["applied"] is True
    assert (
        bot.call(server.object_get, kind, bolt["id"])["object"]["properties"]["grade"] == "A"
    )


def test_객체_지우기는_계획이_먼저고_가리키는_것이_있으면_막는다(bot: Bot) -> None:
    kind = _part_type(bot)
    holder = _uniq("asm")
    bot.call(
        server.ontology_import,
        {
            "types": [
                {
                    "slug": holder,
                    "label": "조립",
                    "properties": [
                        {
                            "key": "part",
                            "label": "부품",
                            "data_type": "object_ref",
                            "ref_type_slug": kind,
                        }
                    ],
                }
            ]
        },
        apply=True,
    )
    bolt = bot.call(server.object_create, kind, label="볼트")
    loose = bot.call(server.object_create, kind, label="와셔")
    bot.call(server.object_create, holder, label="조립1", properties={"part": bolt["id"]})

    plan = bot.call(server.objects_delete, kind, [bolt["id"], loose["id"]])
    assert plan["applied"] is False
    assert {one["label"]: one["action"] for one in plan["rows"]} == {
        "볼트": "error",
        "와셔": "delete",
    }
    assert len(bot.call(server.objects_list, kind)["items"]) == 2, "미리 보기는 안 지운다"

    # 적용은 지울 수 있는 줄만 — 가리키는 것이 있는 줄은 남고 이유가 줄에 남는다.
    partial = bot.call(server.objects_delete, kind, [bolt["id"], loose["id"]], apply=True)
    assert partial["applied"] is True and partial["counts"] == {"delete": 1, "error": 1}
    assert [one["label"] for one in bot.call(server.objects_list, kind)["items"]] == ["볼트"]

    # 사람이 「참조를 비우고 지운다」 고 정했을 때만.
    done = bot.call(server.objects_delete, kind, [bolt["id"]], mode="detach", apply=True)
    assert done["applied"] is True
    assert bot.call(server.objects_list, kind)["items"] == []

    with pytest.raises(ToolError, match="mode"):
        bot.call(server.objects_delete, kind, [bolt["id"]], mode="force")


def test_합치기는_미리_보기가_두_객체와_걸린_것을_보인다(bot: Bot) -> None:
    kind = _part_type(bot)
    keep = bot.call(server.object_create, kind, label="볼트 M8")
    dup = bot.call(server.object_create, kind, label="M8 볼트")

    preview = bot.call(server.object_merge, kind, dup["id"], keep["id"])
    assert preview["applied"] is False
    assert (preview["from"]["label"], preview["into"]["label"]) == ("M8 볼트", "볼트 M8")
    assert "references" in preview
    assert len(bot.call(server.objects_list, kind)["items"]) == 2

    merged = bot.call(server.object_merge, kind, dup["id"], keep["id"], apply=True)
    assert merged["into"] == keep["id"]
    assert [one["label"] for one in bot.call(server.objects_list, kind)["items"]] == [
        "볼트 M8"
    ]


def test_이력의_그_값으로_되돌리고_상태도_고친다(bot: Bot) -> None:
    kind = _part_type(bot, key_policy="optional")
    bolt = bot.call(server.object_create, kind, label="볼트", properties={"w": "3"})
    bot.call(server.object_update, kind, bolt["id"], label="볼트(수정)", properties={"w": "4"})

    first = bot.call(server.object_history, kind, bolt["id"])[-1]
    back = bot.call(server.object_restore, kind, bolt["id"], first["id"])
    assert (back["label"], back["properties"]["w"]) == ("볼트", "3")

    retired = bot.call(server.object_update, kind, bolt["id"], status="deprecated", key="B-1")
    assert (retired["status"], retired["key"]) == ("deprecated", "B-1")


def test_권한은_화면과_같다_부서_관리자는_자기_부서_객체만_정의는_못_지운다(
    client: TestClient, admin: Signed, manager: Signed, bot: Bot
) -> None:
    kind = _part_type(bot)
    made = client.post(
        "/api/auth/tokens",
        json={"name": _uniq("mgr"), "scopes": ["read", "objects:write", "ontology:write"]},
        headers=manager.headers,
    )
    assert made.status_code == 201, made.text
    hand = Bot(made.json()["token"])
    mine = hand.call(
        server.object_create, kind, label="볼트", workspace_slug=manager.workspace
    )
    shared = bot.call(server.object_create, kind, label="전역 볼트")

    assert hand.call(server.objects_delete, kind, [mine["id"]], apply=True)["applied"] is True
    # 전역 객체는 보이지만 고치는 것은 시스템 관리자뿐이다 — 화면과 같은 이유로 그 줄이 막힌다.
    refused = hand.call(server.objects_delete, kind, [shared["id"]], apply=True)
    assert refused["applied"] is False and refused["rows"][0]["action"] == "error"
    assert [one["label"] for one in bot.call(server.objects_list, kind)["items"]] == [
        "전역 볼트"
    ]
    with pytest.raises(ToolError, match="AUTH-0103"):
        hand.call(server.ontology_delete, "property", kind, key="w")


def test_표에서_기록_타입을_만들고_축에_잇는다(bot: Bot) -> None:
    """`table_infer` 가 열이 가리키는 축을 찾아 제안하고, 그 열 그대로 `table_build` →
    `ontology_import` → `objects_import` 로 이어진다 — 기록마다 따로 개발하지 않는다
    (ADR 0009)."""
    model, tag = _uniq("model"), _uniq("M")
    bot.call(
        server.ontology_import,
        {"types": [{"slug": model, "label": "개발모델", "key_policy": "required"}]},
        apply=True,
    )
    seeded = bot.call(
        server.objects_import,
        model,
        [{"key": f"{tag}-{i}", "label": f"모델 {tag} {i}"} for i in range(1, 6)],
    )
    bot.call(server.job_apply, seeded["job_id"])

    rows = [{"이름": f"건 {i}", "모델": f"{tag}-{i % 5 + 1}"} for i in range(10)]
    got = bot.call(server.table_infer, rows)
    assert "raw_rows" not in got, "보낸 행을 되돌려 받지 않는다"
    column = next(one for one in got["columns"] if one["header"] == "모델")
    assert column["data_type"] == "object_ref" and column["ref_type_slug"] == model
    assert column["ref_candidates"][0]["one"] == 10

    case = _uniq("case")
    built = bot.call(server.table_build, case, "시장 서비스", got["columns"], rows)
    bot.call(server.ontology_import, built["schema"], apply=True)
    planned = bot.call(server.objects_import, case, built["import_rows"])
    assert bot.call(server.job_apply, planned["job_id"])["result"]["applied"] is True
    items = bot.call(server.objects_list, case)["items"]
    pointed = {str(one["properties"][column["key"]]) for one in items}
    assert len(items) == 10 and len(pointed) == 5
    assert all(uuid.UUID(one) for one in pointed), "글자가 아니라 모델의 id 로 들어갔다"


def test_글로_넣은_기록을_종류_변경으로_축에_잇는다(bot: Bot) -> None:
    """이미 글로 넣은 칸을 `ontology_retype(data_type="object_ref", ref_type_slug=)` 로 —
    못 찾은 값은 계획의 `failures` 로 오고, 사람이 정한 대체 값으로만 적용된다(ADR 0009)."""
    model, case, tag = _uniq("model"), _uniq("case"), _uniq("M")
    bot.call(
        server.ontology_import,
        {
            "types": [
                {"slug": model, "label": "개발모델", "key_policy": "required"},
                {
                    "slug": case,
                    "label": "시장 서비스",
                    "properties": [{"key": "model", "label": "모델", "data_type": "text"}],
                },
            ]
        },
        apply=True,
    )
    made = bot.call(server.object_create, model, key=f"{tag}-1", label="모델 1")
    hit = bot.call(server.object_create, case, label="건 1", properties={"model": f"{tag}-1"})
    bot.call(server.object_create, case, label="건 2", properties={"model": "없는 모델"})

    asked = {"data_type": "object_ref", "ref_type_slug": model}
    plan = bot.call(server.ontology_retype, case, "model", **asked)
    assert [one["value"] for one in plan["failures"]] == ["없는 모델"], plan
    done = bot.call(
        server.ontology_retype, case, "model", mapping={"없는 모델": None}, apply=True, **asked
    )
    assert done["applied"] is True, done
    got = bot.call(server.object_get, case, hit["id"])
    assert got["object"]["properties"]["model"] == made["id"]


def test_값이_많은_타입의_종류_변경은_작업이_되고_job_apply_로_적용한다(
    bot: Bot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.ontology import retype

    monkeypatch.setattr(retype, "RETYPE_INLINE", 1)
    case = _uniq("case")
    bot.call(
        server.ontology_import,
        {
            "types": [
                {
                    "slug": case,
                    "label": "시장 서비스",
                    "properties": [{"key": "qty", "label": "수량", "data_type": "text"}],
                }
            ]
        },
        apply=True,
    )
    for n in ("1", "2"):
        bot.call(server.object_create, case, label=f"건 {n}", properties={"qty": n})

    # apply=True 로 불러도 계획부터 — 사람이 결과를 본 뒤에 적용한다.
    planned = bot.call(server.ontology_retype, case, "qty", "number", apply=True)
    assert planned["kind"] == "ontology_retype" and planned["status"] == "done", planned
    assert planned["result"]["applied"] is False and "job_apply" in planned["next"]
    done = bot.call(server.job_apply, planned["job_id"])
    assert done["status"] == "done" and done["result"]["applied"] is True, done
    values = {one["properties"]["qty"] for one in bot.call(server.objects_list, case)["items"]}
    assert values == {1, 2}


def test_가리키는_기록이_많은_객체의_합치기와_지우기는_작업으로_한다(
    bot: Bot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.objects import lifecycle

    monkeypatch.setattr(lifecycle, "REWRITE_INLINE", 1)
    model, case = _uniq("model"), _uniq("case")
    bot.call(
        server.ontology_import,
        {
            "types": [
                {"slug": model, "label": "개발모델", "key_policy": "required"},
                {
                    "slug": case,
                    "label": "시장 서비스",
                    "usage": "log",
                    "properties": [
                        {
                            "key": "model",
                            "label": "모델",
                            "data_type": "object_ref",
                            "ref_type_slug": model,
                        }
                    ],
                },
            ]
        },
        apply=True,
    )
    old = bot.call(server.object_create, model, key="OLD", label="옛 모델")
    new = bot.call(server.object_create, model, key="NEW", label="새 모델")
    gone = bot.call(server.object_create, model, key="GONE", label="지울 모델")
    for n in range(2):
        bot.call(server.object_create, case, label=f"건 {n}", properties={"model": old["id"]})
        bot.call(
            server.object_create, case, label=f"건 {n}b", properties={"model": gone["id"]}
        )

    merged = bot.call(server.object_merge, model, old["id"], new["id"], apply=True)
    assert merged["kind"] == "objects_rewrite" and merged["status"] == "done", merged
    assert merged["result"]["property_refs"] == 2

    deleted = bot.call(server.objects_delete, model, [gone["id"]], mode="detach", apply=True)
    row = deleted["rows"][0]
    assert row["job"]["status"] == "done" and row["job"]["result"]["op"] == "detach", deleted
    pointed = {
        one["properties"].get("model") for one in bot.call(server.objects_list, case)["items"]
    }
    assert pointed == {new["id"], None}


def test_사진은_curl_명령으로_붙인다_바이트는_도구를_거치지_않는다(
    client: TestClient, admin: Signed, bot: Bot
) -> None:
    """도구는 표와 명령만 준다 — 파일은 셸이 직접 올린다(ADR 0012). 명령을 그대로 흉내 내
    올리면 붙는다. 경로의 빈칸 · 괄호는 셸이 깨지지 않게 감싼다."""
    import shlex

    from tests.api.test_attachment_images import _png, _world

    w = _world(client, admin)
    token = bot.ctx.request_context.request.headers["authorization"]
    behind_nginx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(
                headers={
                    "authorization": token,
                    "host": "sp.example.com",
                    "x-forwarded-proto": "https",
                }
            )
        )
    )
    got = asyncio.run(
        server.attachment_upload_prepare(
            behind_nginx, w["type"], w["id"], "/home/me/현장 사진 (1).png", field="photo"
        )
    )
    assert "error" not in got, got
    words = shlex.split(got["curl"])
    assert words[:3] == ["curl", "-sS", "-T"]
    assert words[3] == "/home/me/현장 사진 (1).png"
    ticket = words[words.index("-H") + 1].removeprefix("X-Upload-Ticket: ")
    url = words[-1]
    assert url.startswith("https://sp.example.com/api/attachments/upload?filename=")
    assert "base64" not in got["next"]

    # 셸이 하는 일 — 토큰 없이 표만 실어 바이트를 그대로.
    done = client.put(
        "/api/attachments/upload?" + url.split("?", 1)[1],
        content=_png(),
        headers={"X-Upload-Ticket": ticket},
    )
    assert done.status_code == 201, done.text
    assert done.json()["original_name"] == "현장 사진 (1).png"
    assert done.json()["is_image"] is True

    # 읽을 때도 바이트는 없다 — 이름 · 크기 · 판정만.
    detail = bot.call(server.object_get, w["type"], w["id"])
    (brief,) = detail["attachments"]
    assert brief["is_image"] is True and "content" not in brief
    removed = bot.call(server.attachment_remove, brief["id"])
    assert removed == {"ok": True, "message": "완료"}


def test_업로드_주소는_들어온_주소를_따른다() -> None:
    """nginx 뒤면 포트 없이 그 호스트, MCP 포트로 바로 왔으면 앱 포트(-2)."""

    def origin(headers: dict[str, str]) -> Any:
        ctx = SimpleNamespace(
            request_context=SimpleNamespace(request=SimpleNamespace(headers=headers))
        )
        return server._public_origin(ctx)

    assert origin({"host": "sp.example.com", "x-forwarded-proto": "https"}) == (
        "https://sp.example.com"
    )
    assert origin({"host": "10.0.0.5:8042"}) == "http://10.0.0.5:8040"
    assert origin({"host": "10.0.0.5:9000"}) == "http://10.0.0.5:9000"
    assert origin({}) is None


def test_RA_처럼_MCP_직접_주소를_적어도_업로드는_앱_포트로_간다(
    client: TestClient, admin: Signed, bot: Bot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.env` 의 MCP_PUBLIC_URL 에 MCP 직접 주소(`http://<IP>:<앱+2>/mcp`)를 적으면 —
    ReportArchive 가 그렇게 쓴다 — 0.4.29 는 그 주소에서 `/mcp` 만 떼어 업로드를 **MCP
    포트**로 보냈다(404). 그 값이면 백엔드는 완전한 주소를 만들지 않고, MCP 가 들어온
    주소(앱 포트)로 만든다. 앱은 접두어가 붙어 오든 벗겨 오든 받는다(PrefixMiddleware)."""
    import shlex

    from app.config import get_settings
    from tests.api.test_attachment_images import _world

    settings = get_settings()
    monkeypatch.setattr(settings, "public_path", "/sp")
    monkeypatch.setattr(settings, "mcp_public_url", f"http://10.0.0.5:{settings.port + 2}/mcp")
    w = _world(client, admin)
    token = bot.ctx.request_context.request.headers["authorization"]
    direct = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(
                headers={"authorization": token, "host": f"10.0.0.5:{settings.port + 2}"}
            )
        )
    )
    monkeypatch.setenv("MCP_PORT", str(settings.port + 2))
    got = asyncio.run(
        server.attachment_upload_prepare(
            direct, w["type"], w["id"], "/tmp/a.png", field="photo"
        )
    )
    assert "error" not in got, got
    url = shlex.split(got["curl"])[-1].split("?")[0]
    assert url == f"http://10.0.0.5:{settings.port}/sp/api/attachments/upload"


def test_표준이_아닌_포트의_프록시_뒤는_MCP_공개_주소로_만든다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """그 포트(8443)는 프록시가 넘기는 `Host` 에 없다 — 그래서 MCP_PUBLIC_URL 이 있는
    자리다."""
    from app.config import get_settings
    from app.modules.files import services as file_services

    settings = get_settings()
    monkeypatch.setattr(settings, "public_path", "/sp")
    monkeypatch.setattr(settings, "mcp_public_url", "https://hwax.example.com:8443/sp/mcp")
    assert file_services._public_upload_url("/sp/api/attachments/upload") == (
        "https://hwax.example.com:8443/sp/api/attachments/upload"
    )
    monkeypatch.setattr(settings, "mcp_public_url", "")
    assert file_services._public_upload_url("/sp/api/attachments/upload") is None


def test_지표를_도구로_정의하고_읽으면_통계와_같은_수(bot: Bot) -> None:
    """**세는 일은 목록으로 받아 직접 하지 않는다**(ADR 0013) — 계획 → 저장하고 세기 → 읽기가
    진짜 라우터를 타고, 읽은 수가 그때그때 센 통계와 같다."""
    kind = _uniq("case")
    bot.call(
        server.ontology_import,
        {
            "types": [
                {
                    "slug": kind,
                    "label": "기록",
                    "usage": "log",
                    "properties": [
                        {"key": "received", "label": "접수일", "data_type": "date"},
                        {
                            "key": "symptom",
                            "label": "증상",
                            "data_type": "enum",
                            "enum_options": ["소음", "발열"],
                        },
                    ],
                }
            ]
        },
        apply=True,
    )
    for received, symptom in [
        ("2026-01-05", "소음"),
        ("2026-01-20", "발열"),
        ("2026-02-03", "소음"),
        ("2026-03-11", "소음"),
    ]:
        bot.call(
            server.object_create,
            kind,
            label=f"{received} {symptom}",
            properties={"received": received, "symptom": symptom},
        )
    slug = _uniq("m")
    spec = {
        "measure": "count",
        "time": {"address": "properties.received", "grain": "month"},
        "dimensions": [{"name": "symptom", "address": "properties.symptom"}],
    }
    planned = bot.call(server.metric_define, slug, "월별 증상", kind, spec)
    assert planned["ok"] is True and planned["rows"] == 4
    assert [one["name"] for one in planned["dims"]] == ["symptom"]
    # 계획이 거절 사유를 모아 말한다 — 저장되지 않는다.
    bad = bot.call(
        server.metric_define,
        slug,
        "x",
        kind,
        {
            "measure": "count",
            "dimensions": [
                {"name": "period", "address": "properties.symptom"},  # 예약어
                {"name": "gone", "address": "properties.nope"},  # 없는 칸
            ],
        },
    )
    assert bad["ok"] is False and len(bad["errors"]) == 2

    saved = bot.call(server.metric_define, slug, "월별 증상", kind, spec, apply=True)
    assert saved["metric"]["slug"] == slug and saved["updated"] is False
    assert saved["job"]["status"] == "done", saved["job"]
    listed = bot.call(server.metric_list)
    mine = next(one for one in listed["metrics"] if one["slug"] == slug)
    assert mine["grain"] == "month" and mine["last_status"] == "ok" and mine["stale"] is False

    table = bot.call(server.metric_query, slug, dims=["symptom"])
    counts = {one["labels"]["symptom"]: one["count"] for one in table["cells"]}
    summary = bot.call(server.objects_summary, kind, group_by="properties.symptom")
    assert (
        counts
        == {one["label"]: one["count"] for one in summary["buckets"]}
        == {
            "소음": 3,
            "발열": 1,
        }
    )
    assert table["total"] == 4 and table["computed_at"] is not None
    series = bot.call(server.metric_query, slug, shape="series", period_to="2026-04-01")
    assert [one["count"] for one in series["lines"][0]["points"]] == [2, 1, 1]
    # 셀의 건 보기 조건을 목록 조건으로 풀면 같은 수.
    cell = next(one for one in table["cells"] if one["labels"]["symptom"] == "소음")
    conditions = [
        {"field": key[2:].rsplit(".", 1)[0], "op": key.rsplit(".", 1)[1], "value": value}
        for key, value in cell["drill"]["params"].items()
    ]
    assert bot.call(server.objects_list, kind, conditions=conditions)["total"] == 3
    # 같은 slug 로 다시 정의하면 고친다.
    again = bot.call(
        server.metric_define, slug, "월별 증상(고침)", kind, spec, apply=True, description="d"
    )
    assert again["updated"] is True and again["metric"]["label"] == "월별 증상(고침)"
    with pytest.raises(ToolError, match="shape"):
        bot.call(server.metric_query, slug, shape="pie")
    # 분석 — 지표 목록이 되는 분석을 말하고, 셀 위의 통계는 플랫폼이 낸다.
    listed = bot.call(server.metric_list)
    mine = next(one for one in listed["metrics"] if one["slug"] == slug)
    analyses = {one["recipe"]: one for one in mine["analyses"]}
    assert analyses["pareto"]["ok"] is True and analyses["life"]["ok"] is False
    pareto = bot.call(server.metric_analyze, slug, "pareto", options={"dim": "symptom"})
    assert [(one["label"], one["count"]) for one in pareto["items"]] == [
        ("소음", 3),
        ("발열", 1),
    ]
    assert pareto["method"].startswith("파레토") and pareto["computed_at"] is not None
    with pytest.raises(ToolError, match="METRICS-0024"):
        bot.call(server.metric_analyze, slug, "life")
    with pytest.raises(ToolError, match="options"):
        bot.call(server.metric_analyze, slug, "pareto", options={"dim": "symptom", "x": 1})
    # 경보 — 읽기만. 만든 것이 없으면 빈 목록이다.
    assert bot.call(server.metric_alerts, slug) == {"alerts": [], "events": []}


def test_자기소개는_서버가_아는_사실로_쓰고_whoami_가_싣는다(bot: Bot, db: Session) -> None:
    """같은 틀로 띄운 플랫폼 여럿이 붙으면 도구가 전부 같다 — 에이전트가 어디에 물을지 고르는
    단서가 이 소개다. 관리자 에이전트가 사실을 모아 초안을 쓰고, 미리 보기 뒤에 저장한다."""
    from app.modules.server.models import PlatformProfile

    db.query(PlatformProfile).delete()
    db.commit()
    try:
        got = bot.call(server.platform_profile)
        assert got["profile"]["summary"] == ""
        assert got["facts"]["types"] and "unavailable" not in got  # 시스템 관리자의 토큰
        plan = bot.call(server.platform_profile_update, summary="보고서 쌍둥이")
        assert plan["applied"] is False
        assert bot.call(server.whoami)["platform"]["summary"] == ""  # 미리 보기는 안 바꾼다
        done = bot.call(server.platform_profile_update, summary="보고서 쌍둥이", apply=True)
        assert done["profile"]["summary"] == "보고서 쌍둥이"
        assert bot.call(server.whoami)["platform"]["summary"] == "보고서 쌍둥이"
    finally:
        db.query(PlatformProfile).delete()
        db.commit()
