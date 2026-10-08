"""한 장 요약 — 기준 값 하나에 걸린 기록을 한 번에(ADR 0022 D).

기준 하나의 값 하나(예: 고장 메커니즘 「피로」 · 부품 「힌지」)에 대해:

- 기록 수와 전체 중 몫, 그 기록의 근거(건 보기).
- 기간마다의 건수와 전체(추이) — 시간 칸이 있으면.
- 다른 기준마다 **함께 나온 값** — 그 값의 기록 중 몫과 향상도(전체에서보다 몇 배 자주:
  (n_vb / n_v) / (n_b / N)). 「피로는 어느 부품 · 어느 해석법과 함께 나오나」.

축 객체의 상세 화면이 그 타입을 기준으로 가진 지표를 찾아 이것을 스스로 보인다. 셀에서 세므로
여러 값 기준이 있으면 건수에 겹침이 든다(주의).
"""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import common, registry
from app.modules.metrics.recipes.schemas import (
    ProfileGroupOut,
    ProfileOut,
    ProfilePointOut,
    ProfileValueOut,
)
from app.modules.objects import axes

NAME = "profile"
LABEL = "한 장 요약"
METHOD = "기준 값 하나의 건수 · 기간 추이 · 다른 기준의 함께 나온 값(향상도) v1"
TOP = 10
COMPACT_TOP = 5
MAX_OTHERS = 6
FEW = 10


# --- 순수 함수 -----------------------------------------------------------------------


def lift(together: float, mine: float, theirs: float, total: float) -> float | None:
    """(n_vb / n_v) / (n_b / N) — 1 보다 크면 그 값의 기록에서 더 자주."""
    if mine <= 0 or theirs <= 0 or total <= 0:
        return None
    return (together / mine) / (theirs / total)


# --- 셀 어댑터 -----------------------------------------------------------------------


def available(built: spec_module.Built) -> str | None:
    if built.spec.measure != "count":
        return "건수 지표에서만 됩니다 — 그 값의 기록을 셉니다."
    if not built.dims:
        return "기준이 있는 지표에서만 됩니다 — 기준 값 하나를 요약합니다."
    return None


registry.register(registry.Recipe(NAME, LABEL, available))


def run(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    *,
    dim: str,
    value: str,
    compact: bool = False,
) -> ProfileOut:
    reason = available(built)
    if reason is not None:
        raise common.refuse(48, reason)
    target = common.dim_of(built, dim)
    if dim in ask.filters:
        raise common.refuse(
            48, f"d.{dim} 거르기를 빼고 value 로 그 값을 줍니다(요약할 값이 그것입니다)."
        )
    mine = replace(ask, filters={**ask.filters, dim: value})

    def counts(of: query.Ask, dims: list[str], by: tuple[str, ...] = ()) -> list[query.Cell]:
        frame = query.frame(
            db, user, metric, built, replace(of, dims=dims, by=by), with_denominator=False
        )
        common.require_whole(frame)
        return frame.cells

    total = sum(one.count for one in counts(ask, []))
    count = sum(one.count for one in counts(mine, []))
    caveats = common.Caveats()
    # 전체(몫의 분모)는 기준을 묶지 않고 세므로 요약하는 기준 자신의 겹침도 든다.
    common.overlap_note(built, replace(ask, dims=[]), caveats)
    if count < FEW:
        caveats.add(
            "few",
            f"기록이 {count}건이라 함께 나온 값의 몫 · 향상도가 우연으로 크게 흔들립니다.",
            level="info",
            count=count,
        )
    caveats.add(
        "association",
        "함께 나온 값은 같은 기록에 함께 태그된 것이지 원인이 아닙니다.",
        level="info",
    )

    points: list[ProfilePointOut] = []
    if built.time is not None:
        grain = built.time.grain
        run_ = query.current_run(db, metric)
        before = query.closed_before(run_, built.spec.settle_days)
        mine_by = {
            one.period: one.count for one in counts(mine, [], ("period",)) if one.period
        }
        all_by = {one.period: one.count for one in counts(ask, [], ("period",)) if one.period}
        dated = sorted(all_by)
        if dated:
            for when in query.dense(dated[0], axes.next_period(dated[-1], grain), grain):
                whole = all_by.get(when, 0)
                points.append(
                    ProfilePointOut(
                        period=when.isoformat(),
                        label=axes.period_label(when.isoformat(), grain),
                        count=mine_by.get(when, 0),
                        total=whole,
                        share=mine_by.get(when, 0) / whole if whole else None,
                        closed=query.is_closed(when, grain, before),
                    )
                )

    related: list[ProfileGroupOut] = []
    room = COMPACT_TOP if compact else TOP
    for other in [one for one in built.dims if one.name != dim and one.grain is None][
        :MAX_OTHERS
    ]:
        together = counts(mine, [other.name])
        overall = {one.dims.get(other.name): one.count for one in counts(ask, [other.name])}
        ranked = sorted(together, key=lambda one: -one.count)
        shown = ranked[:room]
        labels = query.labels_for(db, built, [other.name], shown)
        related.append(
            ProfileGroupOut(
                dim=other.name,
                label=other.axis.label,
                values=[
                    ProfileValueOut(
                        key=one.dims.get(other.name),
                        label=query.label_of(labels, other.name, one.dims.get(other.name)),
                        count=one.count,
                        share=one.count / count if count else None,
                        lift=lift(
                            one.count, count, overall.get(one.dims.get(other.name), 0), total
                        ),
                        drill=query.drill(built, one, by=(), ask=mine),
                    )
                    for one in shown
                ],
                others=max(0, len(ranked) - room),
            )
        )
    # 요약할 값은 요청이 준 것 — 못 보는 객체면 이름을 풀지 않는다.
    names = common.given_labels(db, user, built, dim, [value])
    head = common.header(
        db,
        user,
        metric,
        built,
        query.Frame([], False, None, query.current_run(db, metric), None),
        recipe=NAME,
        method=METHOD,
        params={"dim": dim, "value": value, "compact": compact},
        caveats=caveats,
        excluded={},
    )
    return ProfileOut(
        **head,
        dim=dim,
        dim_label=target.axis.label,
        value=value,
        value_label=query.label_of(names, dim, value),
        count=count,
        total=total,
        share=count / total if total else None,
        drill=query.drill(
            built,
            query.Cell({dim: value}, None, None, None, count, 0, None, None, None),
            by=(),
            ask=ask,
        ),
        points=points,
        related=related,
    )
