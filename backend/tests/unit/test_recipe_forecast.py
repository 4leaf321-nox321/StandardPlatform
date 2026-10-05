"""클레임 예측 — 손셈과 정답을 아는 와이블 흐름(DB 없음)."""

from __future__ import annotations

import numpy as np
import pytest

from app.modules.metrics.recipes import forecast, life


def test_경과별_확률은_따로_적분한_것과_같고_결함이면_p_에서_멈춘다() -> None:
    beta, eta, p = 1.5, 13.0, 0.05
    pi = forecast.probabilities(beta, eta, p, 60)
    grid = np.linspace(0.0, 1.0, 4001)
    mass = []
    for a in range(60):
        v = a + grid
        mass.append(float(np.trapezoid(-np.expm1(-((v / eta) ** beta)), v)))
    previous = [0.0, *mass[:-1]]
    truth = [p * (now - before) for now, before in zip(mass, previous, strict=True)]
    assert np.allclose(pi, truth, rtol=1e-6, atol=1e-12)
    assert forecast.probabilities(beta, eta, p, 4000).sum() == pytest.approx(p, rel=1e-3)


def test_아직_안_본_경과만_보증_안에서_더한다() -> None:
    """코호트 둘 — 0번(대수 100, 경과 1 까지 봄), 1번(대수 200, 아무것도 안 봄). 첫 열린 기간은
    2, 보증은 3 기간."""
    sold = [forecast.Sold(0, 100.0, 1), forecast.Sold(1, 200.0, -1)]
    ages, units = forecast.schedule(sold, first=2, periods=3, warranty=3)
    # 기간 2: 0번은 경과 2, 1번은 경과 1 / 기간 3: 0번은 보증 밖, 1번은 경과 2 /
    # 기간 4: 둘 다 밖.
    assert ages.tolist() == [[2, -1, -1], [1, 2, -1]]
    pi = np.asarray([0.1, 0.2, 0.3, 0.4])
    assert forecast.expected(ages, units, pi).tolist() == pytest.approx([70.0, 60.0, 0.0])


def _flow(
    seed: int, cohorts: int, ahead: int, units: int, beta: float, eta: float
) -> tuple[life.LifeData, list[forecast.Sold], np.ndarray]:
    """코호트마다 판매월 안 고른 판매 + 와이블 고장 — (관측한 자료, 앞으로 기간마다 실제)."""
    rng = np.random.default_rng(seed)
    horizons: list[int] = []
    counts: list[list[float]] = []
    future = np.zeros(ahead)
    for c in range(cohorts):
        horizon = cohorts - 1 - c
        age = np.floor(rng.random(units) + eta * rng.weibull(beta, units)).astype(int)
        seen = np.bincount(age[age <= horizon], minlength=horizon + 1)[: horizon + 1]
        horizons.append(horizon)
        counts.append([float(one) for one in seen])
        for t in range(ahead):
            future[t] += int((age == cohorts + t - c).sum())
    data = life.collapse([float(units)] * cohorts, horizons, counts)
    sold = [forecast.Sold(c, float(units), cohorts - 1 - c) for c in range(cohorts)]
    return data, sold, future


def test_정답_와이블의_앞으로_12기간을_구간_안에서_맞힌다() -> None:
    """판매 36개월 x 3,000대, 와이블(1.5, 13개월) — 앞으로 12개월의 합이 95% 구간 안인 번이
    대부분이고, 평균 예측은 실제와 몇 % 안이다."""
    inside = 0
    errors = []
    runs = 16
    for seed in range(runs):
        data, sold, future = _flow(seed, 36, 12, 3000, 1.5, 13.0)
        fitted = life.fit(data, "weibull")
        ages, units = forecast.schedule(sold, first=36, periods=12, warranty=None)
        band = forecast.simulate(fitted, ages, units, draws=1000, seed=seed)
        mean, low, high = band.total
        inside += low <= future.sum() <= high
        errors.append(mean / future.sum() - 1)
    assert inside / runs >= 0.8
    assert abs(float(np.mean(errors))) < 0.05


def test_씨앗이_같으면_같은_답() -> None:
    data, sold, _ = _flow(1, 24, 6, 2000, 1.5, 13.0)
    fitted = life.fit(data, "weibull")
    ages, units = forecast.schedule(sold, first=24, periods=6, warranty=None)
    first = forecast.simulate(fitted, ages, units, draws=500)
    second = forecast.simulate(fitted, ages, units, draws=500)
    assert first.total == second.total and np.array_equal(first.high, second.high)
