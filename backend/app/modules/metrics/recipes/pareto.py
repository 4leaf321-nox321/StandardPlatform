"""⑦ 파레토 · 집중도 — 건수가 몇 값에 몰렸나.

기준 하나의 값별 몫을 큰 것부터 쌓아 누적 비중을 내고, 몰린 정도를 수 몇 개로 줄인다.

- **HHI** — 몫의 제곱 합. `1 / HHI` 가 「유효 개수」(몇 개가 고르게 나눠 가진 셈인가).
- **지니** — 0 은 고르게, 1 에 가까울수록 몇 개에 몰림.
- **CR n** — 상위 n 개의 몫.
- **ABC** — 누적 80% 에 처음 닿는 값까지 A, 95% 까지 B, 나머지 C. A 가 「핵심 소수」 다.

여러 값 기준(교체 부품)이면 한 기록이 값마다 한 번씩 들어 **몫은 나온 횟수 기준**이다 —
`basis` 와 주의로 말한다. 한 번도 안 나온 값은 몫이 없어 유효 개수에 들지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import common, registry
from app.modules.metrics.recipes.schemas import (
    ConcentrationOut,
    ParetoItemOut,
    ParetoOut,
    ParetoTrendOut,
)
from app.modules.objects import axes

NAME = "pareto"
LABEL = "파레토 · 집중도"
METHOD = "파레토 · HHI · 지니 · CR v1"
#: 압축 응답(MCP)에 싣는 줄 수.
COMPACT_ITEMS = 10


@dataclass
class Concentration:
    categories: int
    hhi: float
    hhi_norm: float
    effective: float
    gini: float
    cr: dict[int, float]
    vital_few: int
    vital_share: float

    def out(self) -> ConcentrationOut:
        return ConcentrationOut(
            categories=self.categories,
            hhi=self.hhi,
            hhi_norm=self.hhi_norm,
            effective=self.effective,
            gini=self.gini,
            cr1=self.cr[1],
            cr3=self.cr[3],
            cr5=self.cr[5],
            cr10=self.cr[10],
            vital_few=self.vital_few,
            vital_share=self.vital_share,
        )


def abc(shares_desc: Sequence[float]) -> list[str]:
    """큰 것부터의 몫 → 등급. 누적이 80% 에 **처음 닿는** 값까지 A(그 값 포함), 95% 까지 B."""
    out: list[str] = []
    cumulative = 0.0
    stage = "A"
    for share in shares_desc:
        out.append(stage)
        cumulative += share
        if stage == "A" and cumulative >= 0.8 - 1e-12:
            stage = "B"
        elif stage == "B" and cumulative >= 0.95 - 1e-12:
            stage = "C"
    return out


def concentration(values: Sequence[float]) -> Concentration | None:
    """값별 크기 → 집중도. 0 · 음수는 안 센다(나온 적 없는 값). 남는 것이 없으면 None."""
    kept = np.asarray([one for one in values if one > 0], dtype=np.float64)
    if kept.size == 0:
        return None
    desc = np.sort(kept)[::-1]
    total = float(desc.sum())
    shares = desc / total
    k = int(desc.size)
    hhi = float((shares**2).sum())
    hhi_norm = (hhi - 1 / k) / (1 - 1 / k) if k > 1 else 1.0
    asc = desc[::-1]
    ranks = np.arange(1, k + 1, dtype=np.float64)
    gini = float(2 * (ranks * asc).sum() / (k * total) - (k + 1) / k) if k > 1 else 0.0
    cumulative = np.cumsum(shares)
    cr = {n: float(cumulative[min(n, k) - 1]) for n in (1, 3, 5, 10)}
    vital = abc(shares.tolist()).count("A")
    return Concentration(k, hhi, hhi_norm, 1 / hhi, max(0.0, gini), cr, vital, vital / k)


def available(built: spec_module.Built) -> str | None:
    if not built.dims:
        return "기준이 없는 지표입니다 — 파레토는 기준 하나의 값별 몫을 봅니다."
    if built.spec.measure not in ("count", "sum"):
        return (
            "건수 · 합계 지표에서만 됩니다 — 평균 · 최솟값 · 최댓값은 더할 수 없어 몫이 "
            "없습니다."
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
    dim: str,
    top: int = 30,
    include_empty: bool = False,
    by_period: bool = False,
    compact: bool = False,
) -> ParetoOut:
    reason = available(built)
    if reason is not None:
        raise common.refuse(22, reason)
    target = common.dim_of(built, dim)
    ask = replace(ask, dims=[dim], by=())
    common.require_exact_counts(built, ask)
    frame = query.frame(db, user, metric, built, ask)
    common.require_whole(frame)
    caveats = common.Caveats()
    measure = built.spec.measure

    rows = [(cell, float(cell.measure(measure) or 0.0)) for cell in frame.cells]
    empties = [row for row in rows if row[0].dims.get(dim) is None]
    present = [row for row in rows if row[0].dims.get(dim) is not None or include_empty]
    labels = query.labels_for(db, built, [dim], [cell for cell, _ in present])
    present.sort(key=lambda row: (-row[1], query.label_of(labels, dim, row[0].dims.get(dim))))
    total = sum(value for _, value in present)
    found = concentration([value for _, value in present])
    shares = [value / total if total else 0.0 for _, value in present]
    classes = abc(shares)
    items: list[ParetoItemOut] = []
    cumulative = 0.0
    limit = min(top, COMPACT_ITEMS) if compact else top
    for (cell, value), share, cls in zip(present, shares, classes, strict=True):
        cumulative += share
        if len(items) >= limit:
            continue
        ratio = (
            frame.denominator.ratio(value, cell, built.grain)
            if frame.denominator is not None
            else None
        )
        items.append(
            ParetoItemOut(
                key=cell.dims.get(dim),
                label=query.label_of(labels, dim, cell.dims.get(dim)),
                value=value,
                count=cell.count,
                share=share,
                cumulative=cumulative,
                cls=cls,  # type: ignore[arg-type]
                ratio=ratio,
                drill=query.drill(built, cell, by=(), ask=ask),
            )
        )
    shown = len(items)
    other_value = sum(value for _, value in present[shown:])
    if target.axis.multi:
        caveats.add(
            "overlap_basis",
            "한 기록이 여러 값을 가질 수 있어 몫은 기록이 아니라 그 값이 나온 횟수 "
            "기준입니다.",
            level="info",
        )
    empty_count = sum(cell.count for cell, _ in empties)
    empty_value = sum(value for _, value in empties)
    if empties and not include_empty:
        caveats.add(
            "empty_excluded",
            f"값이 빈 기록 {empty_count:,}건은 몫에서 뺐습니다.",
            level="info",
            count=empty_count,
        )
    if found is not None:
        caveats.add(
            "observed_only",
            "본 값만 셉니다 — 한 번도 안 나온 값은 몫이 없어 유효 개수 · 지니에 들지 "
            "않습니다.",
            level="info",
        )
    trend = (
        _trend(db, user, metric, built, ask, dim, frame, caveats, include_empty)
        if by_period and not compact
        else []
    )
    head = common.header(
        db,
        user,
        metric,
        built,
        frame,
        recipe=NAME,
        method=METHOD,
        params={
            "dim": dim,
            "top": top,
            "include_empty": include_empty,
            "by_period": by_period,
            "compact": compact,
        },
        caveats=caveats,
        excluded={"empty": 0 if include_empty else empty_count},
    )
    return ParetoOut(
        **head,
        dim=dim,
        dim_label=target.axis.label,
        basis="occurrences" if target.axis.multi else "records",
        total=total,
        empty_count=empty_count,
        empty_value=empty_value,
        items=items,
        other_categories=max(0, len(present) - shown),
        other_value=other_value,
        concentration=found.out() if found is not None else None,
        trend=trend,
    )


def _trend(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    dim: str,
    frame: query.Frame,
    caveats: common.Caveats,
    include_empty: bool,
) -> list[ParetoTrendOut]:
    """기간마다의 집중도 — 몰림이 커지고 있나. 셀이 상한을 넘으면 건너뛰고 그렇게 적는다."""
    if built.time is None:
        caveats.add("no_time", "시간 칸이 없는 지표라 기간별 추이는 없습니다.", level="info")
        return []
    cells, truncated = query.read(db, user, metric, replace(ask, dims=[dim], by=("period",)))
    if truncated:
        caveats.add(
            "trend_truncated",
            "기간별 추이는 셀이 상한을 넘어 건너뛰었습니다 — 기간을 좁히면 나옵니다.",
            level="info",
        )
        return []
    grain = built.time.grain
    by_period: dict[date, list[float]] = {}
    for cell in cells:
        if cell.period is None or (cell.dims.get(dim) is None and not include_empty):
            continue
        by_period.setdefault(cell.period, []).append(
            float(cell.measure(built.spec.measure) or 0)
        )
    out: list[ParetoTrendOut] = []
    for when in sorted(by_period):
        values = by_period[when]
        found = concentration(values)
        if found is None:
            continue
        out.append(
            ParetoTrendOut(
                period=when.isoformat(),
                label=axes.period_label(when.isoformat(), grain),
                total=float(sum(values)),
                hhi=found.hhi,
                effective=found.effective,
                gini=found.gini,
                top_share=found.cr[1],
                closed=query.is_closed(when, grain, frame.before),
            )
        )
    return out
