"""⑦ 파레토 · 집중도 — 손으로 셈한 값과 같아야 한다(DB 없이)."""

from __future__ import annotations

import pytest

from app.modules.metrics.recipes import pareto


def test_손으로_셈한_집중도() -> None:
    found = pareto.concentration([50, 30, 20])
    assert found is not None
    assert found.categories == 3
    assert found.hhi == pytest.approx(0.38)
    assert found.effective == pytest.approx(1 / 0.38)
    # 지니 — 오름차순 [20, 30, 50]: 2·(1·20 + 2·30 + 3·50)/(3·100) - 4/3 = 0.2
    assert found.gini == pytest.approx(0.2)
    assert found.hhi_norm == pytest.approx((0.38 - 1 / 3) / (1 - 1 / 3))
    assert found.cr[1] == pytest.approx(0.5) and found.cr[3] == pytest.approx(1.0)
    assert found.cr[10] == pytest.approx(1.0)  # 값이 셋뿐이면 상위 10개 = 전부
    assert found.vital_few == 2 and found.vital_share == pytest.approx(2 / 3)


def test_고르면_HHI_는_1_나누기_K_지니는_0() -> None:
    found = pareto.concentration([7] * 10)
    assert found is not None
    assert found.hhi == pytest.approx(0.1) and found.effective == pytest.approx(10)
    assert found.gini == pytest.approx(0.0, abs=1e-12) and found.hhi_norm == pytest.approx(0.0)


def test_한_값뿐이거나_없으면() -> None:
    one = pareto.concentration([5])
    assert one is not None and one.hhi == 1.0 and one.gini == 0.0 and one.vital_few == 1
    assert pareto.concentration([]) is None
    assert pareto.concentration([0, 0]) is None


def test_ABC_는_80_에_처음_닿는_값까지_A() -> None:
    assert pareto.abc([0.5, 0.3, 0.2]) == ["A", "A", "B"]
    # 80% 를 넘기는 값도 A 다(처음 닿는 값까지 포함), 95% 를 넘기는 값까지 B.
    assert pareto.abc([0.7, 0.2, 0.06, 0.04]) == ["A", "A", "B", "C"]
    assert pareto.abc([1.0]) == ["A"]
