"""③ 관리도 — 라니 보정 u-관리도의 한계와 넬슨 규칙의 자리를 손셈 · 합성 자료와 견준다."""

from __future__ import annotations

import math
from itertools import pairwise

import numpy as np
import pytest

from app.modules.metrics.recipes import control


def _all(size: int) -> list[bool]:
    return [True] * size


def test_라니_한계를_손으로_셈한다() -> None:
    counts = [10, 12, 8, 15, 9]
    exposures = [1000, 1200, 800, 1000, 900]
    found = control.chart(counts, exposures, use=_all(5), baseline=_all(5))
    center = sum(counts) / sum(exposures)
    sigma = [math.sqrt(center / n) for n in exposures]
    z = [(c / n - center) / s for c, n, s in zip(counts, exposures, sigma, strict=True)]
    raw = sum(abs(b - a) for a, b in pairwise(z)) / 4 / 1.128
    widen = max(1.0, raw)
    assert found.center == pytest.approx(center)
    assert found.sigma_z_raw == pytest.approx(raw)
    assert found.sigma_z == pytest.approx(widen)
    for index, s in enumerate(sigma):
        assert found.ucl[index] == pytest.approx(center + 3 * widen * s)
        assert found.lcl[index] == pytest.approx(max(0.0, center - 3 * widen * s))
        assert found.z[index] == pytest.approx(z[index] / widen)


def test_흔들림이_포아송보다_작아도_한계를_줄이지_않는다() -> None:
    # 대수에 꼭 비례한 건수 — z 가 모두 0 이라 이동 범위도 0.
    found = control.chart([10, 20, 30], [1000, 2000, 3000], use=_all(3), baseline=_all(3))
    assert found.sigma_z_raw == pytest.approx(0.0) and found.sigma_z == 1.0


def test_건수가_없으면_한계도_없다() -> None:
    found = control.chart([0, 0, 0], [100, 100, 100], use=_all(3), baseline=_all(3))
    assert found.center == 0.0 and found.ucl == [None] * 3 and found.signals == [[], [], []]
    nothing = control.chart([1, 2], [100, 100], use=_all(2), baseline=[False, False])
    assert nothing.center is None


@pytest.mark.parametrize(
    ("z", "values", "expected"),
    [
        # 규칙 1 — 한계 밖 한 점.
        ([0.1, -0.2, 3.5, 0.3], None, {2: [1]}),
        ([0.1, -3.2], None, {1: [1]}),
        # 규칙 2 — 한쪽에 아홉 점 연속(아홉째부터).
        ([0.5] * 10, [1, 2, 1, 2, 1, 2, 1, 2, 1, 2], {8: [2], 9: [2]}),
        ([0.5] * 8 + [-0.5] + [0.5], [1, 2, 1, 2, 1, 2, 1, 2, 1, 2], {}),
        # 규칙 3 — 여섯 점 연속 오름(다섯 번 오른 여섯째 점).
        ([0.1, -0.1] * 3, [1, 2, 3, 4, 5, 6], {5: [3]}),
        ([0.1, -0.1] * 3 + [0.1], [7, 6, 5, 4, 3, 2, 1], {5: [3], 6: [3]}),
        ([0.1, -0.1] * 3, [1, 2, 3, 3, 4, 5], {}),
        # 규칙 5 — 세 점 중 두 점이 2σ 밖, 같은 쪽. 그 무늬를 마친 점에.
        ([0.0, 2.5, 0.5, 2.4], [1, 2, 1, 2], {3: [5]}),
        ([2.5, -2.5, 2.1], [1, 2, 1], {2: [5]}),
        ([2.5, -2.5, -0.1], [1, 2, 1], {}),
    ],
)
def test_넬슨_규칙의_자리(
    z: list[float], values: list[float] | None, expected: dict[int, list[int]]
) -> None:
    found = control.nelson(z, values if values is not None else [1.0, 2.0] * len(z))
    assert {i: one for i, one in enumerate(found) if one} == expected


def test_빠진_점은_줄을_끊지_않고_건너뛴다() -> None:
    z: list[float | None] = [0.5] * 4 + [None] + [0.5] * 5
    values: list[float | None] = [1.0, 2.0] * 5
    found = control.nelson(z, values)
    assert found[4] == [] and found[9] == [2]


def test_큰_부분군의_과분산을_라니가_넓힌다() -> None:
    """대수 2만, 참 비율이 달마다 20% 흔들린다 — 포아송 한계는 점의 3분의 1 가까이를 신호로
    읽고, 라니 보정은 그 흔들림을 재어 넓힌다(참 σz ≈ √(1 + (0.2/0.0707)²) ≈ 3.0)."""
    rng = np.random.default_rng(1)
    size = 36
    exposures = [20000.0] * size
    rates = 0.01 * np.exp(rng.normal(0.0, 0.2, size))
    counts = [float(one) for one in rng.poisson(rates * 20000)]
    plain = control.chart(counts, exposures, use=_all(size), baseline=_all(size), laney=False)
    laney = control.chart(counts, exposures, use=_all(size), baseline=_all(size))
    plain_hits = sum(1 for one in plain.signals if 1 in one)
    laney_hits = sum(1 for one in laney.signals if 1 in one)
    assert laney.sigma_z is not None and 2.0 < laney.sigma_z < 4.5
    assert plain_hits >= 6 and laney_hits <= 1


def test_기준_구간으로_잡은_한계가_뒤의_계단을_잡는다() -> None:
    size = 36
    exposures = [5000.0] * size
    counts = [50.0 + (3 if i % 2 else -3) for i in range(24)] + [75.0] * 12
    baseline = [i < 24 for i in range(size)]
    found = control.chart(counts, exposures, use=_all(size), baseline=baseline)
    assert found.center == pytest.approx(50 / 5000)
    later = [one for one in found.signals[24:] if one]
    assert len(later) == 12 and all(1 in one for one in later)
    assert not any(found.signals[:24])


def test_분모가_없으면_건수_관리도다() -> None:
    counts = [4.0, 6.0, 5.0, 5.0]
    found = control.chart(counts, [1.0] * 4, use=_all(4), baseline=_all(4))
    assert found.center == pytest.approx(5.0)
    assert found.ucl[0] == pytest.approx(5 + 3 * math.sqrt(5) * (found.sigma_z or 1.0))
