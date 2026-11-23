"""설치가 **됐다는 것은 서버가 떠서 도구를 내놓았다는 것** — 진짜 `mcp` 로 띄워 본다.

설치는 「넣었습니다」 라고 했는데 Claude Desktop 에 안 뜬 일이 있었다(실측). 설정 파일에 줄
하나 쓴 것으로 끝내지 않고, Claude 처럼 stdio 로 띄워 도구 목록을 받는다. 안 뜰 때 사람이
누르는 점검(`check.cmd` → `--check`)은 어디서 막혔는지 한 화면에 보인다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import sp_setup

PYTHON = Path(sys.executable)


def test_띄워_보면_도구_목록이_온다() -> None:
    tools = sp_setup.try_server(PYTHON)
    assert {"work_list", "run_preview", "run_undo"} <= set(tools)


def test_뜨다_죽으면_서버가_남긴_말을_보인다(tmp_path: Path) -> None:
    broken = tmp_path / "python"
    broken.write_text("#!/bin/sh\necho 'ModuleNotFoundError: mcp' >&2\nexit 3\n")
    broken.chmod(0o755)
    with pytest.raises(sp_setup.Stop, match="ModuleNotFoundError: mcp"):
        sp_setup.try_server(broken)


def test_점검은_앱이_읽는_파일마다_말한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Store 판은 설정 파일이 둘일 수 있다 — 한쪽에만 있으면 그쪽을 짚는다."""
    monkeypatch.setenv("SP_SETTINGS", str(tmp_path / "settings.json"))
    good = tmp_path / "roaming" / "claude_desktop_config.json"
    good.parent.mkdir()
    good.write_text(json.dumps({"mcpServers": {"sp-pipeline": sp_setup.server_entry(PYTHON)}}))
    private = tmp_path / "store" / "claude_desktop_config.json"
    private.parent.mkdir()
    private.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    monkeypatch.setattr(sp_setup, "claude_configs", lambda: [good, private])
    monkeypatch.setattr(sp_setup, "claude_logs", lambda: [])
    monkeypatch.setattr(sp_setup, "claude_desktop_running", lambda: False)
    # Claude Code — 켠 적이 없는 PC 면 막힌 곳이 아니다. 진짜 사용자 설정을 안 읽게 자리를
    # 옮긴다.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-code"))

    assert sp_setup.check(PYTHON) == 1
    shown = capsys.readouterr().out
    assert f"✓ {good}: 이 키트를 가리킵니다" in shown
    assert f"✗ {private}: sp-pipeline 항목이 없습니다" in shown
    assert "[Claude Code 설정" in shown and "Claude Code 를 켠 적이 없다" in shown
    assert "도구 19개" in shown
    assert "앱이 이 항목을 띄운 적이 없습니다" in shown


def test_점검은_Claude_Code_설정도_본다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SP_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    code = tmp_path / ".claude.json"
    code.write_text(json.dumps({"userID": "u"}))
    monkeypatch.setattr(sp_setup, "claude_configs", lambda: [])
    monkeypatch.setattr(sp_setup, "claude_logs", lambda: [])
    monkeypatch.setattr(sp_setup, "claude_desktop_running", lambda: False)
    assert sp_setup.check(PYTHON) == 1
    assert f"✗ {code}: sp-pipeline 항목이 없습니다" in capsys.readouterr().out
    sp_setup.write_claude_code(sp_setup.server_entry(PYTHON))
    assert sp_setup.check(PYTHON) == 0
    assert f"✓ {code}: 이 키트를 가리킵니다" in capsys.readouterr().out
