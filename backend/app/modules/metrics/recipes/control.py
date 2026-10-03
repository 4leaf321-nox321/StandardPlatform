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
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

import numpy as np
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import common, registry
from app.modules.metrics.recipes.schemas import (
    ControlChartOut,
    ControlOut,
    ControlPointOut,
    ControlRuleOut,
)
from app.modules.metrics.schemas import DrillOut
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
#: 창의 길이를 말할 때의 단위 — 「3개월」 · 「2분기」.
SPAN = {"day": "일", "week": "주", "month": "개월", "quarter": "분기", "year": "년"}


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


@dataclass
class _Point:
    when: date
    count: int
    exposure: float
    closed: bool
    baseline: bool


def default_axis(built: spec_module.Built) -> Literal["period", "cohort"]:
    """분모가 코호트와 짝이면 코호트 축(출고 K 기간 안 비율), 아니면 기간 축."""
    den = built.spec.denominator
    paired = den is not None and den.time == "cohort"
    if built.cohort is not None and built.time is not None and paired:
        return "cohort"
    return "period" if built.time is not None else "cohort"


def run(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    *,
    axis: Literal["period", "cohort"] | None = None,
    window: int = 3,
    split: str | None = None,
    baseline_to: date | None = None,
    compact: bool = False,
) -> ControlOut:
    reason = available(built)
    if reason is not None:
        raise common.refuse(25, reason)
    axis = axis or default_axis(built)
    if axis == "cohort" and (built.cohort is None or built.time is None):
        raise common.refuse(
            26,
            "코호트 관리도는 코호트 칸(생산 · 판매일)과 시간 칸(접수일)이 함께 있어야 "
            "「출고 K 기간 안」 을 셉니다 — axis=period 로 봅니다.",
        )
    if axis == "period" and built.time is None:
        raise common.refuse(26, "시간 칸이 없는 지표입니다 — axis=cohort 로 봅니다.")
    axis_spec = built.cohort if axis == "cohort" else built.time
    assert axis_spec is not None
    grain = axis_spec.grain
    den_in = built.spec.denominator
    uses_den = den_in is not None and den_in.time == axis
    target = common.dim_of(built, split) if split is not None else None
    if target is not None and uses_den and den_in is not None and split not in den_in.on:
        raise common.refuse(
            26,
            f"「{target.axis.label}」 로 나누면 분모도 그 기준으로 나뉘어야 합니다 — 분모 짝"
            f"(on)에 없습니다. 거르기(d.{split}=값)로 하나씩 보거나, 분모 정의의 짝에 그 "
            "기준을 넣습니다.",
        )
    dims = [split] if split is not None else []
    if axis == "cohort":
        ask = replace(ask, dims=dims, by=("cohort", "age"), age_from=0, age_to=window)
    else:
        ask = replace(ask, dims=dims, by=("period",))
    common.require_exact_counts(built, ask)
    frame = query.frame(db, user, metric, built, ask, with_denominator=uses_den)
    common.require_whole(frame)
    den = frame.denominator if uses_den else None
    caveats = common.Caveats()
    excluded = {"missing_denominator": 0, "open": 0}

    grouped: dict[str | None, dict[date, int]] = {}
    for cell in frame.cells:
        when = cell.cohort if axis == "cohort" else cell.period
        if when is None:
            continue
        key = cell.dims.get(split) if split is not None else None
        bucket = grouped.setdefault(key, {})
        bucket[when] = bucket.get(when, 0) + cell.count
    keys = sorted(grouped, key=lambda one: -sum(grouped[one].values()))
    limit = COMPACT_CHARTS if compact else MAX_CHARTS
    other_groups = max(0, len(keys) - limit)
    keys = keys[:limit]
    every = sorted({when for bucket in grouped.values() for when in bucket})
    timeline = (
        query.dense(every[0], axes.next_period(every[-1], grain), grain) if every else []
    )
    labels = query.labels_for(db, built, [split], frame.cells) if split is not None else {}

    charts: list[ControlChartOut] = []
    few = False
    widened: list[float] = []
    for key in keys:
        bucket = grouped[key]
        points = _points(
            bucket,
            timeline,
            key,
            split,
            axis,
            grain,
            window,
            frame,
            den,
            baseline_to,
            excluded,
        )
        found = chart(
            [one.count for one in points],
            [one.exposure for one in points],
            use=[one.closed for one in points],
            baseline=[one.baseline for one in points],
        )
        baseline_points = sum(1 for one in points if one.baseline)
        if baseline_points < FEW_SUBGROUPS:
            few = True
        if found.sigma_z_raw is not None and found.sigma_z_raw > 1.5:
            widened.append(found.sigma_z_raw)
        per = den.per if den is not None else 1.0
        rows: list[ControlPointOut] = []
        for index, point in enumerate(points):
            rate = point.count / point.exposure * per if point.exposure > 0 else None
            low, high = found.lcl[index], found.ucl[index]
            rows.append(
                ControlPointOut(
                    when=point.when.isoformat(),
                    label=axes.period_label(point.when.isoformat(), grain),
                    count=point.count,
                    exposure=point.exposure if den is not None else None,
                    rate=rate,
                    lcl=low * per if low is not None else None,
                    ucl=high * per if high is not None else None,
                    z=found.z[index],
                    closed=point.closed,
                    baseline=point.baseline,
                    signals=found.signals[index],
                    drill=_drill(built, ask, axis, grain, window, split, key, point),
                )
            )
        if compact:
            tail = len(rows) - COMPACT_POINTS
            rows = [one for index, one in enumerate(rows) if index >= tail or one.signals]
        charts.append(
            ControlChartOut(
                key=key,
                label=query.label_of(labels, split, key) if split is not None else "전체",
                center=found.center * per if found.center is not None else None,
                sigma_z=found.sigma_z,
                sigma_z_raw=found.sigma_z_raw,
                subgroups=sum(1 for one in points if one.closed),
                baseline_points=baseline_points,
                signals=sum(1 for one in found.signals if one),
                total=sum(bucket.values()),
                points=rows,
            )
        )
    _caveats(caveats, excluded, den, axis, grain, window, few, widened, other_groups)
    head = common.header(
        db,
        user,
        metric,
        built,
        frame,
        recipe=NAME,
        method=METHOD,
        params={
            "axis": axis,
            "window": window if axis == "cohort" else None,
            "split": split,
            "baseline_to": baseline_to.isoformat() if baseline_to is not None else None,
            "compact": compact,
        },
        caveats=caveats,
        excluded=excluded,
    )
    return ControlOut(
        **head,
        axis=axis,
        window=window if axis == "cohort" else None,
        kind="u" if den is not None else "c",
        per=den.per if den is not None else 1.0,
        split=split,
        split_label=target.axis.label if target is not None else None,
        baseline_to=baseline_to.isoformat() if baseline_to is not None else None,
        rules=[ControlRuleOut(number=number, label=text) for number, text in RULES.items()],
        charts=charts,
        other_groups=other_groups,
    )


