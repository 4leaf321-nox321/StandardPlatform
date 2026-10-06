#!/usr/bin/env python3
"""로컬 정제 MCP 설치 — venv 를 만들고 `mcp` 를 깔고, Claude Desktop · Gemini CLI 설정을
만들고, **이 PC 가 넣을 플랫폼을 등록한다.**

    python sp_setup.py --work-root "D:\\온톨로지작업" --platform rootdesign \\
                       --server http://<서버>:3030/rootdesign --token spt_... --write-claude
    python sp_setup.py --platform qings --server http://<서버>:3040/qings --token spt_... \\
                       --no-install                 # 두 번째 플랫폼 — 앞의 것은 남는다
    python sp_setup.py --forget qings               # 등록을 뺀다

- **플랫폼은 이름으로 여럿 둔다**(사용자 설정 폴더의 `sp-pipeline/settings.json`). 플랫폼마다
  한 번씩 돌리면 더해진다 — 화면 「내 정보」 의 설치 명령이 그 설치의 이름 · 주소 · 토큰을
  채워 준다. 이름을 안 주면 주소의 끝(`/rootdesign` → `rootdesign`)에서 짓는다.

- 묶음에 `wheels/` 가 있으면 **인터넷 없이** 그것으로 깐다(`--online` 이면 PyPI 에서).
- 설정은 기본으로 **보여 주기만** 한다. `--write-claude` · `--write-gemini` 를 주면 그 파일에
  `sp-pipeline` 항목만 넣거나 바꾸고, 원래 파일은 `.bak` 으로 남긴다. 다른 MCP 항목은 안
  건드린다.
- 토큰을 안 주면 `<개인 토큰>` 자리표시로 적는다 — 설정 파일에서 바꾼다.

표준 라이브러리만 쓴다(설치 전이라 아무것도 없다).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.parse
import venv
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
# 같은 폴더의 `sp_pipeline` — 설치 전이라 패키지가 아니다. 설정을 읽고 쓰는 규칙은 거기 한 벌.
sys.path.insert(0, str(HERE))
import sp_pipeline as pipeline  # noqa: E402

NAME = "sp-pipeline"
TOKEN_PLACEHOLDER = "<개인 토큰>"


class Stop(Exception):
    """사람에게 말하고 멈출 일."""


def venv_python(folder: Path) -> Path:
    if platform.system() == "Windows":
        return folder / "Scripts" / "python.exe"
    return folder / "bin" / "python"


def create_venv(folder: Path) -> Path:
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
    if subprocess.run(command, check=False).returncode != 0:
        raise Stop(
            "설치에 실패했습니다 — 휠이 이 PC 의 파이썬 버전 · OS 에 맞는지 보거나, "
            "인터넷이 되면 --online 으로 다시"
        )
    check = [str(python), "-c", "import mcp.server.fastmcp"]
    if subprocess.run(check, check=False).returncode != 0:
        raise Stop("설치는 끝났는데 mcp 를 불러오지 못합니다 — venv 를 지우고 다시 하세요")


def server_entry(python: Path) -> dict[str, Any]:
    """두 클라이언트가 같은 모양(`command` · `args` · `env`)을 받는다.

    **주소 · 토큰 · 작업 폴더를 env 에 넣지 않는다** — 이 PC 의 설정 파일 한 곳에 있다
    (`sp_pipeline.settings_path`). env 에 두면 플랫폼이 여럿일 때 한 벌밖에 못 담고, 사람이
    명령 창에서 치는 적용은 그 env 를 못 본다(맨 끝 단계에서 멈췄다).
    """
    return {"command": str(python), "args": [str(HERE / "sp_mcp.py")], "env": {}}


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


def register(
    *,
    work_root: Path | None,
    platform: str = "",
    server: str = "",
    token: str = "",
    hub: str = "",
    hub_token: str = "",
    forget: str = "",
) -> dict[str, Any]:
    """이 PC 의 설정에 **더한다** — 다른 플랫폼은 그대로 둔다.

    토큰 자리표시는 안 적는다(그것을 토큰으로 보내면 401 이 「토큰이 틀렸다」 로 읽힌다).
    토큰을 안 주고 다시 돌리면 그 플랫폼의 옛 토큰을 그대로 둔다 — 주소만 고칠 때.
    """
    settings = pipeline.load_settings()
    if work_root is not None:
        settings["work_root"] = str(work_root)
    known = settings.get("platforms") if isinstance(settings.get("platforms"), dict) else {}
    if forget:
        if forget not in known:
            raise Stop(f"등록되지 않은 플랫폼입니다: {forget}")
        del known[forget]
    if server:
        name = platform or platform_name(server)
        if not pipeline.PLATFORM_RE.match(name):
            raise Stop(f"플랫폼 이름은 영소문자 · 숫자 · _ · - 로 40자까지입니다: {name!r}")
        before = known.get(name) if isinstance(known.get(name), dict) else {}
        kept = token if token and token != TOKEN_PLACEHOLDER else before.get("token", "")
        known[name] = {"server": server.rstrip("/"), "token": kept}
    settings["platforms"] = known
    if hub:
        settings["hub"] = {"server": hub.rstrip("/"), "token": hub_token}
    pipeline.save_settings(settings)
    return settings


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


def claude_config() -> Path:
    system = platform.system()
    if system == "Windows":
        return (
            Path(os.environ.get("APPDATA", "~")).expanduser()
            / "Claude"
            / "claude_desktop_config.json"
        )
    if system == "Darwin":
        return Path(
            "~/Library/Application Support/Claude/claude_desktop_config.json"
        ).expanduser()
    return Path("~/.config/Claude/claude_desktop_config.json").expanduser()


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
    parser.add_argument("--venv", type=Path, default=HERE / "venv")
    parser.add_argument("--online", action="store_true", help="동봉 휠 대신 PyPI 에서")
    parser.add_argument("--no-install", action="store_true", help="설치는 건너뛰고 설정만")
    parser.add_argument("--write-claude", action="store_true")
    parser.add_argument("--write-gemini", action="store_true")
    args = parser.parse_args(argv)
    try:
        work_root: Path | None = None
        if args.work_root is not None:
            work_root = args.work_root.expanduser().resolve()
        elif not pipeline.load_settings().get("work_root"):
            raise Stop("처음에는 --work-root 로 작업 폴더들을 둘 곳을 정합니다")
        if work_root is not None:
            work_root.mkdir(parents=True, exist_ok=True)
        settings = register(
            work_root=work_root,
            platform=args.platform,
            server=args.server,
            token=args.token,
            hub=args.hub_server,
            hub_token=args.hub_token,
            forget=args.forget,
        )
        print(describe(settings))
        if args.forget:
            return 0

        python = venv_python(args.venv) if args.no_install else create_venv(args.venv)
        if not args.no_install:
            install(python, online=args.online)
        entry = server_entry(python)
        print("\nMCP 항목(두 클라이언트 같음) — mcpServers 안에:")
        print(json.dumps({NAME: entry}, ensure_ascii=False, indent=2))
        for wanted, path, label in (
            (args.write_claude, claude_config(), "Claude Desktop"),
            (args.write_gemini, gemini_config(), "Gemini CLI"),
        ):
            if wanted:
                print(merge(path, entry))
            else:
                print(
                    f"{label}: {path} 에 넣으세요 (--write-{label.split()[0].lower()} 로 자동)"
                )
        print(
            "\n다른 플랫폼도 넣으려면 그 플랫폼 화면 「내 정보」 의 설치 명령을 이 PC 에서 "
            "한 번 더 — 앞에 등록한 것은 남습니다."
        )
        print("Claude Desktop 은 **완전히 종료했다가** 다시 켜야 새 MCP 를 읽습니다.")
    except Stop as stop:
        print(str(stop), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
