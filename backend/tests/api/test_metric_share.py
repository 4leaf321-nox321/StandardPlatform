"""조건 비율 — 같은 기록 중 조건에 맞는 몫(`measure="share"`).

지키는 것: 셀의 조건 건수 · 전체 건수가 기록과 맞고 비율은 % 다 · 「건 보기」 는 분모(셀의 기록
전부)와 분자(조건에 맞는 것) 둘 다 그 수와 같다 · 정의의 잘못(조건 없음 · 분모 지표 · 분모로
쓰기)은 계획이 말한다 · 분석(집단 비교 · 관리도 · 변화점)은 몫에 그대로 붙고 관리도는 p 다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_metric_analysis import _analysis, _month, _plant
from tests.api.test_metrics import _define, _listed, _read, _two, _world


def _noise_share(w: dict[str, Any]) -> dict[str, Any]:
    """접수월 x 공장마다 「소음」 의 몫."""
    return {
        "measure": "share",
        "share_when": [{"field": "symptom", "op": "eq", "value": "소음"}],
        "time": {"address": "properties.received", "grain": "month"},
        "dimensions": [{"name": "factory", "address": "properties.factory"}],
    }


def _plan(client: TestClient, admin: Signed, source: str, spec: dict[str, Any]) -> list[str]:
    got = client.post(
        "/api/metrics/plan",
        json={"source_type_slug": source, "spec": spec},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    return list(got.json()["errors"])


def test_조건_비율은_셀마다_조건_건수와_전체를_세고_건_보기가_둘_다_맞는다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    metric = _define(client, admin, source=w["case"], spec=_noise_share(w), label="소음 몫")
    assert metric["spec"]["measure"] == "share"
    table = _read(client, admin, metric["slug"], dims="factory")
    assert table["measure_label"] == "조건 비율"
    assert table["denominator"]["label"].endswith("같은 기록 전체")
    assert table["denominator"]["per"] == 100
    cells = {one["dims"]["factory"]: one for one in table["cells"]}
    # F1 — 건1 · 2 · 5 · 6 · 8, 소음은 1 · 5 · 8. F2 — 건3 · 4 · 7, 소음은 3 · 7.
    assert cells["F1"]["count"] == 5 and cells["F1"]["value"] == 3
    assert cells["F1"]["ratio"] == 60.0 and cells["F1"]["denominator"] == 5
    assert cells["F2"]["count"] == 3 and cells["F2"]["value"] == 2
    assert abs(cells["F2"]["ratio"] - 200 / 3) < 1e-9
    assert table["total_value"] == 5
    for one in cells.values():
        assert _listed(client, admin, w["case"], one["drill"]["params"]) == one["count"]
        numerator = one["value_drill"]
        assert numerator is not None and numerator["params"]["f.symptom.eq"] == "소음"
        assert _listed(client, admin, w["case"], numerator["params"]) == one["value"]
    # 기간별 — 기록이 없는 달은 비율이 없을 뿐 「분모 없음」 이 아니다.
    series = _read(client, admin, metric["slug"], "series", split="factory")
    assert series["denominator"]["missing"] == 0
    f1 = next(one for one in series["lines"] if one["key"] == "F1")
    january = next(one for one in f1["points"] if one["period"] == "2026-01-01")
    assert january["count"] == 1 and january["ratio"] == 100.0


def test_조건_비율의_정의_잘못은_계획이_말한다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    base = _noise_share(w)
    missing = _plan(client, admin, w["case"], {**base, "share_when": []})
    assert any("조건(share_when)이 하나 이상" in one for one in missing)
    sales, cases = _two(client, admin, w)
    with_den = _plan(
        client,
        admin,
        w["case"],
        {**base, "denominator": {"metric": sales["slug"], "on": [], "time": None}},
    )
    assert any("분모 지표를 두지 않습니다" in one for one in with_den)
    counted = _plan(
        client,
        admin,
        w["case"],
        {**base, "measure": "count"},
    )
    assert any("조건 비율(share)에서만" in one for one in counted)
    wrong_field = _plan(
        client,
        admin,
        w["case"],
        {**base, "share_when": [{"field": "nothing", "op": "eq", "value": "x"}]},
    )
    assert wrong_field
    share = _define(client, admin, source=w["case"], spec=base, label="소음 몫")
    over = _plan(
        client,
        admin,
        w["case"],
        {
            "measure": "count",
            "time": {"address": "properties.received", "grain": "month"},
            "dimensions": [{"name": "factory", "address": "properties.factory"}],
            "denominator": {"metric": share["slug"], "on": ["factory"], "time": "period"},
        },
    )
    assert any("분모의 분모" in one for one in over)
    # 조건 비율에 안 맞는 분석은 이유를 말한다.
    listed = {one["recipe"]: one for one in share["analyses"]}
    assert listed["groups"]["ok"] and listed["control"]["ok"] and listed["changes"]["ok"]
    assert not listed["life"]["ok"] and not listed["sprt"]["ok"]
    assert cases["slug"]  # 분모가 있는 보통 지표는 그대로


def test_조건_비율에_집단_비교_관리도_변화점이_붙는다(
    client: TestClient, admin: Signed
) -> None:
    """센터 다섯 · 1 년. C1~C4 는 달마다 200건 중 20건(10%)이 NTF, C5 는 40건(20%). C6 는 한 달
    10건 중 3건(30%) — 그대로면 가장 나빠 보이지만 작아서 줄인다."""
    w = _world(client, admin)
    share = _define(
        client,
        admin,
        source=w["case"],
        spec={
            "measure": "share",
            "share_when": [{"field": "symptom", "op": "eq", "value": "누수"}],
            "time": {"address": "properties.received", "grain": "month"},
            "dimensions": [{"name": "factory", "address": "properties.factory"}],
            "settle_days": 30,
        },
        label="누수 몫",
    )
    first, watermark = date(2025, 1, 1), datetime(2026, 3, 15, tzinfo=UTC)
    plan = {f"C{i}": (20, 200) for i in range(1, 5)} | {"C5": (40, 200)}
    cells = [
        {
            "period": _month(first, i),
            "dims": {"factory": key},
            "count": total,
            "value_count": total,
            "sum": float(hits),
        }
        for key, (hits, total) in plan.items()
        for i in range(12)
    ] + [
        {
            "period": first,
            "dims": {"factory": "C6"},
            "count": 10,
            "value_count": 10,
            "sum": 3.0,
        }
    ]
    _plant(share["slug"], cells, watermark)

    groups = _analysis(client, admin, share["slug"], "groups", dim="factory")
    assert groups["per"] == 100 and groups["groups"] == 6
    rows = {one["key"]: one for one in groups["rows"]}
    assert groups["rows"][0]["key"] == "C5" and rows["C5"]["flag"] == "high"
    assert rows["C5"]["count"] == 480 and rows["C5"]["exposure"] == 2400
    assert rows["C6"]["rate"] == 30.0 and rows["C6"]["flag"] is None
    assert rows["C6"]["shrunk"] < rows["C6"]["rate"]
    assert all(
        one["shrunk_high"] is None or one["shrunk_high"] <= 100 for one in groups["rows"]
    )
    # 분석의 「N건」 은 조건 건수 — 건 보기도 조건을 건다.
    assert rows["C5"]["drill"]["params"]["f.symptom.eq"] == "누수"

    control = _analysis(client, admin, share["slug"], "control", split="factory")
    assert control["kind"] == "p" and control["per"] == 100
    c5 = next(one for one in control["charts"] if one["key"] == "C5")
    assert abs(c5["center"] - 20.0) < 1e-9
    assert all(
        point["ucl"] is None or point["ucl"] <= 100
        for chart in control["charts"]
        for point in chart["points"]
    )

    changes = _analysis(client, admin, share["slug"], "changes", axis="period")
    assert changes["kind"] == "rate" and changes["per"] == 100
