"""MCP 도구의 알맹이 — **붙여 보기 전에 도는지 아는 유일한 방법.**

`mcp_server/tools.py` 는 `httpx` 만 쓰고 `server.py` 만 `mcp` 를 쓴다. 그래서
여기서 진짜 앱에 붙여 확인할 수 있다 — 어댑터에 규칙을 넣으면 이 확인이
불가능해진다.

**규칙이 두 벌이 아닌지도 여기서 본다.** 서버가 막는 것을 MCP 가 통과시키면
「MCP 로는 되는데 화면에서는 안 되는」 상태가 되고, 그때 어느 쪽이 맞는지 알
방법이 없다.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

# **저장소 루트를 길에 넣는다.** `mcp_server` 는 배포 이미지에 안 들어가고 사람의
# PC 에서 도는 것이라 백엔드 밖에 있다 — 그래도 진짜 앱에 붙여 봐야 확인이 된다.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mcp_server import tools

from tests.api.conftest import Signed


class _ClientPlatform(tools.Platform):
    """진짜 앱(TestClient)에 붙는 Platform.

    HTTP 를 실제로 태우되 서버는 이 프로세스 안이다 — **라우터·권한·검증이 통째로
    돈다**(서비스 함수를 직접 부르면 그것이 전부 빠진다).
    """

    def __init__(self, client: TestClient, token: str) -> None:
        self._client = client
        self.base_url = "/api"
        self.token = token

    def request(self, method: str, path: str, **kw: Any) -> Any:
        response = self._client.request(
            method, f"/api{path}", headers={"Authorization": f"Bearer {self.token}"}, **kw
        )
        if response.status_code >= 400:
            body = response.json()
            error = body.get("error") or {}
            raise tools.PlatformError(f"[{error.get('code')}] {error.get('message')}")
        return response.json() if response.content else None


def _uniq(base: str) -> str:
    return f"{base}_{uuid.uuid4().hex[:6]}"


@pytest.fixture
def bot(client: TestClient, admin: Signed) -> _ClientPlatform:
    """기계 자격 하나. **사람 세션이 아니라 PAT 로 붙는다.**"""
    made = client.post(
        "/api/auth/tokens",
        json={"name": _uniq("bot"), "scopes": ["read", "objects:write", "ontology:write"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return _ClientPlatform(client, made.json()["token"])


def test_토큰이_없으면_무엇을_해야_하는지_말한다() -> None:
    """**「인증 실패」 만 뜨면 무엇을 해야 하는지 알 수 없다.**"""
    with pytest.raises(tools.PlatformError) as caught:
        tools.Platform(base_url="http://127.0.0.1:1/api", token="")
    assert "개인 액세스 토큰" in str(caught.value)


def test_스키마부터_읽는다(bot: _ClientPlatform) -> None:
    """**다른 도구를 부르기 전에 이것부터.** 무엇을 만들 수 있고 각 타입이 어떤
    값을 받는지가 여기 다 있다."""
    got = tools.ontology_schema(bot)
    assert {"groups", "types", "relation_types", "data_types"} <= set(got)


def test_정의를_넣고_객체를_채운다(bot: _ClientPlatform) -> None:
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
    plan = tools.ontology_import(bot, schema)
    assert plan["applied"] is False
    assert all(t["slug"] != slug for t in tools.ontology_schema(bot)["types"])

    done = tools.ontology_import(bot, schema, apply=True)
    assert done["applied"] is True
    assert done["snapshot_id"], "되돌릴 자리가 남아야 한다"

    made = tools.object_create(bot, slug, label="1호기", key="M-001", properties={"power": 15})
    assert made["properties"]["power"] == 15

    listed = tools.objects_list(bot, slug, q="1호기")
    assert [one["label"] for one in listed["items"]] == ["1호기"]

    got = tools.object_get(bot, slug, made["id"])
    assert got["object"]["key"] == "M-001"
    assert [p["key"] for p in got["properties_schema"]] == ["power"]


def test_서버가_막는_것을_그대로_전한다(bot: _ClientPlatform) -> None:
    """**규칙이 두 벌이 아니다.** MCP 가 통과시키면 「MCP 로는 되는데 화면에서는
    안 되는」 상태가 되고, 그때 어느 쪽이 맞는지 알 방법이 없다.

    그리고 **서버의 말을 그대로 전한다** — 오류 문구에 무엇을 고쳐야 하는지가
    적혀 있고, 여기서 고쳐 쓰면 그것을 잃는다.
    """
    slug = _uniq("mach")
    tools.ontology_import(
        bot,
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

    with pytest.raises(tools.PlatformError) as over:
        tools.object_create(bot, slug, label="터빈", properties={"power": 9000})
    assert "kW" in str(over.value) and "9000" in str(over.value)

    with pytest.raises(tools.PlatformError) as unknown:
        tools.object_create(bot, slug, label="터빈", properties={"없는키": 1})
    assert "정의되지 않은 속성" in str(unknown.value)


def test_부분_수정은_다른_속성을_안_건드린다(bot: _ClientPlatform) -> None:
    slug = _uniq("mach")
    tools.ontology_import(
        bot,
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
    made = tools.object_create(
        bot, slug, label="1호기", properties={"power": 10, "memo": "점검함"}
    )
    patched = tools.object_update(bot, slug, made["id"], properties={"power": 20})
    assert patched["properties"] == {"power": 20, "memo": "점검함"}


def test_이을_때_근거를_남긴다(bot: _ClientPlatform) -> None:
    """**근거 없는 연결은 시간이 지나면 아무도 못 믿는다** — 기계가 이은 것이면
    더 그렇다."""
    machine, site = _uniq("mach"), _uniq("site")
    relation = _uniq("installed")
    tools.ontology_import(
        bot,
        {
            "types": [{"slug": machine, "label": "설비"}, {"slug": site, "label": "현장"}],
            "relation_types": [
                {
                    "slug": relation,
                    "label": "설치됨",
                    "inverse_label": "설치함",
                    "cardinality": "many_to_one",
                    "src_type_slugs": [machine],
                    "dst_type_slugs": [site],
                }
            ],
        },
        apply=True,
    )

    one = tools.object_create(bot, machine, label="1호기")
    where = tools.object_create(bot, site, label="A동")
    tools.relation_add(
        bot, machine, one["id"], relation, where["id"], evidence_note="설치 대장"
    )

    got = tools.object_get(bot, machine, one["id"])
    assert [(r["label"], r["object_label"], r["evidence_note"]) for r in got["related"]] == [
        ("설치됨", "A동", "설치 대장")
    ]


def test_도구는_아홉이고_설명이_있다() -> None:
    """**도구 목록이 길수록 모델은 엉뚱한 것을 고른다.** 동적인 것은 도구가
    아니라 스키마다.

    설명은 함수의 docstring 이 정본이다 — 어댑터에 다시 적으면 두 벌이 되고,
    모델이 읽는 것은 그쪽이라 **실제 동작과 다른 설명을 읽게 된다.**
    """
    assert len(tools.TOOLS) == 9
    for name, function in tools.TOOLS.items():
        assert function.__doc__, f"{name} 에 설명이 없습니다"


def test_여러_행을_한_번에_넣고_두_번_넣어도_두_벌이_안_된다(bot: _ClientPlatform) -> None:
    slug = _uniq("tool")
    tools.ontology_import(
        bot,
        {
            "types": [
                {
                    "slug": slug,
                    "label": "도구",
                    "key_policy": "required",
                    "properties": [{"key": "cost", "label": "가격", "data_type": "number"}],
                }
            ]
        },
        apply=True,
    )
    rows = [
        {"key": "T-1", "label": "망치", "cost": 10},
        {"key": "T-2", "label": "톱", "cost": 20},
    ]

    plan = tools.objects_import(bot, slug, rows)
    assert plan["applied"] is False
    assert plan["counts"]["create"] == 2

    done = tools.objects_import(bot, slug, rows, apply=True)
    assert done["applied"] is True
    again = tools.objects_import(bot, slug, rows, apply=True)
    assert again["counts"] == {"create": 0, "update": 0, "unchanged": 2, "error": 0}
    assert tools.objects_list(bot, slug)["total"] == 2
