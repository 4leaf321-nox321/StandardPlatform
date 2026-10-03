"""방문 축과 그 위의 분석 — 같은 시리얼의 차례 · 재방문(ADR 0014, 4단계).

방문 차례 · 재방문은 **같은 시리얼의 다른 기록**에 달린 값이다. 그래서 진짜 기록으로 세어 보고
(차례가 여러 값 기준의 펼침보다 먼저 세어지는지, 닫히지 않은 창이 「아직 열림」 인지), 로지스틱
의 오즈비는 정답을 아는 심은 셀로 본다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_metric_analysis import _analysis, _month, _plant, _refused
from tests.api.test_metrics import _define, _prop, _read
from tests.api.test_ontology import _make_object, _make_type


def _visits_world(client: TestClient, admin: Signed) -> dict[str, Any]:
    """시리얼 다섯 — 날짜는 오늘에서 거꾸로(창이 닫혔는지가 오늘에 달려서).

    A(F1): t0 · t0+41 · t0+142        차례 1 · 2 · 3,  재방문 예 · 아니오(101일) · 아니오
    B(F2): t0+5                       1, 아니오
    C(F1): t0+50(부품 둘) · +4 · +15 · +12 · 오늘-40
                                      1 · 2 · 3 · 4+ · 4+,  예 · 예 · 예 · 아니오 · 아직 열림
    D(F2): 오늘-10                    1, 아직 열림
    E(F2): 오늘-200, 시리얼 없음       (비어 있음)
    """
    case = _make_type(client, admin, label="방문 기록", usage="log")
    part = _make_type(client, admin, label="부품")
    _prop(client, admin, case, "received", "접수일", data_type="date")
    _prop(client, admin, case, "serial", "시리얼", data_type="text")
    _prop(client, admin, case, "factory", "공장", data_type="text")
    _prop(
        client,
        admin,
        case,
        "parts",
        "교체 부품",
        data_type="object_ref",
        ref_type_slug=part,
        multi=True,
    )
    p1 = _make_object(client, admin, part, label="P1")["id"]
    p2 = _make_object(client, admin, part, label="P2")["id"]
    today = date.today()
    t0 = today - timedelta(days=400)
    c1 = t0 + timedelta(days=50)
    rows: list[tuple[str | None, str, date, list[str]]] = [
        ("A", "F1", t0, []),
        ("A", "F1", t0 + timedelta(days=41), []),
        ("A", "F1", t0 + timedelta(days=142), []),
        ("B", "F2", t0 + timedelta(days=5), []),
        ("C", "F1", c1, [p1, p2]),
        ("C", "F1", c1 + timedelta(days=4), []),
        ("C", "F1", c1 + timedelta(days=19), []),
        ("C", "F1", c1 + timedelta(days=31), []),
        ("C", "F1", today - timedelta(days=40), []),
        ("D", "F2", today - timedelta(days=10), []),
        (None, "F2", today - timedelta(days=200), []),
    ]
    for index, (serial, factory, when, parts) in enumerate(rows):
        properties: dict[str, Any] = {
            "received": when.isoformat(),
            "factory": factory,
            "parts": parts,
        }
        if serial is not None:
            properties["serial"] = serial
        _make_object(client, admin, case, label=f"방문{index}", properties=properties)
    return {"case": case, "p1": p1, "p2": p2}


def _visit_spec(**more: Any) -> dict[str, Any]:
    return {
        "measure": "count",
        "time": {"address": "properties.received", "grain": "month"},
        "visits": {"key": "properties.serial", "within_days": 90},
        "dimensions": [
            {"name": "visit_no", "address": "visit.number"},
            {"name": "again", "address": "visit.repeat"},
            {"name": "factory", "address": "properties.factory"},
        ],
        **more,
    }


def _counts(table: dict[str, Any], name: str) -> dict[str | None, int]:
    out: dict[str | None, int] = {}
    for cell in table["cells"]:
        key = cell["dims"][name]
        out[key] = out.get(key, 0) + cell["count"]
    return out


def test_방문_차례와_재방문을_시리얼마다_센다(client: TestClient, admin: Signed) -> None:
    w = _visits_world(client, admin)
    metric = _define(client, admin, source=w["case"], spec=_visit_spec(), label="방문")
    dims = {one["name"]: one for one in metric["dims"]}
    assert dims["visit_no"]["kind"] == "visit" and dims["again"]["label"] == "90일 안 재방문"
    numbers = _read(client, admin, metric["slug"], dims="visit_no")
    assert _counts(numbers, "visit_no") == {"1": 4, "2": 2, "3": 2, "4+": 2, None: 1}
    labels = {
        cell["dims"]["visit_no"]: cell["labels"]["visit_no"] for cell in numbers["cells"]
    }
    assert labels["1"] == "첫 방문" and labels["4+"] == "네 번째 이상"
    again = _read(client, admin, metric["slug"], dims="again")
    assert _counts(again, "again") == {"yes": 4, "no": 4, "open": 2, None: 1}
    # 방문 기준은 목록 조건으로 못 적는다 — 건 보기에 「≈」.
    assert all("again" in cell["drill"]["partial"] for cell in again["cells"])
    # 공장 F1 의 재방문 — A 넷 중 하나, C 다섯 중 셋(마지막은 아직 열림).
    f1 = _read(client, admin, metric["slug"], dims="again", **{"d.factory": "F1"})
    assert _counts(f1, "again") == {"yes": 4, "no": 3, "open": 1}


def test_차례는_여러_값_기준을_펼치기_전에_센다(client: TestClient, admin: Signed) -> None:
    """부품 둘인 기록(C 의 첫 방문)이 두 셀에 들어도 둘 다 첫 방문이다 — 펼친 줄 위에서 세면
    두 번째 방문이 하나 더 생긴다."""
    w = _visits_world(client, admin)
    metric = _define(
        client,
        admin,
        source=w["case"],
        spec={
            **_visit_spec(),
            "dimensions": [
                {"name": "visit_no", "address": "visit.number"},
                {"name": "part", "address": "properties.parts"},
            ],
        },
        label="방문 x 부품",
    )
    table = _read(client, admin, metric["slug"], dims="visit_no,part")
    firsts = {
        cell["dims"]["part"]: cell["count"]
        for cell in table["cells"]
        if cell["dims"]["visit_no"] == "1"
    }
    assert firsts[w["p1"]] == 1 and firsts[w["p2"]] == 1
    assert _counts(table, "visit_no")["2"] == 2


def test_방문_정의를_계획이_검사한다(client: TestClient, admin: Signed) -> None:
    w = _visits_world(client, admin)

    def plan(spec: dict[str, Any]) -> dict[str, Any]:
        got = client.post(
            "/api/metrics/plan",
            json={"source_type_slug": w["case"], "spec": spec},
            headers=admin.headers,
        )
        assert got.status_code == 200, got.text
        return dict(got.json())

    ok = plan(_visit_spec())
    assert ok["ok"] is True
    distinct = {one["name"]: one["distinct"] for one in ok["dims"]}
    assert distinct["visit_no"] == 4 and distinct["again"] == 3
    # 방문 기준인데 visits 가 없다 · 시리얼이 날짜 칸 · 시간 칸이 없다.
    no_visits = plan({**_visit_spec(), "visits": None})
    assert no_visits["ok"] is False and any("visits" in one for one in no_visits["errors"])
    bad_key = plan({**_visit_spec(), "visits": {"key": "properties.received"}})
    assert any("시리얼" in one or "가리키는" in one for one in bad_key["errors"])
    no_time = plan({**_visit_spec(), "time": None})
    assert no_time["ok"] is False and any("시간 칸" in one for one in no_time["errors"])


def test_재방문_위험_요인은_요인별_오즈비를_낸다(client: TestClient, admin: Signed) -> None:
    """심은 셀 — 공장마다 1,000건: F1 예 100, F2 예 200, F3 예 50. 아직 열림 30건.

    기준은 F1(동률이면 이름순), F2 의 오즈비 = (200/800)/(100/900) = 2.25,
    F3 = (50/950)/(100/900) ≈ 0.474."""
    w = _visits_world(client, admin)
    metric = _define(client, admin, source=w["case"], spec=_visit_spec(), label="방문")
    cells: list[dict[str, Any]] = []
    for factory, yes in (("F1", 100), ("F2", 200), ("F3", 50)):
        for state, count in (("yes", yes), ("no", 1000 - yes)):
            cells.append(
                {
                    "period": date(2026, 1, 1),
                    "dims": {"visit_no": "1", "again": state, "factory": factory},
                    "count": count,
                }
            )
    cells.append(
        {
            "period": date(2026, 2, 1),
            "dims": {"visit_no": "1", "again": "open", "factory": "F1"},
            "count": 30,
        }
    )
    _plant(metric["slug"], cells, datetime(2026, 9, 1, tzinfo=UTC))
    found = _analysis(client, admin, metric["slug"], "logit", factors="factory")
    assert found["records"] == 3000 and found["yes"] == 350
    assert found["excluded"] == {"open": 30}
    (factor,) = found["factors"]
    assert factor["df"] == 2 and factor["p_value"] < 1e-10
    levels = {one["key"]: one for one in factor["levels"]}
    assert levels["F1"]["reference"] is True and levels["F1"]["odds_ratio"] == 1.0
    assert levels["F2"]["odds_ratio"] == pytest.approx(2.25, rel=1e-6)
    assert levels["F3"]["odds_ratio"] == pytest.approx((50 / 950) / (100 / 900), rel=1e-6)
    low, high = levels["F2"]["ci"]
    assert low < 2.25 < high
    assert found["baseline_rate"] == pytest.approx(0.1, rel=1e-6)
    assert 0.5 < found["auc"] < 1.0
    codes = {one["code"] for one in found["caveats"]}
    assert {"open_excluded", "not_independent", "association"} <= codes
    # 거절 — 결과를 요인으로, 없는 기준.
    assert _refused(client, admin, metric["slug"], "logit", factors="again")["code"].endswith(
        "METRICS-0032"
    )
    assert _refused(client, admin, metric["slug"], "logit", factors="nope")["code"].endswith(
        "METRICS-0023"
    )


def test_재방문_기준이_없으면_위험_요인을_못_본다(client: TestClient, admin: Signed) -> None:
    w = _visits_world(client, admin)
    metric = _define(
        client,
        admin,
        source=w["case"],
        spec={
            "measure": "count",
            "time": {"address": "properties.received", "grain": "month"},
            "dimensions": [{"name": "factory", "address": "properties.factory"}],
        },
        label="공장",
    )
    reason = next(one for one in metric["analyses"] if one["recipe"] == "logit")
    assert reason["ok"] is False and "visit.repeat" in reason["reason"]
    refused = _refused(client, admin, metric["slug"], "logit", factors="factory")
    assert refused["code"].endswith("METRICS-0031")


def test_수명을_첫_방문으로_세면_다시_온_기록을_뺀다(
    client: TestClient, admin: Signed
) -> None:
    """판매월 코호트 + 방문 — 경과마다 첫 방문 2건 · 다시 온 기록 1건을 심는다. 기록으로 세면
    세 건씩, 첫 방문으로 세면 두 건씩이다."""
    sales_type = _make_type(client, admin, label="판매", usage="log")
    _prop(client, admin, sales_type, "month", "판매월", data_type="date")
    _prop(client, admin, sales_type, "units", "대수", data_type="number")
    w = _visits_world(client, admin)
    _prop(client, admin, w["case"], "sold", "판매일", data_type="date")
    sales = _define(
        client,
        admin,
        source=sales_type,
        spec={
            "measure": "sum",
            "measure_field": "properties.units",
            "time": {"address": "properties.month", "grain": "month"},
        },
        label="판매 대수",
    )
    metric = _define(
        client,
        admin,
        source=w["case"],
        spec={
            **_visit_spec(),
            "cohort": {"address": "properties.sold", "grain": "month"},
            "dimensions": [{"name": "visit_no", "address": "visit.number"}],
            "denominator": {"metric": sales["slug"], "on": [], "time": "cohort", "per": 100},
        },
        label="코호트 방문",
    )
    first = date(2026, 1, 1)
    cells: list[dict[str, Any]] = []
    for i in range(6):
        start = _month(first, i)
        for age in range(6 - i):
            for number, count in (("1", 2), ("2", 1)):
                cells.append(
                    {
                        "period": _month(start, age),
                        "cohort": start,
                        "age": age,
                        "dims": {"visit_no": number},
                        "count": count,
                    }
                )
    watermark = datetime(2026, 7, 15, tzinfo=UTC)
    _plant(metric["slug"], cells, watermark)
    _plant(
        sales["slug"],
        [
            {
                "period": _month(first, i),
                "dims": {},
                "count": 1,
                "value_count": 1,
                "sum": 1000.0,
            }
            for i in range(6)
        ],
        watermark,
    )
    records = _analysis(client, admin, metric["slug"], "life")
    firsts = _analysis(client, admin, metric["slug"], "life", basis="first_visits")
    assert records["basis"] == "records" and firsts["basis"] == "first_visits"
    assert records["failures"] == 3 * 21 and firsts["failures"] == 2 * 21
    assert "first_visits" in {one["code"] for one in firsts["caveats"]}
    assert "records_not_units" not in {one["code"] for one in firsts["caveats"]}
    # 방문 기준이 없는 지표는 첫 방문으로 못 센다.
    plain = _define(
        client,
        admin,
        source=w["case"],
        spec={
            "measure": "count",
            "time": {"address": "properties.received", "grain": "month"},
            "cohort": {"address": "properties.sold", "grain": "month"},
            "denominator": {"metric": sales["slug"], "on": [], "time": "cohort", "per": 100},
        },
        label="코호트",
    )
    refused = _refused(client, admin, plain["slug"], "life", basis="first_visits")
    assert refused["code"].endswith("METRICS-0030")
