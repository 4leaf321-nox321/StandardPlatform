"""⑤ 집단 비교 — 정답을 아는 합성 자료로(DB 없이).

집단마다 비율이 같으면 「다르다」 가 나오지 않아야 하고, 뜨거운 집단은 잡아야 하며, 대수가 작은
집단의 우연한 높은 비율은 전체 쪽으로 줄여 「나쁘다」 로 읽히지 않아야 한다.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.modules.metrics.recipes import groups


def test_이질성_카이제곱은_손셈과_같다() -> None:
    found = groups.compare([10, 30], [1000, 1000])
    assert found is not None
    # 전체 0.02 — 기대 20 · 20, (10-20)²/20 + (30-20)²/20 = 10.
    assert found.pooled == pytest.approx(0.02)
    assert found.chi2 == pytest.approx(10.0) and found.df == 1
    assert found.p_value == pytest.approx(0.00157, abs=1e-4)


def test_비율이_같으면_모두_전체로_줄이고_다르다고_하지_않는다() -> None:
    found = groups.compare([20, 40, 60], [1000, 2000, 3000])
    assert found is not None and found.tau2 == 0.0
    assert all(
        one.shrunk == pytest.approx(0.02) and one.shrinkage == 1.0 for one in found.rows
    )
    assert all(one.q_value > 0.9 for one in found.rows)
    assert groups.compare([0, 0], [100, 100]) is None  # 건수가 없으면 견줄 것이 없다


def test_모두_같으면_어느_하나라도_다르다고_하는_일이_드물다() -> None:
    """집단 40개 · 같은 비율 1% · 대수 1천~10만 — 200번 중 하나라도 q < 0.05 인 번이 α
    언저리."""
    rng = np.random.default_rng(5)
    exposures = np.exp(rng.uniform(np.log(1e3), np.log(1e5), 40))
    any_flag = 0
    for _ in range(200):
        counts = rng.poisson(exposures * 0.01)
        found = groups.compare(counts.tolist(), exposures.tolist())
        assert found is not None
        any_flag += any(one.q_value < 0.05 for one in found.rows)
    assert any_flag / 200 <= 0.08


def test_뜨거운_집단은_잡고_작은_집단의_우연은_줄인다() -> None:
    """공장 넷 — F3 은 두 배, F4 는 대수 150대에 우연히 4건(그대로면 2.7%)."""
    counts = [205, 190, 410, 4]
    exposures = [20_000.0, 20_000.0, 20_000.0, 150.0]
    found = groups.compare(counts, exposures)
    assert found is not None and found.p_value < 1e-6
    hot, small = found.rows[2], found.rows[3]
    assert hot.q_value < 1e-6 and hot.shrunk > found.pooled
    assert small.rate == pytest.approx(4 / 150)
    assert small.q_value > 0.05 and small.shrinkage > 0.5
    assert small.shrunk < small.rate  # 우연한 높은 비율을 전체 쪽으로 줄였다
    assert hot.low is not None and hot.high is not None and hot.low < hot.shrunk < hot.high


def test_줄인_비율이_그대로_비율보다_참값에_가깝다() -> None:
    """참 비율이 감마(평균 1%, 변동 30%)에서 온 집단 60개, 대수 200~20만 — 평균 제곱 오차."""
    rng = np.random.default_rng(11)
    raw_error, shrunk_error = 0.0, 0.0
    for _ in range(30):
        truth = rng.gamma(1 / 0.09, 0.01 * 0.09, 60)
        exposures = np.exp(rng.uniform(np.log(200), np.log(2e5), 60))
        counts = rng.poisson(truth * exposures)
        found = groups.compare(counts.tolist(), exposures.tolist())
        assert found is not None
        raw_error += float(np.mean((np.array([one.rate for one in found.rows]) - truth) ** 2))
        shrunk_error += float(
            np.mean((np.array([one.shrunk for one in found.rows]) - truth) ** 2)
        )
    assert shrunk_error < 0.5 * raw_error
