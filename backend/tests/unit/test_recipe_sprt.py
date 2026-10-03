"""④ 순차 검정 — 경계 · 우도비 · 구간은 손셈과, 1종 오류 · 검정력은 포아송 흐름과 견준다."""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pytest

from app.modules.metrics.recipes import life, sprt


def test_왈드_경계() -> None:
    upper, lower = sprt.boundaries(0.05, 0.10)
    assert upper == pytest.approx(math.log(0.9 / 0.05))
    assert lower == pytest.approx(math.log(0.1 / 0.95))


def test_우도비와_처음_넘은_자리에서_선다() -> None:
    # 기대가 기간마다 2, 관측이 그 두 배 — Λ = 쌓인 O · ln 1.5 - 0.5 · 쌓인 E.
    looks, decided = sprt.walk([4, 4, 4, 0], [2, 2, 2, 2], rho=1.5, alpha=0.05, beta=0.1)
    assert looks[0].llr == pytest.approx(4 * math.log(1.5) - 0.5 * 2)
    assert [one.decision for one in looks[:2]] == ["continue", "continue"]
    # 셋째 기간 12 · ln 1.5 - 3 = 1.87, 넷째는 기대만 쌓여 0.87 — 위 경계(2.89)에 못 닿아 아직.
    assert decided is None and looks[-1].decision == "continue"
    assert looks[-1].llr == pytest.approx(12 * math.log(1.5) - 4)
    worse, at = sprt.walk([6] * 5, [2] * 5, rho=1.5, alpha=0.05, beta=0.1)
    assert at is not None and worse[at].decision == "worse"
    assert all(one.decision == "worse" for one in worse[at:])
    same, at_same = sprt.walk([2] * 30, [2] * 30, rho=1.5, alpha=0.05, beta=0.1)
    # 같으면 기대 하나마다 Λ 가 ln 1.5 - 0.5 = -0.0945 씩 — 24 남짓에서 아래 경계.
    assert at_same is not None and same[at_same].decision == "not_worse"
    assert same[at_same].expected == pytest.approx(24.0)


def test_가우드_구간() -> None:
    low, high = sprt.garwood(10, 10)
    assert low == pytest.approx(0.4795, abs=1e-4) and high == pytest.approx(1.8390, abs=1e-4)
    zero_low, zero_high = sprt.garwood(0, 2)
    assert zero_low == 0.0 and zero_high == pytest.approx(3.6889 / 2, abs=1e-4)
    assert sprt.garwood(3, 0) == (None, None)


def test_결론까지_남은_기대_건수() -> None:
    upper, lower = sprt.boundaries(0.05, 0.1)
    same, worse = sprt.remaining(0.0, rho=1.5, alpha=0.05, beta=0.1)
    assert same == pytest.approx(-lower / (0.5 - math.log(1.5)))
    assert worse == pytest.approx(upper / (1.5 * math.log(1.5) - 0.5))


def test_전작의_경과별_비율은_그_경과까지_본_코호트로() -> None:
    cohorts = [
        life.Cohort(date(2024, 1, 1), 100.0, 2, {0: 1, 1: 2, 2: 3}),
        life.Cohort(date(2024, 2, 1), 300.0, 1, {0: 3, 1: 0}),
    ]
    rates = sprt.reference_rates(cohorts)
    assert sorted(rates) == [0, 1, 2]
    assert rates[0].rate == pytest.approx(4 / 400) and rates[0].units == 400
    assert rates[1].rate == pytest.approx(2 / 400)
    # 경과 2 는 첫 코호트만 봤다.
    assert rates[2].rate == pytest.approx(3 / 100) and rates[2].records == 3


@pytest.mark.parametrize(("ratio", "bound"), [(1.0, "type1"), (1.5, "power")])
def test_포아송_흐름에서_1종_오류와_검정력(ratio: float, bound: str) -> None:
    """기대가 기간마다 2 인 60 기간, 2,000 줄 — 같으면 잘못 「나쁨」 이 α/(1-β) 언저리 아래,
    ρ 배면 「나쁨」 이 1-β 언저리."""
    rng = np.random.default_rng(0)
    expected = [2.0] * 60
    worse = 0
    for _ in range(2000):
        observed = rng.poisson(np.asarray(expected) * ratio)
        looks, _ = sprt.walk(observed.tolist(), expected, rho=1.5, alpha=0.05, beta=0.1)
        worse += looks[-1].decision == "worse"
    share = worse / 2000
    if bound == "type1":
        assert share <= 0.05 / 0.9 + 0.01
    else:
        assert share >= 0.87
