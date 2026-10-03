"""② 수명 — 판매월 코호트의 경과별 인입으로 와이블을 맞추고 B수명을 읽는다(ADR 0014).

## 자료

판매월 코호트 지표(① 모양 — 코호트 칸 + 분모 `time=cohort`) 위. 코호트 c 마다 판매 대수
N_c(분모)와 경과 a 마다의 건수 f[c,a]. 코호트마다 **관측 끝** A_c 는 닫힌 마지막 경과다(계산
시각 - 닫힘 일수) — 그 뒤의 경과는 아직 들어오는 중이라 뺀다. 분모 쪽 달이 안 닫힌
코호트(판매가 덜 들어온 달)도 뺀다.

## 모형

시간의 단위는 지표의 기간 단위(대개 월). 판매일이 그 달 안에 고르다고 보면, 고장 시각의 분포 F
에서 「경과 a 에 들어온다」 의 확률은

    π_a = G(a) - G(a-1),   G(a) = ∫_a^{a+1} F(v) dv   (G(-1) = 0)

이다(경과는 두 달의 달력 차이라 판매일의 위치만큼 번진다). 코호트 c 의 남은 대수는 경과 A_c
까지 안 들어왔다 — 확률 1 - G(A_c). 그래서

    logL = Σ_a D_a ln π_a + Σ_c R_c ln(1 - G(A_c))
    D_a = Σ_{c: A_c ≥ a} f[c,a],   R_c = N_c - Σ_a f[c,a]

적분은 가우스-르장드르 16점, 남은 확률은 생존 함수를 직접 적분해 꼬리의 정밀도를 지킨다.

- **표준 와이블** F(t) = 1 - exp(-(t/η)^β)
- **결함 와이블** F(t) = p · (1 - exp(-(t/η)^β)) — p 는 「결국 고장 나는 비율」.

판매 대수의 몇 %만 기록을 남기는 제품에서 표준 와이블은 평평해지는 곡선을 억지로 따라가며
형상을 1 아래로 끌어내린다 — 「초기 고장」 으로 읽히지만 실제로는 고장 나는 것들이 마모하고
있다. 그래서 둘을 함께 맞추고 LR 검정(경계라 χ²₁ 꼬리의 절반)으로 고른다.

## B수명의 상태

`observed`(비모수 곡선이 그 비율에 닿았다) · `extrapolated`(모형이 관측 밖에서 닿는다) ·
`unreachable`(결함 모형의 p 가 그 비율보다 작다 — **값을 지어내지 않는다**) · `uncertain`(p 의
구간이 그 비율을 걸친다) · `none`(맞춤이 서지 않음).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import _numeric, common, registry
from app.modules.metrics.recipes.schemas import (
    BLifeOut,
    LifeCohortOut,
    LifeCurveOut,
    LifeFitOut,
    LifeOut,
    LifePointOut,
)
from app.modules.objects import axes

NAME = "life"
LABEL = "수명 · B수명"
METHOD = "와이블 최대우도 · 코호트 구간 중도절단 · 표준/결함 v1"
QUANTILES = (0.01, 0.05, 0.10)
#: 기간 단위 하나의 날 수 — B수명을 날로도 말한다.
DAYS = {"day": 1.0, "week": 7.0, "month": 30.4375, "quarter": 91.3125, "year": 365.25}
#: 맞춤에 드는 최소 건수 — 이보다 적으면 비모수 곡선만 낸다.
MIN_FAILURES = 10
MIN_FAILURES_DEFECTIVE = 30
Z95 = 1.959963984540054

Vector = NDArray[np.float64]

_NODES, _WEIGHTS = np.polynomial.legendre.leggauss(16)
_X01: Vector = (_NODES + 1.0) / 2.0
_W01: Vector = _WEIGHTS / 2.0


# --- 순수 함수 -----------------------------------------------------------------------


@dataclass
class LifeData:
    """수명 맞춤의 입력 — 코호트를 경과별로 접은 것."""

    failures: Vector
    """D_a — 관측 끝 안의 경과 a 건수(a = 0..A_max)."""
    at_risk: Vector
    """n_a — 경과 a 에 들어선 대수(그 경과까지 관측된 코호트만)."""
    horizons: Vector
    """코호트마다의 관측 끝."""
    survivors: Vector
    """코호트마다 관측 끝까지 안 들어온 대수."""
    units: float
    cohorts: int

    @property
    def max_age(self) -> int:
        return int(self.failures.size) - 1

    @property
    def total_failures(self) -> float:
        return float(self.failures.sum())


def collapse(
    units: Sequence[float], horizons: Sequence[int], failures: Sequence[Sequence[float]]
) -> LifeData:
    """코호트별(대수 · 관측 끝 · 경과별 건수) → 경과별로 접는다.

    `failures[c][a]` 는 a = 0..끝.
    """
    top = max(horizons) if horizons else -1
    d = np.zeros(top + 1)
    n = np.zeros(top + 1)
    rest: list[float] = []
    for size, end, counts in zip(units, horizons, failures, strict=True):
        f = np.zeros(end + 1)
        given = np.asarray(counts[: end + 1], dtype=np.float64)
        f[: given.size] = given
        d[: end + 1] += f
        before = np.concatenate(([0.0], np.cumsum(f)[:-1]))
        n[: end + 1] += size - before
        rest.append(size - float(f.sum()))
    return LifeData(
        failures=d,
        at_risk=n,
        horizons=np.asarray(horizons, dtype=np.float64),
        survivors=np.asarray(rest, dtype=np.float64),
        units=float(sum(units)),
        cohorts=len(horizons),
    )


def _interval(start: Vector, beta: float, eta: float) -> tuple[Vector, Vector]:
    """구간 [a, a+1] 위의 ∫W 와 ∫S — W 는 와이블의 누적 분포, S = 1 - W."""
    v = start[:, None] + _X01[None, :]
    z = np.power(np.maximum(v, 0.0) / eta, beta)
    return (-np.expm1(-z)) @ _W01, np.exp(-z) @ _W01


def interval_mass(start: Vector, beta: float, eta: float) -> Vector:
    """G(a) = ∫_a^{a+1} W(v) dv — 시험이 닫힌 식과 견준다."""
    return _interval(start, beta, eta)[0]


def log_likelihood(data: LifeData, beta: float, eta: float, p: float = 1.0) -> float:
    ages = np.arange(data.failures.size, dtype=np.float64)
    mass, _ = _interval(ages, beta, eta)
    previous = np.concatenate(([0.0], mass[:-1]))
    pi = np.maximum(p * (mass - previous), 1e-300)
    _, survive = _interval(data.horizons, beta, eta)
    stay = np.maximum((1.0 - p) + p * survive, 1e-300)
    return float((data.failures * np.log(pi)).sum() + (data.survivors * np.log(stay)).sum())


def actuarial(data: LifeData) -> tuple[Vector, Vector, Vector, Vector]:
    """비모수 누적 고장률(보험계리식) — (위험률, F̂, 아래, 위). 구간은 그린우드 분산의
    log(-log) 변환."""
    with np.errstate(divide="ignore", invalid="ignore"):
        hazard = np.where(data.at_risk > 0, data.failures / data.at_risk, 0.0)
        survival = np.cumprod(1.0 - hazard)
        terms = np.where(
            (data.at_risk > data.failures) & (data.at_risk > 0),
            hazard / np.maximum(data.at_risk - data.failures, 1e-300),
            0.0,
        )
        greenwood = np.cumsum(terms)
        log_s = np.log(np.clip(survival, 1e-300, 1.0))
        se = np.sqrt(greenwood) / np.maximum(np.abs(log_s), 1e-300)
        inner = np.log(np.maximum(-log_s, 1e-300))
        low_s = np.exp(-np.exp(inner + Z95 * se))
        high_s = np.exp(-np.exp(inner - Z95 * se))
    observed = 1.0 - survival
    return hazard, observed, 1.0 - high_s, 1.0 - low_s


@dataclass
class Fit:
    model: Literal["weibull", "defective"]
    theta: Vector
    loglik: float
    converged: bool
    covariance: Vector | None
    identifiable: bool
    parameters: int = field(init=False)

    def __post_init__(self) -> None:
        self.parameters = int(self.theta.size)

    @property
    def beta(self) -> float:
        return float(math.exp(self.theta[0]))

    @property
    def eta(self) -> float:
        return float(math.exp(self.theta[1]))

    @property
    def p(self) -> float:
        return _expit(float(self.theta[2])) if self.model == "defective" else 1.0

    def interval(self, index: int) -> tuple[float, float] | None:
        if self.covariance is None:
            return None
        se = math.sqrt(max(float(self.covariance[index, index]), 0.0))
        center = float(self.theta[index])
        low, high = center - Z95 * se, center + Z95 * se
        if index == 2:
            return _expit(low), _expit(high)
        return math.exp(low), math.exp(high)

    @property
    def aic(self) -> float:
        return 2 * self.parameters - 2 * self.loglik


def _expit(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else math.exp(x) / (1.0 + math.exp(x))


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _unpack(theta: Vector, model: str) -> tuple[float, float, float]:
    beta = float(math.exp(min(theta[0], 5.0)))
    eta = float(math.exp(min(theta[1], 30.0)))
    p = _expit(float(theta[2])) if model == "defective" else 1.0
    return beta, eta, p


def _start(data: LifeData, model: str) -> Vector:
    """시작값 — 비모수 F̂ 의 와이블 확률지 회귀(건수로 가중)."""
    _, observed, _, _ = actuarial(data)
    top = float(observed.max()) if observed.size else 0.0
    p0 = min(0.99, max(1.5 * top, 1e-4)) if model == "defective" else 1.0
    target = observed / p0
    ages = np.arange(observed.size, dtype=np.float64) + 0.5
    keep = (target > 0) & (target < 1) & (data.failures > 0)
    beta0, eta0 = 1.0, 2.0 * (data.max_age + 1)
    if keep.sum() >= 2:
        x = np.log(ages[keep])
        y = np.log(-np.log1p(-target[keep]))
        w = data.failures[keep]
        slope, intercept = np.polyfit(x, y, 1, w=np.sqrt(w))
        if np.isfinite(slope) and slope > 0.05:
            beta0 = float(min(slope, 20.0))
            eta0 = float(math.exp(-intercept / beta0))
    eta0 = float(min(max(eta0, 1e-3), 1e9))
    start = [math.log(beta0), math.log(eta0)]
    if model == "defective":
        start.append(_logit(p0))
    return np.asarray(start, dtype=np.float64)


def _hessian(fun: _Objective, theta: Vector) -> Vector:
    k = theta.size
    h = 1e-4 * np.maximum(1.0, np.abs(theta))
    out = np.zeros((k, k))
    base = fun(theta)
    for i in range(k):
        for j in range(i, k):
            if i == j:
                up = theta.copy()
                up[i] += h[i]
                down = theta.copy()
                down[i] -= h[i]
                out[i, i] = (fun(up) - 2 * base + fun(down)) / (h[i] ** 2)
                continue
            corners = []
            for si, sj in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                moved = theta.copy()
                moved[i] += si * h[i]
                moved[j] += sj * h[j]
                corners.append(fun(moved))
            value = (corners[0] - corners[1] - corners[2] + corners[3]) / (4 * h[i] * h[j])
            out[i, j] = out[j, i] = value
    return out


class _Objective:
    def __init__(self, data: LifeData, model: str) -> None:
        self.data = data
        self.model = model

    def __call__(self, theta: Vector) -> float:
        beta, eta, p = _unpack(theta, self.model)
        value = -log_likelihood(self.data, beta, eta, p)
        return value if math.isfinite(value) else 1e300


def fit(data: LifeData, model: Literal["weibull", "defective"]) -> Fit:
    objective = _Objective(data, model)
    found = _numeric.minimize(objective, _start(data, model))
    covariance: Vector | None = None
    identifiable = False
    try:
        hessian = _hessian(objective, found.x)
        eigen = np.linalg.eigvalsh(hessian)
        if eigen.min() > 0 and eigen.max() / eigen.min() < 1e10:
            covariance = np.linalg.inv(hessian)
            identifiable = True
    except np.linalg.LinAlgError:  # pragma: no cover - 수치가 무너지면 구간 없이
        covariance = None
    return Fit(model, found.x, -found.fun, found.success, covariance, identifiable)


def b_life(fit_: Fit, q: float) -> float | None:
    """그 비율이 고장 날 때까지의 경과 — 결함 모형에서 q ≥ p 면 None(닿지 않는다)."""
    if fit_.model == "defective":
        if q >= fit_.p:
            return None
        return fit_.eta * float((-math.log1p(-q / fit_.p)) ** (1 / fit_.beta))
    return fit_.eta * float((-math.log1p(-q)) ** (1 / fit_.beta))


def conditional_life(fit_: Fit, q: float) -> float:
    """「결국 고장 나는 것들」 중 q 가 고장 날 때까지 — 결함 모형에서만 뜻이 있다."""
    return fit_.eta * float((-math.log1p(-q)) ** (1 / fit_.beta))


def b_life_interval(fit_: Fit, q: float) -> tuple[float, float] | None:
    """델타 방법 — ln B 의 기울기를 수치로."""
    if fit_.covariance is None:
        return None
    center = b_life(fit_, q)
    if center is None:
        return None
    gradient = np.zeros(fit_.theta.size)
    for i in range(fit_.theta.size):
        step = 1e-5 * max(1.0, abs(float(fit_.theta[i])))
        moved = replace(fit_, theta=fit_.theta.copy())
        moved.theta[i] += step
        shifted = b_life(moved, q)
        if shifted is None:
            return None
        gradient[i] = (math.log(shifted) - math.log(center)) / step
    variance = float(gradient @ fit_.covariance @ gradient)
    if variance < 0 or not math.isfinite(variance):
        return None
    spread = Z95 * math.sqrt(variance)
    return center * math.exp(-spread), center * math.exp(spread)


def choose(
    standard: Fit | None, defective: Fit | None
) -> tuple[Fit | None, float | None, float | None]:
    """(고른 것, LR 통계량, p 값).

    결함 모형은 유의하고(p<0.05) 서고(구간이 있고) p̂<0.95 일 때 고른다.
    """
    if standard is None:
        return None, None, None
    if defective is None:
        return standard, None, None
    statistic = max(0.0, 2 * (defective.loglik - standard.loglik))
    p_value = 0.5 * _numeric.chi2_sf(statistic, 1) if statistic > 0 else 1.0
    if p_value < 0.05 and defective.identifiable and defective.p < 0.95:
        return defective, statistic, p_value
    return standard, statistic, p_value


def status_of(
    fit_: Fit | None, q: float, reached: float
) -> Literal["observed", "extrapolated", "unreachable", "uncertain", "none"]:
    """`reached` 는 비모수 곡선이 닿은 가장 큰 누적 고장률."""
    if reached >= q:
        return "observed"
    if fit_ is None:
        return "none"
    if fit_.model == "weibull":
        return "extrapolated"
    band = fit_.interval(2)
    if band is None:
        return "uncertain" if q >= fit_.p * 0.8 else "extrapolated"
    low, high = band
    if high < q:
        return "unreachable"
    if low <= q:
        return "uncertain"
    return "extrapolated"


def plateau_unknown(chosen: Fit, fits: Sequence[Fit], lives: Sequence[BLifeOut]) -> bool:
    """표준 모형으로 관측 밖을 읽었는데, 그 비율 아래에서 곡선이 평평해질 수 있음을 못 지우나.

    결함 모형의 p 구간 아래 끝이 외삽한 비율보다 크면 「그 아래에서 멎는다」 는 지워진다.
    """
    if chosen.model != "weibull":
        return False
    outside = [one.q for one in lives if one.status == "extrapolated"]
    if not outside:
        return False
    defective = next((one for one in fits if one.model == "defective"), None)
    band = defective.interval(2) if defective is not None and defective.identifiable else None
    return band is None or band[0] <= max(outside)


def observed_life(observed: Vector, q: float) -> float | None:
    """비모수 곡선이 q 에 처음 닿는 경과(구간의 가운데 사이를 곧게 이어)."""
    above = np.nonzero(observed >= q)[0]
    if above.size == 0:
        return None
    index = int(above[0])
    if index == 0:
        return 0.5 * q / max(float(observed[0]), 1e-300)
    low, high = float(observed[index - 1]), float(observed[index])
    share = (q - low) / max(high - low, 1e-300)
    return index - 0.5 + share


def goodness(data: LifeData, fit_: Fit) -> tuple[float, int, float, float]:
    """피어슨 χ²(기대 5 미만은 뒤에서부터 합친다)와 관측-모형의 가장 큰 상대 차."""
    ages = np.arange(data.failures.size, dtype=np.float64)
    beta, eta, p = fit_.beta, fit_.eta, fit_.p
    mass, _ = _interval(ages, beta, eta)
    previous = np.concatenate(([0.0], mass[:-1]))
    hazard = p * (mass - previous) / np.maximum(1.0 - p * previous, 1e-300)
    expected = data.at_risk * hazard
    observed_counts = data.failures
    bins_o: list[float] = []
    bins_e: list[float] = []
    acc_o = acc_e = 0.0
    for o, e in zip(observed_counts[::-1], expected[::-1], strict=True):
        acc_o += float(o)
        acc_e += float(e)
        if acc_e >= 5:
            bins_o.append(acc_o)
            bins_e.append(acc_e)
            acc_o = acc_e = 0.0
    if acc_e > 0 and bins_e:
        bins_o[-1] += acc_o
        bins_e[-1] += acc_e
    chi2 = float(sum((o - e) ** 2 / e for o, e in zip(bins_o, bins_e, strict=True) if e > 0))
    dof = max(1, len(bins_e) - 1 - fit_.parameters)
    p_value = _numeric.chi2_sf(chi2, dof)
    _, observed, _, _ = actuarial(data)
    keep = data.failures >= 10
    deviation = 0.0
    if keep.any():
        model_f = p * mass
        rel = np.abs(observed[keep] - model_f[keep]) / np.maximum(model_f[keep], 1e-300)
        deviation = float(rel.max())
    return chi2, dof, p_value, deviation


# --- 어댑터 --------------------------------------------------------------------------


def available(built: spec_module.Built) -> str | None:
    if built.spec.measure != "count":
        return "건수 지표에서만 됩니다 — 수명은 「몇 대가 고장 났나」 를 셉니다."
    if built.cohort is None or built.time is None:
        return "코호트 칸(판매일)과 시간 칸(접수일)이 있는 지표에서만 됩니다."
    den = built.spec.denominator
    if den is None or den.time != "cohort":
        return (
            "분모(판매 대수)를 코호트로 짝지은 지표에서만 됩니다 — 대수를 알아야 비율이 "
            "됩니다."
        )
    return None


registry.register(registry.Recipe(NAME, LABEL, available))


@dataclass
class Cohort:
    """코호트 하나 — 대수 · 닫힌 마지막 경과 · 경과별 건수(닫히지 않은 경과도 든다)."""

    start: date
    units: float
    horizon: int
    counts: dict[int, int]

    @property
    def inside(self) -> int:
        return sum(c for a, c in self.counts.items() if a <= self.horizon)


def cohorts_of(
    frame: query.Frame,
    grain: str,
    excluded: dict[str, int],
    *,
    max_age: int | None = None,
    within_units: bool = True,
) -> list[Cohort]:
    """코호트 x 경과 셀 → 쓸 수 있는 코호트. 뺀 것은 `excluded` 에 더한다 — 분모 없음 · 판매가
    덜 들어온 달 · 닫히지 않은 코호트 · 아직 들어오는 경과 · 기록이 대수보다 많음 · 경과 상한
    밖. (순차 검정도 같은 규칙으로 코호트를 고른다.)

    `within_units` — 기록이 대수보다 많은 코호트를 뺀다. 수명은 「몇 대가 고장 났나」 라
    기록이 대수를 넘을 수 없지만, 순차 검정은 대수당 **건수**(포아송 비율)라 1 을 넘어도
    된다(재방문)."""
    for key in (
        "missing_denominator",
        "open_denominator",
        "open_cohort",
        "open_cells",
        "inconsistent_denominator",
        "beyond_max_age",
    ):
        excluded.setdefault(key, 0)
    by_cohort: dict[date, dict[int, int]] = {}
    for cell in frame.cells:
        if cell.cohort is None or cell.age is None:
            continue
        by_cohort.setdefault(cell.cohort, {})[cell.age] = cell.count
    den = frame.denominator
    out: list[Cohort] = []
    for start in sorted(by_cohort):
        counts = by_cohort[start]
        records = sum(counts.values())
        probe = query.Cell({}, None, start, 0, records, 0, None, None, None)
        units = den.lookup(probe, grain) if den is not None else None
        if not units:
            excluded["missing_denominator"] += records
            continue
        if (
            den is not None
            and den.before is not None
            and not query.is_closed(start, grain, den.before)
        ):
            excluded["open_denominator"] += records
            continue
        horizon = -1
        while query.is_closed(query.advance(start, horizon + 1, grain), grain, frame.before):
            horizon += 1
            if horizon > 2400:  # pragma: no cover - 지킴
                break
        if horizon < 0:
            excluded["open_cohort"] += records
            continue
        if max_age is not None:
            horizon = min(horizon, max_age - 1)
        inside = 0
        for a, c in counts.items():
            if a <= horizon:
                inside += c
            elif max_age is not None and a >= max_age:
                excluded["beyond_max_age"] += c
            else:
                excluded["open_cells"] += c
        if within_units and inside > units:
            excluded["inconsistent_denominator"] += records
            continue
        out.append(Cohort(start, float(units), horizon, counts))
    return out


def run(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    *,
    model: Literal["auto", "weibull", "defective"] = "auto",
    max_age: int | None = None,
    compact: bool = False,
) -> LifeOut:
    reason = available(built)
    if reason is not None:
        raise common.refuse(24, reason)
    assert built.cohort is not None and built.time is not None
    grain = built.cohort.grain
    ask = replace(ask, dims=[], by=("cohort", "age"), age_from=0)
    common.require_exact_counts(built, ask)
    frame = query.frame(db, user, metric, built, ask)
    common.require_whole(frame)
    caveats = common.Caveats()
    excluded: dict[str, int] = {
        "missing_denominator": 0,
        "open_denominator": 0,
        "open_cohort": 0,
        "open_cells": 0,
        "inconsistent_denominator": 0,
        "beyond_max_age": 0,
    }
    cohorts = cohorts_of(frame, grain, excluded, max_age=max_age)
    if excluded["inconsistent_denominator"]:
        caveats.add(
            "inconsistent_denominator",
            "기록이 판매 대수보다 많은 코호트를 뺐습니다 — 같은 제품의 재방문이 많거나 "
            "분모가 그 모델의 판매를 다 담지 못했습니다.",
            count=excluded["inconsistent_denominator"],
        )
    if excluded["missing_denominator"]:
        caveats.add(
            "missing_denominator",
            "판매 대수가 없는 코호트를 뺐습니다.",
            count=excluded["missing_denominator"],
        )
    if excluded["open_cells"]:
        caveats.add(
            "open_cells_excluded",
            "아직 닫히지 않은 경과의 기록은 맞춤에서 뺐습니다 — 더 들어올 수 있습니다.",
            level="info",
            count=excluded["open_cells"],
        )
    caveats.add(
        "records_not_units",
        "기록 수를 고장 대수로 봅니다 — 같은 제품이 다시 들어온 기록이 섞이면 수명이 짧게 "
        "나옵니다.",
        level="info",
    )
    if grain in ("quarter", "year"):
        caveats.add(
            "coarse_grain",
            "기간 단위가 거칠어(분기 · 해) 수명이 거칠게 나옵니다 — 월 단위 지표를 권합니다.",
        )
    data = (
        collapse(
            [one.units for one in cohorts],
            [one.horizon for one in cohorts],
            [[one.counts.get(a, 0) for a in range(one.horizon + 1)] for one in cohorts],
        )
        if cohorts
        else None
    )
    fits: list[Fit] = []
    chosen: Fit | None = None
    statistic = p_value = None
    if data is not None and data.total_failures >= MIN_FAILURES:
        standard = fit(data, "weibull") if model in ("auto", "weibull") else None
        defective = (
            fit(data, "defective")
            if model in ("auto", "defective") and data.total_failures >= MIN_FAILURES_DEFECTIVE
            else None
        )
        fits = [one for one in (standard, defective) if one is not None]
        if model == "auto":
            chosen, statistic, p_value = choose(standard, defective)
        else:
            chosen = fits[0] if fits else None
    elif data is not None:
        caveats.add(
            "too_few_failures",
            f"관측 안의 건수가 {int(data.total_failures)}건이라 와이블을 맞추지 않았습니다 — "
            "비모수 곡선만 봅니다.",
        )
    found = _Found(data, cohorts, fits, chosen, statistic, p_value)
    return _out(
        db, user, metric, built, ask, frame, caveats, excluded, found, model, max_age, compact
    )


@dataclass
class _Found:
    data: LifeData | None
    cohorts: list[Cohort]
    fits: list[Fit]
    chosen: Fit | None
    statistic: float | None
    p_value: float | None


def _out(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    frame: query.Frame,
    caveats: common.Caveats,
    excluded: dict[str, int],
    found: _Found,
    model: str,
    max_age: int | None,
    compact: bool,
) -> LifeOut:
    assert built.cohort is not None
    grain = built.cohort.grain
    data, cohorts, fits, chosen = found.data, found.cohorts, found.fits, found.chosen
    statistic, p_value = found.statistic, found.p_value
    days = DAYS.get(grain, 1.0)
    points: list[LifePointOut] = []
    curve: list[LifeCurveOut] = []
    lives: list[BLifeOut] = []
    reached = 0.0
    gof: tuple[float, int, float, float] | None = None
    if data is not None:
        hazard, observed, low, high = actuarial(data)
        valid = data.at_risk > 0
        reached = float(observed[valid].max()) if valid.any() else 0.0
        ages = np.arange(data.failures.size, dtype=np.float64)
        fitted = None
        if chosen is not None:
            mass, _ = _interval(ages, chosen.beta, chosen.eta)
            fitted = chosen.p * mass
            gof = goodness(data, chosen)
        for a in range(data.failures.size):
            if not valid[a]:
                continue
            points.append(
                LifePointOut(
                    age=a,
                    at_risk=float(data.at_risk[a]),
                    failures=float(data.failures[a]),
                    hazard=float(hazard[a]),
                    observed=float(observed[a]),
                    observed_low=float(low[a]) if math.isfinite(low[a]) else None,
                    observed_high=float(high[a]) if math.isfinite(high[a]) else None,
                    fitted=float(fitted[a]) if fitted is not None else None,
                    cohorts=int((data.horizons >= a).sum()),
                )
            )
        if not compact:
            top = max(data.max_age + 1, 1)
            for t in np.linspace(0, top * 2, 61):
                values: dict[str, float | None] = {"weibull": None, "defective": None}
                for one in fits:
                    w = -math.expm1(-((t / one.eta) ** one.beta)) if t > 0 else 0.0
                    values[one.model] = one.p * w
                curve.append(
                    LifeCurveOut(
                        age=float(t), standard=values["weibull"], defective=values["defective"]
                    )
                )
        for q in QUANTILES:
            state = status_of(chosen, q, reached)
            age = b_life(chosen, q) if chosen is not None else None
            band = b_life_interval(chosen, q) if chosen is not None else None
            extrapolation = (
                age / (data.max_age + 1) if age is not None and state != "observed" else None
            )
            lives.append(
                BLifeOut(
                    q=q,
                    status=state,
                    age=age if state != "unreachable" else None,
                    ci=list(band) if band is not None and state != "unreachable" else None,
                    age_days=age * days
                    if age is not None and state != "unreachable"
                    else None,
                    conditional_age=(
                        conditional_life(chosen, q)
                        if chosen is not None and chosen.model == "defective"
                        else None
                    ),
                    extrapolation=extrapolation,
                    observed_age=observed_life(observed, q),
                )
            )
            if state == "extrapolated" and extrapolation is not None and extrapolation > 2:
                caveats.add(
                    "far_extrapolation",
                    f"B{round(q * 100)} 이 관측한 경과의 {extrapolation:.1f}배 밖입니다 — "
                    "모형을 관측 밖으로 늘려 읽은 값입니다.",
                )
        if chosen is not None and plateau_unknown(chosen, fits, lives):
            caveats.add(
                "plateau_unknown",
                "관측이 짧아 「결국 고장 나는 비율」 을 못 가립니다 — 관측 밖의 B수명은 모든 "
                "대수가 결국 고장 난다고 보고 낸 값입니다.",
            )
        if gof is not None and gof[2] < 0.01 and gof[3] > 0.1:
            caveats.add(
                "poor_fit",
                "모형이 관측 곡선과 10% 넘게 어긋나는 경과가 있습니다 — 수명 분포가 와이블 "
                "하나로 설명되지 않습니다(고장 원인이 섞였을 수 있습니다).",
            )
    rows: list[LifeCohortOut] = []
    if not compact:
        for cohort in cohorts:
            end = query.advance(cohort.start, cohort.horizon + 1, grain)
            inside = cohort.inside
            cell = query.Cell({}, None, cohort.start, None, inside, 0, None, None, None)
            ranged = replace(ask, period_from=cohort.start, period_to=end)
            rows.append(
                LifeCohortOut(
                    cohort=cohort.start.isoformat(),
                    label=axes.period_label(cohort.start.isoformat(), grain),
                    units=cohort.units,
                    failures=inside,
                    horizon=cohort.horizon,
                    drill=query.drill(built, cell, by=("cohort",), ask=ranged),
                )
            )
    head = common.header(
        db,
        user,
        metric,
        built,
        frame,
        recipe=NAME,
        method=METHOD,
        params={"model": model, "max_age": max_age, "compact": compact},
        caveats=caveats,
        excluded=excluded,
    )
    return LifeOut(
        **head,
        basis="records",
        time_unit=grain,
        units=data.units if data is not None else 0.0,
        failures=int(data.total_failures) if data is not None else 0,
        cohorts_used=data.cohorts if data is not None else 0,
        max_age=data.max_age if data is not None else -1,
        reached=reached,
        fits=[_fit_out(one, days) for one in fits],
        chosen=chosen.model if chosen is not None else None,
        lrt_statistic=statistic,
        lrt_p_value=p_value,
        b_lives=lives,
        points=points,
        curve=curve,
        cohort_rows=rows,
        gof_chi2=gof[0] if gof is not None else None,
        gof_df=gof[1] if gof is not None else None,
        gof_p_value=gof[2] if gof is not None else None,
        max_rel_dev=gof[3] if gof is not None else None,
    )


def _fit_out(one: Fit, days: float) -> LifeFitOut:
    beta_ci = one.interval(0)
    eta_ci = one.interval(1)
    p_ci = one.interval(2) if one.model == "defective" else None
    return LifeFitOut(
        model=one.model,
        beta=one.beta,
        beta_ci=list(beta_ci) if beta_ci is not None else None,
        eta=one.eta,
        eta_ci=list(eta_ci) if eta_ci is not None else None,
        eta_days=one.eta * days,
        p=one.p if one.model == "defective" else None,
        p_ci=list(p_ci) if p_ci is not None else None,
        loglik=one.loglik,
        aic=one.aic,
        converged=one.converged,
        identifiable=one.identifiable,
    )
