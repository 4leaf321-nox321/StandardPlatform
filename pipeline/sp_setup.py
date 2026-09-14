#!/usr/bin/env python3
"""로컬 정제 MCP 설치 — venv 를 만들고 `mcp` 를 깔고, Claude Desktop · Gemini CLI 설정을
만든다.

    python sp_setup.py --work-root "D:\\온톨로지작업" --server http://<플랫폼>:<포트>
    python sp_setup.py ... --token spt_... --write-claude --write-gemini

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
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
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


def server_entry(
    python: Path,
    *,
    work_root: Path,
    server: str,
    token: str,
    hub: str = "",
    hub_token: str = "",
) -> dict[str, Any]:
    """두 클라이언트가 같은 모양(`command` · `args` · `env`)을 받는다. 허브 주소는 쌍둥이에
    넣는 PC 만."""
    env = {
        "SP_WORK_ROOT": str(work_root),
        "SP_SERVER": server,
        "SP_TOKEN": token or TOKEN_PLACEHOLDER,
    }
    if hub:
        env["SP_HUB_SERVER"] = hub
        env["SP_HUB_TOKEN"] = hub_token or TOKEN_PLACEHOLDER
    return {"command": str(python), "args": [str(HERE / "sp_mcp.py")], "env": env}


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


def _shown(entry: dict[str, Any]) -> dict[str, Any]:
    """화면에는 토큰을 가려 보인다."""
    env = dict(entry["env"])
    for name in ("SP_TOKEN", "SP_HUB_TOKEN"):
        token = env.get(name)
        if token and token != TOKEN_PLACEHOLDER:
            env[name] = token[:6] + "…"
    return {**entry, "env": env}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sp_setup", description=__doc__.split("\n")[0])
    parser.add_argument("--work-root", type=Path, required=True, help="작업 폴더들을 둘 곳")
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
    parser.add_argument("--venv", type=Path, default=HERE / "venv")
    parser.add_argument("--online", action="store_true", help="동봉 휠 대신 PyPI 에서")
    parser.add_argument("--no-install", action="store_true", help="설치는 건너뛰고 설정만")
    parser.add_argument("--write-claude", action="store_true")
    parser.add_argument("--write-gemini", action="store_true")
    args = parser.parse_args(argv)
    try:
        work_root = args.work_root.expanduser().resolve()
        work_root.mkdir(parents=True, exist_ok=True)
        python = venv_python(args.venv) if args.no_install else create_venv(args.venv)
        if not args.no_install:
            install(python, online=args.online)
        entry = server_entry(
            python,
            work_root=work_root,
            server=args.server,
            token=args.token,
            hub=args.hub_server,
            hub_token=args.hub_token,
        )

        print("\n설정(두 클라이언트 같음) — mcpServers 안에:")
        print(json.dumps({NAME: _shown(entry)}, ensure_ascii=False, indent=2))
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
        if not args.server or not args.token:
            print(
                "\nSP_SERVER · SP_TOKEN 이 비어 있습니다 — "
                "미리 보기 · 코어 대조 전에 설정에서 채우세요"
            )
        print("\nClaude Desktop 은 **완전히 종료했다가** 다시 켜야 새 MCP 를 읽습니다.")
    except Stop as stop:
        print(str(stop), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
