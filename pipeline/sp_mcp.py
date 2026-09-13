#!/usr/bin/env python3
"""로컬 정제 MCP 서버 — Claude Desktop · Gemini CLI 가 **사용자 PC 에서** 파이프라인을 돌린다.

Claude Desktop 은 PC 의 명령을 직접 못 돌리고, Gemini CLI 는 돌리지만 사람마다 부르는 법이
달라진다. 둘 다 말하는 MCP 로 도구를 한 벌 두면 **같은 절차 · 같은 멈춤 자리**로 일한다. 원천
파일은 PC 에서 읽히고, 사내망의 플랫폼에도 닿는다.

    <venv>/python sp_mcp.py          # stdio — 클라이언트가 띄운다

환경 변수(클라이언트 설정의 env):

    SP_WORK_ROOT   작업 폴더들을 두는 곳. 도구는 **이 안만** 읽고 쓴다
    SP_SERVER      플랫폼 주소(미리 보기 · 코어 대조)
    SP_TOKEN       개인 토큰(read · objects:write, 정의까지 넣으려면 ontology:write)

**적용(apply)은 도구로 두지 않는다.** AI 는 미리 보기 요약까지 보이고, 넣는 것은 사람이
명령으로 한다 — 「사람이 확인한 뒤에만 넣는다」 를 말이 아니라 도구의 모양으로 지킨다.

절차의 정본은 같은 폴더의 `AGENTS.md` 이고 `pipeline_guide` 가 그것을 그대로 내려준다. 여기에
절차를 옮겨 적지 않는다.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from pathlib import Path
from typing import Any

import sp_pipeline as pipeline
import sp_profile
import sp_table
import sp_work
from mcp.server.fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
AGENTS = HERE / "AGENTS.md"
MODELING = (HERE / "guide" / "GUIDE.md", HERE.parent / "mcp_server" / "guide" / "GUIDE.md")
"""모델링 규약 — 배포 묶음에서는 옆의 `guide/`, 저장소에서는 플랫폼 MCP 의 가이드."""
READ_LIMIT = 300_000
HEAD_LIMIT = 50
WRITABLE = (
    re.compile(r"^01-조사/[^/]+\.md$"),
    re.compile(r"^02-정의/[^/]+\.(json|md)$"),
    re.compile(r"^03-대응/[^/]+\.json$"),
    re.compile(r"^runs/[^/]+/(bundle|ontology|unresolved)\.json$"),
    re.compile(r"^runs/[^/]+/(objects|relations)/[^/]+\.json$"),
)
"""AI 가 쓸 수 있는 자리. 원천 · 결정기록 · work.json · 미리 보기 결과는 도구만 쓴다."""

Stop = pipeline.Stop

# **멈춤(Stop)은 그대로 올린다.** FastMCP 가 도구 오류(isError)로 바꿔 그 말을 AI 에게 준다.
# 오류를 dict 로 돌려주면 `-> str` 도구의 반환 검증에 걸려 말이 pydantic 오류에 묻힌다(실측).
mcp = FastMCP(
    "sp-pipeline",
    instructions=(
        "사용자 PC 의 작업 폴더에서 원천 데이터(표 · 문서)를 "
        "StandardPlatform 에 넣을 묶음으로 "
        "만든다. **처음에 `pipeline_guide()` 를, 작업마다 `work_status` 를 먼저 부르고 그 "
        "「다음」 을 따른다.** 정의 확정 · 미해결의 답 · 적용은 사람이 한다 — "
        "그 자리에서 멈추고 "
        "묻는다. 통계를 스스로 세지 말고 `source_profile` 결과를 쓴다."
    ),
)


def _root() -> Path:
    raw = os.environ.get("SP_WORK_ROOT", "").strip()
    if not raw:
        raise Stop(
            "SP_WORK_ROOT 가 설정돼 있지 않습니다 — "
            "MCP 설정의 env 에 작업 폴더들을 둘 곳을 적으세요"
        )
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise Stop(f"SP_WORK_ROOT 폴더가 없습니다: {root}")
    return root


def _work(work: str, *, exists: bool = True) -> Path:
    root = _root()
    folder = sp_work.inside(root, work)
    if folder == root:
        raise Stop("작업 폴더 이름을 주세요(SP_WORK_ROOT 아래의 폴더)")
    if exists:
        sp_work.load(folder)
    return folder


def _run(folder: Path, run: str) -> Path:
    path = sp_work.inside(folder, run)
    if path.parent != folder / sp_work.RUNS or not (path / pipeline.MANIFEST).exists():
        raise Stop(f"실행 폴더가 아닙니다: {run} — runs/<이름> 이어야 합니다")
    return path


def _source(folder: Path, source: str) -> Path:
    name = source.removeprefix(f"{sp_work.SOURCES}/")
    path = sp_work.inside(folder / sp_work.SOURCES, name)
    if not path.is_file():
        raise Stop(f"{sp_work.SOURCES}/ 에 그 원천이 없습니다: {source}")
    return path


def _sections(text: str) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.split("\n"):
        found = re.match(r"<!--@\s*(\w+)\s*-->", line.strip())
        if found:
            current = found.group(1)
            out[current] = []
        elif current:
            out[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in out.items()}


def _server() -> tuple[str, str]:
    server = os.environ.get("SP_SERVER", "").strip()
    token = os.environ.get("SP_TOKEN", "").strip()
    if not server or not token:
        raise Stop("플랫폼에 닿으려면 MCP 설정의 env 에 SP_SERVER · SP_TOKEN 이 있어야 합니다")
    return server, token


# --------------------------------------------------------------------------
# 안내
# --------------------------------------------------------------------------


@mcp.tool()
def pipeline_guide(topic: str = "") -> dict[str, Any]:
    """**작업을 시작하기 전에 먼저 부른다.** 절차 · 멈춤 자리 · 출력 형식의 정본(AGENTS.md).

    `topic` 없이 부르면 overview — 단계 · 도구 · 사람에게 멈추는 자리. 세부는 주제로:
      - `run` 실행 폴더의 모양(객체 행 · 관계 행 · 미해결)
      - `table` 대응 파일(sp-table/1)과 해석기 — 표를 옮기기 전에
      - `sources` 원천별(표 / 문서)로 다른 것
      - `modeling` 무엇을 타입 · 칸 · 관계로 만드나 — 정의 초안 전에"""
    try:
        sections = _sections(AGENTS.read_text(encoding="utf-8"))
    except OSError:
        raise Stop(f"안내를 읽을 수 없습니다: {AGENTS} — 설치가 온전한지 확인하세요") from None
    for path in MODELING:
        if path.is_file():
            modeling = _sections(path.read_text(encoding="utf-8")).get("modeling")
            if modeling:
                sections["modeling"] = modeling
                break
    key = topic.strip().lower() or "overview"
    if key not in sections:
        return {"error": f"그런 주제가 없습니다: {topic}", "topics": sorted(sections)}
    return {
        "topic": key,
        "content": sections[key],
        "topics": sorted(one for one in sections if one != key),
    }


# --------------------------------------------------------------------------
# 작업 폴더
# --------------------------------------------------------------------------


@mcp.tool()
def work_list() -> dict[str, Any]:
    """SP_WORK_ROOT 아래의 작업 폴더들."""
    root = _root()
    works = []
    for marker in sorted(root.glob(f"*/{sp_work.WORK}")) + sorted(
        root.glob(f"*/*/{sp_work.WORK}")
    ):
        folder = marker.parent
        try:
            title = sp_work.load(folder).get("title")
        except Stop:
            continue
        works.append({"work": folder.relative_to(root).as_posix(), "title": title})
    return {"root": str(root), "works": works}


@mcp.tool()
def work_init(work: str, title: str, group: str = "") -> str:
    """새 작업 폴더(원천 하나 또는 한 묶음의 원천). `work` 는 SP_WORK_ROOT 아래 이름,
    `group` 은 그룹 코드(그룹 전용 slug 의 앞부분)."""
    return sp_work.init(_work(work, exists=False), title=title, group=group)


@mcp.tool()
def work_status(work: str) -> str:
    """**작업마다 먼저 부른다.** 원천 · 정의 확정 여부 · 실행들의 상태와 **다음 할 일**."""
    return sp_work.render(sp_work.status(_work(work)))


@mcp.tool()
def work_read(work: str, path: str) -> str:
    """작업 폴더 안의 글 파일을 읽는다(조사 결과 · 정의 · 대응 · 실행 폴더 · 결정기록).
    원천(00-원천)은 여기서 읽지 않는다 — 표는 `source_profile` · `source_head` 로."""
    folder = _work(work)
    target = sp_work.inside(folder, path)
    relative = target.relative_to(folder).as_posix()
    if relative.split("/")[0] == sp_work.SOURCES:
        raise Stop("원천은 통째로 읽지 않습니다 — source_profile · source_head 를 쓰세요")
    if not target.is_file():
        raise Stop(f"파일이 없습니다: {relative}")
    if target.stat().st_size > READ_LIMIT:
        raise Stop(f"{relative}: {target.stat().st_size}바이트 — 너무 큽니다")
    return target.read_text(encoding="utf-8")


@mcp.tool()
def work_write(work: str, path: str, content: str) -> str:
    """정의 · 판단표 · 대응 파일 · 실행 폴더의 행 파일을 쓴다. 쓸 수 있는 자리:
    `01-조사/*.md` · `02-정의/*.json|md` · `03-대응/*.json` ·
    `runs/<실행>/bundle.json|ontology.json|unresolved.json` ·
    `runs/<실행>/objects|relations/*.json`.
    JSON 은 읽히는지 확인한다. 실행 폴더는 먼저 `run_init` · `table_convert` 로 만든다."""
    folder = _work(work)
    target = sp_work.inside(folder, path)
    relative = target.relative_to(folder).as_posix()
    if not any(pattern.match(relative) for pattern in WRITABLE):
        raise Stop(f"여기는 쓸 수 없습니다: {relative} — 쓸 수 있는 자리는 도구 설명에")
    if relative.startswith(f"{sp_work.RUNS}/"):
        run = folder / sp_work.RUNS / relative.split("/")[1]
        if not (run / pipeline.MANIFEST).exists():
            raise Stop(f"실행 폴더가 없습니다: {run.name} — 먼저 run_init")
    if target.suffix == ".json":
        try:
            json.loads(content)
        except ValueError as failure:
            raise Stop(f"{relative}: JSON 이 아닙니다 ({failure})") from None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    return f"썼습니다: {relative}"


@mcp.tool()
def decision_record(
    work: str,
    topic: str,
    decision: str,
    reason: str = "",
    decided_by: str = "",
    confirms_ontology: bool = False,
) -> str:
    """**사람이 정한 것**을 결정기록에 적는다 — 미해결의 답, 버린 열, 정의 확정.
    `confirms_ontology=true` 는 사람이 **지금의 02-정의/ontology.json** 을 확정했다고
    말했을 때만."""
    return sp_work.record(
        _work(work),
        topic=topic,
        decision=decision,
        reason=reason,
        decided_by=decided_by,
        confirms_ontology=confirms_ontology,
    )


# --------------------------------------------------------------------------
# 원천
# --------------------------------------------------------------------------


@mcp.tool()
def source_profile(
    work: str, source: str, show_values: bool = False, match: list[str] | None = None
) -> str:
    """원천 표(CSV) 조사를 **계산**한다 — 행 단위 · 몇 대 몇 · 갈리는 열 · 형식 · 이름의 조각 ·
    고를 값 · 날짜 · 코어 대조. 결과는 `01-조사/<이름>.profile.txt · .json` 에도 남는다.

    - `show_values=true` 면 고를 값 · 조각 값을 그대로 — 정의에 고를 값을 적을 때
    - `match=["열=<type_slug>"]` 플랫폼의 그 타입 식별자와 대조,
      `"열=@01-조사/keys.txt"` 는 파일과"""
    folder = _work(work)
    path = _source(folder, source)
    for one in match or []:
        target = one.partition("=")[2].strip()
        if target.startswith("@"):
            sp_work.inside(folder, target[1:])
    server = os.environ.get("SP_SERVER", "").strip()
    token = os.environ.get("SP_TOKEN", "").strip()
    previous = Path.cwd()
    try:
        # `@파일` 은 작업 폴더를 기준으로 푼다.
        os.chdir(folder)
        _, text = sp_profile.run(
            path,
            out_dir=folder / sp_work.SURVEY,
            show_values=show_values,
            matches=match or [],
            server=server,
            token=token,
        )
    finally:
        os.chdir(previous)
    return text


@mcp.tool()
def source_head(work: str, source: str, rows: int = 20) -> str:
    """원천 표의 머리글과 앞 몇 행(최대 50) — 규칙을 세울 때 실제 모양을 보려고."""
    path = _source(_work(work), source)
    table = sp_table.read_table(path)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["행", *table.header])
    for number, row in table.rows[: max(1, min(rows, HEAD_LIMIT))]:
        writer.writerow([number, *(row[name] for name in table.header)])
    return f"{table.name} · {len(table.rows)}행 · {table.encoding}\n{buffer.getvalue()}"


# --------------------------------------------------------------------------
# 실행
# --------------------------------------------------------------------------


@mcp.tool()
def table_convert(work: str, mapping: str, source: str, name: str = "") -> dict[str, Any]:
    """대응 파일(03-대응/*.table.json)대로 원천 표를 **새 실행 폴더**로 옮긴다. 보고서(가린
    패턴)와 미해결 여부를 돌려준다. 고쳐 다시 돌리면 또 새 실행 폴더가 생긴다."""
    folder = _work(work)
    mapping_path = sp_work.inside(folder, mapping)
    source_path = _source(folder, source)
    run = sp_work.new_run(folder, name or source_path.stem)
    ok, report = sp_table.convert(mapping_path, source_path, run)
    return {
        "run": run.relative_to(folder).as_posix(),
        "unresolved_empty": ok,
        "report": report,
    }


@mcp.tool()
def run_init(work: str, name: str, title: str = "") -> str:
    """빈 실행 폴더 — 문서에서 뽑은 행을 `work_write` 로 채울 때."""
    folder = _work(work)
    run = sp_work.new_run(folder, name)
    pipeline.cmd_init(run, title=title or name)
    return f"만들었습니다: {run.relative_to(folder).as_posix()}"


@mcp.tool()
def run_validate(work: str, run: str) -> dict[str, Any]:
    """보내기 전 모양 검사 — 식별자 겹침 · 관계 행의 칸 · 확신도 · 출처 · 미해결."""
    folder = _work(work)
    ok, report = pipeline.cmd_validate(_run(folder, run))
    return {"ok": ok, "report": report}


@mcp.tool()
def run_preview(work: str, run: str) -> dict[str, Any]:
    """플랫폼에 **아무것도 저장하지 않고** 미리 본다. 요약을 사람에게 보이고, 괜찮으면
    `apply_command` 를 **사람이 직접** 실행하게 안내한다(이 서버에는 적용 도구가 없다)."""
    folder = _work(work)
    path = _run(folder, run)
    server, token = _server()
    ok, summary = pipeline.cmd_preview(path, server=server, token=token)
    return {
        "ok": ok,
        "summary": summary,
        "apply_command": (
            f'python "{HERE / "sp_pipeline.py"}" apply "{path}"' if ok else None
        ),
        "note": "적용하려면 SP_SERVER · SP_TOKEN 이 설정된 창에서 "
        "사람이 apply_command 를 실행한다.",
    }


if __name__ == "__main__":
    mcp.run()
