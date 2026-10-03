"""② 수명 — 정답을 아는 합성 코호트에서 형상 · 척도 · 「결국 고장 나는 비율」 을
되찾아야 한다.

합성 자료는 지표가 세는 것과 같은 길로 만든다: 판매일이 달 안에 고르고(u), 고장 시각이
와이블(또는 비율 p 만 고장 나는 결함 와이블)이며, 경과는 **두 달의 달력 차이** floor(u + T)
다. 코호트 c 는 관측 끝이 (마지막 - c) — 늦게 판 달일수록 덜 봤다.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.modules.metrics.recipes import life
from app.modules.metrics.recipes.schemas import BLifeOut

BETA = 1.5
#: 400일을 달(30.4375일)로 — 규모 리허설 자료의 정답과 같은 모양.
ETA = 400 / 30.4375


def _simulate(
    seed: int, cohorts: int, units: int, beta: float, eta: float, p: float = 1.0
) -> life.LifeData:
    rng = np.random.default_rng(seed)
    sizes: list[float] = []
    horizons: list[int] = []
    counts: list[list[float]] = []
    for c in range(cohorts):
        horizon = cohorts - 1 - c
        within = rng.random(units)
        failure = eta * rng.weibull(beta, units)
        fails = rng.random(units) < p
        age = np.floor(within + failure)[fails].astype(int)
        found = np.bincount(age[age <= horizon], minlength=horizon + 1)[: horizon + 1]
        sizes.append(float(units))
        horizons.append(horizon)
        counts.append([float(one) for one in found])
    return life.collapse(sizes, horizons, counts)


def _expected(cohorts: int, units: float, beta: float, eta: float, p: float) -> life.LifeData:
    """잡음 없는 자료 — 경과 a 의 기대 건수 N · p · (G(a) - G(a-1)). G 는 가는 사다리꼴로 따로
    적분한다(시험이 레시피의 가우스-르장드르를 빌리지 않게)."""
    top = cohorts
    grid = np.linspace(0.0, 1.0, 4001)
    mass = []
    for a in range(top):
        v = a + grid
        w = -np.expm1(-((v / eta) ** beta))
        mass.append(float(np.trapezoid(w, v)))
    previous = [0.0, *mass[:-1]]
    step = [p * (now - before) * units for now, before in zip(mass, previous, strict=True)]
    horizons = [cohorts - 1 - c for c in range(cohorts)]
    return life.collapse(
        [units] * cohorts, horizons, [step[: horizon + 1] for horizon in horizons]
    )


def _truth(q: float, beta: float, eta: float, p: float = 1.0) -> float:
    return eta * float((-math.log1p(-q / p)) ** (1 / beta))


def _lives(chosen: life.Fit, reached: float) -> list[BLifeOut]:
    return [
        BLifeOut(
            q=q,
            status=life.status_of(chosen, q, reached),
            age=life.b_life(chosen, q),
            ci=None,
            age_days=None,
            conditional_age=None,
            extrapolation=None,
            observed_age=None,
        )
        for q in life.QUANTILES
    ]


# --- 수식 ---------------------------------------------------------------------------


def test_구간_적분이_닫힌_식과_같다() -> None:
    """G(a) = ∫_a^{a+1} W — 형상 1(지수)과 2(레일리)는 닫힌 식이 있다."""
    ages = np.arange(40, dtype=np.float64)
    eta = 7.3
    exponential = 1 - eta * (np.exp(-ages / eta) - np.exp(-(ages + 1) / eta))
    assert np.allclose(life.interval_mass(ages, 1.0, eta), exponential, rtol=0, atol=1e-8)
    erf = np.vectorize(math.erf)
    rayleigh = 1 - eta * math.sqrt(math.pi) / 2 * (erf((ages + 1) / eta) - erf(ages / eta))
    assert np.allclose(life.interval_mass(ages, 2.0, eta), rayleigh, rtol=0, atol=1e-8)


def test_비모수_곡선은_보험계리식이다() -> None:
    """코호트 둘을 손으로 접는다 — 관측 끝이 2 와 1."""
    data = life.collapse([100, 100], [2, 1], [[10, 5, 2], [8, 4]])
    assert data.failures.tolist() == [18, 9, 2]
    # 경과 1 에 들어선 대수 = (100-10) + (100-8), 경과 2 는 첫 코호트만 100-15.
    assert data.at_risk.tolist() == [200, 182, 85]
    assert data.survivors.tolist() == [83, 88]
    hazard, observed, low, high = life.actuarial(data)
    assert hazard.tolist() == pytest.approx([18 / 200, 9 / 182, 2 / 85])
    survival = (1 - 18 / 200) * (1 - 9 / 182) * (1 - 2 / 85)
    assert observed[-1] == pytest.approx(1 - survival)
    assert np.all(low <= observed) and np.all(observed <= high)


def test_비모수_곡선이_닿은_경과() -> None:
    observed = np.asarray([0.004, 0.012, 0.03])
    # 경과 a 의 값은 a + 0.5 에 놓고 곧게 잇는다 — 0.01 은 0.5 와 1.5 사이의 3/4.
    assert life.observed_life(observed, 0.01) == pytest.approx(1.25)
    assert life.observed_life(observed, 0.002) == pytest.approx(0.25)
    assert life.observed_life(observed, 0.05) is None


# --- 맞춤 ---------------------------------------------------------------------------


@pytest.mark.parametrize("p", [1.0, 0.044])
def test_잡음_없는_자료로_1퍼센트_안에서_되찾는다(p: float) -> None:
    data = _expected(36, 5000.0, BETA, ETA, p)
    found = life.fit(data, "weibull" if p == 1.0 else "defective")
    assert found.beta == pytest.approx(BETA, rel=0.01)
    assert found.eta == pytest.approx(ETA, rel=0.01)
    assert found.p == pytest.approx(p, rel=0.01)


def test_모두_고장_나는_제품의_형상과_척도를_되찾는다() -> None:
    data = _simulate(7, 36, 5000, BETA, ETA)
    standard = life.fit(data, "weibull")
    defective = life.fit(data, "defective")
    chosen, _, p_value = life.choose(standard, defective)
    assert chosen is standard and p_value is not None and p_value > 0.05
    assert standard.beta == pytest.approx(BETA, rel=0.08)
    assert standard.eta == pytest.approx(ETA, rel=0.08)
    beta_ci = standard.interval(0)
    assert beta_ci is not None and beta_ci[0] < BETA < beta_ci[1]
    _, observed, _, _ = life.actuarial(data)
    for q in life.QUANTILES:
        assert life.status_of(standard, q, float(observed.max())) == "observed"
        band = life.b_life_interval(standard, q)
        assert band is not None and band[0] < _truth(q, BETA, ETA) < band[1]


def test_결국_고장_나는_비율이_작으면_결함_모형을_고르고_B10_을_지어내지_않는다() -> None:
    """판매의 4.4%만 고장 나는 제품 — 표준 와이블은 평평해지는 곡선을 따라가다 형상을 1 아래로
    끌어내린다(「초기 고장」 으로 잘못 읽힌다). 결함 모형이 마모(1.5)를 되찾아야 한다."""
    p = 0.044
    data = _simulate(11, 36, 5000, BETA, ETA, p)
    standard = life.fit(data, "weibull")
    defective = life.fit(data, "defective")
    assert standard.beta < 1.0  # 치우친 표준 모형
    chosen, statistic, p_value = life.choose(standard, defective)
    assert chosen is defective and statistic is not None and statistic > 100
    assert p_value is not None and p_value < 1e-10
    assert defective.beta == pytest.approx(BETA, rel=0.08)
    assert defective.eta == pytest.approx(ETA, rel=0.08)
    assert defective.p == pytest.approx(p, rel=0.08)
    _, observed, _, _ = life.actuarial(data)
    reached = float(observed.max())
    statuses = {q: life.status_of(defective, q, reached) for q in life.QUANTILES}
    assert statuses == {0.01: "observed", 0.05: "unreachable", 0.10: "unreachable"}
    assert life.b_life(defective, 0.10) is None
    band = life.b_life_interval(defective, 0.01)
    assert band is not None and band[0] < _truth(0.01, BETA, ETA, p) < band[1]
    # 「결국 고장 나는 것들」 의 10% 는 읽을 수 있다 — 그렇게 이름 붙여 낸다.
    assert life.conditional_life(defective, 0.10) == pytest.approx(
        _truth(0.10, BETA, ETA), 0.1
    )
    assert not life.plateau_unknown(
        defective, [standard, defective], _lives(defective, reached)
    )


def test_관측이_짧으면_외삽이라고_말한다() -> None:
    """척도 200개월에 1년만 봤다 — B1 은 관측 안, B5 · B10 은 모형을 관측 밖으로 늘려 읽는다.
    표준 모형을 골라도 「그 아래에서 멎는다」 를 못 지우니 그렇게 말해야 한다."""
    eta = 200.0
    data = _simulate(0, 12, 5000, BETA, eta)
    standard = life.fit(data, "weibull")
    defective = life.fit(data, "defective")
    chosen, _, _ = life.choose(standard, defective)
    assert chosen is standard
    _, observed, _, _ = life.actuarial(data)
    reached = float(observed.max())
    lives = _lives(standard, reached)
    assert [one.status for one in lives] == ["observed", "extrapolated", "extrapolated"]
    for one in lives:
        band = life.b_life_interval(standard, one.q)
        assert band is not None and band[0] < _truth(one.q, BETA, eta) < band[1]
    assert life.plateau_unknown(standard, [standard, defective], lives)


def test_B수명_상태는_p_의_구간으로_가른다() -> None:
    def defective(p: float, se: float) -> life.Fit:
        theta = np.asarray([math.log(1.5), math.log(10.0), math.log(p / (1 - p))])
        return life.Fit(
            "defective", theta, 0.0, True, np.diag([0.01, 0.01, se**2]), identifiable=True
        )

    narrow = defective(0.04, 0.05)  # p 구간 약 0.036~0.044
    assert life.status_of(narrow, 0.10, reached=0.03) == "unreachable"
    assert life.status_of(narrow, 0.01, reached=0.03) == "observed"
    assert life.status_of(narrow, 0.02, reached=0.01) == "extrapolated"
    wide = defective(0.08, 0.6)  # p 구간이 0.10 을 걸친다
    assert life.status_of(wide, 0.10, reached=0.03) == "uncertain"
    assert life.status_of(None, 0.10, reached=0.03) == "none"
    assert life.status_of(None, 0.01, reached=0.03) == "observed"


def test_결함_모형이_유의하지_않으면_표준을_고른다() -> None:
    data = _simulate(3, 24, 2000, BETA, ETA)
    standard = life.fit(data, "weibull")
    defective = life.fit(data, "defective")
    chosen, _, _ = life.choose(standard, defective)
    assert chosen is standard
    assert life.choose(standard, None) == (standard, None, None)
    assert life.choose(None, defective) == (None, None, None)
