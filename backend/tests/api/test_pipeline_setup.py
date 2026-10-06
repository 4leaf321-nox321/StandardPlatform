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


def _entry() -> dict[str, Any]:
    result: dict[str, Any] = setup.server_entry(Path("/kit/venv/bin/python"))
    return result


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """이 PC 의 설정 파일 — 시험마다 따로. 진짜 사용자 설정을 안 건드린다."""
    path = tmp_path / "sp-pipeline" / "settings.json"
    monkeypatch.setenv("SP_SETTINGS", str(path))
    monkeypatch.delenv("SP_SERVER", raising=False)
    monkeypatch.delenv("SP_TOKEN", raising=False)
    return path


def test_클라이언트_설정에는_주소도_토큰도_안_넣는다() -> None:
    """**주소 · 토큰은 이 PC 의 설정 파일 한 곳**에 있다. 클라이언트(Claude Desktop) 설정의
    env 에 두면 플랫폼이 여럿일 때 한 벌밖에 못 담고, 사람이 명령 창에서 치는 적용은 그 env 를
    못 봐서 맨 끝 단계에서 멈췄다."""
    entry = _entry()
    assert entry["command"] == "/kit/venv/bin/python"
    assert entry["args"][0].endswith("sp_mcp.py")
    # UTF-8 모드만 — 한국어 Windows 에서 키트가 한글 파일을 cp949 로 읽지 않게.
    assert entry["env"] == {"PYTHONUTF8": "1"}


