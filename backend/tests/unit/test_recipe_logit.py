"""⑥ 재방문 위험 요인 — 묶인 로지스틱이 손셈 · 합성 자료의 정답과 같아야 한다(DB 없이)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.modules.metrics.recipes import logit


def test_이진_요인의_오즈비는_ad_나누기_bc() -> None:
    """요인 하나 두 수준이면 모형이 포화다 — 오즈비가 2x2 표의 교차곱비와 같고, LR 통계량은
    독립성의 G² 와 같다."""
    a_yes, a_no, b_yes, b_no = 30.0, 170.0, 60.0, 90.0
    found = logit.fit(
        [logit.Group(("A",), a_yes, a_no), logit.Group(("B",), b_yes, b_no)], 1, min_count=10
    )
    assert found.references == ["A"]
    assert math.exp(found.beta[1]) == pytest.approx((b_yes * a_no) / (b_no * a_yes))
    assert found.deviance == pytest.approx(0.0, abs=1e-8)
    total = a_yes + a_no + b_yes + b_no
    yes = a_yes + b_yes
    g2 = 0.0
    for count, rows, cols in (
        (a_yes, a_yes + a_no, yes),
        (a_no, a_yes + a_no, total - yes),
        (b_yes, b_yes + b_no, yes),
        (b_no, b_yes + b_no, total - yes),
    ):
        g2 += count * math.log(count * total / (rows * cols))
    statistic, df, p_value = found.lr[0]
    assert statistic == pytest.approx(2 * g2) and df == 1 and p_value < 1e-6
    assert found.null_deviance == pytest.approx(2 * g2)


def test_두_요인의_오즈비를_되찾는다() -> None:
    """공장(1 · 2 · 0.5) x 증상(1 · 3), 기본 확률 0.1 — 조합마다 이항으로 뽑는다."""
    rng = np.random.default_rng(3)
    base = math.log(0.1 / 0.9)
    factory = {"F1": 0.0, "F2": math.log(2.0), "F3": math.log(0.5)}
    symptom = {"S1": 0.0, "S2": math.log(3.0)}
    groups = []
    for f_key, f_effect in factory.items():
        for s_key, s_effect in symptom.items():
            n = 6000 if s_key == "S1" else 3000
            p = 1 / (1 + math.exp(-(base + f_effect + s_effect)))
            yes = float(rng.binomial(n, p))
            groups.append(logit.Group((f_key, s_key), yes, n - yes))
    found = logit.fit(groups, 2)
    assert found.references == ["F1", "S1"]
    odds = {
        (column.factor, column.level): math.exp(found.beta[index])
        for index, column in enumerate(found.columns, start=1)
    }
    assert odds[(0, "F2")] == pytest.approx(2.0, rel=0.1)
    assert odds[(0, "F3")] == pytest.approx(0.5, rel=0.1)
    assert odds[(1, "S2")] == pytest.approx(3.0, rel=0.1)
    assert found.converged and found.covariance is not None
    assert all(p_value < 1e-10 for _, _, p_value in found.lr)
    assert found.auc is not None and 0.6 < found.auc < 0.8


def test_적거나_한쪽이_0_인_수준은_그_밖으로() -> None:
    groups = [
        logit.Group(("A",), 30, 170),
        logit.Group(("B",), 0, 90),  # 예가 없다 — 오즈비 0
        logit.Group(("C",), 5, 3),  # 여덟 건
    ]
    found = logit.fit(groups, 1, min_count=10)
    assert found.sizes == [[("A", 1), (logit.OTHER, 2)]]
    levels = {one.levels[0]: (one.yes, one.no) for one in found.groups}
    assert levels == {"A": (30, 170), logit.OTHER: (5, 93)}


def test_AUC_는_점수가_가르는_정도() -> None:
    assert logit.auc([1, 0], [0, 1], [2.0, 1.0]) == 1.0
    assert logit.auc([0, 1], [1, 0], [2.0, 1.0]) == 0.0
    assert logit.auc([1, 1], [1, 1], [1.0, 1.0]) == 0.5
    # 셋 — 예 (점수 3: 2건, 점수 1: 1건), 아니오 (점수 2: 1건, 점수 1: 1건).
    found = logit.auc([2, 0, 1], [0, 1, 1], [3.0, 2.0, 1.0])
    # 짝 6개: 3>2 x2, 3>1 x2, 1 vs 2 진다 x1, 1 vs 1 비김 x1 → (4 + 0.5) / 6.
    assert found == pytest.approx(4.5 / 6)
    assert logit.auc([0, 0], [1, 1], [1.0, 2.0]) is None
