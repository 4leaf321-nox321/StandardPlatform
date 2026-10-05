"""비슷한 기록의 무게 · 점수 — 손셈(DB 없음)."""

from __future__ import annotations

import math

import pytest

from app.modules.objects.similar import Tag, score, weights


def test_무게는_드물수록_크고_모두가_가진_태그는_0() -> None:
    a, b, c = Tag("part", "p1"), Tag("model", "m1"), Tag("x", "none")
    found = weights({a: 1, b: 10, c: 0}, 10)
    assert found[a] == pytest.approx(math.log(10))
    assert found[b] == 0.0 and found[c] == 0.0


def test_점수는_무게를_단_자카드() -> None:
    a, b, c = Tag("k", "a"), Tag("k", "b"), Tag("k", "c")
    weight = {a: 2.0, b: 1.0, c: 1.0}
    assert score({a, b}, {a, b}, weight) == 1.0
    assert score({a, b}, {a, c}, weight) == pytest.approx(2.0 / 4.0)
    assert score({a}, {b}, weight) == 0.0
    assert score(set(), set(), weight) == 0.0
