#!/usr/bin/env python3
"""로컬 정제 MCP 설치 — Claude Desktop · Gemini CLI 설정을 만들고, **이 PC 가 넣을 플랫폼을
등록한다.** 설치가 됐다는 것은 MCP 서버를 실제로 띄워 도구 목록을 받았다는 뜻이다.

    python sp_setup.py --work-root "D:\\온톨로지작업" --platform rootdesign \\
                       --server http://<서버>:3030/rootdesign --token spt_... --write-claude
    python sp_setup.py --platform qings --server http://<서버>:3040/qings --token spt_... \\
                       --no-install                 # 두 번째 플랫폼 — 앞의 것은 남는다
    python sp_setup.py --forget qings               # 등록을 뺀다

- **플랫폼은 이름으로 여럿 둔다**(사용자 설정 폴더의 `sp-pipeline/settings.json`). 플랫폼마다
  한 번씩 돌리면 더해진다 — 화면 「내 정보」 의 설치 명령이 그 설치의 이름 · 주소 · 토큰을
  채워 준다. 이름을 안 주면 주소의 끝(`/rootdesign` → `rootdesign`)에서 짓는다.

- **Windows 키트에는 파이썬이 들어 있다**(`python/` — 부품을 깐 채로). 그것을 그대로 쓴다 —
  PC 의 파이썬도, venv 도, pip 도 안 쓴다. 전역 설치가 아니다(레지스트리 · PATH 를 안
  건드린다).
- 그 밖(macOS · 리눅스 · 저장소에서 바로)에서는 venv 를 만들어 깐다. 묶음에 `wheels/` 가 있으면
  **인터넷 없이** 그것으로(`--online` 이면 PyPI 에서).
- 안 뜰 때는 `--check`(키트의 `check.cmd`) — 앱이 읽는 설정 파일 · 서버가 뜨는지 · 앱의 로그.
- 설정은 기본으로 **보여 주기만** 한다. `--write-claude` · `--write-gemini` 를 주면 그 파일에
  `sp-pipeline` 항목만 넣거나 바꾸고, 원래 파일은 `.bak` 으로 남긴다. 다른 MCP 항목은 안
  건드린다.
- 토큰을 안 주면 `<개인 토큰>` 자리표시로 적는다 — 설정 파일에서 바꾼다.

표준 라이브러리만 쓴다(설치 전이라 아무것도 없다).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
# 같은 폴더의 `sp_pipeline` — 설치 전이라 패키지가 아니다. 설정을 읽고 쓰는 규칙은 거기 한 벌.
sys.path.insert(0, str(HERE))
import sp_pipeline as pipeline  # noqa: E402

NAME = "sp-pipeline"
TOKEN_PLACEHOLDER = "<개인 토큰>"
#: 화면 「내 정보」 의 「이 PC 에 등록」 이 클립보드에 넣는 줄의 머리 — 뒤에 JSON 한 덩이.
#:
#:     SP-PIPELINE-PLATFORM {"platform": "rootdesign", "server": "http://…", "token": "spt_…"}
#:
#: 사람이 명령을 고쳐 칠 일을 없앤다 — 더블클릭(`install.cmd`)이 이것을 읽어 등록한다. 토큰이
#: 대화(AI)에 나갈 일도 없다.
REGISTRATION = "SP-PIPELINE-PLATFORM"
#: 동봉한 휠이 맞는 파이썬 — `build_pipeline_kit.sh` 의 KIT_PYTHONS 와 같다(macOS · 리눅스).
PYTHONS = ((3, 11), (3, 12), (3, 13))
#: Windows 키트에 든 파이썬 — python.org 의 내장용(embeddable) 판에 부품을 미리 깐 것.
EMBEDDED = HERE / "python" / "python.exe"
CLAUDE_CONFIG = "claude_desktop_config.json"
#: Claude Desktop 의 실행 파일 자리(Store 판 · 설치 판). 같은 이름(claude.exe)인 Claude Code 는
#: 아니다 — 그것을 끄라고 하면 안 된다.
DESKTOP_MARKS = ("\\windowsapps\\claude_", "\\anthropicclaude\\", "\\programs\\claude\\")


class Stop(Exception):
    """사람에게 말하고 멈출 일."""


def venv_python(folder: Path) -> Path:
    if platform.system() == "Windows":
        return folder / "Scripts" / "python.exe"
    return folder / "bin" / "python"


def kit_python() -> Path | None:
    """키트에 든 파이썬 — 있으면 venv 도 pip 도 안 쓴다(Windows 만)."""
    return EMBEDDED if sys.platform == "win32" and EMBEDDED.is_file() else None


def create_venv(folder: Path) -> Path:
    # **여기서만 부른다** — 키트에 든 파이썬(내장용 판)에는 `venv` 가 없다. 맨 위에서 부르면
    # 그 파이썬으로는 설치가 첫 줄에서 죽는다(Windows 에서 실제로 그랬다).
    import venv

    python = venv_python(folder)
    if not python.exists():
        print(f"venv 를 만듭니다: {folder}")
        venv.EnvBuilder(with_pip=True).create(folder)
    return python


def install(python: Path, *, online: bool) -> None:
    command = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    wheels = HERE / "wheels"
    if wheels.is_dir() and not online:
        command += ["--no-index", "--find-links", str(wheels)]
        print(f"동봉한 휠로 설치합니다: {wheels}")
    else:
        print("PyPI(또는 사내 미러)에서 설치합니다")
    command += ["-r", str(HERE / "requirements.txt")]
    # **UTF-8 모드로.** 한국어 Windows 의 파이썬은 파일을 cp949 로 읽는다 — pip 가 한글 주석이
    # 든 requirements.txt 에서 `UnicodeDecodeError` 로 죽었다(실측: 그 PC 들에서는 설치가 한
    # 번도 안 됐다). 리눅스에서만 시험해서 몰랐다.
    utf8 = {**os.environ, "PYTHONUTF8": "1"}
    if subprocess.run(command, check=False, env=utf8).returncode != 0:
        raise Stop(
            "설치에 실패했습니다 — 휠이 이 PC 의 파이썬 버전 · OS 에 맞는지 보거나, "
            "인터넷이 되면 --online 으로 다시"
        )
    check = [str(python), "-c", "import mcp.server.fastmcp"]
    if subprocess.run(check, check=False, env=utf8).returncode != 0:
        raise Stop("설치는 끝났는데 mcp 를 불러오지 못합니다 — venv 를 지우고 다시 하세요")


def server_entry(python: Path) -> dict[str, Any]:
    """두 클라이언트가 같은 모양(`command` · `args` · `env`)을 받는다.

    **주소 · 토큰 · 작업 폴더를 env 에 넣지 않는다** — 이 PC 의 설정 파일 한 곳에 있다
    (`sp_pipeline.settings_path`). env 에 두면 플랫폼이 여럿일 때 한 벌밖에 못 담고, 사람이
    명령 창에서 치는 적용은 그 env 를 못 본다(맨 끝 단계에서 멈췄다).
    """
    # UTF-8 모드 — 한국어 Windows 에서 `open()` 의 기본이 cp949 라, 키트가 읽고 쓰는 한글
    # 파일이 깨지지 않게(설치에서 pip 가 그것으로 죽었다).
    return {
        "command": str(python),
        "args": [str(HERE / "sp_mcp.py")],
        "env": {"PYTHONUTF8": "1"},
    }


def parse_registration(text: str) -> dict[str, str] | None:
    """클립보드 글에서 등록 정보 한 줄을 찾는다 — 없으면 None. 모양이 틀리면 멈춘다."""
    at = text.find(REGISTRATION)
    if at < 0:
        return None
    rest = text[at + len(REGISTRATION) :].strip()
    raw = rest.splitlines()[0] if rest else ""
    try:
        body = json.loads(raw)
    except ValueError:
        raise Stop(
            "클립보드의 등록 정보를 읽을 수 없습니다 — 화면에서 다시 복사하세요"
        ) from None
    if not isinstance(body, dict) or not all(
        isinstance(body.get(key), str) and body.get(key)
        for key in ("platform", "server", "token")
    ):
        raise Stop(
            "클립보드의 등록 정보에 이름 · 주소 · 토큰이 다 있어야 합니다 — 다시 복사하세요"
        )
    return {key: str(body[key]) for key in ("platform", "server", "token")}


def read_clipboard() -> str:
    """이 PC 의 클립보드 글 — 못 읽으면 빈 값(그때는 등록 없이 설치만 한다)."""
    if sys.platform == "win32":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw",
        ]
    elif sys.platform == "darwin":
        command = ["pbpaste"]
    else:
        command = ["xclip", "-o", "-selection", "clipboard"]
    try:
        done = subprocess.run(command, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.decode("utf-8", errors="replace")


def clear_clipboard() -> None:
    """등록한 뒤 클립보드를 비운다 — **토큰이 다음 붙여넣기에 딸려 나가지 않게.**"""
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "type nul | clip"], capture_output=True, check=False)
    elif sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=b"", capture_output=True, check=False)


def default_work_root() -> Path:
    """처음 설치에서 작업 폴더 자리를 안 줬을 때 — 내 문서 옆(`~/온톨로지작업`)."""
    return Path.home() / "온톨로지작업"


def platform_name(server: str) -> str:
    """주소에서 짓는 이름 — 끝 경로(`/rootdesign`) 아니면 호스트. 그 설치의 slug 와 같게
    된다."""
    parts = urllib.parse.urlsplit(server)
    tail = [one for one in parts.path.split("/") if one]
    raw = (tail[-1] if tail else (parts.hostname or "")).lower()
    name = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-")[:40]
    if not pipeline.PLATFORM_RE.match(name):
        raise Stop(
            f"주소에서 플랫폼 이름을 짓지 못했습니다({server}) — --platform 으로 주세요"
        )
    return name


def _apart(name: str, server: str, known: dict[str, Any]) -> str:
    """같은 이름을 **다른 주소**가 쥐고 있을 때 쓸 이름 — 주소를 붙인다.

    같은 플랫폼의 개발판 · 운영판은 slug 가 같다. 덮어쓰면 앞의 것을 겨누던 작업 폴더가 그
    순간부터 **다른 서버로** 미리 보기 · 적용을 보낸다(개발용 작업이 운영에 들어간다).
    """
    parts = urllib.parse.urlsplit(server)
    tail = re.sub(r"[^a-z0-9]+", "-", f"{parts.hostname or ''}-{parts.port or ''}".lower())
    base = f"{name}-{tail.strip('-')}"[:40].rstrip("-")
    candidate, number = base, 2
    while True:
        held = known.get(candidate)
        if not isinstance(held, dict) or str(held.get("server") or "") == server:
            return candidate
        candidate = f"{base[:37].rstrip('-')}-{number}"
        number += 1


def register(
    *,
    work_root: Path | None,
    platform: str = "",
    server: str = "",
    token: str = "",
    hub: str = "",
    hub_token: str = "",
    forget: str = "",
    move: bool = False,
) -> tuple[dict[str, Any], str]:
    """이 PC 의 설정에 **더한다** — 다른 플랫폼은 그대로 둔다. `(설정, 사람에게 할 말)`.

    토큰 자리표시는 안 적는다(그것을 토큰으로 보내면 401 이 「토큰이 틀렸다」 로 읽힌다).
    같은 이름 · 같은 주소면 토큰만 바꾼다(토큰을 안 주면 옛 토큰을 둔다).

    ⚠️ **같은 이름 · 다른 주소는 덮지 않는다** — 개발판 · 운영판은 slug 가 같다. 덮으면 앞의
       것을 겨누던 작업 폴더가 말없이 다른 서버로 간다. 주소를 붙인 이름으로 **따로** 등록하고
       그렇게 했다고 말한다. 서버를 정말 옮긴 것이면 `move` 로(같은 이름의 주소를 바꾼다).
    """
    settings = pipeline.load_settings()
    if work_root is not None:
        settings["work_root"] = str(work_root)
    known = settings.get("platforms") if isinstance(settings.get("platforms"), dict) else {}
    if forget:
        if forget not in known:
            raise Stop(f"등록되지 않은 플랫폼입니다: {forget}")
        del known[forget]
    note = ""
    if server:
        server = server.rstrip("/")
        name = platform or platform_name(server)
        if not pipeline.PLATFORM_RE.match(name):
            raise Stop(f"플랫폼 이름은 영소문자 · 숫자 · _ · - 로 40자까지입니다: {name!r}")
        held = known.get(name) if isinstance(known.get(name), dict) else None
        if held and str(held.get("server") or "") != server and not move:
            apart = _apart(name, server, known)
            note = (
                f"이름 {name} 은 이미 다른 주소({held.get('server')})입니다 — 덮지 않고 "
                f"{apart} 로 따로 등록했습니다. 서버를 옮긴 것이면 "
                f"`--platform {name} --move` 로."
            )
            name = apart
        before = known.get(name) if isinstance(known.get(name), dict) else {}
        kept = token if token and token != TOKEN_PLACEHOLDER else before.get("token", "")
        known[name] = {"server": server, "token": kept}
    settings["platforms"] = known
    if hub:
        settings["hub"] = {"server": hub.rstrip("/"), "token": hub_token}
    pipeline.save_settings(settings)
    return settings, note


def describe(settings: dict[str, Any]) -> str:
    """등록한 것 — **토큰은 가린다**(화면 · 기록에 남는다)."""
    lines = [
        f"설정: {pipeline.settings_path()}",
        f"작업 폴더: {settings.get('work_root') or '-'}",
    ]
    known = settings.get("platforms") or {}
    if not known:
        lines.append("플랫폼: 없음")
    for name, one in sorted(known.items()):
        token = str(one.get("token") or "")
        shown = f"{token[:6]}…" if token else "토큰 없음 — 미리 보기 전에 채운다"
        lines.append(f"플랫폼 {name}: {one.get('server')} ({shown})")
    hub = settings.get("hub") or {}
    if hub.get("server"):
        lines.append(f"허브: {hub['server']}")
    return "\n".join(lines)


def _store_packages() -> list[Path]:
    """Microsoft Store(MSIX) 판 Claude 의 앱 전용 폴더들 — 없으면 빈 목록."""
    root = Path(os.environ.get("LOCALAPPDATA", "~")).expanduser() / "Packages"
    return sorted(root.glob("Claude_*")) if root.is_dir() else []


def claude_configs() -> list[Path]:
    """Claude Desktop 이 읽을 수 있는 설정 파일들 — **Windows 에서는 둘 이상일 수 있다.**

    Store(MSIX) 판은 `%APPDATA%` 를 앱 전용 자리(`%LOCALAPPDATA%\\Packages\\Claude_…\\
    LocalCache\\Roaming`)로 돌려 쓸 수 있다 — 거기 `Claude` 폴더가 있으면 앱은 그쪽을 읽는다.
    한 곳에만 쓰면 설치는 「넣었습니다」 인데 Claude 에는 안 뜬다. 있는 곳에 다 쓴다.

    실측: 사용자 PC 에서 설치는 「넣었습니다」 였는데 「Edit Config」 로 연 파일에 항목이
    없었다. 이것이 한 갈래, 켜진 앱이 파일을 다시 써서 지운 것이 다른 갈래다
    (`wait_for_desktop_quit`).
    """
    system = platform.system()
    if system == "Windows":
        roaming = Path(os.environ.get("APPDATA", "~")).expanduser()
        found = [roaming / "Claude" / CLAUDE_CONFIG]
        for package in _store_packages():
            private = package / "LocalCache" / "Roaming" / "Claude"
            if private.is_dir():
                found.append(private / CLAUDE_CONFIG)
        return found
    if system == "Darwin":
        return [Path("~/Library/Application Support/Claude").expanduser() / CLAUDE_CONFIG]
    return [Path("~/.config/Claude").expanduser() / CLAUDE_CONFIG]


def claude_logs() -> list[Path]:
    """Claude Desktop 이 이 서버에 대해 남긴 로그 — 없으면 앱이 이 항목을 띄운 적이 없다."""
    if platform.system() == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", "~")).expanduser()
        roaming = Path(os.environ.get("APPDATA", "~")).expanduser()
        folders = [local / "Claude" / "Logs", roaming / "Claude" / "logs"]
        for package in _store_packages():
            cache = package / "LocalCache"
            folders += [
                cache / "Local" / "Claude" / "Logs",
                cache / "Roaming" / "Claude" / "logs",
            ]
    elif platform.system() == "Darwin":
        folders = [Path("~/Library/Logs/Claude").expanduser()]
    else:
        folders = [Path("~/.config/Claude/logs").expanduser()]
    found = [one / f"mcp-server-{NAME}.log" for one in folders]
    return sorted(
        {one.resolve() for one in found if one.is_file()},
        key=lambda one: one.stat().st_mtime,
        reverse=True,
    )


def claude_desktop_running() -> bool:
    """Claude Desktop 이 켜져 있나(Windows 만 본다 — 다른 곳은 모른다고 거짓)."""
    if sys.platform != "win32":
        return False
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        "Get-Process -Name claude -ErrorAction SilentlyContinue | ForEach-Object { $_.Path }",
    ]
    try:
        done = subprocess.run(command, capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    paths = done.stdout.decode("utf-8", errors="replace").lower().splitlines()
    return any(mark in one for one in paths for mark in DESKTOP_MARKS)


def wait_for_desktop_quit() -> None:
    """Claude Desktop 이 켜져 있으면 **끄라고 하고 기다린다** — 설정을 쓰기 전에.

    앱은 설정을 켤 때 읽고, 켜져 있는 동안 **같은 파일을 스스로 다시 쓴다**(앱 로그의
    「Config file written」). 켜진 채로 넣으면 다시 켜기 전까지 안 뜨고, 그 사이 앱이 제가 읽어
    둔 것으로 파일을 쓰면 넣은 항목이 사라질 수 있다.
    """
    for _ in range(3):
        if not claude_desktop_running():
            return
        print(
            "\nClaude Desktop 이 켜져 있습니다 — 작업 표시줄 오른쪽(숨겨진 아이콘)의 "
            "Claude 를 오른쪽 클릭해 「종료(Quit)」 한 뒤 Enter 를 누르세요."
        )
        if not sys.stdin or not sys.stdin.isatty():
            break
        input()
    if claude_desktop_running():
        print(
            "⚠ Claude Desktop 이 아직 켜져 있습니다 — 끝나면 반드시 완전히 종료했다가 다시 "
            "켜세요. 그래도 안 보이면 check.cmd."
        )


def try_server(python: Path, *, timeout: float = 120.0) -> list[str]:
    """Claude Desktop 처럼 띄워 **도구 목록을 받아 본다** — 못 받으면 설치가 안 된 것이다.

    처음에는 부품을 읽어 들이느라 몇 초 걸린다. 서버가 남긴 오류를 그대로 보인다.
    """
    env = {**os.environ, "PYTHONUTF8": "1"}
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            [str(python), str(HERE / "sp_mcp.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
            env=env,
        )
        assert process.stdin is not None and process.stdout is not None
        stdin, stdout = process.stdin, process.stdout
        lines: queue.Queue[bytes] = queue.Queue()

        def pump() -> None:
            for line in iter(stdout.readline, b""):
                lines.put(line)
            lines.put(b"")

        threading.Thread(target=pump, daemon=True).start()

        def failed(why: str) -> Stop:
            # 죽은 서버는 오류를 다 쓰고 끝나게 잠깐 기다린다 — 끝까지 읽어야 이유가 보인다.
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=3)
            errors.seek(0)
            tail = errors.read().decode("utf-8", errors="replace").strip()[-1500:]
            return Stop(f"MCP 서버가 {why}({python})" + (f":\n{tail}" if tail else ""))

        def send(body: dict[str, Any]) -> None:
            stdin.write(json.dumps(body).encode("utf-8") + b"\n")
            stdin.flush()

        def answer(number: int) -> dict[str, Any]:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    line = lines.get(timeout=max(deadline - time.monotonic(), 0.01))
                except queue.Empty:
                    raise failed(f"{timeout:.0f}초 안에 답하지 않습니다") from None
                if not line:
                    raise failed("뜨다 멈췄습니다")
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict) and message.get("id") == number:
                    return message

        try:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "sp_setup", "version": "1"},
                    },
                }
            )
            answer(1)
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tools = answer(2).get("result", {}).get("tools", [])
            return [str(one.get("name")) for one in tools if isinstance(one, dict)]
        except OSError as caught:
            raise failed(f"입력을 받지 않습니다({caught})") from None
        finally:
            process.kill()
            process.wait(timeout=10)


def check(python: Path) -> int:
    """점검 — 안 뜰 때 **어디서 막혔는지 한 화면에.** 아무것도 안 바꾼다. 0 이면 이 PC 쪽은
    다 됐다(그래도 안 보이면 Claude Desktop 을 다시 켠다)."""
    problems = 0
    version = (
        (HERE / "VERSION").read_text(encoding="utf-8").strip()
        if (HERE / "VERSION").is_file()
        else "개발판"
    )
    which = "키트에 든 것" if python == EMBEDDED else "venv"
    print(f"[키트] {HERE} ({version}) · 파이썬: {python} ({which})")
    print("\n[등록]")
    print(describe(pipeline.load_settings()))

    print("\n[Claude Desktop 설정 — 앱이 읽을 수 있는 파일마다]")
    expected = server_entry(python)
    for path in claude_configs():
        if not path.is_file():
            print(f"  - {path}: 파일 없음")
            continue
        try:
            entry = (
                json.loads(path.read_text(encoding="utf-8") or "{}").get("mcpServers") or {}
            ).get(NAME)
        except (ValueError, AttributeError):
            print(f"  ✗ {path}: JSON 이 아닙니다")
            problems += 1
            continue
        if not entry:
            print(f"  ✗ {path}: {NAME} 항목이 없습니다 — install.cmd 를 다시")
            problems += 1
        elif (
            entry.get("command") != expected["command"]
            or entry.get("args") != expected["args"]
        ):
            print(f"  ✗ {path}: 다른 자리를 가리킵니다 — {entry.get('command')}")
            print("    (키트 폴더를 옮겼거나 지웠다) — 이 폴더의 install.cmd 를 다시")
            problems += 1
        else:
            print(f"  ✓ {path}: 이 키트를 가리킵니다")

    running = claude_desktop_running()
    print(f"\n[Claude Desktop] {'켜져 있음' if running else '꺼져 있음(또는 모름)'}")

    print("\n[MCP 서버 — Claude 처럼 띄워 봄]")
    try:
        tools = try_server(python)
        print(f"  ✓ 도구 {len(tools)}개 — {', '.join(tools[:4])} …")
    except Stop as stop:
        print(f"  ✗ {stop}")
        problems += 1

    print("\n[Claude Desktop 의 로그]")
    logs = claude_logs()
    if not logs:
        print(
            f"  mcp-server-{NAME}.log 가 없습니다 — 앱이 이 항목을 띄운 적이 없습니다. "
            "위의 설정이 ✓ 면 Claude Desktop 을 완전히 종료했다가 다시 켜세요."
        )
    for log in logs[:1]:
        print(f"  {log}")
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        for line in tail:
            print(f"    {line[:200]}")
    print(f"\n{'이 PC 쪽은 다 됐습니다.' if not problems else f'막힌 곳 {problems}군데.'}")
    return 0 if not problems else 1


def gemini_config() -> Path:
    return Path("~/.gemini/settings.json").expanduser()


def merge(path: Path, entry: dict[str, Any]) -> str:
    """설정 파일에 `sp-pipeline` 항목만 넣거나 바꾼다. 읽을 수 없는 파일은 **덮지 않는다.**"""
    body: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError as failure:
            raise Stop(f"{path} 가 JSON 이 아닙니다({failure}) — 덮지 않고 멈춥니다") from None
        if not isinstance(loaded, dict):
            raise Stop(f"{path} 의 맨 위가 {{...}} 가 아닙니다 — 덮지 않고 멈춥니다")
        body = loaded
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
    servers = body.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise Stop(f"{path} 의 mcpServers 가 {{...}} 가 아닙니다 — 덮지 않고 멈춥니다")
    replaced = NAME in servers
    servers[NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"{'바꿨습니다' if replaced else '넣었습니다'}: {path}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sp_setup", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--work-root",
        type=Path,
        default=None,
        help="작업 폴더들을 둘 곳 — 처음 한 번(다음부터는 기억한다)",
    )
    parser.add_argument(
        "--platform", default="", help="플랫폼 이름 — 그 설치의 slug(비우면 주소에서 짓는다)"
    )
    parser.add_argument(
        "--server", default=os.environ.get("SP_SERVER", ""), help="플랫폼 주소"
    )
    parser.add_argument("--token", default=os.environ.get("SP_TOKEN", ""), help="개인 토큰")
    parser.add_argument(
        "--hub-server",
        default=os.environ.get("SP_HUB_SERVER", ""),
        help="허브 주소(쌍둥이에 넣는 PC 만)",
    )
    parser.add_argument("--hub-token", default=os.environ.get("SP_HUB_TOKEN", ""))
    parser.add_argument("--forget", default="", help="이 이름의 플랫폼 등록을 뺀다")
    parser.add_argument(
        "--move",
        action="store_true",
        help="같은 이름의 주소를 바꾼다(서버를 옮겼을 때) — 없으면 다른 주소는 따로 등록한다",
    )
    parser.add_argument(
        "--from-clipboard",
        action="store_true",
        help="화면 「이 PC 에 등록」 이 복사한 등록 정보를 읽는다(install.cmd 가 쓴다)",
    )
    parser.add_argument("--venv", type=Path, default=HERE / "venv")
    parser.add_argument("--online", action="store_true", help="동봉 휠 대신 PyPI 에서")
    parser.add_argument("--no-install", action="store_true", help="설치는 건너뛰고 설정만")
    parser.add_argument("--write-claude", action="store_true")
    parser.add_argument("--write-gemini", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="점검 — 앱이 읽는 설정 · 서버가 뜨는지 · 앱의 로그(아무것도 안 바꾼다)",
    )
    args = parser.parse_args(argv)
    embedded = kit_python()
    try:
        if args.check:
            return check(embedded or venv_python(args.venv))
        if embedded is None and not args.online and sys.version_info[:2] not in PYTHONS:
            raise Stop(
                f"이 PC 의 파이썬은 {sys.version_info[0]}.{sys.version_info[1]} 입니다 — "
                "키트에 든 부품은 3.11 ~ 3.13 용입니다. 그 판의 파이썬으로 다시 실행하세요."
            )
        if args.from_clipboard and not args.server:
            found = parse_registration(read_clipboard())
            if found:
                args.platform, args.server, args.token = (
                    found["platform"],
                    found["server"],
                    found["token"],
                )
                clear_clipboard()
                shown = f"{found['platform']} ({found['server']})"
                print(f"클립보드의 등록 정보를 읽었습니다: {shown}")
            elif not pipeline.platforms():
                raise Stop(
                    "클립보드에 등록 정보가 없습니다 — 플랫폼 화면 「내 정보」 의 정제 도구 "
                    "키트에서 「이 PC 에 등록 정보 복사」 를 누른 뒤 다시 실행하세요."
                )
            else:
                print(
                    "클립보드에 등록 정보가 없습니다 — 등록은 그대로 두고 설치만 확인합니다."
                )
        work_root: Path | None = None
        if args.work_root is not None:
            work_root = args.work_root.expanduser().resolve()
        elif not pipeline.load_settings().get("work_root"):
            # 처음 — 더블클릭 설치에는 물을 자리가 없다. 정해진 자리에 두고 알린다.
            work_root = default_work_root()
        if work_root is not None:
            work_root.mkdir(parents=True, exist_ok=True)
        settings, note = register(
            work_root=work_root,
            platform=args.platform,
            server=args.server,
            token=args.token,
            hub=args.hub_server,
            hub_token=args.hub_token,
            forget=args.forget,
            move=args.move,
        )
        if note:
            print(note)
        print(describe(settings))
        if args.forget:
            return 0

        if embedded is not None:
            python = embedded
            print(f"\n키트에 든 파이썬을 씁니다: {python}")
        elif args.no_install:
            python = venv_python(args.venv)
        else:
            python = create_venv(args.venv)
            install(python, online=args.online)
        if not args.no_install:
            tools = try_server(python)
            print(f"MCP 서버를 띄워 봤습니다 — 도구 {len(tools)}개")
        entry = server_entry(python)
        print("\nMCP 항목(두 클라이언트 같음) — mcpServers 안에:")
        print(json.dumps({NAME: entry}, ensure_ascii=False, indent=2))
        if args.write_claude:
            wait_for_desktop_quit()
            for path in claude_configs():
                print(merge(path, entry))
        else:
            shown = " · ".join(str(one) for one in claude_configs())
            print(f"Claude Desktop: {shown} 에 넣으세요 (--write-claude 로 자동)")
        if args.write_gemini:
            print(merge(gemini_config(), entry))
        else:
            print(f"Gemini CLI: {gemini_config()} 에 넣으세요 (--write-gemini 로 자동)")
        print(
            "\n다른 플랫폼도 넣으려면 그 플랫폼 화면 「내 정보」 에서 「이 PC 에 등록 정보 "
            "복사」 를 누르고 install.cmd 를 다시 더블클릭 — 앞에 등록한 것은 남습니다."
        )
        print(
            "Claude Desktop 은 **켤 때** 새 MCP 를 읽습니다 — 켜져 있었다면 완전히 "
            "종료했다가 다시."
        )
    except Stop as stop:
        print(str(stop), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