def test_다른_MCP_항목은_두고_제_항목만_넣고_원본을_남긴다(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    original = {"mcpServers": {"other-server": {"url": "http://x"}}, "theme": "dark"}
    path.write_text(json.dumps(original), encoding="utf-8")

    assert "넣었습니다" in setup.merge(path, _entry())
    merged = json.loads(path.read_text(encoding="utf-8"))
    assert merged["theme"] == "dark"
    assert merged["mcpServers"]["other-server"] == {"url": "http://x"}
    assert merged["mcpServers"]["sp-pipeline"] == _entry()
    assert json.loads((tmp_path / "claude_desktop_config.json.bak").read_text()) == original

    # 두 번째 플랫폼을 더해도 클라이언트 항목은 **하나**다 — 도구가 플랫폼 수만큼 불어나지
    # 않는다.
    assert "바꿨습니다" in setup.merge(path, _entry())
    assert list(json.loads(path.read_text(encoding="utf-8"))["mcpServers"]) == [
        "other-server",
        "sp-pipeline",
    ]


def test_읽을_수_없는_설정_파일은_덮지_않는다(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ 주석이 섞인 설정 // ", encoding="utf-8")
    with pytest.raises(setup.Stop, match="덮지 않고"):
        setup.merge(path, _entry())
    assert path.read_text(encoding="utf-8") == "{ 주석이 섞인 설정 // "

    fresh = tmp_path / "new" / "settings.json"
    setup.merge(fresh, _entry())
    assert "sp-pipeline" in json.loads(fresh.read_text(encoding="utf-8"))["mcpServers"]


# --- 한 PC 가 플랫폼 여럿을 겨눈다 -------------------------------------------------
#
# 허브 · 쌍둥이가 여럿이면 한 사람이 둘 이상에 넣는다. 플랫폼을 **이름으로 여럿** 등록하고,
# 작업 폴더가 자기 플랫폼을 기억하고, 적용은 미리 본 곳으로만 간다.


def test_플랫폼마다_설치하면_더해지고_앞의_것은_남는다(settings: Path) -> None:
    setup.register(
        work_root=Path("/work"),
        platform="rootdesign",
        server="http://10.0.0.5:3030/rootdesign/",
        token="spt_root",
    )
    # 이름을 안 주면 주소 끝에서 짓는다 — 그 설치의 slug 와 같아진다.
    setup.register(work_root=None, server="http://10.0.0.5:3040/qings", token="spt_qings")
    body = json.loads(settings.read_text(encoding="utf-8"))
    assert body["work_root"] == "/work"
    assert body["platforms"] == {
        "rootdesign": {"server": "http://10.0.0.5:3030/rootdesign", "token": "spt_root"},
        "qings": {"server": "http://10.0.0.5:3040/qings", "token": "spt_qings"},
    }
    if sys.platform != "win32":
        # 토큰이 평문이다 — 이 사용자만 읽는다.
        assert settings.stat().st_mode & 0o777 == 0o600

    # 서버를 옮겼을 때 — **`move` 를 줘야** 같은 이름의 주소를 바꾼다. 토큰을 안 주면 옛 토큰을
    # 둔다. 자리표시는 토큰으로 안 적는다.
    setup.register(
        work_root=None,
        platform="qings",
        server="http://10.0.0.9:3040/qings",
        token="<개인 토큰>",
        move=True,
    )
    qings = json.loads(settings.read_text(encoding="utf-8"))["platforms"]["qings"]
    assert qings == {"server": "http://10.0.0.9:3040/qings", "token": "spt_qings"}

    setup.register(work_root=None, forget="qings")
    assert list(json.loads(settings.read_text(encoding="utf-8"))["platforms"]) == [
        "rootdesign"
    ]
    with pytest.raises(setup.Stop, match="영소문자"):
        setup.register(work_root=None, platform="Root Design", server="http://x", token="t")


def test_같은_이름_다른_주소는_덮지_않고_따로_등록한다(settings: Path) -> None:
    """**개발판 · 운영판은 slug 가 같다.** 덮어쓰면 개발용 작업 폴더가 그 순간부터 운영으로
    미리 보기 · 적용을 보낸다. 주소를 붙인 이름으로 따로 등록하고 그렇게 했다고 말한다."""
    _, note = setup.register(
        work_root=Path("/w"),
        platform="rootdesign",
        server="http://localhost:8041",
        token="spt_dev",
    )
    assert note == ""
    _, note = setup.register(
        work_root=None,
        platform="rootdesign",
        server="http://10.0.0.5:8040/",
        token="spt_prod",
    )
    known = json.loads(settings.read_text(encoding="utf-8"))["platforms"]
    assert known["rootdesign"] == {"server": "http://localhost:8041", "token": "spt_dev"}
    assert known["rootdesign-10-0-0-5-8040"] == {
        "server": "http://10.0.0.5:8040",
        "token": "spt_prod",
    }
    assert "덮지 않고" in note and "rootdesign-10-0-0-5-8040" in note and "--move" in note

    # 운영을 다시 등록하면(토큰 갱신) **같은 따로 이름**으로 간다 — 셋째가 생기지 않는다.
    setup.register(
        work_root=None,
        platform="rootdesign",
        server="http://10.0.0.5:8040",
        token="spt_2",
    )
    known = json.loads(settings.read_text(encoding="utf-8"))["platforms"]
    assert sorted(known) == ["rootdesign", "rootdesign-10-0-0-5-8040"]
    assert known["rootdesign-10-0-0-5-8040"]["token"] == "spt_2"
    # 같은 이름 · 같은 주소는 토큰만 바꾼다.
    setup.register(
        work_root=None,
        platform="rootdesign",
        server="http://localhost:8041",
        token="spt_d2",
    )
    assert json.loads(settings.read_text(encoding="utf-8"))["platforms"]["rootdesign"] == {
        "server": "http://localhost:8041",
        "token": "spt_d2",
    }


def test_어느_플랫폼인지_짐작하지_않는다(settings: Path) -> None:
    """하나뿐이면 그것, 여럿인데 이름이 없으면 **멈춘다** — 엉뚱한 곳에 넣은 수만 줄은
    되돌리기 전까지 그곳의 데이터다."""
    pipeline = setup.pipeline
    with pytest.raises(pipeline.Stop, match="내 정보"):
        pipeline.target()

    setup.register(work_root=Path("/w"), platform="rootdesign", server="http://a", token="ta")
    assert pipeline.target() == pipeline.Target("rootdesign", "http://a", "ta")

    setup.register(work_root=None, platform="qings", server="http://b", token="tb")
    with pytest.raises(pipeline.Stop, match="여럿"):
        pipeline.target()
    assert pipeline.target("qings").server == "http://b"
    with pytest.raises(pipeline.Stop, match="등록되지 않은"):
        pipeline.target("nope")


def test_적용은_미리_본_플랫폼으로_간다(
    settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**명령 창에서 아무것도 안 주고** 적용해도 미리 본 곳으로 — 사람은 AI 가 알려 준 명령을
    그대로 붙인다."""
    pipeline = setup.pipeline
    setup.register(work_root=Path("/w"), platform="rootdesign", server="http://a", token="ta")
    setup.register(work_root=None, platform="qings", server="http://b", token="tb")
    run = tmp_path / "작업" / "runs" / "2026-10-07-시장"
    run.mkdir(parents=True)
    (run / pipeline.PREVIEW).write_text(
        json.dumps({"server": "http://b", "platform": "qings"}), encoding="utf-8"
    )
    seen: dict[str, Any] = {}

    def fake_apply(path: Path, *, server: str, token: str) -> tuple[bool, str]:
        seen.update(server=server, token=token)
        return True, "넣었다"

    monkeypatch.setattr(pipeline, "cmd_apply", fake_apply)
    assert pipeline.main(["apply", str(run)]) == 0
    assert seen == {"server": "http://b", "token": "tb"}


def test_작업_폴더가_넣을_곳을_기억하고_바꾸면_기록한다(
    settings: Path, tmp_path: Path
) -> None:
    spec = importlib.util.spec_from_file_location(
        "sp_work_under_test", SETUP.with_name("sp_work.py")
    )
    assert spec is not None and spec.loader is not None
    sp_work: Any = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = sp_work
    spec.loader.exec_module(sp_work)

    setup.register(work_root=Path("/w"), platform="rootdesign", server="http://a", token="ta")
    setup.register(work_root=None, platform="qings", server="http://b", token="tb")
    folder = tmp_path / "시장자료"
    with pytest.raises(sp_work.Stop, match="어느 플랫폼"):
        sp_work.init(folder, title="시장 서비스")
    assert not (folder / sp_work.WORK).exists()

    sp_work.init(folder, title="시장 서비스", platform="qings")
    assert sp_work.platform_of(folder) == "qings"
    # 실행 폴더는 두 단계 위의 작업 폴더에서 읽는다 — 미리 보기가 갈 곳.
    assert setup.pipeline.work_platform(folder / "runs" / "첫") == "qings"

    # **새 정의 없이 확정** — 이미 있는 타입에 자료만 넣는 작업(가장 흔하다). 이 길이
    # 없어서 상태가 끝까지 「정의 초안을 쓰라」 고 졸랐다(실측). 그 뒤에 정의가 생기면 다시
    # 확정받는다.
    (folder / sp_work.SOURCES / "표.csv").write_text("a\n1\n", encoding="utf-8")
    assert any("정의 초안" in one for one in sp_work.status(folder)["next"])
    sp_work.record(folder, topic="정의", decision="새 정의 없음", confirms_ontology=True)
    state = sp_work.status(folder)
    assert state["ontology"]["none"] and state["ontology"]["confirmed"]
    assert not any("정의" in one for one in state["next"]), state["next"]
    assert "새 정의 없음" in sp_work.render(state)
    (folder / sp_work.ONTOLOGY).write_text('{"types": []}', encoding="utf-8")
    assert sp_work.status(folder)["ontology"]["changed_after_confirm"] is True

    assert "다시" in sp_work.set_platform(folder, "rootdesign")
    assert sp_work.platform_of(folder) == "rootdesign"
    assert "qings → rootdesign" in (folder / sp_work.DECISIONS).read_text(encoding="utf-8")


# --- 더블클릭 설치 — 클립보드의 등록 정보 -------------------------------------------
#
# 받는 사람은 개발자가 아니다. 화면 「이 PC 에 등록」 이 토큰을 발급해 한 줄로 복사하고,
# `install.cmd`(더블클릭)가 그것을 읽는다 — 명령을 고쳐 칠 일도, 토큰이 대화에 나갈 일도 없다.


def test_등록_정보_한_줄을_찾고_모양이_틀리면_말한다() -> None:
    assert setup.parse_registration("아무 글이나") is None
    line = (
        'SP-PIPELINE-PLATFORM {"platform":"qings","server":"http://h/qings","token":"spt_1"}'
    )
    assert setup.parse_registration(f"앞 글 {line}\n다음 줄") == {
        "platform": "qings",
        "server": "http://h/qings",
        "token": "spt_1",
    }
    with pytest.raises(setup.Stop, match="다시 복사"):
        setup.parse_registration("SP-PIPELINE-PLATFORM")
    with pytest.raises(setup.Stop, match="이름 · 주소 · 토큰"):
        setup.parse_registration('SP-PIPELINE-PLATFORM {"platform":"q"}')


def test_더블클릭_설치가_클립보드에서_등록하고_비운다(
    settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cleared: list[bool] = []
    line = (
        'SP-PIPELINE-PLATFORM {"platform":"qings","server":"http://h/qings","token":"spt_q"}'
    )
    monkeypatch.setattr(setup, "read_clipboard", lambda: line)
    monkeypatch.setattr(setup, "clear_clipboard", lambda: cleared.append(True))
    # 처음 설치에는 작업 폴더를 물을 자리가 없다 — 정해진 자리에 둔다.
    monkeypatch.setattr(setup, "default_work_root", lambda: tmp_path / "온톨로지작업")
    assert setup.main(["--from-clipboard", "--no-install", "--venv", str(tmp_path / "v")]) == 0
    body = json.loads(settings.read_text(encoding="utf-8"))
    assert body["platforms"] == {"qings": {"server": "http://h/qings", "token": "spt_q"}}
    assert body["work_root"] == str(tmp_path / "온톨로지작업")
    assert cleared == [True]  # 토큰이 다음 붙여넣기에 딸려 나가지 않게

    # 클립보드에 아무것도 없으면 — 등록이 있으면 설치만 확인하고, 없으면 무엇을 누를지 말한다.
    monkeypatch.setattr(setup, "read_clipboard", lambda: "")
    assert setup.main(["--from-clipboard", "--no-install", "--venv", str(tmp_path / "v")]) == 0
    settings.unlink()
    assert setup.main(["--from-clipboard", "--no-install", "--venv", str(tmp_path / "v")]) == 2
