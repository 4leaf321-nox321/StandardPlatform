#!/usr/bin/env python3
"""한국어 정답 셋 채점 — AI 의 답(끝의 json 블록)을 **정답 호출의 지금 결과**와 견준다.

## 왜 있나

`score.py` 는 AI 가 **헤맸는지**(자취)를 센다. 이것은 **맞혔는지**를 본다. GUIDE · 도구 설명을
고친 뒤 정답률이 오르는지 내리는지가 수로 보여야 그 고침을 지킬 까닭이 생긴다.

## 어떻게 쓰나

1. 문제지를 뽑는다 — 문항마다 질문과 「답 끝에 붙일 json 꼴」:

       python eval/golden.py prompts > 문제지.md

2. AI 에게 **한 문항씩, 대화를 새로 열고** 문제지의 질문을 그대로 시킨다. 답 전체를 한 줄씩
   모은다 — `answers.jsonl` 에 `{"id": "g01", "answer": "<답 전체>"}`(또는 폴더에 `g01.md` …).
3. 채점 — 정답 호출을 **그 자리에서** 돌려 견준다(정답 값은 저장하지 않는다 — 자료가 늘면
   바뀐다):

       PLATFORM_API_BASE=http://127.0.0.1:8041 SP_TOKEN=<토큰> \\
           python eval/golden.py grade answers.jsonl --record runs.jsonl \\
           --client "Claude Desktop" --data synthetic

   문항마다 ✓/✗ 와 까닭, 갈래별 · 전체 정답률. `--record` 는 한 줄을 덧붙인다 — 판 · GUIDE ·
   도구 목록 · 질문 셋의 지문과 함께라 **같은 지문끼리만** 견준다.

**CI 에 넣지 않는다**(AI 의 답은 같은 물음에도 흔들린다) — 추세를 보는 자다. 합성 자료(`--data
synthetic`)의 정답률은 「고친 뒤 깨졌나」 를 보는 수이지 성과가 아니다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

HERE = Path(__file__).resolve().parent
SET = HERE / "golden" / "sp-golden-1.jsonl"

#: 답 끝에 붙일 json 의 꼴 — 견주는 법(`check.how`)마다. 문제지가 질문 뒤에 싣는다.
FORMS: dict[str, str] = {
    "groups": '{"groups": {"<이름>": <건수>, ...}}',
    "total": '{"total": <건수>}',
    "near_first": '{"asked": true, "candidate": "<확인을 청한 이름>"}',
    "diagnosis_value": '{"asked": true, "candidate": "<확인을 청한 값>"}',
    "rows_top": '{"top": "<1위>"}',
    "decision": '{"decision": "<분석이 낸 판정 코드 그대로>"}',
    "signals": '{"signals": {"<집단>": ["YYYY-MM", ...]}}',
    "life_b10": '{"b10": <개월 — 없으면 null>}',
    "change_near": '{"changes": [{"at": "YYYY-MM", "direction": "up 또는 down"}]}',
    "groups_high": '{"high": ["<높음인 집단>", ...]}',
    "insights_any": '{"mentioned": ["<옮긴 결론 한 줄>", ...]}',
    "logit_top": '{"top": "<가장 많은 값>"}',
    "data_through": '{"data_through": "YYYY-MM"}',
    "search_first": '{"found": "<찾은 것의 이름>"}',
}
TAIL = (
    "\n\n(정답 셋 시험) 답을 다 한 뒤 **맨 끝에** 아래 꼴의 json 블록을 붙여 주세요 — 확인 "
    "질문으로 멈췄으면 그 상태를 적습니다:\n```json\n{form}\n```"
)


def load(path: Path = SET) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def prompts(items: list[dict[str, Any]]) -> str:
    out = ["# 정답 셋 문제지 — 한 문항씩, 대화를 새로 열고 그대로 시킨다\n"]
    for one in items:
        form = FORMS[one["check"]["how"]]
        out.append(f"## {one['id']} · {one['kind']}\n\n{one['q']}{TAIL.format(form=form)}\n")
    return "\n".join(out)


# --- 답 읽기 -------------------------------------------------------------------------------

_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


def answer_json(text: str) -> dict[str, Any] | None:
    """답 글의 **마지막** json 블록 — 없거나 못 읽으면 None."""
    found = _BLOCK.findall(text or "")
    if not found:
        return None
    try:
        got = json.loads(found[-1])
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


# --- 정답 호출의 결과 → 견줄 것 ------------------------------------------------------------


def _groups(out: dict[str, Any], top: int) -> dict[str, int]:
    return {b["label"]: int(b["count"]) for b in (out.get("buckets") or [])[:top]}


def _rows_top(out: dict[str, Any], dim: str) -> str | None:
    cells = sorted(out.get("cells") or [], key=lambda one: -(one.get("ratio") or 0))
    return cells[0]["labels"].get(dim) if cells else None


def _signals(out: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (_norm(chart.get("label")), point["label"])
        for chart in out.get("charts") or []
        for point in chart.get("points") or []
        if point.get("signals")
    }


def _b10(out: dict[str, Any]) -> float | None:
    for one in out.get("b_lives") or []:
        if abs(float(one.get("q") or 0) - 0.1) < 1e-9:
            return None if one.get("status") == "unreachable" else float(one["age"])
    return None


def _changes(out: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (
            str(one.get("label") or one.get("at"))[:7],
            "up" if one["after"] > one["before"] else "down",
        )
        for one in out.get("changes") or []
    ]


def _logit_top(out: dict[str, Any]) -> str | None:
    best: tuple[float, str] | None = None
    for factor in out.get("factors") or []:
        for level in factor.get("levels") or []:
            # 기준 수준 · 불안정 · 드문 값을 모은 「그 밖」(`pooled`)은 답이 될 수 없다.
            if level.get("reference") or level.get("unstable") or level.get("pooled"):
                continue
            ratio = float(level.get("odds_ratio") or 0)
            if best is None or ratio > best[0]:
                best = (ratio, str(level["label"]))
    return best[1] if best else None


def _month_gap(a: str, b: str) -> int:
    try:
        ya, ma = (int(x) for x in a[:7].split("-"))
        yb, mb = (int(x) for x in b[:7].split("-"))
    except ValueError:
        return 99
    return abs((ya * 12 + ma) - (yb * 12 + mb))


Verdict = tuple[bool, str]


def judge(item: dict[str, Any], out: Any, got: dict[str, Any] | None) -> Verdict:
    """한 문항 — (맞았나, 까닭). `out` 은 정답 호출의 지금 결과, `got` 은 답 끝의 json."""
    if got is None:
        return False, "답 끝에 json 블록이 없다"
    if isinstance(out, dict) and out.get("error"):
        return False, f"정답 호출이 실패했다: {str(out['error'])[:120]}"
    check = item["check"]
    how = check["how"]
    rules: dict[str, Callable[[], Verdict]] = {
        "groups": lambda: _same_groups(
            _groups(out, int(check.get("top", 8))), got.get("groups")
        ),
        "total": lambda: _same(out.get("total"), got.get("total")),
        "near_first": lambda: _asked(
            (out.get("near") or [{}])[0].get("label") if out.get("match") == "none" else None,
            got,
        ),
        "diagnosis_value": lambda: _asked(
            (((out.get("diagnosis") or {}).get("values") or [{}])[0].get("near") or [None])[0],
            got,
        ),
        "rows_top": lambda: _same_text(_rows_top(out, check["dim"]), got.get("top")),
        "decision": lambda: _same_text(out.get("decision"), got.get("decision")),
        "signals": lambda: _same_signals(_signals(out), got.get("signals")),
        "life_b10": lambda: _same_b10(_b10(out), got.get("b10")),
        "change_near": lambda: _same_changes(_changes(out), got.get("changes")),
        "groups_high": lambda: _same_set(
            {_norm(r["label"]) for r in out.get("rows") or [] if r.get("flag") == "high"},
            got.get("high"),
        ),
        "insights_any": lambda: _mentions(check.get("expect") or [], got.get("mentioned")),
        "logit_top": lambda: _same_text(_logit_top(out), got.get("top")),
        "data_through": lambda: _same_text(
            str(out.get("data_through") or "")[:7], got.get("data_through")
        ),
        "search_first": lambda: _same_text(
            ((out.get("items") or [{}])[0]).get("label"), got.get("found")
        ),
    }
    rule = rules.get(how)
    if rule is None:
        return False, f"모르는 견주는 법: {how}"
    return rule()


def _same(want: Any, said: Any) -> Verdict:
    return (want == said, f"정답 {want} · 답 {said}")


def _same_text(want: Any, said: Any) -> Verdict:
    return (want is not None and _norm(want) == _norm(said), f"정답 {want} · 답 {said}")


def _same_groups(want: dict[str, int], said: Any) -> Verdict:
    if not isinstance(said, dict):
        return False, "groups 가 없다"
    by_name = {_norm(k): v for k, v in said.items()}
    wrong = [
        f"{k} {v}≠{by_name.get(_norm(k))}"
        for k, v in want.items()
        if by_name.get(_norm(k)) != v
    ]
    return (not wrong, "모두 맞다" if not wrong else "틀린 칸: " + ", ".join(wrong[:4]))


def _asked(want: Any, got: dict[str, Any]) -> Verdict:
    if want is None:
        return False, "정답 호출에 짐작 후보가 없다(정답 셋을 다시 본다)"
    if got.get("asked") is not True:
        return False, "확인을 청하지 않았다 — 짐작으로 넘어갔다"
    return _same_text(want, got.get("candidate"))


def _same_signals(want: set[tuple[str, str]], said: Any) -> Verdict:
    if not isinstance(said, dict):
        return False, "signals 가 없다"
    got = {(_norm(k), str(m)[:7]) for k, months in said.items() for m in (months or [])}
    return (got == want, f"정답 {sorted(want)} · 답 {sorted(got)}")


def _same_b10(want: float | None, said: Any) -> Verdict:
    if want is None:
        return (
            said is None,
            "정답: 없음(unreachable) — 숫자를 지어내지 않는다" + f" · 답 {said}",
        )
    try:
        close = abs(float(said) - want) <= 0.1 * want
    except (TypeError, ValueError):
        close = False
    return close, f"정답 {want:.1f} · 답 {said}(±10%)"


def _same_changes(want: list[tuple[str, str]], said: Any) -> Verdict:
    if not isinstance(said, list) or not said:
        return False, "changes 가 없다"
    got = [
        (str(one.get("at"))[:7], str(one.get("direction")))
        for one in said
        if isinstance(one, dict)
    ]
    missed = [
        w for w in want if not any(d == w[1] and _month_gap(a, w[0]) <= 1 for a, d in got)
    ]
    extra = [
        g for g in got if not any(w[1] == g[1] and _month_gap(w[0], g[0]) <= 1 for w in want)
    ]
    ok = not missed and not extra
    return ok, f"정답 {want} · 답 {got}" + (
        "" if ok else f" (빠짐 {missed} · 없는 것 {extra})"
    )


def _same_set(want: set[str], said: Any) -> Verdict:
    got = {_norm(one) for one in said or []} if isinstance(said, list) else set()
    return (got == want, f"정답 {sorted(want)} · 답 {sorted(got)}")


def _mentions(keywords: list[str], said: Any) -> Verdict:
    text = " ".join(str(one) for one in said or []) if isinstance(said, list) else ""
    hit = [k for k in keywords if _norm(k) in _norm(text)]
    return (len(hit) >= 2, f"심은 신호 중 {len(hit)}개({', '.join(hit)}) — 둘 이상이면 맞음")


# --- 정답 호출 ----------------------------------------------------------------------------


def _ctx(token: str) -> Any:
    request = SimpleNamespace(headers={"authorization": f"Bearer {token}"})
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


async def _fill(server: Any, ctx: Any, value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$ref"}:
        type_slug, name = str(value["$ref"]).split(":", 1)
        got = await server.object_resolve(ctx, type_slug, name)
        if got.get("match") != "exact":
            raise SystemExit(f"$ref {value['$ref']} 이 하나로 안 풀립니다: {got.get('match')}")
        return got["object"]["id"]
    if isinstance(value, dict):
        return {key: await _fill(server, ctx, one) for key, one in value.items()}
    if isinstance(value, list):
        return [await _fill(server, ctx, one) for one in value]
    return value


async def run_gold(server: Any, ctx: Any, item: dict[str, Any]) -> Any:
    args = await _fill(server, ctx, item["gold"]["args"])
    try:
        return await getattr(server, item["gold"]["tool"])(ctx, **args)
    except Exception as caught:
        return {"error": f"{type(caught).__name__}: {caught}"}


# --- 지문 ---------------------------------------------------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


async def fingerprint(server: Any, ctx: Any, client: str, data: str) -> dict[str, Any]:
    """견줄 수 있는 실행인가를 가르는 것 — 다르면 정답률을 나란히 놓지 않는다."""
    tools = await server.mcp.list_tools()
    listing = "\n".join(
        f"{one.name}\n{one.description or ''}" for one in sorted(tools, key=lambda t: t.name)
    )
    guide = Path(server._GUIDE_PATH).read_text(encoding="utf-8")
    version = re.search(r"GUIDE_VERSION:\s*(\S+)", guide)
    try:
        health = await server._get(ctx, "/api/health")
    except Exception:
        health = {}
    try:
        git = subprocess.run(
            ["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        git = ""
    return {
        "set": SET.name,
        "set_sha": _sha(SET.read_text(encoding="utf-8")),
        "app_version": (health or {}).get("version"),
        "git": git or None,
        "guide_version": version.group(1) if version else None,
        "guide_sha": _sha(guide),
        "tools": len(tools),
        "tools_sha": _sha(listing),
        "client": client,
        "data": data,
    }


# --- 채점 ---------------------------------------------------------------------------------


def read_answers(path: Path) -> dict[str, str]:
    """`answers.jsonl`(`{"id", "answer"}` 줄) 또는 폴더(`g01.md` …)."""
    if path.is_dir():
        return {one.stem: one.read_text(encoding="utf-8") for one in sorted(path.glob("*.md"))}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[str(row["id"])] = str(row.get("answer") or "")
    return out


def report(items: list[dict[str, Any]], results: dict[str, Verdict]) -> str:
    lines: list[str] = []
    kinds: dict[str, list[bool]] = {}
    for one in items:
        if one["id"] not in results:
            continue
        ok, why = results[one["id"]]
        kinds.setdefault(one["kind"], []).append(ok)
        lines.append(f"{'✓' if ok else '✗'} {one['id']} [{one['kind']}] {one['q']}\n    {why}")
    done = [ok for ok, _ in results.values()]
    lines.append("")
    lines.append("갈래별: " + " · ".join(f"{k} {sum(v)}/{len(v)}" for k, v in kinds.items()))
    if done:
        lines.append(f"전체 {sum(done)}/{len(done)} ({sum(done) / len(done):.0%})")
    return "\n".join(lines)


async def grade(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(HERE.parent))
    import server

    token = os.environ.get("SP_TOKEN", "").strip()
    if not token:
        raise SystemExit("SP_TOKEN(그 서버의 개인 토큰 · 접근 토큰)을 주세요.")
    ctx = _ctx(token)
    items = load()
    answers = read_answers(args.answers)
    results: dict[str, Verdict] = {}
    for one in items:
        if one["id"] not in answers:
            continue
        out = await run_gold(server, ctx, one)
        results[one["id"]] = judge(one, out, answer_json(answers[one["id"]]))
    print(report(items, results))
    if args.record:
        row = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            **await fingerprint(server, ctx, args.client, args.data),
            "score": [sum(ok for ok, _ in results.values()), len(results)],
            "items": {key: ok for key, (ok, _) in results.items()},
        }
        with args.record.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="한국어 정답 셋 — 문제지 뽑기 · 채점")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prompts", help="문제지(질문 + 답 끝의 json 꼴)")
    graded = sub.add_parser("grade", help="답을 정답 호출의 지금 결과와 견준다")
    graded.add_argument("answers", type=Path, help="answers.jsonl 또는 g01.md … 가 든 폴더")
    graded.add_argument("--record", type=Path, default=None, help="결과 한 줄을 덧붙일 JSONL")
    graded.add_argument("--client", default="", help="AI 클라이언트(Claude Desktop · 포털 …)")
    graded.add_argument("--data", choices=("synthetic", "real"), default="synthetic")
    args = parser.parse_args(argv)
    if args.command == "prompts":
        print(prompts(load()))
        return 0
    return asyncio.run(grade(args))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
