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
WORK = "work.json"
SOURCES = "00-원천"
SURVEY = "01-조사"
DEFINITION = "02-정의"
MAPPING = "03-대응"
RUNS = "runs"
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


def init(folder: Path, *, title: str, group: str = "") -> str:
    if (folder / WORK).exists():
        raise Stop(f"이미 작업 폴더입니다: {folder}")
    for name in (SOURCES, SURVEY, DEFINITION, MAPPING, RUNS):
        (folder / name).mkdir(parents=True, exist_ok=True)
    pipeline._write(
        folder / WORK,
        {
            "format": FORMAT,
            "title": title,
            "group": group,
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
    return f"작업 폴더를 만들었습니다: {folder} — 원천을 {SOURCES}/ 에 넣으세요"


def new_run(folder: Path, name: str) -> Path:
    """`runs/<오늘>-<이름>` — 있으면 `-2`, `-3` … 지난 실행을 덮지 않는다."""
    clean = re.sub(r"[^\w가-힣.-]+", "-", name).strip("-.") or "run"
    stem = f"{datetime.now().date().isoformat()}-{clean}"
    candidate = folder / RUNS / stem
    number = 2
    while candidate.exists():
        candidate = folder / RUNS / f"{stem}-{number}"
        number += 1
    return candidate


def record(
    folder: Path,
    *,
    topic: str,
    decision: str,
    reason: str = "",
    decided_by: str = "",
    confirms_ontology: bool = False,
) -> str:
    """결정 한 건을 덧붙인다. `confirms_ontology` 면 지금 정의의 지문을 확정으로 남긴다."""
    work = load(folder)
    if not topic.strip() or not decision.strip():
        raise Stop("무엇에 대한 결정인지(topic)와 결정(decision)이 있어야 합니다")
    lines = [f"\n## {_now()} — {topic.strip()}", "", f"- 결정: {decision.strip()}"]
    if reason.strip():
        lines.append(f"- 이유: {reason.strip()}")
    lines.append(f"- 정한 사람: {decided_by.strip() or '(적지 않음)'}")
    if confirms_ontology:
        digest = _sha(folder / ONTOLOGY)
        if digest is None:
            raise Stop(f"확정할 정의가 없습니다: {ONTOLOGY}")
        work.setdefault("confirmed", {})["ontology_sha256"] = digest
        work["confirmed"]["at"] = _now()
        work["confirmed"]["by"] = decided_by.strip()
        pipeline._write(folder / WORK, work)
        lines.append(f"- 정의 확정: {ONTOLOGY} sha256 {digest[:12]}")
    with (folder / DECISIONS).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return f"{DECISIONS} 에 적었습니다: {topic.strip()}"


def _run_state(folder: Path, run: Path) -> dict[str, Any]:
    loaded = pipeline.load(run)
    state: dict[str, Any] = {
        "run": run.relative_to(folder).as_posix(),
        "sources": [str(one.get("name")) for one in loaded.manifest.get("sources") or []],
        "unresolved": len(loaded.unresolved),
        "objects": sum(len(batch.rows) for batch in loaded.objects),
        "preview": "없음",
        "applied": (run / pipeline.APPLIED).exists(),
    }
    seen = pipeline._read_json(run / pipeline.PREVIEW, [])
    if isinstance(seen, dict):
        if seen.get("digest") != pipeline.digest(pipeline.payload(loaded)):
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
        "confirmed": bool(current and confirmed == current),
        "changed_after_confirm": bool(current and confirmed and confirmed != current),
    }
    runs = [
        _run_state(folder, run)
        for run in sorted((folder / RUNS).glob("*"))
        if (run / pipeline.MANIFEST).exists()
    ]

    steps: list[str] = []
    if not sources:
        steps.append(f"원천 파일을 {SOURCES}/ 에 넣는다")
    for one in sources:
        if one["kind"] == "엑셀":
            steps.append(f"{one['name']}: 시트마다 CSV(UTF-8)로 저장해 {SOURCES}/ 에 둔다")
        elif one["kind"] == "표" and not one["profiled"]:
            steps.append(f"{one['name']}: 조사 — source_profile")
    if any(one["kind"] in {"표", "문서"} for one in sources):
        if not ontology["exists"]:
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
            if one["kind"] == "표" and not one["mapping"]:
                steps.append(
                    f"{one['name']}: 대응 파일 — {MAPPING}/{Path(one['name']).stem}.table.json"
                )
            elif one["kind"] == "표" and not converted:
                steps.append(f"{one['name']}: 변환 — table_convert")
            elif one["kind"] == "문서" and not converted:
                steps.append(
                    f"{one['name']}: 추출 — run_init 뒤 objects/ · relations/ 를 쓴다"
                )
    for run in runs:
        if run["applied"]:
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
            steps.append(
                f"{run['run']}: 사람이 미리 보기를 확인하고 **직접** 적용 — "
                f"python sp_pipeline.py apply {(folder / run['run']).as_posix()}"
            )
    if not steps:
        steps.append("할 일이 없습니다 — 새 원천을 넣거나 새 실행을 만든다")
    return {
        "work": work.get("title"),
        "group": work.get("group"),
        "sources": sources,
        "ontology": ontology,
        "runs": runs,
        "next": steps,
    }


def render(state: dict[str, Any]) -> str:
    lines = [f"작업: {state['work']} (그룹 {state['group'] or '-'})", "", "원천:"]
    for one in state["sources"]:
        marks = [one["kind"], "조사함" if one["profiled"] else "조사 전"]
        if one["mapping"]:
            marks.append(f"대응 {one['mapping']}")
        lines.append(f"- {one['name']} ({' · '.join(marks)})")
    if not state["sources"]:
        lines.append("- 없음")
    ontology = state["ontology"]
    word = (
        "없음"
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
        lines.append(
            f"- {run['run']}: 객체 {run['objects']} · 미해결 {run['unresolved']} · "
            f"미리 보기 {run['preview']} · {'적용함' if run['applied'] else '적용 전'}"
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
    look = sub.add_parser("status", help="어디까지 했고 다음이 무엇인가")
    look.add_argument("folder", type=Path)
    note = sub.add_parser("record", help="결정 한 건을 적는다")
    note.add_argument("folder", type=Path)
    note.add_argument("--topic", required=True)
    note.add_argument("--decision", required=True)
    note.add_argument("--reason", default="")
    note.add_argument("--by", default="")
    note.add_argument("--confirms-ontology", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(init(args.folder, title=args.title, group=args.group))
        elif args.command == "status":
            print(render(status(args.folder)))
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
