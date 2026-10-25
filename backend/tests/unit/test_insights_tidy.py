"""눈여겨볼 것의 점수와 다듬기(ADR 0032) — DB 없이.

- 점수는 「몇 배 · 몇 건」 으로 설명된다: |ln 적게 잡은 비| x log10(1 + 건수), 잠정이면 절반.
  적게 잡은 비는 비의 95% 구간 중 1 에 가까운 끝 — 작은 칸의 큰 비가 큰 칸 위에 서지 않게.
- 전체가 움직인 만큼은 뺀다, 같은 칸은 하나, 거른 값이 아무것도 더하지 않는 조합은 뺀다.
"""

from __future__ import annotations

import math
from typing import Any

from app.modules.metrics.insights import Found, bound_of, score_of, strength_of, tidy


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


def test_세기는_적게_잡은_비로_잰다() -> None:
    assert bound_of((1.2, 3.0)) == 1.2
    assert bound_of((0.2, 0.6)) == 0.6
    assert bound_of((0.8, 1.3)) == 1.0  # 1 을 걸치면 「달라졌다고 못 한다」
    assert bound_of(None) is None
    assert strength_of(2.0, (1.5, 2.6)) == math.log(1.5)
    assert strength_of(2.0, (0.9, 4.0)) == 0.0
    assert strength_of(2.0) == math.log(2.0)  # 구간을 모르면 비 그대로
    # 개발 DB 에서 드러난 것 — 28건의 0.31배가 2,852건의 1.39배 위에 섰다. 구간으로 재면
    # 아래로.
    small = score_of(0.31, 28, interval=(0.17, 0.58))
    big = score_of(1.39, 2852, interval=(1.34, 1.44))
    assert score_of(0.31, 28) > score_of(1.39, 2852)
    assert big > small


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


def test_전체를_뺄_때_구간도_같이_나눈다() -> None:
    overall = _found(
        key="up:2029-09-01", direction="up", at="2029-09-01", ratio=1.5, support=30000
    )
    kept_up = _found(
        key="up:2029-09-01@P5",
        direction="up",
        at="2029-09-01",
        ratio=1.8,
        interval=(1.6, 2.0),
        support=500,
        cell=frozenset({("base_model", "P5")}),
        detail={"by": "base_model"},
    )
    lost = _found(
        key="up:2029-09-01@P6",
        direction="up",
        at="2029-09-01",
        ratio=1.8,
        interval=(1.2, 2.7),
        support=500,
        cell=frozenset({("base_model", "P6")}),
        detail={"by": "base_model"},
    )
    kept = {one.key: one for one in tidy([overall, kept_up, lost], "month")}
    assert kept["up:2029-09-01@P5"].detail["ratio_bound"] == round(1.6 / 1.5, 4)
    assert kept["up:2029-09-01@P5"].score == score_of(
        1.2, 500, interval=(1.6 / 1.5, 2.0 / 1.5)
    )
    # 전체의 1.5배를 빼면 구간 0.8 ~ 1.8 — 전체보다 더 올랐다고 못 한다.
    assert kept["up:2029-09-01@P6"].detail["ratio_bound"] == 1.0
    assert kept["up:2029-09-01@P6"].score == 0.0


def test_위로의_변화점이_말한_관리도_신호는_뺀다() -> None:
    """계단이 2025-03 에 섰고 관리도는 그 끝(2025-05)에 걸렸다 — 「언제부터」 를 말하는 변화점
    하나면 된다. 다른 칸의 신호 · 변화점보다 앞선 신호 · 멀리 떨어진 신호는 남는다."""
    cell = frozenset({("base_model", "S")})
    step = _found(
        key="up:2025-03-01@S",
        direction="up",
        at="2025-03-01",
        ratio=1.8,
        support=900,
        cell=cell,
    )
    signals = {
        "explained": _found(
            key="signal:a",
            kind="signal",
            recipe="control",
            direction="up",
            at="2025-05-01",
            ratio=2.0,
            support=300,
            cell=cell,
        ),
        "other_cell": _found(
            key="signal:b",
            kind="signal",
            recipe="control",
            direction="up",
            at="2025-05-01",
            ratio=2.0,
            support=300,
            cell=frozenset({("base_model", "A")}),
        ),
        "before": _found(
            key="signal:c",
            kind="signal",
            recipe="control",
            direction="up",
            at="2025-01-01",
            ratio=2.0,
            support=300,
            cell=cell,
        ),
        "far": _found(
            key="signal:d",
            kind="signal",
            recipe="control",
            direction="up",
            at="2026-03-01",
            ratio=2.0,
            support=300,
            cell=cell,
        ),
    }
    kept = {one.key for one in tidy([step, *signals.values()], "month")}
    assert "up:2025-03-01@S" in kept
    assert "signal:a" not in kept
    assert {"signal:b", "signal:c", "signal:d"} <= kept


def test_관리도의_시그마는_잘리지_않은_한계에서_읽는다() -> None:
    from types import SimpleNamespace

    from app.modules.metrics.insights import _sigma

    # 중심선 10, 위 한계 13(3σ = 3), 아래 한계는 0 에서 잘렸다(10 - 3σ 이면 7 이어야).
    assert _sigma(SimpleNamespace(ucl=13.0, lcl=8.5), 10.0) == 1.0
    # p-관리도의 위 한계가 1 에서 잘리면 아래 쪽이 맞다(0.85 - 0.4 = 3σ).
    capped = _sigma(SimpleNamespace(ucl=1.0, lcl=0.4), 0.85)
    assert capped is not None and abs(capped - 0.15) < 1e-9
    assert _sigma(SimpleNamespace(ucl=None, lcl=None), 10.0) is None


def test_이어지는_관리도_신호는_한_줄로_이어_간다() -> None:
    """같은 차트 · 같은 규칙의 신호가 마지막에 본 점에서 석 달 안이면 그 결론을 이어 간다 —
    그만 본 것도 다시 안 뜬다. 규칙이 다르거나 멀면 새 결론."""
    from app.modules.metrics.insights import _same_signal
    from app.modules.metrics.models import MetricInsight

    values = {"split": "base_model"}
    old = MetricInsight(
        key="signal:base_model=S@2025-03-01:1",
        kind="signal",
        values=values,
        detail={"when": "2025-04-01"},
    )
    existing = {old.key: old}

    def found(when: str, rule: int = 1, **kw: Any) -> Found:
        return _found(
            key=f"signal:base_model=S@{when}:{rule}",
            kind="signal",
            recipe="control",
            values=kw.get("values", values),
            at=when,
        )

    assert _same_signal(found("2025-06-01"), existing, "month") is old
    assert _same_signal(found("2025-06-01", rule=2), existing, "month") is None
    assert _same_signal(found("2025-10-01"), existing, "month") is None
    assert _same_signal(found("2025-06-01", values={}), existing, "month") is None
