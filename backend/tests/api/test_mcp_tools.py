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

from app.main import app as fastapi_app
from tests.api.conftest import Signed

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
    try:
        yield Bot(made.json()["token"])
    finally:
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
    plan = bot.call(server.objects_import, slug, rows)
    assert plan["applied"] is False
    assert [r["action"] for r in plan["rows"]] == ["create", "error"]

    refused = bot.call(server.objects_import, slug, rows, apply=True)
    assert refused["applied"] is False
    assert bot.call(server.objects_list, slug)["items"] == []

    rows[1]["power"] = 20
    done = bot.call(server.objects_import, slug, rows, apply=True)
    assert done["applied"] is True
    assert {o["key"] for o in bot.call(server.objects_list, slug)["items"]} == {
        "M-001",
        "M-002",
    }


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


def test_묶음을_도구로_미리_본다(bot: Bot) -> None:
    """AI 가 만든 묶음이 어떻게 들어갈지 **스스로** 확인하는 자리 — 아무것도 안 남는다."""
    slug = _uniq("memo")
    bundle = {
        "ontology": {"types": [{"slug": slug, "label": "메모", "properties": []}]},
        "objects": [{"type_slug": slug, "rows": [{"label": "첫 메모"}]}],
    }
    seen = bot.call(server.bundle_import, bundle)
    assert seen["ok"] is True and seen["applied"] is False
    assert seen["counts"]["objects_create"] == 1
    with pytest.raises(ToolError):
        bot.call(server.objects_list, slug)
