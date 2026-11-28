#!/usr/bin/env python3
"""원천 표 조사 — AI 가 표를 「읽어서」 답하던 것을 **계산**한다.

    python sp_profile.py <원천.csv|.xlsx> [--out 01-조사] [--values] [--match 열=타입|@파일]
                         [--sheet 시트]

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
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

import sp_pipeline as pipeline
import sp_table
import sp_work

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
            **({"sheet": table.sheet} if table.sheet else {}),
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
# 정의 초안 — 조사 결과를 정의(ontology.json) 조각으로
# --------------------------------------------------------------------------
#
# AI 가 정의를 매번 새로 쓰면 「중량(kg)」 이 글 칸으로, 「품번」 이 보통 칸으로 들어가고 같은
# 원천을 두 번 돌리면 정의가 달라진다. 셀 수 있는 것(유일한 열 · 숫자 · 단위 · 날짜 · 종류가
# 적은 값)은 여기서 정하고, AI 는 이 초안을 사람과 함께 고친다. 칸마다 `_why` 가 근거다.

DRAFT_FORMAT = "sp-draft/1"
KEY_HINTS = ("품번", "코드", "번호", "식별", "code", "id", "no", "key", "number")
LABEL_HINTS = ("이름", "명", "제목", "name", "title", "label")
UNIT_IN_HEADER = re.compile(r"^(.*?)\s*[\(\[]\s*([^\)\]]{1,10})\s*[\)\]]\s*$")
NUMBER = re.compile(r"^([-+]?\d+(?:\.\d+)?)\s*([A-Za-z%°℃㎏㎜㎝㎞]+)?$")
BOOL_VALUES = frozenset({"y", "n", "yes", "no", "true", "false", "예", "아니오", "o", "x"})
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
#: 숫자로 볼 몫 · 날짜로 볼 몫 · 주소로 볼 몫.
NUMBER_SHARE = 0.95
DATE_DRAFT_SHARE = 0.9
#: 평균 글자 수가 이만큼이면 긴 글.
LONG_TEXT = 100


def key_of(header: str, index: int) -> str:
    """머리글 → 키 초안. 머리글이 영문이 아니면 `col_<n>` — AI 가 사람과 정한다(「중량(kg)」 을
    `kg` 로 만들지 않게 글자를 덜어 내지 않는다)."""
    if not re.fullmatch(r"[A-Za-z0-9 _\-./]+", header.strip()):
        return f"col_{index}"
    ascii_key = re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")
    return ascii_key if KEY_RE.match(ascii_key) else f"col_{index}"


def _hinted(name: str, hints: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in hints)


def _number(value: str) -> tuple[str, str] | None:
    found = NUMBER.match(value.replace(",", "").strip())
    return (found.group(1), found.group(2) or "") if found else None


def _guess(
    name: str,
    values: list[str],
    *,
    show_values: bool,
    enum_limit: int,
) -> dict[str, Any]:
    """값으로 정한 칸 하나 — 키는 부르는 쪽이 붙인다."""
    header, unit_in_header = name, ""
    found = UNIT_IN_HEADER.match(name)
    if found and found.group(1):
        header, unit_in_header = found.group(1).strip(), found.group(2).strip()
    filled = [one for one in values if one]
    out: dict[str, Any] = {"label": header, "data_type": "text"}
    if not filled:
        out["_why"] = "값이 하나도 없음 — 글로 두었다"
        return out
    count = len(filled)
    dates = sum(1 for one in filled if sp_table._as_date(one))
    numbers = [one for one in (_number(v) for v in filled) if one]
    distinct = sorted(set(filled))
    if dates >= DATE_DRAFT_SHARE * count:
        out["data_type"] = "date"
        out["_why"] = f"값 {count}개 중 {dates}개가 날짜"
    elif sum(one.startswith(("http://", "https://")) for one in filled) >= 0.9 * count:
        out["data_type"] = "url"
        out["_why"] = "값이 주소(http)"
    elif len(numbers) >= NUMBER_SHARE * count:
        units = Counter(unit for _, unit in numbers if unit)
        unit = unit_in_header
        why = f"값 {count}개 중 {len(numbers)}개가 숫자"
        if not unit and units:
            common, seen = units.most_common(1)[0]
            if seen >= 0.9 * len(numbers):
                unit = common
                why += f" · 값에 붙은 단위 {common}"
        elif unit:
            why += f" · 머리글의 단위 ({unit})"
        out["data_type"] = "number"
        if unit:
            out["unit"] = unit
        out["_why"] = why
    elif {one.lower() for one in distinct} <= BOOL_VALUES and len(distinct) <= 2:
        out["data_type"] = "bool"
        out["_why"] = f"값이 둘뿐({' · '.join(distinct)})"
    elif len(distinct) <= enum_limit and count >= 2 * len(distinct):
        out["data_type"] = "enum"
        out["enum_options"] = distinct if show_values else None
        out["_why"] = f"값 종류 {len(distinct)}개가 되풀이됨" + (
            "" if show_values else " — 고를 값은 --values(show_values) 로 다시 돌리면 채워진다"
        )
    elif sum(len(one) for one in filled) / count >= LONG_TEXT:
        out["data_type"] = "text_long"
        out["_why"] = "평균 글자 수가 많음"
    else:
        out["_why"] = "글"
    if unit_in_header and out["data_type"] != "number":
        out["_why"] += f" — 머리글의 ({unit_in_header}) 는 단위로 쓰지 않았다(숫자가 아니다)"
    return out


def draft(
    table: sp_table.Table,
    result: dict[str, Any],
    *,
    slug: str,
    show_values: bool = False,
    enum_limit: int = ENUM_LIMIT,
) -> dict[str, Any]:
    """조사 결과 → 정의 초안(`ontology_import` 의 `types[]` 하나 + 근거). 아무것도 안
    보낸다."""
    header = [name for name in table.header if name]
    values = {name: [row[name].strip() for _, row in table.rows] for name in header}
    unique = list(result["unique"]["columns"])
    notes: list[str] = []
    key_column = next((one for one in unique if _hinted(one, KEY_HINTS)), None) or (
        unique[0] if unique else None
    )
    by_name = {one["name"]: one for one in result["columns"]}
    candidates = [
        one for one in header if one != key_column and by_name[one]["distinct"] > enum_limit
    ]
    label_column = next((one for one in candidates if _hinted(one, LABEL_HINTS)), None) or (
        max(candidates, key=lambda one: by_name[one]["distinct"]) if candidates else None
    )
    if key_column:
        notes.append(f"식별자: 「{key_column}」 — 빈 값 없이 행마다 다르다")
    elif result["unique"]["pairs"]:
        pair = result["unique"]["pairs"][0]
        notes.append(
            f"한 열로 유일한 것이 없다 — 「{pair[0]}」 + 「{pair[1]}」 이 함께 유일하다. "
            "식별자를 "
            "이어 붙일지, 아래 「잇는 표」 인지 사람과 정한다"
        )
    else:
        notes.append(
            "행을 유일하게 하는 열이 없다 — 식별자 없이(optional) 두거나 사람에게 묻는다"
        )
    if label_column:
        notes.append(f"이름: 「{label_column}」")

    core_refs = {
        one["column"]: one
        for one in result.get("core") or []
        if one["rows"] and (one["exact"] + one["case"]) >= 0.9 * one["rows"]
    }
    properties = []
    for index, name in enumerate(header, start=1):
        if name in (key_column, label_column):
            continue
        one = _guess(name, values[name], show_values=show_values, enum_limit=enum_limit)
        if name in core_refs:
            matched = core_refs[name]
            one = {
                "label": one["label"],
                "data_type": "object_ref",
                "ref_type_slug": matched["core"],
                "_why": f"값의 {matched['exact'] + matched['case']}/{matched['rows']}개가 "
                f"{matched['core']} 의 식별자",
            }
        properties.append({"key": key_of(name, index), "_column": name, **one})
    if any(one["key"].startswith("col_") for one in properties):
        notes.append("머리글을 영문 키로 못 바꾼 칸은 col_<n> — 키를 사람과 정한다")

    out: dict[str, Any] = {
        "format": DRAFT_FORMAT,
        "source": result["source"]["name"],
        "types": [
            {
                "slug": slug,
                # 시트가 있으면 시트 이름, 아니면 파일 이름(확장자 뺌) — 사람이 고친다.
                "label": result["source"].get("sheet") or Path(result["source"]["name"]).stem,
                "key_policy": "required" if key_column else "optional",
                "properties": properties,
            }
        ],
        "_key_column": key_column,
        "_label_column": label_column,
        "_notes": notes,
    }
    bridge = _bridge(result, header)
    if bridge:
        out["_bridge"] = bridge
        notes.append(
            f"잇는 표 후보 — 「{bridge['src']}」 + 「{bridge['dst']}」 이 함께 유일하고 서로 "
            "N:M 이다. "
            "타입이 아니라 관계(앞 → 뒤)와 선의 칸(나머지 열)으로 넣는 것을 사람과 정한다"
        )
    return out


def _bridge(result: dict[str, Any], header: list[str]) -> dict[str, Any] | None:
    """BOM 같은 잇는 표 — 한 열로는 유일하지 않고, 두 열이 함께 유일하며 서로 N:M."""
    if result["unique"]["columns"] or not result["unique"]["pairs"]:
        return None
    many = {(one["a"], one["b"]) for one in result["cardinality"] if one["kind"] == "N:M"}
    for a, b in result["unique"]["pairs"]:
        if (a, b) in many or (b, a) in many:
            return {
                "src": a,
                "dst": b,
                "edge_fields": [one for one in header if one not in (a, b)],
            }
    return None


def render_draft(found: dict[str, Any]) -> str:
    """초안을 말로 — 칸마다 종류와 근거."""
    kind = found["types"][0]
    lines = [f"## 정의 초안 — {kind['slug']}(식별자 {kind['key_policy']})"]
    lines += [f"- {one}" for one in found["_notes"]]
    for one in kind["properties"]:
        unit = f" · {one['unit']}" if one.get("unit") else ""
        lines.append(
            f"- {one['_column']} → {one['key']}: {one['data_type']}{unit} ({one['_why']})"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 사람이 읽는 보고서
# --------------------------------------------------------------------------


def _place(place: str) -> str:
    return f"{place}번" if place.isdigit() else place


def brief(result: dict[str, Any]) -> str:
    """조사 결과를 **말로 전할 몇 줄**로 — 행 · 유일 열 · 몇 대 몇 · 갈리는 칸 · 고를 값 ·
    코어 대조. 값은 없고 수와 열 이름뿐이라 그대로 읽어 전해도 된다."""
    source = result["source"]
    lines = [f"행 {source['rows']} · 열 {len(result['columns'])}"]
    unique = result["unique"]
    if unique["columns"]:
        lines.append("유일한 열: " + ", ".join(unique["columns"]))
    elif unique["pairs"]:
        lines.append(
            "유일한 쌍: " + " / ".join("+".join(pair) for pair in unique["pairs"][:3])
        )
    else:
        lines.append("한 열 · 두 열로 유일하지 않음")
    for one in result["cardinality"][:8]:
        lines.append(f"{one['a']} ↔ {one['b']}: {one['kind']}")
    for one in result["varying"]:
        split = [f"{c['column']}({c['keys']})" for c in one["varying"][:4]]
        if split:
            lines.append(f"{one['key']} 기준 갈림: " + ", ".join(split))
    enums = [f"{one['column']}({one['distinct']})" for one in result["enums"]]
    if enums:
        lines.append("고를 값 후보: " + ", ".join(enums))
    for one in result["dates"]:
        lines.append(
            f"날짜 {one['column']}: 날짜 {one['dates']} · 아님 {one['not_date_rows']}"
        )
    for one in result["core"]:
        rows = one["rows"] or 1
        lines.append(
            f"코어 대조 {one['column']}→{one['core']}: 그대로 {one['exact'] / rows:.0%} · "
            f"대소문자 {one['case']} · 줄여 씀 {one['shorter']} · 없음 {one['none']}"
        )
    return "\n".join(lines)


def render(result: dict[str, Any]) -> str:
    source = result["source"]
    total = source["rows"]
    lines = [
        f"원천: {source['name']}"
        + (f" 「{source['sheet']}」" if source.get("sheet") else "")
        + f" · {total}행(빈 행 {source['blank_rows']}) · {source['encoding']}",
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
    sheet: str = "",
    server: str = "",
    token: str = "",
) -> tuple[dict[str, Any], str]:
    """조사 · 정의 초안. 엑셀의 시트를 고르면(`sheet`) 결과 파일 이름에 시트가 붙는다 —
    `<이름>-<시트>.profile.json`."""
    table = sp_table.read_table(source, encoding=encoding, delimiter=delimiter, sheet=sheet)
    if not table.rows:
        raise Stop(f"{table.name}: 값이 있는 행이 없습니다")
    core = core_sources(matches or [], base=Path.cwd(), server=server, token=token)
    result = profile(
        table, show_values=show_values, enum_limit=enum_limit, separator=separator, core=core
    )
    found = draft(
        table,
        result,
        # 파일 이름을 영문 slug 로 못 바꾸면 자리 표시 — AI 가 사람과 정한다.
        slug=key_of(sheet or source.stem, 0).replace("col_0", "type_draft"),
        show_values=show_values,
        enum_limit=enum_limit,
    )
    text = render(result) + "\n\n" + render_draft(found)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        # 이름의 시트는 **읽은 시트**(CSV 는 시트가 없어 `sheet` 를 안 본다 — 예전에는 그
        # 값이 검사 없이 이름이 되어 Windows 에서 `01-조사/` 밖에 썼다). 경로 글자는 지우고,
        # 쓰는 자리는 다시 `inside` 로 본다.
        stem = sp_table.survey_stem(source, table.sheet if sheet else "")
        pipeline._write(sp_work.inside(out_dir, f"{stem}.profile.json"), result)
        pipeline._write(sp_work.inside(out_dir, f"{stem}.draft.json"), found)
        sp_work.inside(out_dir, f"{stem}.profile.txt").write_text(
            text + "\n", encoding="utf-8"
        )
    return result, text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sp_profile", description=__doc__.split("\n")[0])
    parser.add_argument("source", type=Path, help="원천 표(CSV · 엑셀 .xlsx)")
    parser.add_argument("--out", type=Path, help="결과를 쓸 폴더(<이름>.profile.txt · .json)")
    parser.add_argument(
        "--values", action="store_true", help="고를 값 · 조각 값을 그대로 보인다"
    )
    parser.add_argument("--enum-limit", type=int, default=ENUM_LIMIT)
    parser.add_argument("--separator", default="_", help="이름을 자를 글자(빈 값이면 안 자름)")
    parser.add_argument("--match", action="append", default=[], help="열=타입 또는 열=@파일")
    parser.add_argument("--encoding", default="auto")
    parser.add_argument("--delimiter", default=",")
    parser.add_argument("--sheet", default="", help="엑셀의 시트(비면 첫 보이는 시트)")
    parser.add_argument("--server", default=os.environ.get("SP_SERVER", ""))
    parser.add_argument("--token", default=os.environ.get("SP_TOKEN", ""))
    args = parser.parse_args(argv)
    try:
        result, text = run(
            args.source,
            out_dir=args.out,
            show_values=args.values,
            enum_limit=args.enum_limit,
            separator=args.separator,
            matches=args.match,
            encoding=args.encoding,
            delimiter=args.delimiter,
            sheet=args.sheet,
            server=args.server,
            token=args.token,
        )
    except Stop as stop:
        print(str(stop), file=sys.stderr)
        return 2
    print(text)
    print("\n== 말로 전할 요약 ==\n" + brief(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
