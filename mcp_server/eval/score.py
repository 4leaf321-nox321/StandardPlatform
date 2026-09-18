#!/usr/bin/env python3
"""AI 가 이 플랫폼에서 **헤맸는지** 를 수로 본다.

## 왜 있나

도구 설명을 고치고 가이드를 고쳐도, 그 고침이 실제로 도움이 됐는지는 아무도 모른다.
「전보다 나아 보인다」 는 근거가 아니다 — 다음 사람이 그 문장을 되돌려도 아무 일도
안 일어난다. 그래서 **자취를 남기고 점수를 매긴다.** 고치기 전과 후의 수를 나란히
놓을 수 있으면, 그 문장은 지킬 이유가 생긴다.

## 어떻게 쓰나

1. MCP 서버를 자취를 켜고 띄운다(기본은 꺼짐):

       MCP_TRACE_FILE=~/mcp-trace.jsonl ./venv/bin/python server.py

2. AI 클라이언트로 평소처럼 일을 시킨다 — 시나리오는 `cases.md` 에 있다.
3. 점수를 본다:

       ./venv/bin/python eval/score.py ~/mcp-trace.jsonl
       ./venv/bin/python eval/score.py ~/mcp-trace.jsonl --baseline 지난주.jsonl

**CI 에 넣지 않는다.** AI 의 답은 같은 물음에도 흔들리고, 흔들리는 수로 빌드를 막으면
사람은 그 시험을 끄는 법부터 배운다. 이것은 **추세를 보는 자**다.

## 자취에 무엇이 남나

도구 이름 · 걸린 시간 · 판정(`ok`·`empty`·`error`) · 몇 건인지 · `match` · 진단의
`reason`. **값은 안 남긴다** — 객체 이름과 속성 값이 쌓이면 그 파일이 유출 경로가 된다.

## 무엇을 세나

| 수 | 뜻 | 낮을수록 좋은가 |
| --- | --- | --- |
| `calls` | 한 대화에서 부른 도구 수 | 낮을수록 (헤매면 늘어난다) |
| `guide_first` | 첫 호출이 `get_guide`·`ontology_schema` 인 대화의 비율 | 높을수록 |
| `empty` | 0건이 돌아온 호출의 비율 | 낮을수록 (조건을 못 맞추고 있다는 뜻) |
| `error` | 오류가 돌아온 호출의 비율 | 낮을수록 |
| `unresolved_write` | 후보가 여럿인데 **곧바로** 쓰기 도구를 부른 수 | **0 이어야 한다** |
| `blind_write` | 해소 없이 이름으로 무언가를 만든 대화 수 | 낮을수록 |
| `ms_p50` · `ms_p95` | 호출 하나의 시간 | — |

`unresolved_write` 가 0 이 아니면 **문구로는 안 막힌 것이다** — 그때는 안내를 고칠 게
아니라 서버가 막아야 한다(이름으로 쓰는 자리에서 후보가 여럿이면 거절).
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import pairwise
from pathlib import Path
from typing import Any

#: 쓰는 도구 — 해소 없이 이것부터 부르면 짐작으로 쓰는 것이다.
WRITE_TOOLS = {
    "object_create",
    "object_update",
    "objects_import",
    "relation_add",
    "relations_import",
    "bundle_import",
    "ontology_import",
}
FIRST_OK = {"get_guide", "ontology_schema"}
GAP_SECONDS = 120.0
"""이만큼 조용하면 **다른 대화**로 본다. 자취에는 대화 id 가 없다 — 남기려면 토큰이나
세션을 적어야 하고, 그것은 자취를 개인 기록으로 만든다."""


def read(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return sorted(rows, key=lambda one: one.get("ts", 0))


def sessions(rows: list[dict[str, Any]], gap: float) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    for row in rows:
        if out and row.get("ts", 0) - out[-1][-1].get("ts", 0) <= gap:
            out[-1].append(row)
        else:
            out.append([row])
    return out


def _percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(share * (len(ordered) - 1) + 0.5), len(ordered) - 1)
    return ordered[index]


def score(rows: list[dict[str, Any]], gap: float = GAP_SECONDS) -> dict[str, Any]:
    talks = sessions(rows, gap)
    calls = len(rows)
    if not calls:
        return {"calls": 0}

    empty = sum(1 for one in rows if one.get("outcome") == "empty")
    errors = sum(1 for one in rows if one.get("outcome") in ("error", "raised"))
    guide_first = sum(1 for talk in talks if talk[0].get("tool") in FIRST_OK)

    unresolved = 0
    blind = 0
    for talk in talks:
        resolved_here = any(one.get("tool") == "object_resolve" for one in talk)
        wrote_here = any(one.get("tool") in WRITE_TOOLS for one in talk)
        if wrote_here and not resolved_here:
            blind += 1
        for before, after in pairwise(talk):
            if (
                before.get("tool") == "object_resolve"
                and before.get("match") == "candidates"
                and after.get("tool") in WRITE_TOOLS
            ):
                unresolved += 1

    times = [float(one.get("ms", 0)) for one in rows]
    return {
        "calls": calls,
        "talks": len(talks),
        "calls_per_talk": round(calls / len(talks), 1),
        "guide_first": round(guide_first / len(talks), 2),
        "empty": round(empty / calls, 2),
        "error": round(errors / calls, 2),
        "unresolved_write": unresolved,
        "blind_write": blind,
        "ms_p50": round(_percentile(times, 0.5)),
        "ms_p95": round(_percentile(times, 0.95)),
    }


def by_tool(rows: list[dict[str, Any]]) -> list[tuple[str, int, int, int]]:
    """도구마다 (이름, 호출, 0건, 오류) — **어느 도구에서 헤맸는지**가 여기 보인다."""
    names = sorted({str(one.get("tool", "?")) for one in rows})
    out = []
    for name in names:
        mine = [one for one in rows if one.get("tool") == name]
        out.append(
            (
                name,
                len(mine),
                sum(1 for one in mine if one.get("outcome") == "empty"),
                sum(1 for one in mine if one.get("outcome") in ("error", "raised")),
            )
        )
    return sorted(out, key=lambda row: -row[1])


#: 낮을수록 좋은 수 — 견줄 때 화살표 방향을 정한다.
LOWER_IS_BETTER = {"calls_per_talk", "empty", "error", "unresolved_write", "blind_write"}


def report(now: dict[str, Any], before: dict[str, Any] | None) -> str:
    lines = ["| 수 | 지금 | 지난번 | |", "| --- | ---: | ---: | --- |"]
    for key, value in now.items():
        old = (before or {}).get(key)
        mark = ""
        if isinstance(old, int | float) and isinstance(value, int | float) and value != old:
            better = (value < old) if key in LOWER_IS_BETTER else (value > old)
            mark = "좋아짐" if better else "나빠짐"
        lines.append(f"| {key} | {value} | {old if old is not None else '—'} | {mark} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="MCP 자취를 점수로")
    parser.add_argument("trace", type=Path, help="MCP_TRACE_FILE 이 쌓은 JSONL")
    parser.add_argument("--baseline", type=Path, default=None, help="견줄 지난 자취")
    parser.add_argument("--gap", type=float, default=GAP_SECONDS, help="대화를 가르는 초")
    parser.add_argument("--json", action="store_true", help="표 대신 JSON")
    args = parser.parse_args(argv)

    if not args.trace.exists():
        print(f"자취 파일이 없습니다: {args.trace}", file=sys.stderr)
        print("MCP_TRACE_FILE 을 켜고 서버를 띄운 뒤 다시 보세요.", file=sys.stderr)
        return 2

    rows = read(args.trace)
    now = score(rows, args.gap)
    before = score(read(args.baseline), args.gap) if args.baseline else None

    if args.json:
        print(json.dumps({"now": now, "baseline": before}, ensure_ascii=False, indent=2))
        return 0

    print(report(now, before))
    print("\n| 도구 | 호출 | 0건 | 오류 |\n| --- | ---: | ---: | ---: |")
    for name, count, empty, errors in by_tool(rows):
        print(f"| {name} | {count} | {empty} | {errors} |")
    if now.get("unresolved_write"):
        print(
            "\n**후보가 여럿인데 그대로 썼다** — 안내로는 안 막힌 것이다. "
            "그 자리는 서버가 거절해야 한다."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
