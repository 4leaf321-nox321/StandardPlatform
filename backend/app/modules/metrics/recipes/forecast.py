"""클레임 예측 — 이미 판 물량에서 앞으로 몇 건(얼마)이 더 들어오나(ADR 0021).

## 무엇으로

수명 분석과 **같은 맞춤**(판매월 코호트 · 판매 대수 분모 · 표준 / 결함 와이블에서 고름)을
쓴다. 수명 우도는 판매일이 달 안에 고르다고 보고 경과 a 의 고장 확률을
π_a = p (G(a) - G(a-1)), G(a) = ∫_a^{a+1} F(v) dv 로 둔다 — 예측도 같은 π 다.

## 예측

판매월 코호트 c(대수 N_c, 닫힌 마지막 경과 h_c)마다 **아직 안 본 경과** a > h_c 의 예상 건수는
N_c π_a — 처음의 N_c 대 중 그 경과에 고장 나는 수의 기댓값이다(살아남은 것으로 조건 짓지
않는다 — π 가 처음 대수 기준이다). 달력 기간 t 의 예상 = Σ_c N_c π_{t - c}. 보증 기간 W 를 주면
경과 W 부터는 세지 않고(관측도 W 앞까지로 자른다), 「보증 끝까지 남은 총량」 도 낸다.

판매가 아직 덜 들어온 달의 코호트도 넣는다 — 이미 판 것이다(주의로 말한다). 앞으로 팔 것은
넣지 않는다.

## 구간

맞춤의 모수 분포(헤세 행렬의 역)에서 모수를 2,000번 뽑아 뽑을 때마다 예측하고, 건수의
우연(포아송)을 얹어 80 · 95% 구간을 낸다. 씨앗을 고정한다 — 같은 물음에 같은 답.

## 되짚어 보기

B 기간 전까지만 관측한 것처럼 다시 맞춰(분자 · 분모의 닫힘을 그 날로 당김) 그 뒤 B 기간을
예측하고, 실제(그때 이미 판 코호트의 건수)와 견준다 — 이 예측을 얼마나 믿을지의 근거다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import numpy as np
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import common, life, registry
from app.modules.metrics.recipes._numeric import Vector
from app.modules.metrics.recipes.schemas import (
    ForecastBacktestOut,
    ForecastOut,
    ForecastPointOut,
    ForecastTotalOut,
)
from app.modules.objects import axes

NAME = "forecast"
LABEL = "클레임 예측"
METHOD = (
    "수명 모형(표준 · 결함 와이블, 판매월 안 고른 판매)의 경과별 확률 x 판매 대수 · 모수 "
    "2,000번 뽑기 + 포아송 · 되짚어 보기 v1"
)
HORIZON = 12
BACKTEST = 6
HISTORY = 24
DRAWS = 2000
SEED = 20261005


# --- 순수 함수 -----------------------------------------------------------------------


@dataclass
class Sold:
    """판매 코호트 하나 — 달력 위의 자리(기간 번호) · 대수 · 닫힌 마지막 경과(-1 이면 없음)."""

    index: int
    units: float
    observed: int


def probabilities(beta: float, eta: float, p: float, ages: int) -> Vector:
    """경과 0 .. ages-1 의 고장 확률 π_a = p (G(a) - G(a-1)) — 수명 우도와 같은 식."""
    if ages <= 0:
        return np.zeros(0)
    mass = life.interval_mass(np.arange(ages, dtype=np.float64), beta, eta)
    previous = np.concatenate(([0.0], mass[:-1]))
    return p * (mass - previous)


def schedule(
    sold: list[Sold], *, first: int, periods: int, warranty: int | None
) -> tuple[np.ndarray, np.ndarray]:
    """(경과, 대수) 행렬 — 코호트 x 달력 기간. 셀 경과가 이미 본 것 · 보증 밖이면 경과 -1."""
    ages = np.full((len(sold), periods), -1, dtype=np.int64)
    for row, one in enumerate(sold):
        for t in range(periods):
            age = first + t - one.index
            if age <= one.observed or age < 0:
                continue
            if warranty is not None and age >= warranty:
                continue
            ages[row, t] = age
    units = np.asarray([one.units for one in sold], dtype=np.float64)
    return ages, units


def expected(ages: np.ndarray, units: np.ndarray, pi: Vector) -> Vector:
    """달력 기간마다 예상 건수 = Σ_c N_c π_{경과}."""
    if ages.size == 0:
        return np.zeros(ages.shape[1] if ages.ndim == 2 else 0)
    safe = np.where(ages >= 0, ages, 0)
    picked = np.where(ages >= 0, pi[np.minimum(safe, pi.size - 1)], 0.0)
    return (units[:, None] * picked).sum(axis=0)


@dataclass
class Band:
    mean: Vector
    low: Vector
    high: Vector
    low80: Vector
    high80: Vector
    total: tuple[float, float, float]
    """(평균, 95% 아래, 95% 위) — 기간을 다 더한 것."""


def simulate(
    fit_: life.Fit,
    ages: np.ndarray,
    units: np.ndarray,
    *,
    draws: int = DRAWS,
    seed: int = SEED,
) -> Band:
    """모수를 뽑아(구간이 있으면) 예측하고 포아송을 얹는다. 구간이 없으면 모수는 고정."""
    rng = np.random.default_rng(seed)
    top = int(ages.max()) + 1 if ages.size and ages.max() >= 0 else 1
    beta, eta, p = life._unpack(fit_.theta, fit_.model)
    center = expected(ages, units, probabilities(beta, eta, p, top))
    if fit_.covariance is not None:
        thetas = rng.multivariate_normal(fit_.theta, fit_.covariance, size=draws)
    else:
        thetas = np.repeat(fit_.theta[None, :], draws, axis=0)
    means = np.empty((draws, center.size))
    for i, theta in enumerate(thetas):
        b, e, q = life._unpack(theta, fit_.model)
        means[i] = expected(ages, units, probabilities(b, e, q, top))
    counts = rng.poisson(np.maximum(means, 0.0))
    totals = counts.sum(axis=1)
    return Band(
        mean=center,
        low=np.percentile(counts, 2.5, axis=0),
        high=np.percentile(counts, 97.5, axis=0),
        low80=np.percentile(counts, 10, axis=0),
        high80=np.percentile(counts, 90, axis=0),
        total=(
            float(center.sum()),
            float(np.percentile(totals, 2.5)),
            float(np.percentile(totals, 97.5)),
        ),
    )


# --- 셀 어댑터 -----------------------------------------------------------------------


def available(built: spec_module.Built) -> str | None:
    return life.available(built)


registry.register(registry.Recipe(NAME, LABEL, available))


@dataclass
class _Calendar:
    origin: date
    grain: str
    _seen: dict[date, int] = field(default_factory=dict)

    def index(self, when: date) -> int:
        if when in self._seen:
            return self._seen[when]
        self._seen[when] = self._count(when)
        return self._seen[when]

    def _count(self, when: date) -> int:
        count = 0
        current = self.origin
        step = 1 if when >= current else -1
        while current != when and abs(count) < 100_000:
            current = query.advance(current, step, self.grain)
            count += step
        return count

    def at(self, index: int) -> date:
        return query.advance(self.origin, index, self.grain)

    def label(self, index: int) -> str:
        return axes.period_label(self.at(index).isoformat(), self.grain)


def run(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    *,
    horizon: int = HORIZON,
    warranty: int | None = None,
    model: Literal["auto", "weibull", "defective"] = "auto",
    basis: Literal["records", "first_visits"] = "records",
    cost: float | None = None,
    backtest: int = BACKTEST,
) -> ForecastOut:
    reason = available(built)
    if reason is not None:
        raise common.refuse(38, reason)
    assert built.cohort is not None
    grain = built.cohort.grain
    got = life.prepare(
        db, user, metric, built, ask, model=model, max_age=warranty, basis=basis
    )
    chosen = got.found.chosen
    if chosen is None:
        failures = int(got.found.data.total_failures) if got.found.data is not None else 0
        raise common.refuse(
            38,
            f"맞출 건수가 모자라 예측하지 않습니다(닫힌 관측 안 {failures}건) — 수명 분석이 "
            "와이블을 맞출 수 있어야 예측합니다.",
        )
    caveats = got.caveats
    sold = _sold(got.frame, grain, warranty)
    if not sold:
        raise common.refuse(38, "판매 대수가 있는 코호트가 없어 예측하지 않습니다.")
    calendar = _Calendar(min(one[0] for one in sold), grain)
    before = got.frame.before
    first_open = _first_open(calendar, before)
    cohorts = [Sold(calendar.index(start), units, observed) for start, units, observed in sold]
    ages, units = schedule(cohorts, first=first_open, periods=horizon, warranty=warranty)
    band = simulate(chosen, ages, units)
    points = [
        ForecastPointOut(
            period=calendar.at(first_open + t).isoformat(),
            label=calendar.label(first_open + t),
            expected=float(band.mean[t]),
            low=float(band.low[t]),
            high=float(band.high[t]),
            low80=float(band.low80[t]),
            high80=float(band.high80[t]),
            actual=None,
        )
        for t in range(horizon)
    ]
    remaining: ForecastTotalOut | None = None
    if warranty is not None:
        last = max(one.index for one in cohorts) + warranty
        span = max(0, last - first_open)
        tail_ages, tail_units = schedule(
            cohorts, first=first_open, periods=span, warranty=warranty
        )
        tail = simulate(chosen, tail_ages, tail_units)
        remaining = _total(tail.total, cost)
    observed_max = max((one.observed for one in cohorts), default=-1)
    reach = int(ages.max()) if ages.size else -1
    if reach > max(observed_max, 0) and chosen.model == "weibull":
        caveats.add(
            "extrapolated",
            f"예측이 관측한 가장 긴 경과({observed_max + 1}기간) 너머까지 갑니다 — 그 부분은 "
            "모형을 늘려 읽은 것입니다.",
        )
    open_sales = sum(
        1
        for start, _, _ in sold
        if got.frame.denominator is not None
        and got.frame.denominator.before is not None
        and not query.is_closed(start, grain, got.frame.denominator.before)
    )
    if open_sales:
        caveats.add(
            "open_sales",
            f"판매가 아직 덜 들어온 달이 {open_sales}개 있습니다 — 그 달들의 예측은 지금까지 "
            "들어온 대수로 셌습니다(늘 수 있습니다).",
            level="info",
            count=open_sales,
        )
    caveats.add(
        "already_sold",
        "이미 판 물량의 예측입니다 — 앞으로 팔 것과 리콜 · 캠페인 같은 일회성 급증은 들어 "
        "있지 않습니다.",
        level="info",
    )
    if chosen.covariance is None:
        caveats.add(
            "fixed_parameters",
            "맞춤의 모수 구간이 서지 않아 모수는 고정하고 건수의 우연만 넣었습니다 — 구간이 "
            "실제보다 좁습니다.",
        )
    checked = (
        _backtest(db, user, metric, built, ask, got, calendar, first_open, backtest, warranty)
        if backtest > 0
        else None
    )
    if checked is not None and not checked.within:
        caveats.add(
            "backtest_miss",
            f"되짚어 보기에서 실제({checked.actual:.0f}건)가 예측의 95% 구간 밖이었습니다 — "
            "예측을 조심해서 읽습니다.",
        )
    history = _history(got, calendar, first_open, warranty)
    head = common.header(
        db,
        user,
        metric,
        built,
        got.frame,
        recipe=NAME,
        method=METHOD,
        params={
            "horizon": horizon,
            "warranty": warranty,
            "model": model,
            "basis": basis,
            "cost": cost,
            "backtest": backtest,
        },
        caveats=caveats,
        excluded=got.excluded,
    )
    beta, eta, p = life._unpack(chosen.theta, chosen.model)
    return ForecastOut(
        **head,
        model=chosen.model,
        beta=beta,
        eta=eta,
        p=p,
        horizon=horizon,
        warranty=warranty,
        basis=basis,
        cost=cost,
        units=float(units.sum()),
        cohorts=len(cohorts),
        start=calendar.label(first_open),
        total=_total(band.total, cost),
        remaining=remaining,
        points=points,
        history=history,
        backtest=checked,
    )


def _total(total: tuple[float, float, float], cost: float | None) -> ForecastTotalOut:
    mean, low, high = total
    return ForecastTotalOut(
        expected=mean,
        low=low,
        high=high,
        cost=mean * cost if cost is not None else None,
        cost_low=low * cost if cost is not None else None,
        cost_high=high * cost if cost is not None else None,
    )


def _sold(
    frame: query.Frame, grain: str, warranty: int | None
) -> list[tuple[date, float, int]]:
    """(판매월, 대수, 닫힌 마지막 경과) — 분모의 코호트 전부(이미 판 것). 기록이 없는 달도."""
    den = frame.denominator
    if den is None or den.time != "cohort":
        return []
    out: list[tuple[date, float, int]] = []
    for (_, when), units in den.values.items():
        if when is None or not units:
            continue
        observed = -1
        while query.is_closed(query.advance(when, observed + 1, grain), grain, frame.before):
            observed += 1
            if observed > 2400:  # pragma: no cover - 지킴
                break
        if warranty is not None:
            observed = min(observed, warranty - 1)
        out.append((when, float(units), observed))
    return sorted(out)


def _first_open(calendar: _Calendar, before: date | None) -> int:
    """닫히지 않은 첫 달력 기간 — 예측의 첫 기간."""
    if before is None:
        return 0
    index = 0
    while query.is_closed(calendar.at(index), calendar.grain, before) and index < 100_000:
        index += 1
    return index


def _actual(
    prepared: life.Prepared,
    calendar: _Calendar,
    *,
    first: int,
    periods: int,
    sold_before: int | None,
    warranty: int | None,
) -> Vector:
    """달력 기간마다 실제 건수 — 판매월이 `sold_before` 앞인 코호트만(그때 이미 판 것)."""
    out = np.zeros(periods)
    for cell in prepared.frame.cells:
        if cell.cohort is None or cell.age is None or cell.age < 0:
            continue
        if warranty is not None and cell.age >= warranty:
            continue
        start = calendar.index(cell.cohort)
        if sold_before is not None and start >= sold_before:
            continue
        t = start + cell.age - first
        if 0 <= t < periods:
            out[t] += cell.count
    return out


def _history(
    prepared: life.Prepared, calendar: _Calendar, first_open: int, warranty: int | None
) -> list[ForecastPointOut]:
    """예측 앞의 닫힌 기간의 실제 건수(그림의 앞부분)."""
    start = max(0, first_open - HISTORY)
    actual = _actual(
        prepared,
        calendar,
        first=start,
        periods=first_open - start,
        sold_before=None,
        warranty=warranty,
    )
    return [
        ForecastPointOut(
            period=calendar.at(start + t).isoformat(),
            label=calendar.label(start + t),
            expected=None,
            low=None,
            high=None,
            low80=None,
            high80=None,
            actual=float(actual[t]),
        )
        for t in range(first_open - start)
    ]


def _backtest(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    prepared: life.Prepared,
    calendar: _Calendar,
    first_open: int,
    periods: int,
    warranty: int | None,
) -> ForecastBacktestOut | None:
    """`periods` 기간 전까지만 본 것처럼 맞춰 그 뒤를 예측하고 실제와 견준다."""
    back = first_open - periods
    if back <= 0:
        return None
    cutoff = calendar.at(back)
    then = life.prepare(
        db,
        user,
        metric,
        built,
        ask,
        model=prepared.found.chosen.model if prepared.found.chosen else "auto",
        max_age=warranty,
        basis=prepared.found.basis,
        before=cutoff,
    )
    if then.found.chosen is None:
        return None
    assert built.cohort is not None
    sold = [
        Sold(calendar.index(start), units, observed)
        for start, units, observed in _sold(then.frame, built.cohort.grain, warranty)
        if start < cutoff
    ]
    if not sold:
        return None
    ages, units = schedule(sold, first=back, periods=periods, warranty=warranty)
    band = simulate(then.found.chosen, ages, units)
    actual = _actual(
        prepared, calendar, first=back, periods=periods, sold_before=back, warranty=warranty
    )
    total_actual = float(actual.sum())
    _, low, high = band.total
    return ForecastBacktestOut(
        start=calendar.label(back),
        periods=periods,
        predicted=band.total[0],
        low=low,
        high=high,
        actual=total_actual,
        within=low <= total_actual <= high,
        points=[
            ForecastPointOut(
                period=calendar.at(back + t).isoformat(),
                label=calendar.label(back + t),
                expected=float(band.mean[t]),
                low=float(band.low[t]),
                high=float(band.high[t]),
                low80=float(band.low80[t]),
                high80=float(band.high80[t]),
                actual=float(actual[t]),
            )
            for t in range(periods)
        ],
    )