def _points(
    bucket: dict[date, int],
    timeline: list[date],
    key: str | None,
    split: str | None,
    axis: str,
    grain: str,
    window: int,
    frame: query.Frame,
    den: query.Denominator | None,
    baseline_to: date | None,
    excluded: dict[str, int],
) -> list[_Point]:
    """한 차트의 부분군 — 빈 기간은 0 건(대수가 있으면 진짜 관측이다)."""
    out: list[_Point] = []
    for when in timeline:
        count = bucket.get(when, 0)
        exposure = 1.0
        den_closed = True
        if den is not None:
            probe = query.Cell(
                {split: key} if split is not None else {},
                when if axis == "period" else None,
                when if axis == "cohort" else None,
                None,
                count,
                0,
                None,
                None,
                None,
            )
            units = den.lookup(probe, grain)
            if not units:
                excluded["missing_denominator"] += count
                continue
            exposure = float(units)
            den_closed = query.is_closed(when, grain, den.before)
        last = query.advance(when, window - 1, grain) if axis == "cohort" else when
        closed = query.is_closed(last, grain, frame.before) and den_closed
        if not closed:
            excluded["open"] += count
        baseline = closed and (baseline_to is None or when < baseline_to)
        out.append(_Point(when, count, exposure, closed, baseline))
    return out


def _drill(
    built: spec_module.Built,
    ask: query.Ask,
    axis: str,
    grain: str,
    window: int,
    split: str | None,
    key: str | None,
    point: _Point,
) -> DrillOut:
    cell = query.Cell(
        {split: key} if split is not None else {},
        point.when if axis == "period" else None,
        point.when if axis == "cohort" else None,
        None,
        point.count,
        0,
        None,
        None,
        None,
    )
    if axis == "period":
        return query.drill(built, cell, by=("period",), ask=ask)
    # 코호트의 창 — 접수일이 [코호트 시작, K 기간 뒤) 이고, 읽을 때 건 접수 범위와 겹친다.
    end = query.advance(point.when, window, grain)
    start = max(point.when, ask.period_from) if ask.period_from is not None else point.when
    stop = min(end, ask.period_to) if ask.period_to is not None else end
    return query.drill(
        built, cell, by=("cohort",), ask=replace(ask, period_from=start, period_to=stop)
    )


def _caveats(
    caveats: common.Caveats,
    excluded: dict[str, int],
    den: query.Denominator | None,
    axis: str,
    grain: str,
    window: int,
    few: bool,
    widened: list[float],
    other_groups: int,
) -> None:
    if den is None:
        caveats.add(
            "count_chart",
            "분모(대수)가 이 축과 짝지어져 있지 않아 건수 관리도입니다 — 판매 · 생산이 늘면 "
            "건수도 늘어 신호처럼 보입니다.",
        )
    if excluded["missing_denominator"]:
        caveats.add(
            "missing_denominator",
            "대수가 없는 부분군의 기록을 뺐습니다.",
            count=excluded["missing_denominator"],
        )
    if excluded["open"]:
        caveats.add(
            "open_excluded",
            "아직 닫히지 않은 부분군은 그리되 한계 · 규칙에서 뺐습니다 — 더 들어올 수 "
            "있습니다.",
            level="info",
            count=excluded["open"],
        )
    if axis == "cohort":
        caveats.add(
            "window_basis",
            f"코호트마다 출고 뒤 {window}{SPAN.get(grain, grain)} 안의 건수입니다 — 그 "
            "창이 닫힌 코호트만 한계 · 규칙에 씁니다.",
            level="info",
        )
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
    if other_groups:
        caveats.add(
            "other_groups",
            f"건수가 적은 {other_groups}개 값의 차트는 싣지 않았습니다 — 거르기로 봅니다.",
            level="info",
            count=other_groups,
        )
