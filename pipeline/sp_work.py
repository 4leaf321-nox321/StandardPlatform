#!/usr/bin/env python3
"""작업 폴더 — 원천 하나를 온톨로지로 옮기는 일의 **진행 상태를 파일로** 둔다.

    python sp_work.py init   <작업 폴더> --title "해석팀 의뢰 대장" --group cae
    python sp_work.py status <작업 폴더>

대화가 끊겨도, 사람이 바뀌어도, AI 가 바뀌어도(Claude Desktop ↔ Gemini CLI) **폴더를
보면 어디까지 했고 다음이 무엇인지** 안다. 결정은 `결정기록.md` 에 쌓여 같은 질문을 두 번
하지 않는다.

    <작업 폴더>/
      work.json      무엇 · 누구 · 정의를 확정한 지문
      00-원천/       원본(표 · 문서) — 도구가 고치지 않는다
      01-조사/       sp_profile 결과
      02-정의/       ontology.json · 판단표.md
      03-대응/       <원천 이름>.table.json
      runs/          실행 폴더들(sp_pipeline)
      docs/          Word 에서 꺼낸 글 초안 · 그림(sp_docx — 문서 속성에 넣기 전에)
      결정기록.md    누가 · 언제 · 무엇을 · 왜

표준 라이브러리만 쓴다. 작업 폴더는 **저장소 밖**에 둔다 — 사내 데이터가 git 에 섞이지 않게.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sp_pipeline as pipeline

FORMAT = "sp-work/1"
#: 「새 정의 없음」 을 확정했다는 표시 — 정의 파일의 지문 자리에 둔다.
NO_DEFINITION = "none"
WORK = "work.json"
SOURCES = "00-원천"
SURVEY = "01-조사"
DEFINITION = "02-정의"
MAPPING = "03-대응"
RUNS = "runs"
DOCS = "docs"
"""Word(.docx)에서 꺼낸 글 초안과 그림 — `docs/<이름>/` 의 `draft.md` · `media/` ·
`manifest.json`."""
DECISIONS = "결정기록.md"
ONTOLOGY = f"{DEFINITION}/ontology.json"
TABLES = {".csv"}
DOCUMENTS = {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".hwp", ".txt", ".md"}
SHEETS = {".xlsx", ".xls"}

Stop = pipeline.Stop


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def inside(folder: Path, relative: str) -> Path:
    """작업 폴더 안의 경로만 — `..` 나 절대 경로로 밖을 가리키면 멈춘다."""
    base = folder.resolve()
    target = (base / relative).resolve()
    if target != base and base not in target.parents:
        raise Stop(f"작업 폴더 밖의 경로입니다: {relative}")
    return target


def load(folder: Path) -> dict[str, Any]:
    try:
        body = json.loads((folder / WORK).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Stop(f"작업 폴더가 아닙니다(work.json 없음): {folder} — 먼저 init") from None
    except ValueError as failure:
        raise Stop(f"{folder / WORK} 를 읽을 수 없습니다 ({failure})") from failure
    if not isinstance(body, dict) or body.get("format") != FORMAT:
        raise Stop(f"{folder / WORK}: format 이 {FORMAT} 가 아닙니다")
    return body


def choose_platform(platform: str) -> str:
    """이 작업을 **어느 플랫폼에 넣나** — 등록한 것 중에서.

    이름이 없으면 하나뿐일 때만 그것을 쓰고, 여럿이면 **짐작하지 않고 묻는다**(엉뚱한 곳에
    넣은 수만 줄은 되돌리기 전까지 그곳의 데이터다). 하나도 없으면 빈 값 — 옛 설치(환경 변수)
    이거나 아직 안 붙였다. 그때는 미리 보기가 무엇이 없는지 말한다.
    """
    known = pipeline.platforms()
    if platform:
        if platform not in known:
            names = " · ".join(sorted(known)) or "없음"
            raise Stop(
                f"이 PC 에 등록되지 않은 플랫폼입니다: {platform} — 등록된 것: {names}. "
                "그 플랫폼 화면 「내 정보」 의 정제 도구 키트에 있는 설치 명령을 한 번 "
                "실행하면 더해집니다."
            )
        return platform
    if len(known) == 1:
        return next(iter(known))
    if len(known) > 1:
        raise Stop(
            f"이 작업을 어느 플랫폼에 넣나요 — {' · '.join(sorted(known))}. "
            "사람에게 묻고 platform 을 정해 다시 만드세요."
        )
    return ""


def platform_of(folder: Path) -> str:
    return str(load(folder).get("platform") or "")


def set_platform(folder: Path, platform: str) -> str:
    """작업 폴더의 플랫폼을 정한다(바꾼다) — **결정기록에도 남긴다.** 미리 본 실행은 옛 곳을
    기억하므로, 바꾼 뒤에는 미리 보기를 다시 해야 적용된다."""
    if not platform:
        raise Stop("플랫폼 이름을 주세요")
    chosen = choose_platform(platform)
    body = load(folder)
    before = str(body.get("platform") or "")
    body["platform"] = chosen
    pipeline._write(folder / WORK, body)
    record(
        folder,
        topic="넣을 플랫폼",
        decision=f"{before or '(정하지 않음)'} → {chosen}",
        reason="이 작업의 미리 보기 · 적용이 가는 곳",
    )
    return f"이 작업은 {chosen} 에 넣습니다" + (
        " — 이미 미리 본 실행은 미리 보기를 다시 해야 적용됩니다" if before else ""
    )


def init(folder: Path, *, title: str, group: str = "", platform: str = "") -> str:
    if (folder / WORK).exists():
        raise Stop(f"이미 작업 폴더입니다: {folder}")
    chosen = choose_platform(platform)
    for name in (SOURCES, SURVEY, DEFINITION, MAPPING, RUNS):
        (folder / name).mkdir(parents=True, exist_ok=True)
    pipeline._write(
        folder / WORK,
        {
            "format": FORMAT,
            "title": title,
            "group": group,
            # 미리 보기 · 적용이 가는 곳 — 한 PC 가 여러 플랫폼에 넣는다.
            "platform": chosen,
            "created_at": _now(),
            "confirmed": {},
        },
    )
    if not (folder / DECISIONS).exists():
        (folder / DECISIONS).write_text(
            f"# 결정기록 — {title}\n\n"
            "정의 확정 · 미해결에 대한 답 · 버린 열처럼 **사람이 정한 것**을 적는다. "
            "도구가 덧붙인다.\n",
            encoding="utf-8",
        )
    where = f" · 넣을 곳 {chosen}" if chosen else ""
    return f"작업 폴더를 만들었습니다: {folder}{where} — 원천을 {SOURCES}/ 에 넣으세요"


#: 실행 이름 앞의 날짜 — 이미 있으면 또 붙이지 않는다.
DATED = re.compile(r"^\d{4}-\d{2}-\d{2}-")


def new_run(folder: Path, name: str) -> Path:
    """`runs/<오늘>-<이름>` — 있으면 `-2`, `-3` … 지난 실행을 덮지 않는다. 이름이 이미
    날짜로 시작하면(`2026-11-25-부품`) 날짜를 또 붙이지 않고, 앞의 `runs/` 는 뗀다."""
    name = name.strip().removeprefix(f"{RUNS}/")
    clean = re.sub(r"[^\w가-힣.-]+", "-", name).strip("-.") or "run"
    stem = clean if DATED.match(clean) else f"{datetime.now().date().isoformat()}-{clean}"
    candidate = folder / RUNS / stem
    number = 2
    while candidate.exists():
        candidate = folder / RUNS / f"{stem}-{number}"
        number += 1
    return candidate


def find_run(folder: Path, name: str) -> Path:
    """실행 폴더 — `runs/<이름>` · `<이름>`(날짜까지) · 날짜를 뺀 이름(`부품` → 하나뿐인
    `runs/<날짜>-부품`). 날짜를 뺀 이름이 여럿과 맞으면 고르지 않고 후보를 말한다."""
    raw = name.strip().removeprefix(f"{RUNS}/").strip("/")
    if not raw:
        raise Stop("실행 이름을 주세요(runs/<이름>)")
    runs = folder / RUNS
    direct = inside(folder, f"{RUNS}/{raw}")
    if direct.parent == runs and (direct / pipeline.MANIFEST).exists():
        return direct

    def bare(one: Path) -> set[str]:
        # 날짜를 뗀 이름과 그 뒤의 `-2` 까지 뗀 이름.
        undated = DATED.sub("", one.name)
        return {undated, re.sub(r"-\d+$", "", undated)}

    matched = sorted(
        one
        for one in (runs.glob("*") if runs.is_dir() else [])
        if (one / pipeline.MANIFEST).exists() and raw in bare(one)
    )
    if len(matched) == 1:
        return matched[0]
    if matched:
        shown = ", ".join(f"{RUNS}/{one.name}" for one in matched[-5:])
        raise Stop(f"「{raw}」 과 맞는 실행이 여럿입니다 — 하나를 적으세요: {shown}")
    raise Stop(f"실행 폴더가 없습니다: {name} — runs/<이름> 또는 그 이름")


def record(
    folder: Path,
    *,
    topic: str,
    decision: str,
    reason: str = "",
    decided_by: str = "",
    confirms_ontology: bool = False,
) -> str:
    """결정 한 건을 덧붙인다. `confirms_ontology` 면 지금 정의의 지문을 확정으로 남긴다.

    **정의 파일이 없으면 「새 정의 없음」 을 확정한다** — 이미 있는 타입에 자료만 넣는 작업이다
    (가장 흔한 일이다). 이 길이 없어서 그런 작업에도 상태가 「정의 초안을 쓰라」 고 끝까지
    졸랐다(실측). 그 뒤에 정의 파일이 생기면 다시 확정받는다.
    """
    work = load(folder)
    if not topic.strip() or not decision.strip():
        raise Stop("무엇에 대한 결정인지(topic)와 결정(decision)이 있어야 합니다")
    lines = [f"\n## {_now()} — {topic.strip()}", "", f"- 결정: {decision.strip()}"]
    if reason.strip():
        lines.append(f"- 이유: {reason.strip()}")
    lines.append(f"- 정한 사람: {decided_by.strip() or '(적지 않음)'}")
    if confirms_ontology:
        digest = _sha(folder / ONTOLOGY)
        work.setdefault("confirmed", {})["ontology_sha256"] = digest or NO_DEFINITION
        work["confirmed"]["at"] = _now()
        work["confirmed"]["by"] = decided_by.strip()
        pipeline._write(folder / WORK, work)
        lines.append(
            f"- 정의 확정: {ONTOLOGY} sha256 {digest[:12]}"
            if digest
            else "- 정의 확정: 새 정의 없음 — 플랫폼에 있는 타입에만 넣는다"
        )
    with (folder / DECISIONS).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return f"{DECISIONS} 에 적었습니다: {topic.strip()}"


def _applied_on_screen(seen: dict[str, Any], job: str) -> bool:
    """화면에서 적용했는지 **미리 본 그 플랫폼에** 묻는다 — 못 닿거나 등록이 바뀌었으면 모른다.

    안 물으면 화면에서 적용한 뒤에도 상태가 「적용 전 — 적용하라」 고 조른다(실측).
    """
    try:
        chosen = pipeline.target(str(seen.get("platform") or ""))
    except Stop:
        return False
    if chosen.server.rstrip("/") != str(seen.get("server") or "").rstrip("/"):
        return False
    return bool(pipeline.plan_applied(chosen.server, chosen.token, job))


def _previews(run: Path) -> list[dict[str, Any]]:
    """이 실행의 미리 보기 잡 전부 — 앞선 것(`previews.json`)과 마지막 것(`preview.json`),
    새것부터."""
    out: list[dict[str, Any]] = []
    history = pipeline._read_json(run / pipeline.PREVIEWS, [])
    for one in history if isinstance(history, list) else []:
        if isinstance(one, dict) and one.get("job_id"):
            out.append(one)
    last = pipeline._read_json(run / pipeline.PREVIEW, [])
    if isinstance(last, dict) and (last.get("result") or {}).get("job_id"):
        out.append(
            {key: last.get(key) for key in ("at", "server", "platform", "digest")}
            | {"job_id": (last.get("result") or {}).get("job_id")}
        )
    return list(reversed(out))


def close(folder: Path, run: Path, *, reason: str) -> str:
    """쓰지 않을 실행을 닫는다 — 할 일 목록에서 빠지고 다시 미리 보지 않는다. 지우지 않는다
    (무엇을 했는지 남는다)."""
    if (run / pipeline.APPLIED).exists():
        raise Stop("이미 적용한 실행입니다 — 닫을 것이 없습니다(되돌리려면 run_undo)")
    if not reason.strip():
        raise Stop("왜 닫는지(reason)를 적으세요 — 뒤에 보는 사람이 압니다")
    pipeline._write(run / pipeline.CLOSED, {"at": _now(), "reason": reason.strip()})
    return f"닫았습니다: {run.relative_to(folder).as_posix()} — {reason.strip()}"


def _run_state(folder: Path, run: Path) -> dict[str, Any]:
    loaded = pipeline.load(run)
    body = pipeline.payload(loaded)
    state: dict[str, Any] = {
        "run": run.relative_to(folder).as_posix(),
        "sources": [str(one.get("name")) for one in loaded.manifest.get("sources") or []],
        "unresolved": len(loaded.unresolved),
        "objects": sum(len(batch.rows) for batch in loaded.objects),
        "preview": "없음",
        "applied": (run / pipeline.APPLIED).exists(),
        "closed": "",
        "screen": "",
        "digest": pipeline.digest(body),
        "same_as": "",
    }
    closed = pipeline._read_json(run / pipeline.CLOSED, [])
    if isinstance(closed, dict):
        state["closed"] = str(closed.get("reason") or "닫음")
    jobs = _previews(run)
    if not state["applied"] and not state["closed"]:
        # **미리 본 잡 전부에** 묻는다 — 같은 실행을 다시 미리 보면 마지막 잡만 남아, 앞
        # 잡으로 화면에서 이미 적용한 실행이 「적용 전」 으로 남고 다음 잡의 적용 링크가 또
        # 떴다(실측).
        for one in jobs:
            if one.get("server") and _applied_on_screen(one, str(one["job_id"])):
                state["applied"] = True
                # 다음부터 다시 묻지 않게 적어 둔다 — 화면에서 적용한 것.
                pipeline._write(
                    run / pipeline.APPLIED,
                    {
                        "at": _now(),
                        "server": one.get("server"),
                        "digest": one.get("digest"),
                        "job_id": one.get("job_id"),
                        "via": "screen",
                    },
                )
                break
    seen = pipeline._read_json(run / pipeline.PREVIEW, [])
    if isinstance(seen, dict):
        job = str((seen.get("result") or {}).get("job_id") or "")
        if job and seen.get("server") and not state["applied"] and not state["closed"]:
            # 그 플랫폼 화면에서 이 계획을 펼친 채로 — 적용은 거기서 사람이 누른다.
            state["screen"] = pipeline.jobs_link(str(seen["server"]), job)
        if seen.get("digest") != state["digest"]:
            state["preview"] = "미리 본 뒤 바뀜"
        elif (seen.get("result") or {}).get("ok"):
            state["preview"] = "괜찮음"
        else:
            state["preview"] = "오류 있음"
    return state


def status(folder: Path) -> dict[str, Any]:
    work = load(folder)
    sources = []
    for path in sorted((folder / SOURCES).glob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        kind = (
            "표"
            if suffix in TABLES
            else "엑셀"
            if suffix in SHEETS
            else "문서"
            if suffix in DOCUMENTS
            else "기타"
        )
        mapping = folder / MAPPING / f"{path.stem}.table.json"
        sources.append(
            {
                "name": path.name,
                "kind": kind,
                "profiled": (folder / SURVEY / f"{path.stem}.profile.txt").exists(),
                "mapping": mapping.relative_to(folder).as_posix()
                if mapping.exists()
                else None,
            }
        )
    current = _sha(folder / ONTOLOGY)
    confirmed = (work.get("confirmed") or {}).get("ontology_sha256")
    ontology = {
        "exists": current is not None,
        "judgement": (folder / DEFINITION / "판단표.md").exists(),
        # 새 정의 없이 확정한 작업 — 정의 파일이 없는 그대로여야 확정이다.
        "none": current is None and confirmed == NO_DEFINITION,
        "confirmed": bool(confirmed and confirmed == (current or NO_DEFINITION)),
        "changed_after_confirm": bool(confirmed and confirmed != (current or NO_DEFINITION)),
    }
    runs = [
        _run_state(folder, run)
        for run in sorted((folder / RUNS).glob("*"))
        if (run / pipeline.MANIFEST).exists()
    ]
    # **같은 묶음(지문)을 이미 적용한 실행이 있으면** 그 실행은 또 넣을 것이 아니다 — 같은
    # 원천을 다시 변환하면 실행 폴더는 새로 생기지만 묶음은 같다.
    applied_by = {one["digest"]: one["run"] for one in runs if one["applied"]}
    for one in runs:
        if not one["applied"] and one["digest"] in applied_by:
            one["same_as"] = applied_by[one["digest"]]
            one["screen"] = ""

    steps: list[str] = []
    if not sources:
        steps.append(f"원천 파일을 {SOURCES}/ 에 넣는다")
    for one in sources:
        if one["name"].lower().endswith(".xls"):
            steps.append(f"{one['name']}: 옛 엑셀(.xls)은 .xlsx 로 저장해 {SOURCES}/ 에 둔다")
        elif one["kind"] in {"표", "엑셀"} and not one["profiled"]:
            steps.append(
                f"{one['name']}: 조사 — source_profile"
                + ("(다른 시트는 sheet)" if one["kind"] == "엑셀" else "")
            )
    if any(one["kind"] in {"표", "엑셀", "문서"} for one in sources):
        if ontology["none"]:
            pass  # 새 정의 없음으로 확정 — 있는 타입에만 넣는다
        elif not ontology["exists"]:
            steps.append(
                f"정의 초안 — {ONTOLOGY} 와 {DEFINITION}/판단표.md 를 쓰고 사람에게 보인다"
            )
        elif ontology["changed_after_confirm"]:
            steps.append("정의가 확정 뒤 바뀌었다 — 바뀐 곳을 사람에게 보이고 다시 확정받는다")
        elif not ontology["confirmed"]:
            steps.append(
                "정의 확정 대기 — 온톨로지 담당이 판단표를 확인하면 "
                "decision_record(confirms_ontology=true)"
            )
    if ontology["confirmed"]:
        for one in sources:
            converted = [run for run in runs if one["name"] in run["sources"]]
            if one["kind"] in {"표", "엑셀"} and not one["mapping"]:
                steps.append(
                    f"{one['name']}: 대응 파일 — {MAPPING}/{Path(one['name']).stem}.table.json"
                )
            elif one["kind"] in {"표", "엑셀"} and not converted:
                steps.append(f"{one['name']}: 변환 — table_convert")
            elif one["kind"] == "문서" and not converted:
                steps.append(
                    f"{one['name']}: 추출 — run_init 뒤 objects/ · relations/ 를 쓴다"
                )
    for run in runs:
        if run["applied"] or run["closed"]:
            continue
        if run["same_as"]:
            steps.append(
                f"{run['run']}: {run['same_as']} 에서 이미 적용한 묶음과 같습니다(지문) — "
                "또 넣지 않는다. 쓰지 않으면 run_close"
            )
            continue
        if run["unresolved"]:
            steps.append(
                f"{run['run']}: 미해결 {run['unresolved']}건 — 사람에게 묻고 decision_record, "
                "정의 · 대응을 고쳐 **새 실행으로** 다시"
            )
        elif run["preview"] in {"없음", "미리 본 뒤 바뀜"}:
            steps.append(f"{run['run']}: 검증 · 미리 보기 — run_validate → run_preview")
        elif run["preview"] == "오류 있음":
            steps.append(f"{run['run']}: 미리 보기 오류를 고쳐 새 실행으로 다시")
        else:
            where = (
                f"그 플랫폼 화면 「작업」 에서 「적용」 — {run['screen']}"
                if run.get("screen")
                else "그 플랫폼 화면 「작업」 에서 「적용」"
            )
            steps.append(
                f"{run['run']}: 사람이 미리 보기를 확인하고 **직접** 적용 — {where} "
                "(명령 창이면 "
                + pipeline.command_line("apply", f'"{(folder / run["run"]).resolve()}"')
                + ")"
            )
    if not steps:
        steps.append("할 일이 없습니다 — 새 원천을 넣거나 새 실행을 만든다")
    return {
        "work": work.get("title"),
        "group": work.get("group"),
        "platform": work.get("platform") or "",
        "sources": sources,
        "ontology": ontology,
        "runs": runs,
        "next": steps,
    }


def render(state: dict[str, Any]) -> str:
    lines = [
        f"작업: {state['work']} (그룹 {state['group'] or '-'})",
        f"넣을 곳: {state.get('platform') or '정하지 않음'}",
        "",
        "원천:",
    ]
    for one in state["sources"]:
        marks = [one["kind"], "조사함" if one["profiled"] else "조사 전"]
        if one["mapping"]:
            marks.append(f"대응 {one['mapping']}")
        lines.append(f"- {one['name']} ({' · '.join(marks)})")
    if not state["sources"]:
        lines.append("- 없음")
    ontology = state["ontology"]
    word = (
        "새 정의 없음(있는 타입에만 넣는다 — 확정됨)"
        if ontology.get("none")
        else "없음 — 새 타입이 필요 없으면 판단표에 그렇게 적고 확정받는다"
        if not ontology["exists"]
        else "확정됨"
        if ontology["confirmed"]
        else "확정 뒤 바뀜"
        if ontology["changed_after_confirm"]
        else "확정 전"
    )
    lines += [
        "",
        f"정의: {word}" + ("" if ontology["judgement"] else " · 판단표 없음"),
        "",
        "실행:",
    ]
    for run in state["runs"]:
        where = (
            f"닫음({run['closed']})"
            if run.get("closed")
            else "적용함"
            if run["applied"]
            else f"같은 묶음을 {run['same_as']} 에서 적용함"
            if run.get("same_as")
            else "적용 전"
        )
        lines.append(
            f"- {run['run']}: 객체 {run['objects']} · 미해결 {run['unresolved']} · "
            f"미리 보기 {run['preview']} · {where}"
        )
    if not state["runs"]:
        lines.append("- 없음")
    lines += ["", "다음:"] + [
        f"{index}. {step}" for index, step in enumerate(state["next"], 1)
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sp_work", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    made = sub.add_parser("init", help="빈 작업 폴더를 만든다")
    made.add_argument("folder", type=Path)
    made.add_argument("--title", required=True)
    made.add_argument("--group", default="")
    made.add_argument("--platform", default="", help="등록한 플랫폼 이름(하나뿐이면 생략)")
    where = sub.add_parser("platform", help="이 작업을 넣을 플랫폼을 정한다(바꾼다)")
    where.add_argument("folder", type=Path)
    where.add_argument("name")
    look = sub.add_parser("status", help="어디까지 했고 다음이 무엇인가")
    look.add_argument("folder", type=Path)
    note = sub.add_parser("record", help="결정 한 건을 적는다")
    note.add_argument("folder", type=Path)
    note.add_argument("--topic", required=True)
    note.add_argument("--decision", required=True)
    note.add_argument("--reason", default="")
    note.add_argument("--by", default="")
    note.add_argument("--confirms-ontology", action="store_true")
    shut = sub.add_parser("close", help="쓰지 않을 실행을 닫는다(할 일에서 뺀다)")
    shut.add_argument("folder", type=Path)
    shut.add_argument("run", help="runs/<이름> 또는 그 이름")
    shut.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(
                init(args.folder, title=args.title, group=args.group, platform=args.platform)
            )
        elif args.command == "platform":
            print(set_platform(args.folder, args.name))
        elif args.command == "status":
            print(render(status(args.folder)))
        elif args.command == "close":
            print(close(args.folder, find_run(args.folder, args.run), reason=args.reason))
        else:
            print(
                record(
                    args.folder,
                    topic=args.topic,
                    decision=args.decision,
                    reason=args.reason,
                    decided_by=args.by,
                    confirms_ontology=args.confirms_ontology,
                )
            )
    except Stop as stop:
        print(str(stop), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
