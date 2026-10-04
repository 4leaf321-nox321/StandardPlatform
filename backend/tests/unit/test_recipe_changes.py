"""⑩ 계절 · 변화점 — 계단을 심은 합성 자료에서 자리를 찾고, 없으면 지어내지 않아야 한다.

합성 자료는 월 단위(계절 12달). 과분산은 음이항(분산 = φ · 평균)으로 낸다.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.modules.metrics.recipes import changes


def _series(
    rng: np.random.Generator,
    n: int,
    base: float,
    *,
    step_at: int | None = None,
    step: float = 0.0,
    amplitude: float = 0.0,
    phi: float = 1.0,
) -> tuple[list[float], list[float], list[int]]:
    t = np.arange(n)
    season = 1 + amplitude * np.sin(2 * np.pi * t / 12)
    level = np.full(n, base)
    if step_at is not None:
        level[step_at:] *= 1 + step
    mu = level * season
    if phi > 1:
        size = mu / (phi - 1)
        y = rng.negative_binomial(size, size / (size + mu))
    else:
        y = rng.poisson(mu)
    return [float(one) for one in y], [1.0] * n, [int(one) % 12 for one in t]


def _changes(fit: changes.Fit) -> list[int]:
    return fit.starts[1:]


def test_잡음_없는_계단은_그_자리에서_나눈다() -> None:
    y = [100.0] * 20 + [130.0] * 16
    assert changes.partition(y, [1.0] * 36, penalty=3 * math.log(36)) == [20, 36]
    assert changes.partition([100.0] * 36, [1.0] * 36, penalty=3 * math.log(36)) == [36]
    # 대수가 다르면 비율로 — 건수는 그대로여도 대수가 반이 되면 비율이 두 배다.
    exposure = [1000.0] * 18 + [500.0] * 18
    assert changes.partition([50.0] * 36, exposure, penalty=3 * math.log(36)) == [18, 36]


def test_변화가_없으면_변화점을_지어내지_않는다() -> None:
    rng = np.random.default_rng(42)
    found = [_changes(changes.detect(*_series(rng, 36, 100.0), 12)) for _ in range(200)]
    assert sum(1 for one in found if not one) / len(found) >= 0.95


def test_계절이_있어도_변화가_없으면_지어내지_않는다() -> None:
    rng = np.random.default_rng(7)
    fits = [changes.detect(*_series(rng, 48, 100.0, amplitude=0.2), 12) for _ in range(200)]
    assert sum(1 for one in fits if not _changes(one)) / len(fits) >= 0.95
    assert sum(1 for one in fits if one.seasonal is not None) / len(fits) >= 0.95


def test_계절과_30퍼센트_계단을_가른다() -> None:
    rng = np.random.default_rng(42)
    hits = 0
    for _ in range(100):
        fit = changes.detect(*_series(rng, 48, 100.0, step_at=24, step=0.3, amplitude=0.2), 12)
        found = _changes(fit)
        hits += len(found) == 1 and abs(found[0] - 24) <= 1
    assert hits >= 90
    # 한 번을 자세히 — 수준의 비와 계절 지수.
    fit = changes.detect(
        *_series(np.random.default_rng(1), 48, 100.0, step_at=24, step=0.3, amplitude=0.2),
        12,
    )
    assert fit.levels[1] / fit.levels[0] == pytest.approx(1.3, rel=0.08)
    assert fit.seasonal is not None
    truth = 1 + 0.2 * np.sin(2 * np.pi * np.arange(12) / 12)
    assert float(np.corrcoef(fit.seasonal, truth)[0, 1]) > 0.9


def test_주기_한가운데의_계단도_계절이_삼키지_않는다() -> None:
    """건수가 크고(달 1만) 과분산(φ=20)인 3 년 — 계단이 둘째 해 한가운데에 있으면 계절
    지수를 수준 하나로 잡을 때 계단이 지수에 반쯤 들어간다."""
    rng = np.random.default_rng(3)
    hits = 0
    for _ in range(100):
        fit = changes.detect(
            *_series(rng, 36, 10000.0, step_at=18, step=0.1, amplitude=0.1, phi=20.0), 12
        )
        hits += any(abs(one - 18) <= 2 for one in _changes(fit))
    assert hits >= 75


def test_과분산이면_작은_흔들림을_변화점으로_읽지_않는다() -> None:
    rng = np.random.default_rng(11)
    fits = [changes.detect(*_series(rng, 36, 100.0, phi=3.0), 12) for _ in range(200)]
    assert sum(1 for one in fits if _changes(one)) / len(fits) <= 0.10
    assert 2.0 < float(np.median([one.dispersion for one in fits])) < 4.5


def test_포아송이면_과분산은_1이다() -> None:
    rng = np.random.default_rng(5)
    fits = [changes.detect(*_series(rng, 36, 100.0), 12) for _ in range(50)]
    assert float(np.median([one.dispersion for one in fits])) < 1.5


def test_움직이는_중앙값은_계단을_비탈로_뭉개지_않는다() -> None:
    y = np.asarray([10.0] * 12 + [20.0] * 12)
    level = changes.rolling_level(y, np.ones(24), 7)
    assert level[:9].tolist() == [10.0] * 9 and level[15:].tolist() == [20.0] * 9


def test_수준_비의_구간() -> None:
    low, high = changes.ratio_interval(1.0, 1.3, 1200, 1560, phi=2.0) or (0.0, 0.0)
    spread = 1.959963984540054 * math.sqrt(2.0 * (1 / 1200 + 1 / 1560))
    assert low == pytest.approx(1.3 * math.exp(-spread))
    assert high == pytest.approx(1.3 * math.exp(spread))
    assert changes.ratio_interval(0.0, 1.0, 0, 10, phi=1.0) is None


def test_계절의_자리와_이름() -> None:
    from datetime import date

    assert changes.season_length("month") == 12 and changes.season_length("week") == 1
    assert changes.season_of(date(2026, 3, 1), "month") == 2
    assert changes.season_of(date(2026, 7, 1), "quarter") == 2
    assert changes.season_label(2, "month") == "3월"
    assert changes.season_label(0, "quarter") == "1분기"
    assert changes.season_label(changes.season_of(date(2026, 10, 3), "day"), "day") == "토"


def test_여럿을_함께_보면_벌점을_올려_헛_변화점이_값의_수만큼_늘지_않는다() -> None:
    """값 10개 · 과분산 3배인 평평한 줄 — 하나씩 보는 벌점이면 열 중 하나가 우연히 변화점을
    내는 일이 잦다. 2·ln K 를 더하면 가족 단위로 줄고, 포아송 그대로의 30% 계단은 여전히
    찾는다."""
    assert changes.scan_penalty(1) == 0.0
    assert changes.scan_penalty(10) == pytest.approx(2 * np.log(10))
    rng = np.random.default_rng(3)
    n, k, families = 36, 10, 40
    flat = [0] * n

    def any_change(extra: float) -> float:
        hits = 0
        for _ in range(families):
            found = False
            for _ in range(k):
                lam = rng.gamma(100.0 / 2.0, 2.0, n)  # 평균 100 · φ 3
                fit = changes.detect(
                    rng.poisson(lam).tolist(), [1.0] * n, flat, 1, extra=extra
                )
                found = found or len(fit.starts) > 1
            hits += found
        return hits / families

    alone, scanned = any_change(0.0), any_change(changes.scan_penalty(k))
    assert scanned <= alone and scanned <= 0.05
    caught = 0
    for _ in range(40):
        mean = np.full(n, 100.0)
        mean[18:] *= 1.3
        fit = changes.detect(
            rng.poisson(mean).tolist(), [1.0] * n, flat, 1, extra=changes.scan_penalty(k)
        )
        caught += any(abs(start - 18) <= 1 for start in fit.starts[1:])
    assert caught / 40 >= 0.85
