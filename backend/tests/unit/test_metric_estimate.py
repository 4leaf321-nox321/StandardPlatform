"""셀 수 어림 — 표본의 조합 수를 늘리는 식과 표본의 문턱(ADR 0023).

지키는 것: 표본이 전부면 센 수 그대로 · 조합이 `a · n^b` 로 늘면 그 식대로 늘린다(b 가 1 이면
줄에 비례, 0 이면 그대로) · 줄 수를 넘지 않는다 · 셀 어림(chao1 · GEE 의 큰 쪽)이 규모 DB 의
실측 표본 통계에서 실제 셀의 0.9 ~ 1.5배 · 문턱은 표본 크기만큼의 몫이다.
"""

from __future__ import annotations

import uuid

import pytest

from app.modules.metrics.spec import SAMPLE_ROWS, estimate_combos, extrapolate, sample_cut


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


def test_표본이_전부면_조합_통계도_센_수_그대로() -> None:
    assert estimate_combos([(10, 4), (20, 7)], 3, 1, 20) == 7


@pytest.mark.parametrize(
    ("rows", "seen", "once", "twice", "total", "truth"),
    [
        # 규모 DB 실측(ADR 0023) — 표본 20만 줄의 조합 통계와 계산이 센 실제 셀.
        (200_000, 182_535, 170_496, 10_295, 1_999_728, 1_079_040),  # 코호트 · 200만 건
        (350_000, 265_779, 202_631, 52_841, 3_499_513, 692_348),  # 기본 모델 x 부품 · 200만
        (200_000, 176_894, 159_996, 14_452, 19_997_260, 1_655_254),  # 코호트 · 2,000만 건
    ],
)
def test_실측_표본_통계에서_실제_셀의_0_9_에서_1_5_배(
    rows: int, seen: int, once: int, twice: int, total: int, truth: int
) -> None:
    # 거듭제곱 늘리기는 줄에 비례(점 하나 — 기울기 1)라 위로만 묶는다.
    found = estimate_combos([(rows, seen)], once, twice, total)
    assert 0.9 * truth <= found <= 1.5 * truth, found


def test_조합_어림은_거듭제곱_늘리기와_줄_수를_넘지_않는다() -> None:
    # 다 나온 조합(한 번 · 두 번 나온 것이 없다) — 늘리지 않는다.
    assert estimate_combos([(25_000, 3_000), (200_000, 3_000)], 0, 0, 10_000_000) == 3_000
    # 줄마다 새 조합 — 줄 수를 넘지 않는다.
    assert estimate_combos([(200_000, 200_000)], 200_000, 0, 1_000_000) == 1_000_000


def test_표본의_문턱은_표본_크기만큼의_몫이다() -> None:
    assert sample_cut(SAMPLE_ROWS) is None
    cut = sample_cut(SAMPLE_ROWS * 4)
    assert cut is not None
    assert cut.int / 2**128 == pytest.approx(0.25)
    # 무작위 id 의 몫이 그 비율이다.
    ids = [uuid.uuid4() for _ in range(40_000)]
    share = sum(one < cut for one in ids) / len(ids)
    assert share == pytest.approx(0.25, abs=0.01)
