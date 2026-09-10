"""어댑터가 서는가 — **`mcp` 가 API 를 바꾸면 여기서 걸린다.**

이 시험만 따로 도는 이유: `mcp` 는 `httpx2` 를 끌어오고, 그것이 깔리면
`starlette.testclient` 가 **HTTP 스택을 바꾼다.** 백엔드 개발 환경에 섞으면
그쪽 시험의 타입이 흔들린다 — 실측으로 겪었다. 그래서 **환경을 가른다.**

    python -m venv mcp_server/.venv
    mcp_server/.venv/bin/pip install -r mcp_server/requirements.txt pytest
    mcp_server/.venv/bin/python -m pytest mcp_server/tests

알맹이(`tools.py`)의 시험은 백엔드 쪽에 있다 — 진짜 앱에 붙여야 확인이 되기 때문이다.
"""

from __future__ import annotations

import asyncio
import os

from mcp_server import server, tools


def test_도구가_그대로_선다() -> None:
    """**설명은 `tools.py` 의 docstring 이 정본이다.**

    어댑터에 다시 적으면 두 벌이 되고, 모델이 읽는 것은 그쪽이라 **실제 동작과
    다른 설명을 읽게 된다.**
    """
    os.environ.setdefault("PLATFORM_TOKEN", "여기서는 안 부른다")
    listed = asyncio.run(server.mcp.list_tools())
    assert {one.name for one in listed} == set(tools.TOOLS)

    by_name = {one.name: one for one in listed}
    for name, function in tools.TOOLS.items():
        described = by_name[name].description or ""
        assert described.strip(), f"{name} 에 설명이 없습니다"
        assert described.splitlines()[0] in (function.__doc__ or "")

        # **`platform` 은 인자가 아니다.** 노출되면 모델이 그것을 채우려 든다.
        assert "platform" not in (by_name[name].input_schema.get("properties") or {})


def test_꼭_있어야_하는_인자가_필수로_선다() -> None:
    os.environ.setdefault("PLATFORM_TOKEN", "여기서는 안 부른다")
    listed = {one.name: one for one in asyncio.run(server.mcp.list_tools())}
    assert set(listed["relation_add"].input_schema["required"]) == {
        "type_slug",
        "object_id",
        "relation",
        "dst_object_id",
    }
    # 스키마 읽기는 인자가 없다 — **먼저 부르는 도구라 문턱이 없어야 한다.**
    assert not (listed["ontology_schema"].input_schema.get("properties") or {})
