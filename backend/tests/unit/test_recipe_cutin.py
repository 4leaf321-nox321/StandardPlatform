"""전후 비교 — 손셈과 정답 있는 시뮬레이션(DB 없음)."""

from __future__ import annotations

import numpy as np
import pytest

from app.modules.metrics.recipes import cutin


def test_비와_구간은_손셈과_같고_줄었으면_줄었다() -> None:
    before = [(100.0, 10_000.0)] * 12
    after = [(70.0, 10_000.0)] * 6
    found = cutin.compare(before, after)
    assert found.ratio == pytest.approx(0.7)
    assert found.dispersion == 1.0  # 부분군이 고르다
    assert found.low is not None and found.high is not None
    assert found.low < 0.7 < found.high < 0.8
    assert found.p_value is not None and found.p_value < 1e-6
    assert cutin.decide(found, 0.2) == "reduced"


def test_앞에_건수가_없거나_뒤가_없으면_비가_없다() -> None:
    assert cutin.compare([(0.0, 100.0)] * 5, [(3.0, 100.0)]).ratio is None
    nothing = cutin.compare([(5.0, 100.0)] * 5, [])
    assert nothing.ratio is None and cutin.decide(nothing, 0.2) == "too_early"


def test_뒤에_한_건도_없으면_비는_0_이고_위_끝만_있다() -> None:
    found = cutin.compare([(50.0, 1000.0)] * 10, [(0.0, 1000.0)] * 3)
    assert found.ratio == 0.0 and found.low == 0.0
    assert found.high is not None and 0 < found.high < 0.2
    assert cutin.decide(found, 0.2) == "reduced"


def test_앞_건수가_아주_적고_흔들림이_크면_위_끝이_없다고_말한다() -> None:
    """앞 1건 · 뒤가 크게 흔들림(φ ≈ 64) — 실효 건수가 0.016건이라 위 끝의 π 가 1.0 으로
    떨어져 0 으로 나눴고, 그 요청은 500 이었다(2026-10-08). 위 끝은 없음(None)이고 결론은
    「아직 이르다」 다."""
    before = [(1.0, 1000.0)] + [(0.0, 1000.0)] * 3
    after = [(10.0, 1000.0), (200.0, 1000.0), (50.0, 1000.0), (5.0, 1000.0)]
    found = cutin.compare(before, after)
    assert found.ratio == pytest.approx(265.0) and found.dispersion > 60
    assert found.high is None and found.low is not None and found.low < 1
    assert cutin.decide(found, 0.2) == "too_early"


def test_흔들림의_자유도는_잰_평균의_수만큼_뺀다() -> None:
    """피어슨 χ² 의 자유도 = 부분군 수 - 잰 모수의 수. 양쪽은 각자의 평균(둘)이라 n - 2,
    한쪽만(앞쪽 추세의 φ · 뒤가 0 건)이면 평균 하나라 n - 1 이다."""
    groups = [(10.0, 100.0), (20.0, 100.0), (30.0, 100.0), (40.0, 100.0), (50.0, 100.0)]
    chi2 = (400 + 100 + 0 + 100 + 400) / 30  # 평균 30 둘레
    assert cutin.dispersion(groups, []) == pytest.approx(chi2 / 4)
    # 뒤(모두 5건 — χ² 0)를 더하면 부분군 아홉에 평균 둘.
    assert cutin.dispersion(groups, [(5.0, 100.0)] * 4) == pytest.approx(chi2 / 7)
    # 뒤가 0 건이면 그쪽은 평균을 안 잰다(χ² 에도 안 든다) — 앞만의 n - 1 그대로.
    assert cutin.dispersion(groups, [(0.0, 100.0)] * 4) == pytest.approx(chi2 / 4)


def test_크기가_크고_비가_1_언저리면_차이_없음() -> None:
    found = cutin.compare([(1000.0, 100_000.0)] * 12, [(1005.0, 100_000.0)] * 12)
    assert cutin.decide(found, 0.2) == "no_difference"


def test_변화가_없으면_과분산이어도_잘못_결론이_드물다() -> None:
    """부분군 평균 50 · 분산이 평균의 3배(음이항), 앞 12 · 뒤 6 — 포아송 그대로면 우연한 달
    차이를 효과로 읽는다. φ 로 넓히면 잘못 「줄었다 · 늘었다」 가 5% 언저리."""
    rng = np.random.default_rng(7)
    phi = 3.0
    wrong = plain_wrong = 0
    runs = 400
    for _ in range(runs):
        counts = rng.poisson(rng.gamma(50.0 / (phi - 1), phi - 1, 18))
        before = [(float(c), 1000.0) for c in counts[:12]]
        after = [(float(c), 1000.0) for c in counts[12:]]
        found = cutin.compare(before, after)
        wrong += cutin.decide(found, 0.2) in ("reduced", "increased")
        # 비교 — φ 없이(실효 건수 = 건수).
        c0 = sum(c for c, _ in before)
        c1 = sum(c for c, _ in after)
        from app.modules.metrics.recipes import _numeric

        low = _numeric.beta_ppf(0.025, c1, c0 + 1) if c1 else 0.0
        high = _numeric.beta_ppf(0.975, c1 + 1, c0)
        ratio_low, ratio_high = low / (1 - low) * 2, high / (1 - high) * 2
        plain_wrong += ratio_high < 1 or ratio_low > 1
    assert plain_wrong / runs > 0.15
    assert wrong / runs <= 0.08


def test_30퍼센트_줄면_대부분_잡는다() -> None:
    rng = np.random.default_rng(11)
    caught = 0
    runs = 300
    for _ in range(runs):
        before = [(float(rng.poisson(50)), 1000.0) for _ in range(12)]
        after = [(float(rng.poisson(35)), 1000.0) for _ in range(6)]
        caught += cutin.decide(cutin.compare(before, after), 0.2) == "reduced"
    assert caught / runs >= 0.9


def test_결론까지_더_닫혀야_할_부분군_수() -> None:
    # 앞 360건 — 20% 차이를 가리려면 뒤에 부분군 열둘쯤(부분군마다 기대 24건)이 든다.
    before = [(30.0, 1000.0)] * 12
    after = [(25.0, 1000.0)]
    more = cutin.more_needed(before, after, effect=0.2, phi=1.0)
    assert more == 11
    # 과분산이면 더 든다.
    wider = cutin.more_needed(before, after, effect=0.2, phi=1.5)
    assert wider is None or wider > more
    # 앞의 건수가 너무 적으면 뒤를 아무리 기다려도 20% 차이는 못 가린다.
    assert cutin.more_needed([(2.0, 1000.0)] * 3, [], effect=0.2, phi=1.0) is None


def test_앞쪽_추세는_내려가는_흐름을_잡고_평평하면_조용하다() -> None:
    falling = [(100.0 * 0.95**t, 1000.0) for t in range(12)]
    change, p_value = cutin.trend(falling)
    assert change is not None and change == pytest.approx(-0.05, abs=0.01)
    assert p_value is not None and p_value < 0.01
    flat_change, flat_p = cutin.trend([(100.0, 1000.0)] * 12)
    assert flat_change == pytest.approx(0.0, abs=1e-9) and flat_p is not None and flat_p > 0.5
    assert cutin.trend([(1.0, 10.0)] * 3) == (None, None)
