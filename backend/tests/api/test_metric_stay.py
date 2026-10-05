"""머무는 기간 — 기록을 그 기간부터 N기간 동안 센다(`stay`, ADR 0023).

지키는 것: 기간마다의 값이 최근 N기간의 합이다 · 기록마다 기간 수를 참조 너머의 숫자 칸에서
읽고(비면 기본, 넘으면 자른다) · 계산 시점의 기간을 넘는 미래는 만들지 않는다 · 「건 보기」 는
N-1 기간 앞부터의 기록이고, 기록마다 기간 수가 다르면 「≈」 다 · 접수월 인입률의 분모(기간끼리)
로 그대로 짝지어진다 · 정의의 잘못(시간 칸 없음 · 코호트 · 숫자 아닌 칸)은 계획이 말한다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed, finish_job
from tests.api.test_metrics import _define, _listed, _prop, _read
from tests.api.test_ontology import _make_object, _make_type


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    """국가 둘(보증 2 · 3개월)과 국가가 빈 판매 하나. 이번 달 판매는 미래로 펼치면 안 된다.

    판매  KOR 2026-01 100 · 2026-02 50 · 2026-04 10 · DEU 2026-01 200 · (국가 없음) 2026-01 7
          KOR 이번 달 1
    접수  KOR 2026-02 둘 · 2026-03 하나 · DEU 2026-02 하나
    """
    country = _make_type(client, admin, label="국가")
    _prop(client, admin, country, "warranty", "보증 기간", data_type="number")
    _prop(client, admin, country, "zone", "권역", data_type="text")
    sales = _make_type(client, admin, label="판매", usage="log")
    _prop(
        client, admin, sales, "country", "국가", data_type="object_ref", ref_type_slug=country
    )
    _prop(client, admin, sales, "month", "판매월", data_type="date")
    _prop(client, admin, sales, "units", "대수", data_type="number")
    _prop(client, admin, sales, "sizes", "용량들", data_type="number", multi=True)
    case = _make_type(client, admin, label="접수", usage="log")
    _prop(
        client, admin, case, "country", "국가", data_type="object_ref", ref_type_slug=country
    )
    _prop(client, admin, case, "received", "접수일", data_type="date")
    _prop(client, admin, case, "sold", "판매일", data_type="date")
    kor = _make_object(client, admin, country, label="KOR", properties={"warranty": 2})["id"]
    deu = _make_object(client, admin, country, label="DEU", properties={"warranty": 3})["id"]
    this_month = date.today().replace(day=1).isoformat()
    for where, month, units in [
        (kor, "2026-01-01", 100),
        (kor, "2026-02-01", 50),
        (kor, "2026-04-01", 10),
        (deu, "2026-01-01", 200),
        (None, "2026-01-01", 7),
        (kor, this_month, 1),
    ]:
        properties: dict[str, Any] = {"month": month, "units": units}
        if where:
            properties["country"] = where
        _make_object(client, admin, sales, label=f"판매 {month}", properties=properties)
    for where, received in [
        (kor, "2026-02-03"),
        (kor, "2026-02-20"),
        (kor, "2026-03-11"),
        (deu, "2026-02-14"),
    ]:
        _make_object(
            client,
            admin,
            case,
            label=f"접수 {received}",
            properties={"country": where, "received": received, "sold": "2026-01-10"},
        )
    return {
        "country": country,
        "sales": sales,
        "case": case,
        "kor": kor,
        "deu": deu,
        "this_month": this_month,
    }


def _units_spec(**stay: Any) -> dict[str, Any]:
    return {
        "measure": "sum",
        "measure_field": "properties.units",
        "time": {"address": "properties.month", "grain": "month"},
        "dimensions": [{"name": "country", "address": "properties.country"}],
        "stay": {"periods": 3, **stay},
    }


def _by_month(table: dict[str, Any], key: str | None) -> dict[str, float]:
    return {
        one["period"][:7]: one["value"]
        for one in table["cells"]
        if one["dims"]["country"] == key
    }


def test_머무는_기간은_기간마다_최근_N기간의_합이고_미래는_만들지_않는다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    plan = client.post(
        "/api/metrics/plan",
        json={"source_type_slug": w["sales"], "spec": _units_spec()},
        headers=admin.headers,
    ).json()
    metric = _define(client, admin, source=w["sales"], spec=_units_spec(), label="쓰이는 대수")
    # 셀 어림 — 펼침이 이웃 기간끼리 겹치므로 「셀 x N」 이 아니라 (기간 수 + N - 1) 로 묶인다.
    # 넘치게는 어림해도 모자라게는 하지 않는다.
    assert metric["cells"] <= plan["estimated_cells"] <= 2 * metric["cells"]
    table = _read(client, admin, metric["slug"], dims="country", by="period")
    assert table["stay"] == {"periods": 3, "periods_from": None, "periods_from_label": None}
    kor = _by_month(table, w["kor"])
    assert kor["2026-01"] == 100 and kor["2026-02"] == 150 and kor["2026-03"] == 150
    assert kor["2026-04"] == 60 and kor["2026-05"] == 10 and kor["2026-06"] == 10
    assert "2026-07" not in kor
    assert _by_month(table, w["deu"]) == {"2026-01": 200, "2026-02": 200, "2026-03": 200}
    # 이번 달 판매는 이번 달에만 — 다음 달 · 그다음 달은 아직 오지 않았다.
    this = w["this_month"][:7]
    later = [one for one in kor if one > this]
    assert later == [], later
    # 건 보기 — 기간 수가 같으면 N-1 기간 앞부터 그 기간까지가 정확히 그 수다.
    march = next(
        one
        for one in table["cells"]
        if one["dims"]["country"] == w["kor"] and one["period"].startswith("2026-03")
    )
    params = march["drill"]["params"]
    assert params["f.month.gte"] == "2026-01-01" and params["f.month.lt"] == "2026-04-01"
    assert "stay" not in march["drill"]["partial"]
    assert _listed(client, admin, w["sales"], params) == march["count"] == 2


def test_기간_수를_참조_너머의_숫자_칸에서_읽고_비면_기본_넘으면_자른다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    spec = _units_spec(periods_from="ref.country.warranty")
    metric = _define(client, admin, source=w["sales"], spec=spec, label="보증 중 대수")
    table = _read(client, admin, metric["slug"], dims="country", by="period")
    assert table["stay"]["periods_from"] == "ref.country.warranty"
    assert "보증 기간" in table["stay"]["periods_from_label"]
    # KOR 2개월 — 1월 판매는 2월까지, 2월 판매는 3월까지.
    kor = _by_month(table, w["kor"])
    assert kor["2026-01"] == 100 and kor["2026-02"] == 150 and kor["2026-03"] == 50
    assert kor["2026-04"] == 10 and kor["2026-05"] == 10 and "2026-06" not in kor
    # DEU 3개월 — 그대로 3기간.
    assert _by_month(table, w["deu"]) == {"2026-01": 200, "2026-02": 200, "2026-03": 200}
    # 국가가 빈 판매 — 기간 수를 못 읽어 기본(3).
    assert _by_month(table, None) == {"2026-01": 7, "2026-02": 7, "2026-03": 7}
    # 기록마다 기간 수가 달라 건 보기는 「≈」 — 범위는 가장 긴 것(기본) 기준.
    march = next(
        one
        for one in table["cells"]
        if one["dims"]["country"] == w["kor"] and one["period"].startswith("2026-03")
    )
    assert "stay" in march["drill"]["partial"]
    assert march["drill"]["params"]["f.month.gte"] == "2026-01-01"
    # 보증 기간을 3보다 길게 적어도 3에서 자른다.
    patched = client.patch(
        f"/api/objects/{w['country']}/{w['deu']}",
        json={"properties": {"warranty": 9}},
        headers=admin.headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["properties"]["warranty"] == 9
    again = client.post(f"/api/metrics/{metric['slug']}/recompute", headers=admin.headers)
    assert again.status_code == 202, again.text
    assert finish_job(client, admin, again.json())["status"] == "done"
    table = _read(client, admin, metric["slug"], dims="country", by="period")
    assert _by_month(table, w["deu"]) == {"2026-01": 200, "2026-02": 200, "2026-03": 200}


def test_접수월_인입률의_분모로_기간끼리_짝지어진다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    units = _define(
        client,
        admin,
        source=w["sales"],
        spec=_units_spec(periods_from="ref.country.warranty"),
        label="보증 중 대수",
    )
    rate = _define(
        client,
        admin,
        source=w["case"],
        spec={
            "measure": "count",
            "time": {"address": "properties.received", "grain": "month"},
            "dimensions": [{"name": "country", "address": "properties.country"}],
            "denominator": {
                "metric": units["slug"],
                "on": ["country"],
                "time": "period",
                "per": 1000,
            },
        },
        label="보증 중 1,000대당 접수",
    )
    table = _read(client, admin, rate["slug"], dims="country", by="period")
    cells = {(one["dims"]["country"], one["period"][:7]): one for one in table["cells"]}
    assert cells[(w["kor"], "2026-02")]["count"] == 2
    assert cells[(w["kor"], "2026-02")]["denominator"] == 150
    assert abs(cells[(w["kor"], "2026-02")]["ratio"] - 2 / 150 * 1000) < 1e-9
    assert cells[(w["kor"], "2026-03")]["denominator"] == 50
    assert cells[(w["deu"], "2026-02")]["denominator"] == 200
    assert table["denominator"]["missing"] == 0


def test_머무는_기간의_정의_잘못은_계획이_말한다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)

    def errors(spec: dict[str, Any]) -> list[str]:
        got = client.post(
            "/api/metrics/plan",
            json={"source_type_slug": w["sales"], "spec": spec},
            headers=admin.headers,
        )
        assert got.status_code == 200, got.text
        return list(got.json()["errors"])

    no_time = {key: value for key, value in _units_spec().items() if key != "time"}
    assert any("시간 칸이 있어야" in one for one in errors(no_time))
    with_cohort = {
        **_units_spec(),
        "cohort": {"address": "properties.month", "grain": "month"},
    }
    assert any("코호트는 함께 둘 수 없습니다" in one for one in errors(with_cohort))
    assert any(
        "숫자 칸이어야" in one for one in errors(_units_spec(periods_from="ref.country.zone"))
    )
    assert any(
        "값이 하나여야" in one for one in errors(_units_spec(periods_from="properties.sizes"))
    )
    too_long = {**_units_spec(), "stay": {"periods": 121}}
    got = client.post(
        "/api/metrics/plan",
        json={"source_type_slug": w["sales"], "spec": too_long},
        headers=admin.headers,
    )
    assert got.status_code == 422
    plan = client.post(
        "/api/metrics/plan",
        json={"source_type_slug": w["sales"], "spec": _units_spec()},
        headers=admin.headers,
    ).json()
    assert plan["ok"] and not plan["errors"]
