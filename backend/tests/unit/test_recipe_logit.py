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


def test_기록이_최소_건수_안_되는_수준은_불안정이고_오즈비를_싣지_않는다() -> None:
    """드문 값을 모은 「그 밖」 이 합쳐도 적으면(7건) 계수가 작아 보여도 흔들린다 — 오즈비
    5.19 로 1위에 서 AI 가 「가장 많은 처리」 로 옮길 뻔했다(2026-11-08 정답 셋 시험)."""
    from types import SimpleNamespace

    groups = [
        logit.Group(("부품 교체",), 400, 5600),
        logit.Group(("점검 후 반환",), 400, 2600),
        logit.Group(("드문 1",), 2, 2),
        logit.Group(("드문 2",), 1, 2),
    ]
    found = logit.fit(groups, 1, min_count=30)
    dim = SimpleNamespace(axis=SimpleNamespace(label="처리"))
    (factor,) = logit._factors(found, ["handling"], [dim], {}, False, 30)  # type: ignore[list-item]
    by_label = {one.label: one for one in factor.levels}
    other = by_label[logit.OTHER_LABEL]
    assert (
        other.count == 7 and other.unstable and other.odds_ratio is None and other.ci is None
    )
    kept = by_label["점검 후 반환"]
    assert not kept.unstable and kept.odds_ratio is not None and kept.odds_ratio > 1
