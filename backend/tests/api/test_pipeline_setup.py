"""로컬 MCP 설치(`pipeline/sp_setup.py`) — **남의 설정 파일을 망가뜨리지 않는다.**

venv 를 만들고 휠을 까는 부분은 시험하지 않는다(느리고 네트워크 · OS 에 달렸다). 사람이 가장
크게 다치는 자리 — Claude Desktop · Gemini CLI 설정 파일에 합치는 것 — 만 본다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SETUP = Path(__file__).resolve().parents[3] / "pipeline" / "sp_setup.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("sp_setup_under_test", SETUP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


setup = _load()


def _entry(token: str = "") -> dict[str, Any]:
    result: dict[str, Any] = setup.server_entry(
        Path("/kit/venv/bin/python"), work_root=Path("/work"), server="http://p", token=token
    )
    return result


def test_두_클라이언트가_같은_모양을_받고_토큰이_없으면_자리표시(tmp_path: Path) -> None:
    entry = _entry()
    assert entry["command"] == "/kit/venv/bin/python"
    assert entry["args"][0].endswith("sp_mcp.py")
    assert entry["env"] == {
        "SP_WORK_ROOT": "/work",
        "SP_SERVER": "http://p",
        "SP_TOKEN": "<개인 토큰>",
    }
    # 화면에는 토큰을 가린다.
    assert setup._shown(_entry("spt_abcdefgh"))["env"]["SP_TOKEN"] == "spt_ab…"


def test_다른_MCP_항목은_두고_제_항목만_넣고_원본을_남긴다(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    original = {"mcpServers": {"other-server": {"url": "http://x"}}, "theme": "dark"}
    path.write_text(json.dumps(original), encoding="utf-8")

    assert "넣었습니다" in setup.merge(path, _entry("spt_1"))
    merged = json.loads(path.read_text(encoding="utf-8"))
    assert merged["theme"] == "dark"
    assert merged["mcpServers"]["other-server"] == {"url": "http://x"}
    assert merged["mcpServers"]["sp-pipeline"]["env"]["SP_TOKEN"] == "spt_1"
    assert json.loads((tmp_path / "claude_desktop_config.json.bak").read_text()) == original

    assert "바꿨습니다" in setup.merge(path, _entry("spt_2"))
    assert (
        json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["sp-pipeline"]["env"][
            "SP_TOKEN"
        ]
        == "spt_2"
    )


def test_읽을_수_없는_설정_파일은_덮지_않는다(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ 주석이 섞인 설정 // ", encoding="utf-8")
    with pytest.raises(setup.Stop, match="덮지 않고"):
        setup.merge(path, _entry())
    assert path.read_text(encoding="utf-8") == "{ 주석이 섞인 설정 // "

    fresh = tmp_path / "new" / "settings.json"
    setup.merge(fresh, _entry())
    assert "sp-pipeline" in json.loads(fresh.read_text(encoding="utf-8"))["mcpServers"]
