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
        "묻는다. 통계를 스스로 세지 말고 `source_profile` 결과를 쓴다. "
        "**이 PC 는 플랫폼 여럿에 넣을 수 있다** — 작업 폴더가 넣을 곳을 기억하고"
        "(`work_list` 에 보인다), 여럿인데 정해지지 않았으면 짐작하지 말고 사람에게 묻는다."
    ),
)


def _root() -> Path:
    raw = pipeline.setting("SP_WORK_ROOT")
    if not raw:
        raise Stop(
            "작업 폴더들을 둘 곳(SP_WORK_ROOT)이 정해지지 않았습니다 — 플랫폼 화면 "
            "「내 정보」 의 정제 도구 키트에 있는 설치 명령(`sp_setup.py --work-root …`)을 "
            "실행하세요"
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


def _target(folder: Path | None = None, platform: str = "") -> pipeline.Target:
    """넣을 곳 — 이름을 주면 그것, 아니면 **작업 폴더가 기억하는 것**, 그것도 없으면 하나뿐인
    등록 플랫폼. 여럿인데 정해지지 않았으면 `pipeline.target` 이 묻는다."""
    return pipeline.target(platform or (sp_work.platform_of(folder) if folder else ""))


def _maybe(folder: Path) -> tuple[str, str]:
    """**물어보기만 하는** 자리(조사 · 변환 · 검증)의 주소 — 정해지지 않았으면 빈 값
    (모양만 본다)."""
    try:
        chosen = _target(folder)
    except Stop:
        return "", ""
    return chosen.server, chosen.token


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
    """작업 폴더들과 **각자 넣을 플랫폼**, 그리고 이 PC 에 등록한 플랫폼들(토큰은 안
    보인다)."""
    root = _root()
    works = []
    for marker in sorted(root.glob(f"*/{sp_work.WORK}")) + sorted(
        root.glob(f"*/*/{sp_work.WORK}")
    ):
        folder = marker.parent
        try:
            body = sp_work.load(folder)
        except Stop:
            continue
        works.append(
            {
                "work": folder.relative_to(root).as_posix(),
                "title": body.get("title"),
                "platform": body.get("platform") or "",
            }
        )
    platforms = {name: one["server"] for name, one in pipeline.platforms().items()}
    return {"root": str(root), "platforms": platforms, "works": works}


@mcp.tool()
def work_init(work: str, title: str, group: str = "", platform: str = "") -> str:
    """새 작업 폴더(원천 하나 또는 한 묶음의 원천). `work` 는 SP_WORK_ROOT 아래 이름,
    `group` 은 그룹 코드(그룹 전용 slug 의 앞부분).

    `platform` 은 **이 작업을 넣을 플랫폼**(`work_list` 의 `platforms` 중 하나). 하나뿐이면
    생략해도 그것이 되고, 여럿인데 생략하면 멈춘다 — 사람에게 어디에 넣을지 묻고 다시 부른다.
    """
    return sp_work.init(_work(work, exists=False), title=title, group=group, platform=platform)


@mcp.tool()
def work_platform(work: str, platform: str) -> str:
    """이 작업을 넣을 플랫폼을 정한다(바꾼다). **사람이 정한 것만** — 결정기록에 남는다.
    이미 미리 본 실행은 옛 곳을 기억하므로 미리 보기를 다시 해야 적용된다."""
    return sp_work.set_platform(_work(work), platform)


@mcp.tool()
def platform_schema(work: str, types: list[str] | None = None) -> dict[str, Any]:
    """그 작업이 넣을 플랫폼의 **지금 정의** — 정의 초안을 잡기 전에 읽는다(무엇이 이미
    있나 · 어떤 이름 · 어떤 속성). 서버 MCP 를 따로 붙이지 않아도 된다 — 키트가 등록된 토큰으로
    읽는다.

    `types` 를 주면 그 타입들과 그 타입에 닿는 관계 종류만(타입이 많으면 전부는 길다). 처음에는
    없이 불러 `types` 의 slug · label 을 훑고, 필요한 것만 다시 부른다.
    """
    chosen = _target(_work(work))
    return {
        "platform": chosen.shown,
        **pipeline.platform_schema(chosen.server, chosen.token, types),
    }


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
    server, token = _maybe(folder)
    previous = Path.cwd()
    try:
        # `@파일` 은 작업 폴더를 기준으로 푼다.
        os.chdir(folder)
        result, text = sp_profile.run(
            path,
            out_dir=folder / sp_work.SURVEY,
            show_values=show_values,
            matches=match or [],
            server=server,
            token=token,
        )
    finally:
        os.chdir(previous)
    return text + "\n\n== 말로 전할 요약 ==\n" + sp_profile.brief(result)


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
    server, token = _maybe(folder)
    ok, report = sp_table.convert(mapping_path, source_path, run, server=server, token=token)
    return {
        "run": run.relative_to(folder).as_posix(),
        "unresolved_empty": ok,
        "report": report,
        "brief": sp_table.brief(report),
    }


@mcp.tool()
def run_init(work: str, name: str, title: str = "", backfill: bool = False) -> str:
    """빈 실행 폴더 — 문서에서 뽑은 행을 `work_write` 로 채울 때.

    `backfill` 은 **수만 줄을 한 번에 넣을 때** 켠다 — 미리 보기는 계획만 보고(적용을 두 번
    돌지 않는다), 웹훅 · 감사는 묶음 한 건으로, 못 찾은 참조는 그 칸만 비운다. 평소 적재에는
    켜지 않는다(기본값이 더 엄하다: 전부 아니면 무 · 줄마다 기록).
    """
    folder = _work(work)
    run = sp_work.new_run(folder, name)
    pipeline.cmd_init(run, title=title or name, backfill=backfill)
    return f"만들었습니다: {run.relative_to(folder).as_posix()}"


@mcp.tool()
def hub_pull(work: str, group: str | list[str], name: str = "") -> str:
    """허브가 내보낸 사이드바 묶음(PLM 기준정보면 `plm`)을 **새 실행 폴더로** 받는다 — 이 PC 가
    쌍둥이 플랫폼에 넣을 때. 그 뒤는 `run_validate` → `run_preview` → 사람이 적용. 받은 타입은
    받는 플랫폼에서 허브 관리가 되어 거기서는 못 고친다. env 에 SP_HUB_SERVER · SP_HUB_TOKEN 이
    있어야 한다.

    **묶음을 여럿 적어도 된다**(`["plm", "core"]`) — 코어를 축별로 나눠 둔 허브에서 하나씩
    받으면 축끼리 가리키는 참조 때문에 어느 쪽도 못 받는다."""
    folder = _work(work)
    hub = pipeline.setting("SP_HUB_SERVER")
    token = pipeline.setting("SP_HUB_TOKEN")
    if not hub or not token:
        raise Stop(
            "허브에서 받으려면 MCP 설정의 env 에 SP_HUB_SERVER · SP_HUB_TOKEN 이 있어야 합니다"
        )
    wanted = [group] if isinstance(group, str) else list(group)
    run = sp_work.new_run(folder, name or f"허브-{'-'.join(wanted)}")
    text = pipeline.cmd_pull(run, hub=hub, hub_token=token, group=wanted)
    return f"{text}\n실행: {run.relative_to(folder).as_posix()}"


@mcp.tool()
def run_validate(work: str, run: str, ask_platform: bool = True) -> dict[str, Any]:
    """보내기 전 검사 — 식별자 겹침 · 관계 행의 칸 · 확신도 · 출처 · 미해결.

    `ask_platform` 이면(기본) **끝점이 풀리는지도 플랫폼에 묻는다** — 가리키는 것이 없거나
    이름이 여럿과 맞는 것을 여기서 잡는다. 예전에는 미리 보기에서야 알았고, 미리 보기는
    계획을 세우느라 몇 분이 걸린다. `SP_SERVER` · `SP_TOKEN` 이 없으면 모양만 본다.
    """
    folder = _work(work)
    server, token = _maybe(folder) if ask_platform else ("", "")
    ok, report = pipeline.cmd_validate(_run(folder, run), server=server, token=token)
    return {"ok": ok, "report": report}


@mcp.tool()
def run_preview(work: str, run: str) -> dict[str, Any]:
    """플랫폼에 **아무것도 저장하지 않고** 미리 본다. 요약을 사람에게 보이고, 괜찮으면 **사람이
    직접** 적용하게 안내한다 — 이 도구들에는 적용이 없다.

    적용하는 길은 **`apply_on_screen` 이 먼저다**: 그 플랫폼 화면 「작업」 에 이 계획이 펼쳐진
    채로 열리고, 사람이 「적용」 을 누른다(명령 창이 필요 없다 · 그 플랫폼의 화면이니 엉뚱한
    곳에 안 들어간다). 명령 창이 편한 사람에게는 `apply_command`.
    """
    folder = _work(work)
    path = _run(folder, run)
    chosen = _target(folder)
    ok, summary = pipeline.cmd_preview(
        path, server=chosen.server, token=chosen.token, platform=chosen.name
    )
    seen = pipeline._read_json(path / pipeline.PREVIEW, [])
    job = (
        str(((seen or {}).get("result") or {}).get("job_id") or "")
        if isinstance(seen, dict)
        else ""
    )
    return {
        "ok": ok,
        # **어디에 미리 봤는지를 먼저** — 사람은 숫자보다 그것을 먼저 확인해야 한다.
        "platform": chosen.shown,
        "summary": summary,
        "apply_on_screen": pipeline.jobs_link(chosen.server, job) if ok and job else None,
        "apply_command": (pipeline.command_line("apply", f'"{path}"') if ok else None),
        "note": "사람이 apply_on_screen 을 열어 「적용」 을 누른다(계획을 본 사람 — 이 토큰의 "
        "주인 — 으로 로그인해 있어야 한다). 명령 창이면 apply_command 를 아무 창에나.",
    }


@mcp.tool()
def runs_list(limit: int = 20, platform: str = "") -> dict[str, Any]:
    """**넣은 판들** — 되돌릴 번호를 여기서 찾는다(적용한 것만, 최근 것부터).

    `platform` 은 등록한 플랫폼 이름 — 하나뿐이면 생략한다."""
    chosen = _target(platform=platform)
    ok, text = pipeline.cmd_runs(server=chosen.server, token=chosen.token, limit=limit)
    return {"ok": ok, "platform": chosen.shown, "runs": text}


@mcp.tool()
def run_undo(run_id: str, platform: str = "") -> dict[str, Any]:
    """넣은 판 하나를 되돌리면 **무엇이 되돌아가나** — 계획만. 아무것도 안 바뀐다.

    요약을 사람에게 보이고, 되돌릴지는 사람이 `undo_command` 를 실행해 정한다(이 서버에는
    되돌리는 도구가 없다 — 적용과 같은 규칙이다). 건너뛰는 줄의 이유도 함께 보인다.
    """
    chosen = _target(platform=platform)
    ok, summary = pipeline.cmd_undo(run_id, server=chosen.server, token=chosen.token)
    where = f" --platform {chosen.name}" if chosen.name else ""
    return {
        "ok": ok,
        "platform": chosen.shown,
        "summary": summary,
        "undo_command": (
            pipeline.command_line("undo", f"{run_id}{where}", "--apply") if ok else None
        ),
        "note": "사람이 아무 명령 창에서 undo_command 를 실행한다.",
    }


if __name__ == "__main__":
    mcp.run()
