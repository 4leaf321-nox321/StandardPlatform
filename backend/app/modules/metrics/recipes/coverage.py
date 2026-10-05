"""커버리지 — 다뤄야 할 축 조합 중 기록이 다룬 것과 빈 칸(ADR 0022).

## 다뤄야 할 조합

지표의 기준을 차례로 고른다 — 예: 모델 · 부품 · 고장 메커니즘 · 해석법(모두 축을 가리키는
기준). 첫 기준의 값(거르면 그 값, 아니면 기록에 나온 값)에서 **온톨로지의 길**(모델 → 부품의
관계, 부품 → 메커니즘의 참조 칸 …)을 따라 다음 축의 값으로 편다. 그렇게 편 끝 조합이 「다뤄야
할 것」 이다. 다음 기준을 거르면(`d.<기준>=값`) 그 값만 편다.

## 다룬 것 · 빈 칸 · 기대 밖

셀(기준을 모두 묶은 것)이 「기록이 다룬 조합」 이다. 조합 하나가 다뤄졌다는 것은 그것으로
시작하는 셀에 기록이 있다는 것이다(위 조합은 아래의 합). **빈 칸**은 다룬 기록이 없는 조합 중
바로 위 조합은 다룬 것 — 위가 비었으면 그 위가 빈 칸이다(그 아래 끝 조합 수를 함께). **기대
밖**은 기록은 있는데 길로 닿지 않는 조합 — 온톨로지(관계가 빠졌다)나 태깅(잘못 붙었다)을 고칠
곳이다.

한 기록에 태그가 여럿이면(부품 둘 · 메커니즘 둘) 그 모든 짝을 다룬 것으로 본다 — 셀이 그렇게
센다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import common, registry, ways
from app.modules.metrics.recipes.schemas import (
    CoverageDepthOut,
    CoverageExtraOut,
    CoverageGapOut,
    CoverageLevelOut,
    CoverageOut,
    CoverageRootOut,
)
from app.modules.objects import axes

NAME = "coverage"
LABEL = "커버리지"
METHOD = "온톨로지의 길로 편 기대 조합 x 셀이 다룬 조합 v1"
MAX_LEVELS = 4
MAX_EXPECTED = 50_000
MAX_ROOTS = 500
GAPS = 200
COMPACT_GAPS = 30
EXTRAS = 30
COMPACT_EXTRAS = 10
COMPACT_ROOTS = 15

Combo = tuple[str, ...]
#: 셀의 조합 — 기준이 비어 있으면 None 이 낀다.
Seen = tuple[str | None, ...]


# --- 순수 함수 -----------------------------------------------------------------------


def expand(
    roots: Sequence[str],
    steps: Sequence[Mapping[str, set[str]]],
    only: Sequence[str | None] = (),
) -> list[set[Combo]]:
    """깊이마다 다뤄야 할 조합 — `steps[i]` 는 깊이 i 의 값 → 다음 축의 값들. `only[i + 1]` 이
    있으면 그 깊이는 그 값만."""
    level: set[Combo] = {(one,) for one in roots}
    out = [level]
    for depth, step in enumerate(steps, start=1):
        wanted = only[depth] if depth < len(only) else None
        level = {
            (*combo, child)
            for combo in level
            for child in step.get(combo[-1], set())
            if wanted is None or child == wanted
        }
        out.append(level)
    return out


def prefixes(observed: Mapping[Seen, int]) -> dict[Seen, int]:
    """조합마다 그것으로 시작하는 셀의 합 — 비어 있는 값이 끼면 그 앞까지만."""
    out: dict[Seen, int] = {}
    for combo, count in observed.items():
        for size in range(1, len(combo) + 1):
            head = combo[:size]
            if head[-1] is None:
                break
            out[head] = out.get(head, 0) + count
    return out


@dataclass
class Gap:
    combo: Combo
    leaves: int


def gaps(expected: Sequence[set[Combo]], covered: Mapping[Seen, int]) -> list[Gap]:
    """빈 칸 — 다룬 기록이 없는 조합 중 바로 위는 다룬 것(뿌리면 뿌리 자체). 끝 조합 수와
    함께."""
    leaves: dict[Combo, int] = {}
    for leaf in expected[-1]:
        for size in range(1, len(leaf) + 1):
            leaves[leaf[:size]] = leaves.get(leaf[:size], 0) + 1
    out: list[Gap] = []
    for depth, level in enumerate(expected):
        for combo in sorted(level):
            if covered.get(combo):
                continue
            if depth == 0 or covered.get(combo[:-1]):
                out.append(Gap(combo, leaves.get(combo, 0)))
    return out


# --- 셀 어댑터 -----------------------------------------------------------------------


def available(built: spec_module.Built) -> str | None:
    if built.spec.measure != "count":
        return "건수 지표에서만 됩니다 — 기록이 다룬 조합을 셉니다."
    axes_ = [one for one in built.dims if one.signature[0] == "ref" and one.signature[1]]
    if len(axes_) < 2:
        return (
            "축(다른 타입을 가리키는 참조 · 관계)인 기준이 둘 이상 있는 지표에서만 됩니다 — "
            "그 사이의 길을 따라 다뤄야 할 조합을 폅니다."
        )
    return None


registry.register(registry.Recipe(NAME, LABEL, available))


def run(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    *,
    levels: Sequence[str],
    via: Sequence[str | None] = (),
    compact: bool = False,
) -> CoverageOut:
    reason = available(built)
    if reason is not None:
        raise common.refuse(46, reason)
    levels = list(levels)
    if not 2 <= len(levels) <= MAX_LEVELS or len(set(levels)) != len(levels):
        raise common.refuse(
            46, f"기준을 서로 다른 것으로 2~{MAX_LEVELS}개 차례로 고릅니다(levels)."
        )
    if len(via) > len(levels) - 1:
        raise common.refuse(46, "길(via)은 기준 사이마다 하나 — 기준 수보다 하나 적습니다.")
    dims = [common.dim_of(built, one) for one in levels]
    types = [ways.axis_type(built, one) for one in levels]
    given = [*via, *([None] * (len(levels) - 1 - len(via)))]
    chosen = [
        ways.pick(
            db,
            types[i],
            types[i + 1],
            given[i] or None,
            what=f"「{dims[i].axis.label}」 → 「{dims[i + 1].axis.label}」",
        )
        for i in range(len(levels) - 1)
    ]

    # 다룬 조합 — 기준을 모두 묶은 셀(기간은 합친다).
    observed_ask = replace(ask, dims=list(levels), by=())
    frame = query.frame(db, user, metric, built, observed_ask, with_denominator=False)
    common.require_whole(frame)
    observed: dict[Seen, int] = {}
    for cell in frame.cells:
        combo = tuple(cell.dims.get(one) for one in levels)
        observed[combo] = observed.get(combo, 0) + cell.count
    covered = prefixes(observed)

    caveats = common.Caveats()
    common.overlap_note(built, observed_ask, caveats)
    root = levels[0]
    if root in ask.filters:
        value = ask.filters[root]
        if value is None:
            raise common.refuse(46, "첫 기준을 「(비어 있음)」 으로 거르면 펼 값이 없습니다.")
        roots = [value]
    else:
        counts: dict[str, int] = {}
        for combo, count in observed.items():
            if combo[0] is not None:
                counts[combo[0]] = counts.get(combo[0], 0) + count
        roots = sorted(counts, key=lambda one: -counts[one])
        if len(roots) > MAX_ROOTS:
            caveats.add(
                "roots_capped",
                f"「{dims[0].axis.label}」 값이 {len(roots)}개라 기록이 많은 {MAX_ROOTS}개만 "
                "폈습니다 — 거르면(d.<기준>=값) 그 값만 봅니다.",
                count=len(roots) - MAX_ROOTS,
            )
            roots = roots[:MAX_ROOTS]
        caveats.add(
            "roots_from_records",
            f"「{dims[0].axis.label}」 은 기록에 나온 값만 폈습니다 — 기록이 하나도 없는 값은 "
            "여기 없습니다(거르면 그 값을 기록이 없어도 폅니다).",
            level="info",
        )
    steps: list[dict[str, set[str]]] = []
    frontier = set(roots)
    only = [ask.filters.get(one) if one in ask.filters else None for one in levels]
    for i in range(len(levels) - 1):
        step = ways.follow(db, user, types[i], chosen[i].address, frontier)
        steps.append(step)
        frontier = {child for children in step.values() for child in children}
        wanted = only[i + 1]
        if wanted is not None:
            frontier &= {wanted}
    expected = expand(roots, steps, only)
    if any(len(level) > MAX_EXPECTED for level in expected):
        raise common.refuse(
            46,
            f"다뤄야 할 조합이 {MAX_EXPECTED:,}개를 넘습니다 — 첫 기준을 "
            "거르거나(d.<기준>=값) 기준을 줄입니다.",
        )
    found_gaps = gaps(expected, covered)
    leaves = expected[-1]
    leaf_set = set(leaves)
    root_set = set(roots)
    extras = sorted(
        (
            (combo, count)
            for combo, count in observed.items()
            if None not in combo and combo[0] in root_set and combo not in leaf_set
        ),
        key=lambda one: -one[1],
    )
    untagged = sum(count for combo, count in observed.items() if None in combo)

    labels = _labels(db, dims, [*observed, *leaves, *(gap.combo for gap in found_gaps)])

    def named(combo: Sequence[str | None]) -> list[str]:
        return [
            labels[i].get(value, value) if value is not None else axes.EMPTY_LABEL
            for i, value in enumerate(combo)
        ]

    if any(len(dim.axis.label) and dim.axis.multi for dim in dims):
        caveats.add(
            "cross_product",
            "한 기록에 태그가 여럿이면(부품 둘 · 메커니즘 둘) 그 모든 짝을 다룬 것으로 "
            "봅니다.",
            level="info",
        )
    if extras:
        caveats.add(
            "extras",
            f"기록은 있는데 온톨로지의 길로 닿지 않는 조합이 {len(extras)}개입니다 — 관계가 "
            "빠졌거나 태그가 잘못 붙은 곳입니다.",
            count=len(extras),
        )
    if untagged:
        caveats.add(
            "untagged",
            f"기준 중 하나라도 빈 기록이 {untagged}건(겹침 포함)입니다 — 그 기록은 다룬 "
            "조합에 들지 않습니다.",
            level="info",
            count=untagged,
        )

    roots_out: list[CoverageRootOut] = []
    for value in roots:
        mine = [leaf for leaf in leaves if leaf[0] == value]
        done = sum(1 for leaf in mine if covered.get(leaf))
        roots_out.append(
            CoverageRootOut(
                key=value,
                label=labels[0].get(value, value),
                expected=len(mine),
                covered=done,
                share=done / len(mine) if mine else None,
            )
        )
    roots_out.sort(key=lambda one: (one.share if one.share is not None else 2, one.label))
    room = COMPACT_GAPS if compact else GAPS
    head = common.header(
        db,
        user,
        metric,
        built,
        frame,
        recipe=NAME,
        method=METHOD,
        params={
            "levels": levels,
            "via": [one.address for one in chosen],
            "compact": compact,
        },
        caveats=caveats,
        excluded={},
    )
    return CoverageOut(
        **head,
        levels=[
            CoverageLevelOut(
                dim=levels[i],
                label=dims[i].axis.label,
                type_slug=types[i],
                way=chosen[i - 1].address if i else None,
                way_label=chosen[i - 1].label if i else None,
            )
            for i in range(len(levels))
        ],
        depths=[
            CoverageDepthOut(
                depth=depth,
                labels=[one.axis.label for one in dims[: depth + 1]],
                expected=len(level),
                covered=sum(1 for combo in level if covered.get(combo)),
            )
            for depth, level in enumerate(expected)
        ],
        roots=roots_out[:COMPACT_ROOTS] if compact else roots_out,
        expected_leaves=len(leaves),
        covered_leaves=sum(1 for leaf in leaves if covered.get(leaf)),
        gaps=[
            CoverageGapOut(
                keys=list(gap.combo),
                labels=named(gap.combo),
                depth=len(gap.combo) - 1,
                leaves=gap.leaves,
            )
            for gap in found_gaps[:room]
        ],
        gaps_total=len(found_gaps),
        extras=[
            CoverageExtraOut(
                keys=[str(one) for one in combo],
                labels=named(combo),
                count=count,
                drill=query.drill(
                    built,
                    query.Cell(
                        dict(zip(levels, combo, strict=True)),
                        None,
                        None,
                        None,
                        count,
                        0,
                        None,
                        None,
                        None,
                    ),
                    by=(),
                    ask=ask,
                ),
            )
            for combo, count in extras[: COMPACT_EXTRAS if compact else EXTRAS]
        ],
        extras_total=len(extras),
        untagged=untagged,
    )


def _labels(
    db: Session, dims: Sequence[spec_module.Dim], combos: Sequence[Sequence[str | None]]
) -> list[dict[str, str]]:
    """깊이마다 값의 이름 — 기대 조합의 값은 셀에 없을 수 있어 직접 푼다."""
    out: list[dict[str, str]] = []
    for i, dim in enumerate(dims):
        seen: set[str] = set()
        for combo in combos:
            value = combo[i] if len(combo) > i else None
            if value is not None:
                seen.add(value)
        keys = sorted(seen)
        found = axes.labels(db, dim.axis, keys[: query.MAX_LABELS]) if keys else {}
        out.append({key: found.get(key, key) for key in keys})
    return out
