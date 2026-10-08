"""재발 — 전작에서 나온 축 조합이 다음 모델에서 다시 나왔나(ADR 0022 C).

세대 기준(예: 모델)과 서명 기준(예: 부품 x 고장 메커니즘)을 고른다. 셀에서 세대마다 나온
서명의 집합 S(g) 를 얻고, 세대 축 타입에서 **전작으로 가는 길**(전작 참조 칸 · 관계)을 따라
전작 p 를 찾아 견준다:

    다시 나온 것   S(g) ∩ S(p)
    재발률         |S(g) ∩ S(p)| / |S(p)|
    새로 나온 것   S(g) - S(p)

모든 쌍을 합친 재발률, 여러 세대에 걸쳐 되풀이된 서명(다시 나온 쌍의 수)도 낸다. 「교훈이 다음
모델로 이어졌나」 의 답이다 — 다시 나온 서명은 대책이 이어지지 않았거나 같은 설계를 물려받은
곳이다.

날짜 순서는 보지 않는다 — 후속 모델의 기록이 전작의 기록보다 먼저 났을 수도 있다(주의). 서명에
빈 값이 낀 기록(태그가 모자란)은 서명이 아니다.
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
    RecurrenceItemOut,
    RecurrenceOut,
    RecurrencePairOut,
    RecurrenceSignatureOut,
)
from app.modules.objects import axes

NAME = "recurrence"
LABEL = "재발"
METHOD = "세대마다 서명 집합 x 전작의 서명 집합(교집합 · 재발률) v1"
MAX_SIGNATURE = 3
ITEMS = 20
COMPACT_ITEMS = 5
COMPACT_PAIRS = 15
SIGNATURES = 30
COMPACT_SIGNATURES = 10

Signature = tuple[str, ...]


# --- 순수 함수 -----------------------------------------------------------------------


@dataclass
class Pair:
    generation: str
    predecessor: str
    before: dict[Signature, int]
    now: dict[Signature, int]

    @property
    def recurring(self) -> list[Signature]:
        return sorted(set(self.now) & set(self.before))

    @property
    def rate(self) -> float | None:
        return len(self.recurring) / len(self.before) if self.before else None

    @property
    def new(self) -> int:
        return len(set(self.now) - set(self.before))


def pairs(
    signatures: Mapping[str, dict[Signature, int]], predecessors: Mapping[str, set[str]]
) -> tuple[list[Pair], int]:
    """세대마다 전작과 짝 — (쌍들, 전작이 없거나 전작에 서명이 없는 세대 수)."""
    out: list[Pair] = []
    lonely = 0
    for generation, now in signatures.items():
        found = [one for one in predecessors.get(generation, set()) if signatures.get(one)]
        if not found:
            lonely += 1
            continue
        for predecessor in sorted(found):
            out.append(Pair(generation, predecessor, signatures[predecessor], now))
    return out, lonely


def overall(found: Sequence[Pair]) -> float | None:
    before = sum(len(one.before) for one in found)
    return sum(len(one.recurring) for one in found) / before if before else None


# --- 셀 어댑터 -----------------------------------------------------------------------


def available(built: spec_module.Built) -> str | None:
    if built.spec.measure != "count":
        return "건수 지표에서만 됩니다 — 세대마다 나온 조합을 셉니다."
    axes_ = [one for one in built.dims if one.signature[0] == "ref" and one.signature[1]]
    if not axes_ or len(built.dims) < 2:
        return (
            "축(다른 타입을 가리키는 참조 · 관계)인 세대 기준과 서명 기준이 있는 지표에서만 "
            "됩니다 — 세대(모델)의 전작을 온톨로지에서 찾아 견줍니다."
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
    generation: str,
    signature: Sequence[str],
    via: str | None = None,
    compact: bool = False,
) -> RecurrenceOut:
    reason = available(built)
    if reason is not None:
        raise common.refuse(47, reason)
    # 같은 서명 기준을 두 번 주면(`signature=x,x`) 같은 이름의 칸이 겹쳐 500 이었다 — 한
    # 번만(뜻이 같다, 2026-10-08).
    signature = list(dict.fromkeys(signature))
    if not 1 <= len(signature) <= MAX_SIGNATURE or generation in signature:
        raise common.refuse(
            47,
            f"서명 기준을 세대 기준과 다른 것으로 1~{MAX_SIGNATURE}개 고릅니다(signature).",
        )
    gen_dim = common.dim_of(built, generation)
    sig_dims = [common.dim_of(built, one) for one in signature]
    gen_type = ways.axis_type(built, generation)
    what = f"「{gen_dim.axis.label}」 의 전작"
    if via:
        way = ways.pick(db, gen_type, gen_type, via, what=what)
    else:
        # 같은 타입으로 가는 길 중 들어오는 것(나를 전작으로 가리키는 것 = 후속)은 뺀다.
        forward = [
            one
            for one in ways.ways(db, gen_type, gen_type)
            if not one.address.startswith("in.")
        ]
        way = (
            forward[0]
            if len(forward) == 1
            else ways.pick(db, gen_type, gen_type, None, what=what)
        )

    levels = [generation, *signature]
    observed_ask = replace(ask, dims=levels, by=())
    frame = query.frame(db, user, metric, built, observed_ask, with_denominator=False)
    common.require_whole(frame)
    by_generation: dict[str, dict[Signature, int]] = {}
    for cell in frame.cells:
        values = [cell.dims.get(one) for one in levels]
        if any(value is None for value in values):
            continue
        key = str(values[0])
        sig: Signature = tuple(str(value) for value in values[1:])
        bucket = by_generation.setdefault(key, {})
        bucket[sig] = bucket.get(sig, 0) + cell.count
    predecessors = ways.follow(db, user, gen_type, way.address, by_generation)
    found, lonely = pairs(by_generation, predecessors)
    found.sort(key=lambda one: (-(one.rate or 0.0), one.generation))

    gen_names = _names(
        db, gen_dim, {one for one in by_generation} | {p.predecessor for p in found}
    )
    sig_names = [
        _names(db, dim, {sig[i] for one in found for sig in one.recurring})
        for i, dim in enumerate(sig_dims)
    ]

    def named(sig: Signature) -> list[str]:
        return [sig_names[i].get(value, value) for i, value in enumerate(sig)]

    repeated: dict[Signature, list[str]] = {}
    for one in found:
        for sig in one.recurring:
            repeated.setdefault(sig, []).append(gen_names.get(one.generation, one.generation))
    caveats = common.Caveats()
    common.overlap_note(built, observed_ask, caveats)
    caveats.add(
        "order_ignored",
        "날짜 순서는 보지 않습니다 — 후속 모델의 기록이 전작의 기록보다 먼저 났을 수도 "
        "있습니다.",
        level="info",
    )
    if lonely:
        caveats.add(
            "no_predecessor",
            f"전작이 없거나 전작에 기록이 없는 「{gen_dim.axis.label}」 이 {lonely}개입니다 — "
            "견주지 않았습니다.",
            level="info",
            count=lonely,
        )
    if not found:
        caveats.add(
            "no_pairs",
            "전작과 짝지은 세대가 없습니다 — 전작으로 가는 길(via)과 거르기를 확인합니다.",
        )
    room = COMPACT_ITEMS if compact else ITEMS
    head = common.header(
        db,
        user,
        metric,
        built,
        frame,
        recipe=NAME,
        method=METHOD,
        params={
            "generation": generation,
            "signature": signature,
            "via": way.address,
            "compact": compact,
        },
        caveats=caveats,
        excluded={},
    )
    shown_pairs = found[:COMPACT_PAIRS] if compact else found
    top = sorted(repeated.items(), key=lambda one: (-len(one[1]), one[0]))
    return RecurrenceOut(
        **head,
        generation=generation,
        generation_label=gen_dim.axis.label,
        way=way.address,
        way_label=way.label,
        signature=signature,
        signature_labels=[one.axis.label for one in sig_dims],
        pairs=[
            RecurrencePairOut(
                key=one.generation,
                label=gen_names.get(one.generation, one.generation),
                predecessor=one.predecessor,
                predecessor_label=gen_names.get(one.predecessor, one.predecessor),
                predecessor_signatures=len(one.before),
                signatures=len(one.now),
                recurring=len(one.recurring),
                rate=one.rate,
                new=one.new,
                items=[
                    RecurrenceItemOut(
                        keys=list(sig),
                        labels=named(sig),
                        before=one.before[sig],
                        now=one.now[sig],
                        drill=query.drill(
                            built,
                            query.Cell(
                                dict(zip(levels, (one.generation, *sig), strict=True)),
                                None,
                                None,
                                None,
                                one.now[sig],
                                0,
                                None,
                                None,
                                None,
                            ),
                            by=(),
                            ask=ask,
                        ),
                    )
                    for sig in sorted(one.recurring, key=lambda s: -one.now[s])[:room]
                ],
            )
            for one in shown_pairs
        ],
        pairs_total=len(found),
        rate=overall(found),
        no_predecessor=lonely,
        signatures=[
            RecurrenceSignatureOut(
                keys=list(sig), labels=named(sig), pairs=len(gens), generations=gens
            )
            for sig, gens in top[: COMPACT_SIGNATURES if compact else SIGNATURES]
        ],
    )


def _names(db: Session, dim: spec_module.Dim, keys: set[str]) -> dict[str, str]:
    wanted = sorted(keys)[: query.MAX_LABELS]
    found = axes.labels(db, dim.axis, wanted) if wanted else {}
    return {key: found.get(key, key) for key in wanted}
