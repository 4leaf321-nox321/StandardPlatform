"""⑨ 연관 · 묶음 — 심은 덩어리를 되찾고, 덩어리가 없으면 짝을 지어내지 않아야 한다(DB 없이)."""

from __future__ import annotations

import numpy as np
import pytest

from app.modules.metrics.recipes import assoc, logit


def _blocks(seed: int = 1) -> np.ndarray:
    """증상 8 x 부품 10 — 증상 0~3 은 부품 0~4 와, 증상 4~7 은 부품 5~9 와 자주 함께 나온다."""
    rng = np.random.default_rng(seed)
    rate = np.full((8, 10), 2.0)
    rate[:4, :5] = 20.0
    rate[4:, 5:] = 20.0
    return np.asarray(rng.poisson(rate), dtype=np.float64)


def test_BH_는_순서를_지키고_단조다() -> None:
    q = assoc.benjamini_hochberg([0.01, 0.04, 0.03, 0.005])
    # 정렬하면 0.005 · 0.01 · 0.03 · 0.04 → x4/1, x4/2, x4/3, x4/4 = 0.02 · 0.02 · 0.04 · 0.04.
    assert q == pytest.approx([0.02, 0.04, 0.04, 0.02])
    assert assoc.benjamini_hochberg([]) == []
    assert assoc.benjamini_hochberg([0.9, 0.95]) == pytest.approx([0.95, 0.95])


def test_향상도는_기대보다_몇_배인가() -> None:
    table = np.array([[30.0, 10.0], [10.0, 50.0]])
    found = {(one.row, one.col): one for one in assoc.pairs(table, min_count=5)}
    one = found[(0, 0)]
    assert one.expected == pytest.approx(40 * 40 / 100)
    assert one.lift == pytest.approx(30 / 16)
    assert one.p_value < 1e-4 and one.q_value >= one.p_value
    # 덜 나온 짝은 향상도가 1 아래이고 위쪽 꼬리 확률이 크다.
    assert found[(0, 1)].lift < 1 and found[(0, 1)].p_value > 0.5


def test_심은_덩어리의_짝과_묶음을_되찾는다() -> None:
    table = _blocks()
    found = assoc.pairs(table)
    strong = [one for one in found if one.lift > 1 and one.q_value < 0.05]
    assert strong and all((one.row < 4) == (one.col < 5) for one in strong)
    grouped = assoc.clusters(table)
    assert grouped is not None and grouped.k == 2
    assert len(set(grouped.labels[:4])) == 1 and len(set(grouped.labels[4:])) == 1
    assert grouped.labels[0] != grouped.labels[4]
    assert grouped.silhouette > 0.5
    mapped = assoc.correspondence(table)
    assert mapped is not None
    rows, cols, explained = mapped
    # 첫 축이 두 덩어리를 가른다 — 부호가 반대.
    assert np.sign(rows[:4, 0]).tolist() == [np.sign(rows[0, 0])] * 4
    assert np.sign(rows[4:, 0]).tolist() == [-np.sign(rows[0, 0])] * 4
    assert np.sign(cols[0, 0]) == np.sign(rows[0, 0])
    assert explained > 0.5


def test_덩어리가_없으면_짝을_지어내지_않는다() -> None:
    rng = np.random.default_rng(2)
    rows = rng.uniform(1, 3, 12)
    cols = rng.uniform(1, 3, 15)
    table = rng.poisson(np.outer(rows, cols) * 3).astype(np.float64)
    found = assoc.pairs(table)
    assert not [one for one in found if one.q_value < 0.05]
    grouped = assoc.clusters(table)
    assert grouped is not None and grouped.silhouette < 0.3


def test_묶음_자르기와_실루엣() -> None:
    distance = np.array(
        [
            [0.0, 0.1, 0.9, 0.9],
            [0.1, 0.0, 0.9, 0.9],
            [0.9, 0.9, 0.0, 0.2],
            [0.9, 0.9, 0.2, 0.0],
        ]
    )
    merges = assoc.average_linkage(distance)
    assert assoc.cut(merges, 4, 2) == [0, 0, 1, 1]
    assert assoc.cut(merges, 4, 4) == [0, 1, 2, 3]
    assert assoc.silhouette(distance, [0, 0, 1, 1]) == pytest.approx(
        np.mean([1 - 0.1 / 0.9, 1 - 0.1 / 0.9, 1 - 0.2 / 0.9, 1 - 0.2 / 0.9])
    )


def test_요인이_겹치면_오즈비를_가르지_않는다() -> None:
    """홀수 증상이 F2 · F4 에서만 나오면 「홀수 증상 더미의 합 = F2 + F4」 — 계수가 하나로 안
    정해진다(리허설에서 겪은 것). LR 은 그대로 낸다."""
    groups = []
    for f in range(4):
        for s in range(10):
            if f % 2 != s % 2:
                continue
            groups.append(logit.Group((f"F{f + 1}", f"S{s:02d}"), 50.0 + 5 * f + s, 450.0))
    found = logit.fit(groups, 2)
    assert found.aliased and found.covariance is None
    assert len(found.lr) == 2


def test_원인분산도는_원인이_몇_개에_고르게_갈렸나다() -> None:
    """한 원인에 몰리면 1, 둘에 반반이면 2, 넷에 고르면 4 — 많이 갈린 증상부터 선다. 몇 건짜리
    증상은 우연이 값을 정해 뺀다."""
    table = np.array(
        [[100.0, 0, 0, 0], [50, 50, 0, 0], [25, 25, 25, 25], [5, 5, 0, 0]], dtype=np.float64
    )
    found = assoc.dispersion(table, minimum=20)
    assert [one.row for one in found] == [2, 1, 0]
    assert [one.effective for one in found] == pytest.approx([4.0, 2.0, 1.0])
    assert found[2].top == 0 and found[2].top_share == 1.0 and found[2].causes == 1
    assert assoc.effective_count(np.zeros(3)) == 0.0
