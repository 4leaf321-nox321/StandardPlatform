"""눈여겨볼 것의 점수와 다듬기(ADR 0032) — DB 없이.

- 점수는 「몇 배 · 몇 건」 으로 설명된다: |ln 비| x log10(1 + 건수), 잠정이면 절반.
- 전체가 움직인 만큼은 뺀다, 같은 칸은 하나, 거른 값이 아무것도 더하지 않는 조합은 뺀다.
"""

from __future__ import annotations

import math
from typing import Any

from app.modules.metrics.insights import Found, score_of, tidy


def _found(**kw: Any) -> Found:
    base: dict[str, Any] = {
        "key": "k",
        "kind": "change",
        "recipe": "changes",
        "values": {},
        "title": "t",
        "detail": {},
        "score": 0.0,
        "run_id": None,
    }
    base.update(kw)
    one = Found(**base)
    if one.score == 0.0:
        one.score = score_of(one.ratio, one.support, one.provisional)
    return one


def test_점수는_세기와_근거의_곱이고_잠정이면_절반() -> None:
    assert score_of(2.0, 99) == round(math.log(2.0) * 2.0, 4)
    assert score_of(0.5, 99) == score_of(2.0, 99)  # 내려감도 같은 세기
    assert score_of(2.0, 99, provisional=True) == round(math.log(2.0), 4)
    assert score_of(None, 9) == 1.0  # 비를 모르면 세기 1
    assert score_of(3.0, 0) == 0.0  # 근거가 없으면 0


def test_전체가_움직인_만큼은_빼고_본다() -> None:
    overall = _found(
        key="down:2025-03-01", direction="down", at="2025-03-01", ratio=0.88, support=20000
    )
    same = _found(
        key="down:2025-04-01@서울",
        direction="down",
        at="2025-04-01",
        ratio=0.88,
        support=2000,
        cell=frozenset({("center", "서울")}),
    )
    sharp = _found(
        key="down:2025-04-01@브라질",
        direction="down",
        at="2025-04-01",
        ratio=0.31,
        support=2000,
        cell=frozenset({("country", "브라질")}),
    )
    kept = {one.key: one for one in tidy([overall, same, sharp], "month")}
    assert kept["down:2025-04-01@서울"].score == 0.0  # 전체와 같은 만큼 — 남는 것이 없다
    assert kept["down:2025-04-01@브라질"].score > 1.0
    assert kept["down:2025-04-01@브라질"].detail["overall_ratio"] == 0.88


def test_같은_칸은_하나_거른_값이_보태지_않는_조합은_뺀다() -> None:
    cell = frozenset({("country", "브라질"), ("base_model", "P5")})
    one = _found(
        key="a",
        direction="down",
        at="2026-03-01",
        ratio=0.3,
        support=100,
        cell=cell,
        combo=True,
    )
    mirror = _found(
        key="b",
        direction="down",
        at="2026-03-01",
        ratio=0.3,
        support=90,
        cell=cell,
        combo=True,
    )
    single = _found(
        key="c",
        kind="high",
        recipe="groups",
        direction="high",
        ratio=1.3,
        support=2600,
        cell=frozenset({("center", "뭄바이")}),
    )
    redundant = _found(
        key="d",
        kind="high",
        recipe="groups",
        direction="high",
        ratio=1.35,
        support=2600,
        cell=frozenset({("center", "뭄바이"), ("country", "인도")}),
        combo=True,
    )
    kept = {one.key for one in tidy([one, mirror, single, redundant], "month")}
    assert kept == {"a", "c"}


def test_그_값_때문에_전체가_오른_것은_그_값에서_빼지_않는다() -> None:
    """증상처럼 같은 대수를 나누는 기준은 비율이 더해진다 — 「그 값을 뺀 나머지」 의 비로
    나눈다. 누수가 100 → 150 이라 전체가 510 → 560 이 됐으면 나머지는 그대로(410 → 410)다."""
    overall = _found(
        key="up:2029-09-01",
        direction="up",
        at="2029-09-01",
        ratio=560 / 510,
        support=3000,
        detail={"before": 510.0, "after": 560.0},
    )
    leak = _found(
        key="up:2029-09-01@누수",
        direction="up",
        at="2029-09-01",
        ratio=1.5,
        support=300,
        cell=frozenset({("symptom", "누수")}),
        detail={"by": "symptom", "before": 100.0, "after": 150.0},
    )
    (kept,) = [
        one for one in tidy([overall, leak], "month", frozenset({"symptom"})) if one.cell
    ]
    assert kept.detail["rest_ratio"] == 1.0
    assert kept.score == score_of(1.5, 300)
    # 대수가 따로인 기준(분모 짝)이면 전체의 비로 나눈다.
    model = _found(
        key="up:2029-09-01@P5",
        direction="up",
        at="2029-09-01",
        ratio=1.5,
        support=300,
        cell=frozenset({("base_model", "P5")}),
        detail={"by": "base_model", "before": 100.0, "after": 150.0},
    )
    (kept,) = [
        one for one in tidy([overall, model], "month", frozenset({"symptom"})) if one.cell
    ]
    assert "rest_ratio" not in kept.detail
    assert kept.score == score_of(1.5 / (560 / 510), 300)
