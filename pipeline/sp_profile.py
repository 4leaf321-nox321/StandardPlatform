#!/usr/bin/env python3
"""원천 표 조사 — AI 가 표를 「읽어서」 답하던 것을 **계산**한다.

    python sp_profile.py <원천.csv> [--out 01-조사] [--values] [--match 열=타입|@파일]

행 단위 · 몇 대 몇 · 같은 식별자인데 갈리는 열 · 값의 형식 · 이름에 박힌 조각 · 고를 값 ·
날짜 · 코어 대조를 한 번에 낸다. AI 는 **계산하지 않고 이 결과를 해석한다** — 수천 행의 통계를
AI 가 읽어서 내면 틀려도 알 길이 없다.

값은 기본으로 **가린 패턴**(영문 A · 숫자 9 · 한글 가)으로만 적는다. `--values` 를 주면 고를 값
후보(종류가 적은 열)와 조각 값이 그대로 나온다 — 정의에 고를 값을 적을 때 쓴다.

`--match 열=<type_slug>` 는 플랫폼(`SP_SERVER` · `SP_TOKEN`)에서 그 타입의 식별자를 받아,
`--match 열=@keys.txt` 는 파일(한 줄에 하나)에서 읽어 **그 열이 코어에 붙을 수 있는지** 센다.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

import sp_pipeline as pipeline
import sp_table

FORMAT = "sp-profile/1"
ENUM_LIMIT = 20
"""값 종류가 이 이하면 「고를 값 후보」 로 본다."""
ID_COLUMNS = 12
"""몇 대 몇 · 갈리는 열을 볼 식별자 후보 열의 수 — 쌍마다 한 번씩 훑으므로 막아 둔다."""
TOP = 5
DATE_SHARE = 0.3
SPLIT_SHARE = 0.5
BOUNDARIES = "_-/. "
"""코어 대조에서 「앞부분만 같다」 를 가르는 자리 — 코드가 끊기는 글자."""
SHAPES = 15

Stop = pipeline.Stop


@dataclass
class Column:
    name: str
    values: list[str]
    counts: Counter[str]
    blank: int
    dates: int

    @property
    def filled(self) -> int:
        return len(self.values) - self.blank

    @property
    def distinct(self) -> int:
        return len(self.counts)


def _column(name: str, values: list[str]) -> Column:
    counts = Counter(value for value in values if value)
    return Column(
        name=name,
        values=values,
        counts=counts,
        blank=sum(1 for value in values if not value),
        dates=sum(count for value, count in counts.items() if sp_table._as_date(value)),
    )


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _fanout(a: Column, b: Column) -> dict[str, Any]:
    """a 의 값 하나에 b 의 값이 몇 가지 붙나."""
    groups: dict[str, set[str]] = defaultdict(set)
    for left, right in zip(a.values, b.values, strict=True):
        if left and right:
            groups[left].add(right)
    sizes = [len(one) for one in groups.values()] or [0]
    return {
        "min": min(sizes),
        "max": max(sizes),
        "avg": round(sum(sizes) / len(sizes), 2),
        "over_one": sum(1 for size in sizes if size > 1),
    }


def profile(
    table: sp_table.Table,
    *,
    show_values: bool = False,
    enum_limit: int = ENUM_LIMIT,
    separator: str = "_",
    core: dict[str, tuple[str, set[str]]] | None = None,
) -> dict[str, Any]:
    """조사 결과(JSON 으로 저장할 수 있는 dict). `core` 는 열 → (출처 이름, 식별자들)."""
    total = len(table.rows)

    def show(value: str) -> str:
        return value if show_values else sp_table.mask(value)

    def shown(counter: Counter[str], limit: int) -> list[list[Any]]:
        merged: Counter[str] = Counter()
        for value, count in counter.items():
            merged[show(value)] += count
        return [[value, count] for value, count in merged.most_common(limit)]

    columns = [
        _column(name, [row[name].strip() for _, row in table.rows])
        for name in table.header
        if name
    ]
    by_name = {one.name: one for one in columns}
    out: dict[str, Any] = {
        "format": FORMAT,
        "source": {
            "name": table.name,
            "rows": total,
            "blank_rows": table.blank_rows,
            "encoding": table.encoding,
            "sha256": table.sha256,
        },
        "values_shown": show_values,
    }

    # ── 1. 열마다 ──────────────────────────────────────────────────────────
    out["columns"] = []
    for one in columns:
        shapes: Counter[str] = Counter()
        for value, count in one.counts.items():
            shapes[sp_table.mask(value)] += count
        out["columns"].append(
            {
                "name": one.name,
                "distinct": one.distinct,
                "blank_ratio": _ratio(one.blank, total),
                "date_ratio": _ratio(one.dates, one.filled),
                "patterns": [
                    [pattern, count, _ratio(count, one.filled)]
                    for pattern, count in shapes.most_common(TOP)
                ],
            }
        )

    # ── 2. 행을 유일하게 만드는 열 ─────────────────────────────────────────
    # 식별자 후보 — 날짜 모양이 섞인 열(날짜 · 주차)과 너무 거친 열(행의 1% 미만 종류)은 뺀다.
    # 넣으면 「몇 대 몇」 이 뜻 없는 쌍으로 차고, 정작 봐야 할 쌍이 묻힌다(리허설에서 겪었다).
    floor = max(enum_limit, total // 100)
    ids = [
        one
        for one in columns
        if one.distinct > floor
        and one.dates < DATE_SHARE * max(one.filled, 1)
        and one.blank < total / 2
    ][:ID_COLUMNS]
    unique_columns = [one.name for one in columns if one.blank == 0 and one.distinct == total]
    unique_pairs: list[list[str]] = []
    if not unique_columns:
        for a, b in combinations(ids, 2):
            if len(set(zip(a.values, b.values, strict=True))) == total:
                unique_pairs.append([a.name, b.name])
            if len(unique_pairs) >= 10:
                break
    out["unique"] = {"columns": unique_columns, "pairs": unique_pairs}

    # ── 3. 몇 대 몇 ────────────────────────────────────────────────────────
    cardinality = []
    for a, b in combinations(ids, 2):
        forward, backward = _fanout(a, b), _fanout(b, a)
        if forward["max"] <= 1 and backward["max"] <= 1:
            kind = "1:1"
        elif forward["max"] <= 1:
            kind = "N:1"
        elif backward["max"] <= 1:
            kind = "1:N"
        else:
            kind = "N:M"
        cardinality.append(
            {"a": a.name, "b": b.name, "kind": kind, "a_to_b": forward, "b_to_a": backward}
        )
    out["cardinality"] = cardinality

    # ── 4. 같은 식별자인데 행마다 갈리는 열 ────────────────────────────────
    varying = []
    for key in ids:
        if key.distinct >= key.filled:
            continue  # 한 번씩만 나오는 열 — 갈릴 수가 없다
        changes = []
        steady = []
        finer = []
        for other in columns:
            if other is key:
                continue
            if other in ids and other.distinct > key.distinct:
                # 더 잘게 나뉘는 식별자(프로젝트 아래 과제코드)는 갈리는 게 당연하다
                # — 따로 적는다.
                finer.append(other.name)
                continue
            groups: dict[str, set[str]] = defaultdict(set)
            rows: Counter[str] = Counter()
            for left, right in zip(key.values, other.values, strict=True):
                if left:
                    groups[left].add(right)
                    rows[left] += 1
            split = [value for value, seen in groups.items() if len(seen) > 1]
            if split:
                changes.append(
                    {
                        "column": other.name,
                        "keys": len(split),
                        "rows": sum(rows[v] for v in split),
                    }
                )
            else:
                steady.append(other.name)
        varying.append(
            {
                "key": key.name,
                "keys": key.distinct,
                "varying": sorted(changes, key=lambda one: -one["keys"]),
                "finer": finer,
                "steady": steady,
            }
        )
    out["varying"] = varying

    # ── 5. 이름에 박힌 조각 ────────────────────────────────────────────────
    splits = []
    for one in columns:
        if not separator or one.distinct <= enum_limit:
            continue
        with_separator = sum(c for v, c in one.counts.items() if separator in v)
        if with_separator < SPLIT_SHARE * max(one.filled, 1):
            continue
        pieces: Counter[int] = Counter()
        positions: dict[str, Counter[str]] = defaultdict(Counter)
        shapes: dict[str, Counter[str]] = defaultdict(Counter)
        for value, count in one.counts.items():
            parts = value.split(separator)
            pieces[len(parts)] += count
            for index, part in enumerate(parts, start=1):
                place = str(index)
                positions[place][part] += count
                shapes[sp_table.mask(part)][place] += count
            positions["마지막"][parts[-1]] += count
            shapes[sp_table.mask(parts[-1])]["마지막"] += count
        splits.append(
            {
                "column": one.name,
                "separator": separator,
                "pieces": {str(size): count for size, count in sorted(pieces.items())},
                "positions": [
                    {
                        "position": place,
                        "distinct": len(tokens),
                        "top": shown(tokens, enum_limit if show_values else TOP),
                    }
                    for place, tokens in sorted(
                        positions.items(),
                        key=lambda item: int(item[0]) if item[0].isdigit() else 10**6,
                    )
                ],
                "shapes": {
                    shape: dict(places)
                    for shape, places in sorted(
                        shapes.items(), key=lambda item: -sum(item[1].values())
                    )[:SHAPES]
                },
            }
        )
    out["splits"] = splits

    # ── 6. 고를 값 후보 ────────────────────────────────────────────────────
    out["enums"] = [
        {"column": one.name, "distinct": one.distinct, "values": shown(one.counts, enum_limit)}
        for one in columns
        if 0 < one.distinct <= enum_limit
    ]

    # ── 7. 날짜 ────────────────────────────────────────────────────────────
    dates = []
    for one in columns:
        if one.filled == 0 or one.dates < DATE_SHARE * one.filled:
            continue
        formats: Counter[str] = Counter()
        other: Counter[str] = Counter()
        for value, count in one.counts.items():
            if sp_table._as_date(value):
                formats[sp_table.mask(value)] += count
            else:
                other[value] += count
        dates.append(
            {
                "column": one.name,
                "dates": one.dates,
                "formats": [[shape, count] for shape, count in formats.most_common(TOP)],
                "not_dates": shown(other, TOP),
                "not_date_rows": sum(other.values()),
            }
        )
    out["dates"] = dates

    # ── 8. 코어 대조 ───────────────────────────────────────────────────────
    matches = []
    for name, (origin, keys) in (core or {}).items():
        column = by_name.get(name)
        if column is None:
            raise Stop(
                f"--match 의 열 {name!r} 가 원천에 없습니다"
                f" — 원천의 열: {', '.join(table.header)}"
            )
        exact_keys = set(keys)
        upper_keys = {key.upper() for key in keys}
        fronts = {
            key[:index].upper()
            for key in keys
            for index, char in enumerate(key)
            if index and char in BOUNDARIES
        }
        tally: Counter[str] = Counter()
        missing: Counter[str] = Counter()
        for value, count in column.counts.items():
            upper = value.upper()
            if value in exact_keys:
                tally["exact"] += count
            elif upper in upper_keys:
                tally["case"] += count
            elif upper in fronts:
                tally["shorter"] += count
            elif any(
                upper[:index] in upper_keys
                for index, char in enumerate(upper)
                if index and char in BOUNDARIES
            ):
                tally["longer"] += count
            else:
                tally["none"] += count
                missing[value] += count
        matches.append(
            {
                "column": name,
                "core": origin,
                "core_keys": len(keys),
                "rows": column.filled,
                **{
                    kind: tally[kind]
                    for kind in ("exact", "case", "shorter", "longer", "none")
                },
                "none_distinct": len(missing),
                "none_top": shown(missing, TOP),
            }
        )
    out["core"] = matches
    return out


# --------------------------------------------------------------------------
# 사람이 읽는 보고서
# --------------------------------------------------------------------------


def _place(place: str) -> str:
    return f"{place}번" if place.isdigit() else place


def render(result: dict[str, Any]) -> str:
    source = result["source"]
    total = source["rows"]
    lines = [
        f"원천: {source['name']} · {total}행(빈 행 {source['blank_rows']})"
        f" · {source['encoding']}",
        "값: "
        + ("그대로" if result["values_shown"] else "가린 패턴(A 영문 · 9 숫자 · 가 한글)"),
        "",
        "## 1. 열",
    ]
    for one in result["columns"]:
        patterns = " · ".join(f"{p} {c}" for p, c, _ in one["patterns"][:3])
        date = f" · 날짜 {one['date_ratio']:.0%}" if one["date_ratio"] else ""
        lines.append(
            f"- {one['name']}: 고유 {one['distinct']} · 빈 칸 {one['blank_ratio']:.1%}{date}"
            f" — {patterns}"
        )

    lines += ["", "## 2. 행을 유일하게 만드는 것"]
    unique = result["unique"]
    if unique["columns"]:
        lines.append("- 한 열로: " + ", ".join(unique["columns"]))
    elif unique["pairs"]:
        lines.append("- 두 열로: " + " / ".join(" + ".join(pair) for pair in unique["pairs"]))
    else:
        lines.append("- 한 열 · 두 열로는 유일하지 않다")

    lines += ["", "## 3. 몇 대 몇 (식별자 후보 열끼리)"]
    for one in result["cardinality"]:
        ab, ba = one["a_to_b"], one["b_to_a"]
        lines.append(
            f"- {one['a']} ↔ {one['b']}: **{one['kind']}** — {one['a']} 하나에 {one['b']}"
            f" {ab['min']}~{ab['max']}(평균 {ab['avg']}) · {one['b']} 하나에 {one['a']}"
            f" {ba['min']}~{ba['max']}(평균 {ba['avg']})"
        )
    if not result["cardinality"]:
        lines.append("- 식별자 후보 열이 둘 이상 없다")

    lines += ["", "## 4. 같은 식별자인데 행마다 갈리는 열"]
    for one in result["varying"]:
        lines.append(f"- 기준 {one['key']}(고유 {one['keys']})")
        for change in one["varying"]:
            lines.append(
                f"  - {change['column']}: {change['keys']}개 식별자"
                f" · {change['rows']}행에서 갈림"
            )
        if one["finer"]:
            lines.append("  - 더 잘게 나뉘는 식별자(갈려도 당연): " + ", ".join(one["finer"]))
        if one["steady"]:
            lines.append("  - 늘 같음: " + ", ".join(one["steady"]))
    if not result["varying"]:
        lines.append("- 반복되는 식별자 후보가 없다")

    lines += ["", "## 5. 이름에 박힌 조각"]
    for one in result["splits"]:
        pieces = " · ".join(f"{size}조각 {count}" for size, count in one["pieces"].items())
        lines.append(f"- {one['column']} (`{one['separator']}` 로 자름): {pieces}")
        for place in one["positions"]:
            top = " · ".join(f"{v} {c}" for v, c in place["top"])
            lines.append(f"  - {_place(place['position'])}: 고유 {place['distinct']} — {top}")
        lines.append("  - 조각 모양과 자리:")
        for shape, places in one["shapes"].items():
            spread = " · ".join(f"{_place(place)} {count}" for place, count in places.items())
            lines.append(f"    - {shape}: {spread}")
    if not result["splits"]:
        lines.append("- 없음")

    lines += ["", "## 6. 고를 값 후보"]
    for one in result["enums"]:
        values = " · ".join(f"{v} {c}" for v, c in one["values"])
        lines.append(f"- {one['column']}({one['distinct']}종): {values}")
    if not result["values_shown"] and result["enums"]:
        lines.append("- (값 이름을 보려면 --values)")

    lines += ["", "## 7. 날짜"]
    for one in result["dates"]:
        formats = " · ".join(f"{v} {c}" for v, c in one["formats"])
        other = " · ".join(f"{v} {c}" for v, c in one["not_dates"])
        lines.append(
            f"- {one['column']}: 날짜 {one['dates']}행({formats})"
            + (
                f" · 날짜 아님 {one['not_date_rows']}행 — {other}"
                if one["not_date_rows"]
                else ""
            )
        )
    if not result["dates"]:
        lines.append("- 없음")

    if result["core"]:
        lines += ["", "## 8. 코어 대조"]
        for one in result["core"]:
            rows = one["rows"]
            lines.append(
                f"- {one['column']} → {one['core']}(식별자 {one['core_keys']}개) · {rows}행: "
                f"그대로 {one['exact']}({_ratio(one['exact'], rows):.1%}) · "
                f"대소문자만 다름 {one['case']} · 앞부분만(줄여 씀) {one['shorter']} · "
                f"코어 뒤에 더 붙음 {one['longer']} · 없음 {one['none']}"
                f"({one['none_distinct']}종)"
            )
            if one["none_top"]:
                lines.append(
                    "  - 없는 것: " + " · ".join(f"{v} {c}" for v, c in one["none_top"])
                )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 코어 식별자
# --------------------------------------------------------------------------


fetch_keys = pipeline.fetch_keys
"""식별자 받기는 `sp_pipeline` 에 한 벌 — 변환기의 참조 대조도 같은 것을 쓴다."""


def core_sources(
    matches: list[str], *, base: Path, server: str, token: str
) -> dict[str, tuple[str, set[str]]]:
    """`열=타입` · `열=@파일` 목록 → 열 → (출처, 식별자들)."""
    out: dict[str, tuple[str, set[str]]] = {}
    for one in matches:
        column, sep, target = one.partition("=")
        column, target = column.strip(), target.strip()
        if not sep or not column or not target:
            raise Stop(f"--match 는 열=타입 또는 열=@파일 이어야 합니다: {one!r}")
        if target.startswith("@"):
            path = (base / target[1:]).resolve()
            out[column] = (path.name, pipeline.read_keys_file(path))
        else:
            if not server or not token:
                raise Stop(
                    "플랫폼에서 코어 식별자를 받으려면 SP_SERVER · SP_TOKEN 이 필요합니다"
                )
            out[column] = (target, fetch_keys(server, token, target))
    return out


def run(
    source: Path,
    *,
    out_dir: Path | None = None,
    show_values: bool = False,
    enum_limit: int = ENUM_LIMIT,
    separator: str = "_",
    matches: list[str] | None = None,
    encoding: str = "auto",
    delimiter: str = ",",
    server: str = "",
    token: str = "",
) -> tuple[dict[str, Any], str]:
    table = sp_table.read_table(source, encoding=encoding, delimiter=delimiter)
    if not table.rows:
        raise Stop(f"{table.name}: 값이 있는 행이 없습니다")
    core = core_sources(matches or [], base=Path.cwd(), server=server, token=token)
    result = profile(
        table, show_values=show_values, enum_limit=enum_limit, separator=separator, core=core
    )
    text = render(result)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = source.stem
        pipeline._write(out_dir / f"{stem}.profile.json", result)
        (out_dir / f"{stem}.profile.txt").write_text(text + "\n", encoding="utf-8")
    return result, text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sp_profile", description=__doc__.split("\n")[0])
    parser.add_argument("source", type=Path, help="원천 표(CSV)")
    parser.add_argument("--out", type=Path, help="결과를 쓸 폴더(<이름>.profile.txt · .json)")
    parser.add_argument(
        "--values", action="store_true", help="고를 값 · 조각 값을 그대로 보인다"
    )
    parser.add_argument("--enum-limit", type=int, default=ENUM_LIMIT)
    parser.add_argument("--separator", default="_", help="이름을 자를 글자(빈 값이면 안 자름)")
    parser.add_argument("--match", action="append", default=[], help="열=타입 또는 열=@파일")
    parser.add_argument("--encoding", default="auto")
    parser.add_argument("--delimiter", default=",")
    parser.add_argument("--server", default=os.environ.get("SP_SERVER", ""))
    parser.add_argument("--token", default=os.environ.get("SP_TOKEN", ""))
    args = parser.parse_args(argv)
    try:
        _, text = run(
            args.source,
            out_dir=args.out,
            show_values=args.values,
            enum_limit=args.enum_limit,
            separator=args.separator,
            matches=args.match,
            encoding=args.encoding,
            delimiter=args.delimiter,
            server=args.server,
            token=args.token,
        )
    except Stop as stop:
        print(str(stop), file=sys.stderr)
        return 2
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
