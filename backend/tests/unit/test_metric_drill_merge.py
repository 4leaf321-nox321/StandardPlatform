"""건 보기 조건 합치기(`query._Conds`) — 같은 칸 · 같은 연산이 둘이면 **둘 다 맞는** 하나로.

목록 조건은 `{키: 값}` 이라 같은 키가 둘이면 하나가 덮인다. 규칙 기준의 조건과 정의의 거르기가
같은 키면 덮인 쪽만큼 목록이 셀보다 많아진다(ADR 0015) — 합칠 수 있으면 합치고, 못 합치면
「≈」(`partial`)라고 말한다.
"""

from __future__ import annotations

from app.modules.metrics.query import _Conds


def _conds() -> _Conds:
    return _Conds(lambda name: name in {"cost", "diag", "label", "received", "center"})


def test_범위는_좁은_쪽_값_여럿은_겹치는_값() -> None:
    conds = _conds()
    conds.put("f.cost.gte", "10", "rule")
    conds.put("f.cost.gte", "20", "filters")
    conds.put("f.cost.gte", "9.5", "filters")
    conds.put("f.received.lt", "2026-03-01", "period")
    conds.put("f.received.lt", "2026-02-01", "filters")
    conds.put("f.diag.in", "C01|C02|C07", "rule")
    conds.put("f.diag.in", "C02|C07|C09", "filters")
    assert conds.params == {
        "f.cost.gte": "20",
        "f.received.lt": "2026-02-01",
        "f.diag.in": "C02|C07",
    }
    assert conds.partial == []


def test_다름_둘은_제외_묶음_하나가_된다() -> None:
    conds = _conds()
    conds.params["nf1.action.starts"] = "SW"  # 앞 규칙의 제외 묶음
    conds.put("f.center.ne", "서울", "rule")
    conds.put("f.center.ne", "부산", "filters")
    assert "f.center.ne" not in conds.params
    assert conds.params["nf2.center.in"] == "서울|부산"
    assert conds.group() == 3


def test_로_시작은_긴_쪽_값과_상관없는_연산은_그대로() -> None:
    conds = _conds()
    conds.put("f.label.starts", "SW", "rule")
    conds.put("f.label.starts", "sw1", "filters")
    conds.put("f.label.empty", "", "rule")
    conds.put("f.label.empty", "1", "filters")
    assert conds.params == {"f.label.starts": "sw1", "f.label.empty": ""}
    assert conds.partial == []


def test_여러_값_칸과_너머와_포함은_못_합쳐_어림이라고_말한다() -> None:
    conds = _conds()
    conds.put("f.parts.eq", "P1", "nature")
    conds.put("f.parts.eq", "P2", "filters")  # 여러 값 칸 — 둘 다 담은 것이라 합치면 틀린다
    conds.put("f.ref.model.series.in", "X|Y", "nature")
    conds.put("f.ref.model.series.in", "Y|Z", "share_when")  # 너머는 「하나라도」
    conds.put("f.label.contains", "가", "nature")
    conds.put("f.label.contains", "나", "filters")
    assert conds.partial == ["filters", "share_when"]
    assert conds.params["f.parts.eq"] == "P1"
