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


def test_과분산은_코호트째_뭉치면_크고_포아송이면_1_언저리다() -> None:
    """한 코호트에 수십 건 · 이웃은 0 건인 몰림은 셀(코호트 x 경과)로 재면 안 보인다 — 코호트의
    닫힌 경과까지 합으로 잰다."""
    rng = np.random.default_rng(3)
    ages = 12

    def cohorts(counts: list[dict[int, int]]) -> list[life.Cohort]:
        return [life.Cohort(date(2024, 1, 1), 1000.0, ages - 1, one) for one in counts]

    poisson = cohorts([{a: int(rng.poisson(2.0)) for a in range(ages)} for _ in range(40)])
    phi = sprt.overdispersion(poisson, sprt.reference_rates(poisson))
    assert 0.5 < phi < 1.6
    # 넷 중 하나의 코호트에만 기록이 몰린다(평균은 같다).
    clumped = cohorts([{a: (8 if k % 4 == 0 else 0) for a in range(ages)} for k in range(40)])
    assert sprt.overdispersion(clumped, sprt.reference_rates(clumped)) > 10
    assert sprt.overdispersion(clumped[:4], sprt.reference_rates(clumped[:4])) == 1.0


def test_흔들림이_큰_흐름은_우도비를_φ_로_나눠야_잘못_나쁨이_α_언저리다() -> None:
    """같은 비율 · 분산이 평균의 5배(음이항) — 포아송 그대로면 잘못 「나쁨」 이 잦고, φ 로
    나누면 α/(1-β) 언저리 아래로 돌아온다."""
    rng = np.random.default_rng(1)
    expected = [10.0] * 40
    phi = 5.0
    plain = adjusted = 0
    for _ in range(1000):
        # 평균 10 · 분산 50 인 음이항(감마-포아송).
        observed = rng.poisson(rng.gamma(10.0 / (phi - 1), phi - 1, 40)).tolist()
        plain += (
            sprt.walk(observed, expected, rho=1.5, alpha=0.05, beta=0.1)[0][-1].decision
            == "worse"
        )
        adjusted += (
            sprt.walk(observed, expected, rho=1.5, alpha=0.05, beta=0.1, dispersion=phi)[0][
                -1
            ].decision
            == "worse"
        )
    assert plain / 1000 > 0.1
    assert adjusted / 1000 <= 0.05 / 0.9 + 0.02


def test_과분산이면_표준화_비의_구간을_로그_척도에서_넓힌다() -> None:
    low, high = sprt.widened(2.0, 1.5, 2.6, 4.0)
    assert low == pytest.approx(2.0 * (1.5 / 2.0) ** 2) and high == pytest.approx(2.0 * 1.3**2)
    assert sprt.widened(2.0, 1.5, 2.6, 1.0) == (1.5, 2.6)
    assert sprt.widened(None, None, None, 4.0) == (None, None)
