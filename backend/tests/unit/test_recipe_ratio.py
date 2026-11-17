"""④-2 전작 대비 배율(ADR 0082) — 배율 · 구간은 손셈과, 구간의 덮는 몫은 포아송 흐름과 견준다.
값마다 나누는 틀(`_split`)은 분모가 그 기준과 짝인 때와 아닌 때를 본다."""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pytest

from app.modules.metrics import query
from app.modules.metrics.recipes import life, ratio


def _cohorts(units: float, per_age: list[int], months: int) -> list[life.Cohort]:
    """코호트 `months` 개 — 코호트 i 는 경과 `months - 1 - i` 까지 닫혔다(늦게 판 것일수록
    짧다)."""
    return [
        life.Cohort(
            start=date(2024, 1 + i % 12, 1) if i < 12 else date(2025, 1 + i % 12, 1),
            units=units,
            horizon=months - 1 - i,
            counts={a: per_age[a] for a in range(months - i)},
        )
        for i in range(months)
    ]


def test_같은_비율이면_배율_1_과_구간이_1_을_품는다() -> None:
    reference = _cohorts(1000, [4] * 24, 24)
    target = _cohorts(1000, [4] * 24, 12)
    got = ratio.estimate(target, reference)
    assert got.ratio == pytest.approx(1.0)
    assert got.low is not None and got.high is not None and got.low < 1 < got.high
    assert got.verdict == "unclear" and got.new is False


def test_세_배면_배율_3_이고_높다고_선다() -> None:
    reference = _cohorts(1000, [2] * 24, 24)
    target = _cohorts(1000, [6] * 24, 12)
    got = ratio.estimate(target, reference)
    assert got.ratio == pytest.approx(3.0)
    assert got.verdict == "higher" and got.low is not None and got.low > 1


def test_구간은_두_모델의_흔들림을_함께_넣는다() -> None:
    """ln 척도 반폭 = 1.96 √(1/O + V_E/E²) — 손셈. 전작이 작으면(V_E 큼) 넓어진다."""
    reference = [life.Cohort(date(2024, 1, 1), 100.0, 1, {0: 4, 1: 6})]
    target = [life.Cohort(date(2025, 1, 1), 200.0, 1, {0: 10, 1: 14})]
    got = ratio.estimate(target, reference, dispersion=1.0)
    # 비율 r0 = 0.04 · r1 = 0.06, 기대 = 200·0.04 + 200·0.06 = 20, 관측 24.
    assert got.expected == pytest.approx(20.0) and got.observed == 24
    v_e = 200**2 * 4 / 100**2 + 200**2 * 6 / 100**2
    half = ratio.Z * math.sqrt(1 / 24 + v_e / 20**2)
    assert got.ratio == pytest.approx(1.2)
    assert got.low == pytest.approx(1.2 * math.exp(-half))
    assert got.high == pytest.approx(1.2 * math.exp(half))
    # 단순 배율 — (24/200) ÷ (10/100) = 1.2(경과 구성이 같으니 같다).
    assert got.crude == pytest.approx(1.2)


def test_전작에_없던_값은_배율이_없고_0건이면_위_끝만() -> None:
    reference = _cohorts(1000, [0] * 12, 12)
    target = _cohorts(1000, [1] * 12, 6)
    got = ratio.estimate(target, reference)
    assert got.ratio is None and got.new is True and got.verdict == "unclear"
    none = ratio.estimate(_cohorts(1000, [0] * 12, 6), _cohorts(1000, [3] * 12, 12))
    assert none.ratio == 0.0 and none.low == 0.0
    # 0 건의 정확 포아송 위 끝(3.69 / E)에 전작 비율의 흔들림만큼 넓힌 것 — 그보다 조금 크다.
    exact = 3.6889 / none.expected
    assert none.high is not None and exact < none.high < 1.5 * exact
    assert none.verdict == "lower"


def test_전작이_못_간_경과의_건수는_배율에서_뺀다() -> None:
    reference = _cohorts(1000, [2] * 6, 6)  # 전작은 경과 5 까지
    target = [life.Cohort(date(2025, 1, 1), 1000.0, 9, {a: 2 for a in range(10)})]
    got = ratio.estimate(target, reference)
    assert got.beyond == 8 and got.observed == 12
    assert got.ratio == pytest.approx(1.0)
    assert got.target_records == 20  # 단순 배율의 분자는 닫힌 경과 전부


def test_95퍼센트_구간이_참값을_덮는_몫() -> None:
    """참 배율 1.5 — 포아송으로 뽑은 400번 중 구간이 1.5 를 덮는 몫이 95% 언저리."""
    rng = np.random.default_rng(7)
    ages = 18
    covered = 0
    for _ in range(400):
        reference = [
            life.Cohort(
                date(2023, 1, 1),
                2000.0,
                ages - 1,
                {a: int(rng.poisson(6)) for a in range(ages)},
            )
        ]
        target = [
            life.Cohort(
                date(2025, 1, 1),
                1000.0,
                ages - 1,
                {a: int(rng.poisson(4.5)) for a in range(ages)},
            )
        ]
        got = ratio.estimate(target, reference, dispersion=1.0)
        assert got.low is not None and got.high is not None
        covered += got.low <= 1.5 <= got.high
    assert 0.92 <= covered / 400 <= 0.98


def _frame(cells: list[query.Cell], den: query.Denominator | None) -> query.Frame:
    return query.Frame(cells, False, den, None, date(2030, 1, 1))


def test_값마다_나누면_분모가_짝인_기준은_그_값의_대수다() -> None:
    month = date(2025, 1, 1)
    cells = [
        query.Cell({"market": "KR"}, None, month, 0, 3, 0, None, None, None),
        query.Cell({"market": "US"}, None, month, 0, 5, 0, None, None, None),
    ]
    den = query.Denominator(
        metric=None,  # type: ignore[arg-type]
        spec=None,  # type: ignore[arg-type]
        on=["market"],
        time="cohort",
        per=1.0,
        values={(("KR",), month): 100.0, (("US",), month): 400.0, (("JP",), month): 50.0},
        truncated=False,
    )
    frame = _frame(cells, den)
    assert ratio._values(frame, "market", shared_units=False) == {"KR", "US", "JP"}
    kr = ratio._split(frame, "market", "KR", shared_units=False)
    assert [one.count for one in kr.cells] == [3] and kr.cells[0].dims == {}
    assert kr.denominator is not None and kr.denominator.on == []
    assert kr.denominator.values == {((), month): 100.0}
    # 짝이 아닌 기준(증상)은 모델의 대수를 그대로 함께 쓴다.
    shared = query.Denominator(
        metric=None,  # type: ignore[arg-type]
        spec=None,  # type: ignore[arg-type]
        on=[],
        time="cohort",
        per=1.0,
        values={((), month): 1000.0},
        truncated=False,
    )
    noise = ratio._split(
        _frame(
            [query.Cell({"symptom": "소음"}, None, month, 0, 2, 0, None, None, None)], shared
        ),
        "symptom",
        "소음",
        shared_units=True,
    )
    assert noise.denominator is shared and [one.count for one in noise.cells] == [2]
