"""셀 수 어림 — 표본의 조합 수를 늘리는 식과 표본의 문턱(ADR 0023).

지키는 것: 표본이 전부면 센 수 그대로 · 조합이 `a · n^b` 로 늘면 그 식대로 늘린다(b 가 1 이면
줄에 비례, 0 이면 그대로) · 줄 수를 넘지 않는다 · 펼친 줄의 비(`scale`)를 쓴다 · 문턱은 표본
크기만큼의 몫이다.
"""

from __future__ import annotations

import uuid

import pytest

from app.modules.metrics.spec import SAMPLE_ROWS, extrapolate, sample_cut


def _points(a: float, b: float) -> list[tuple[int, int]]:
    sizes = [25_000, 50_000, 100_000, 200_000]
    return [(n, round(a * n**b)) for n in sizes]


def test_표본이_전부면_센_수_그대로() -> None:
    assert extrapolate([(10, 4), (20, 7), (40, 9)], 40) == 9
    assert extrapolate([(10, 4)], 5) == 4
    assert extrapolate([], 1000) == 0


@pytest.mark.parametrize("b", [0.3, 0.5, 0.8])
def test_조합이_거듭제곱으로_늘면_그대로_늘린다(b: float) -> None:
    total = 20_000_000
    found = extrapolate(_points(5.0, b), total)
    # 표본의 조합 수는 정수라 작은 수(b=0.3 이면 백여 개)에서 1~2% 어긋난다.
    assert found == pytest.approx(5.0 * total**b, rel=0.02)


def test_조합이_줄마다_새로우면_줄_수에_비례하고_넘지_않는다() -> None:
    # 자유 글자 기준 — 줄마다 다른 값.
    fresh = [(n, n) for n in (25_000, 50_000, 100_000, 200_000)]
    assert extrapolate(fresh, 7_000_000) == 7_000_000
    # 잡음으로 기울기가 1 을 넘어도 줄 수를 넘지 않는다.
    assert extrapolate([(25_000, 1_000), (200_000, 200_000)], 1_000_000) == 1_000_000


def test_조합이_다_나왔으면_더_늘리지_않는다() -> None:
    flat = [(n, 3_000) for n in (25_000, 50_000, 100_000, 200_000)]
    assert extrapolate(flat, 50_000_000) == 3_000


def test_펼친_줄의_비를_쓴다() -> None:
    # 한 기록이 부품 셋으로 펼쳐진다 — 표본 20만 기록 = 60만 줄, 전체는 펼친 600만 줄.
    points = [(n, n) for n in (25_000, 50_000, 100_000, 200_000)]
    assert extrapolate(points, 6_000_000, scale=3.0) == pytest.approx(2_000_000, rel=0.01)


def test_표본의_문턱은_표본_크기만큼의_몫이다() -> None:
    assert sample_cut(SAMPLE_ROWS) is None
    cut = sample_cut(SAMPLE_ROWS * 4)
    assert cut is not None
    assert cut.int / 2**128 == pytest.approx(0.25)
    # 무작위 id 의 몫이 그 비율이다.
    ids = [uuid.uuid4() for _ in range(40_000)]
    share = sum(one < cut for one in ids) / len(ids)
    assert share == pytest.approx(0.25, abs=0.01)
