"""로컬 정제 MCP 가 **진짜 `mcp`** 로 서는가 — `mcp` 가 API 를 바꾸면 여기서 걸린다.

    python -m venv venv && venv/bin/pip install -r pipeline/requirements.txt pytest
    venv/bin/python -m pytest pipeline/tests

도구가 앱에 붙어 한 바퀴 도는지는 백엔드 쪽(`backend/tests/api/test_pipeline_mcp.py`)이
가짜 `mcp` 로 본다. 여기서는 등록 · 인자 모양, 그리고 **클라이언트가 stdio 로 띄워 부를 때
멈춤의 말이 그대로 가나**를 본다 — 가짜로는 반환 검증이 안 돌아 못 잡는다(실측으로 겪었다).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
import sp_mcp
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PY = Path(sp_mcp.__file__).resolve()
TOOLS = {
    "pipeline_guide",
    "work_list",
    "work_init",
    "work_status",
    "work_read",
    "work_write",
    "decision_record",
    "source_profile",
    "source_head",
    "table_convert",
    "run_init",
    "run_validate",
    "run_preview",
}


def test_도구가_등록되고_인자를_잃지_않는다() -> None:
    tools = {tool.name: tool for tool in asyncio.run(sp_mcp.mcp.list_tools())}
    assert set(tools) == TOOLS
    assert list(tools["work_write"].inputSchema["properties"]) == ["work", "path", "content"]
    assert tools["source_profile"].inputSchema["required"] == ["work", "source"]
    assert "먼저 부른다" in (tools["pipeline_guide"].description or "")


def test_클라이언트가_stdio_로_띄워_부르면_멈춤의_말이_그대로_간다(tmp_path: Path) -> None:
    """Claude Desktop · Gemini CLI 가 하는 그대로 — 프로세스를 띄우고 stdio 로 말한다."""

    async def talk() -> list[tuple[bool, str]]:
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(SERVER_PY)],
            env={**os.environ, "SP_WORK_ROOT": str(tmp_path)},
        )
        seen: list[tuple[bool, str]] = []
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            for name, arguments in (
                ("work_init", {"work": "해석팀/대장", "title": "대장", "group": "cae"}),
                ("work_status", {"work": "해석팀/대장"}),
                (
                    "work_write",
                    {"work": "해석팀/대장", "path": "../../x.json", "content": "{}"},
                ),
            ):
                result = await session.call_tool(name, arguments)
                text = " ".join(getattr(block, "text", "") for block in result.content)
                seen.append((bool(result.isError), text))
        return seen

    made, status, escape = asyncio.run(talk())
    assert made[0] is False and "작업 폴더를 만들었습니다" in made[1]
    assert status[0] is False and "원천 파일을 00-원천/" in status[1]
    # 오류로 표시되고, **사람에게 전할 말**이 검증 오류에 묻히지 않는다.
    assert escape[0] is True
    assert "작업 폴더 밖의 경로입니다" in escape[1] and "validation" not in escape[1]


def test_설정이_없으면_무엇을_적을지_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SP_WORK_ROOT", raising=False)
    with pytest.raises(Exception, match="SP_WORK_ROOT"):
        asyncio.run(sp_mcp.mcp.call_tool("work_list", {}))
