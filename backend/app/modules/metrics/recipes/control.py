"""③ 관리도 — 기간마다(또는 생산월 코호트마다)의 비율이 평소의 흔들림 안에 있나(ADR 0014).

## u-관리도와 라니 보정

부분군 i 의 건수 c_i, 대수 n_i, 비율 u_i = c_i / n_i. 중심선 ū = Σc / Σn(기준 구간의 점으로),
포아송이면 σ_i = √(ū / n_i) 이고 한계는 ū ± 3σ_i 다.

대수가 수천 · 수만이면 σ_i 가 아주 작아진다 — 달마다 실제로 있는 흔들림(부품 로트 · 계절 ·
판매 구성)이 포아송보다 훨씬 커서 **거의 모든 점이 한계 밖**이 된다. 라니(2002)는 표준 점수
z_i = (u_i - ū) / σ_i 의 흔들림을 이동 범위로 잰다: σz = 평균 |z_i - z_{i-1}| / 1.128. 한계는
ū ± 3 σz σ_i. σz 가 1 보다 작게 나오면(우연) 한계를 줄이지 않는다 — 포아송보다 좁은 한계는
거짓 신호만 늘린다.

분모가 없으면 n_i = 1 인 건수(c) 관리도다 — 판매가 늘면 건수도 늘어 신호처럼 보이므로 그렇게
말한다.

## 코호트 축

생산월(판매월) 코호트마다 **출고 K 기간 안의 건수 / 그 달의 대수** — 창이 닫힌 코호트만 본다
(마지막 경과 K-1 이 닫혔나). 같은 창으로 재야 늦게 만든 달이 덜 들어온 것을 품질로 읽지 않는다.

## 넬슨 규칙(1 · 2 · 3 · 5)

1. 한계 밖 한 점(|z| > 3).
2. 중심선 한쪽에 아홉 점 연속.
3. 여섯 점 연속 오르거나 내림(비율로).
5. 세 점 중 두 점이 2σ 밖, 같은 쪽.

규칙은 닫힌 점에만 건다. 걸린 점은 그 무늬를 **마친** 점이다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import common, registry, series
from app.modules.metrics.recipes.schemas import (
    ControlChartOut,
    ControlOut,
    ControlPointOut,
    ControlRuleOut,
)
from app.modules.objects import axes

NAME = "control"
LABEL = "관리도"
METHOD = "u-관리도 · 라니 보정(σz ≥ 1) · 넬슨 1·2·3·5 v1"
RULES = {
    1: "한계 밖 한 점",
    2: "중심선 한쪽에 아홉 점 연속",
    3: "여섯 점 연속 오르거나 내림",
    5: "세 점 중 두 점이 2σ 밖(같은 쪽)",
}
#: 이동 범위 → 표준편차(d2, 두 점).
D2 = 1.128
#: 한계를 잡는 데 보통 바라는 부분군 수 — 이보다 적으면 한계가 흔들린다고 말한다.
FEW_SUBGROUPS = 12
MAX_CHARTS = 12
COMPACT_CHARTS = 6
COMPACT_POINTS = 12


# --- 순수 함수 -----------------------------------------------------------------------


@dataclass
class Chart:
    center: float | None
    sigma_z: float | None
    sigma_z_raw: float | None
    lcl: list[float | None]
    ucl: list[float | None]
    z: list[float | None]
    signals: list[list[int]]


def nelson(z: Sequence[float | None], values: Sequence[float | None]) -> list[list[int]]:
    """점마다 걸린 규칙. `z` 가 None 인 점은 줄에서 빠진다(열린 기간 · 분모 없음)."""
    out: list[list[int]] = [[] for _ in z]
    order = [i for i, one in enumerate(z) if one is not None and values[i] is not None]
    run_side = run_length = 0
    trend = 0
    trend_length = 1
    previous: float | None = None
    window: list[float] = []
    for i in order:
        score = z[i]
        value = values[i]
        assert score is not None and value is not None
        if abs(score) > 3:
            out[i].append(1)
        side = 1 if score > 0 else -1 if score < 0 else 0
        if side != 0 and side == run_side:
            run_length += 1
        else:
            run_side, run_length = side, 1 if side else 0
        if run_length >= 9:
            out[i].append(2)
        if previous is not None:
            step = 1 if value > previous else -1 if value < previous else 0
            if step != 0 and step == trend:
                trend_length += 1
            else:
                trend, trend_length = step, 2 if step else 1
            if trend_length >= 6:
                out[i].append(3)
        previous = value
        window = [*window[-2:], score]
        if len(window) == 3:
            for sign in (1, -1):
                if sign * score > 2 and sum(1 for one in window if sign * one > 2) >= 2:
                    out[i].append(5)
                    break
    return out


def chart(
    counts: Sequence[float],
    exposures: Sequence[float],
    *,
    use: Sequence[bool],
    baseline: Sequence[bool],
    laney: bool = True,
) -> Chart:
    """부분군을 차례로 — `use` 는 규칙을 걸 점(닫힌 점), `baseline` 은 중심선과 σz 를 잡는
    점(`use` 의 부분)."""
    size = len(counts)
    empty = Chart(None, None, None, [None] * size, [None] * size, [None] * size, [])
    empty.signals = [[] for _ in range(size)]
    c = np.asarray(counts, dtype=np.float64)
    n = np.asarray(exposures, dtype=np.float64)
    base = np.asarray(baseline, dtype=bool) & (n > 0)
    if not base.any():
        return empty
    center = float(c[base].sum() / n[base].sum())
    if center <= 0:
        empty.center = 0.0
        return empty
    positive = n > 0
    sigma = np.where(positive, np.sqrt(center / np.where(positive, n, 1.0)), np.nan)
    rate = np.where(positive, c / np.where(positive, n, 1.0), np.nan)
    score = (rate - center) / sigma
    in_base = score[base]
    raw = float(np.mean(np.abs(np.diff(in_base))) / D2) if in_base.size >= 2 else None
    widen = max(1.0, raw) if laney and raw is not None else 1.0
    lcl = np.maximum(center - 3 * widen * sigma, 0.0)
    ucl = center + 3 * widen * sigma
    z = score / widen
    shown: list[float | None] = [
        float(z[i]) if positive[i] and use[i] else None for i in range(size)
    ]
    return Chart(
        center=center,
        sigma_z=widen,
        sigma_z_raw=raw,
        lcl=[float(one) if np.isfinite(one) else None for one in lcl],
        ucl=[float(one) if np.isfinite(one) else None for one in ucl],
        z=[float(z[i]) if positive[i] else None for i in range(size)],
        signals=nelson(shown, [float(one) for one in rate]),
    )


# --- 어댑터 --------------------------------------------------------------------------


def available(built: spec_module.Built) -> str | None:
    if built.spec.measure != "count":
        return "건수 지표에서만 됩니다 — 관리도는 건수 · 비율의 흔들림을 봅니다."
    if built.time is None and built.cohort is None:
        return "시간 칸이나 코호트 칸이 있는 지표에서만 됩니다."
    return None


registry.register(registry.Recipe(NAME, LABEL, available))


def run(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    *,
    axis: series.Axis | None = None,
    window: int = 3,
    split: str | None = None,
    baseline_to: date | None = None,
    compact: bool = False,
) -> ControlOut:
    reason = available(built)
    if reason is not None:
        raise common.refuse(25, reason)
    found = series.read(
        db,
        user,
        metric,
        built,
        ask,
        axis=axis,
        window=window,
        split=split,
        limit=COMPACT_CHARTS if compact else MAX_CHARTS,
    )
    caveats = common.Caveats()
    charts: list[ControlChartOut] = []
    few = False
    widened: list[float] = []
    per = found.per
    for line in found.series:
        groups = line.subgroups
        baseline = [
            one.closed and (baseline_to is None or one.when < baseline_to) for one in groups
        ]
        drawn = chart(
            [one.count for one in groups],
            [one.exposure for one in groups],
            use=[one.closed for one in groups],
            baseline=baseline,
        )
        baseline_points = sum(baseline)
        if baseline_points < FEW_SUBGROUPS:
            few = True
        if drawn.sigma_z_raw is not None and drawn.sigma_z_raw > 1.5:
            widened.append(drawn.sigma_z_raw)
        rows: list[ControlPointOut] = []
        for index, one in enumerate(groups):
            low, high = drawn.lcl[index], drawn.ucl[index]
            rows.append(
                ControlPointOut(
                    when=one.when.isoformat(),
                    label=axes.period_label(one.when.isoformat(), found.grain),
                    count=one.count,
                    exposure=one.exposure if found.den is not None else None,
                    rate=found.rate(one),
                    lcl=low * per if low is not None else None,
                    ucl=high * per if high is not None else None,
                    z=drawn.z[index],
                    closed=one.closed,
                    baseline=baseline[index],
                    signals=drawn.signals[index],
                    drill=series.drill(found, built, line.key, one),
                )
            )
        if compact:
            tail = len(rows) - COMPACT_POINTS
            rows = [one for index, one in enumerate(rows) if index >= tail or one.signals]
        charts.append(
            ControlChartOut(
                key=line.key,
                label=line.label,
                center=drawn.center * per if drawn.center is not None else None,
                sigma_z=drawn.sigma_z,
                sigma_z_raw=drawn.sigma_z_raw,
                subgroups=sum(1 for one in groups if one.closed),
                baseline_points=baseline_points,
                signals=sum(1 for one in drawn.signals if one),
                total=line.total,
                points=rows,
            )
        )
    series.caveats(found, caveats)
    if few:
        caveats.add(
            "few_subgroups",
            f"한계를 잡은 부분군이 {FEW_SUBGROUPS}개보다 적은 차트가 있습니다 — 한계가 "
            "흔들립니다(보통 20개 이상으로 잡습니다).",
        )
    if widened:
        caveats.add(
            "laney_widened",
            f"대수가 커 포아송 한계가 지나치게 좁습니다 — 라니 보정으로 한계를 "
            f"{min(widened):.1f}~{max(widened):.1f}배 넓혔습니다.",
            level="info",
        )
    head = common.header(
        db,
        user,
        metric,
        built,
        found.frame,
        recipe=NAME,
        method=METHOD,
        params={
            "axis": found.axis,
            "window": window if found.axis == "cohort" else None,
            "split": split,
            "baseline_to": baseline_to.isoformat() if baseline_to is not None else None,
            "compact": compact,
        },
        caveats=caveats,
        excluded=found.excluded,
    )
    return ControlOut(
        **head,
        axis=found.axis,
        window=window if found.axis == "cohort" else None,
        kind="u" if found.den is not None else "c",
        per=per,
        split=split,
        split_label=found.split_dim.axis.label if found.split_dim is not None else None,
        baseline_to=baseline_to.isoformat() if baseline_to is not None else None,
        rules=[ControlRuleOut(number=number, label=text) for number, text in RULES.items()],
        charts=charts,
        other_groups=found.other_groups,
    )
