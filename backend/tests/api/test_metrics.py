"""지표 — **기록을 미리 세어 두고, 물으면 데이터로 바로 답한다**(ADR 0013).

이 시험은 언제나 **통계 · 목록과 같은 수**를 나란히 본다 — 미리 센 값이 그때그때 센 값과
다르면 어느 쪽이 맞는지 아무도 모른다. 그리고 응답이 숨기지 말아야 할 것(겹침 · 못 묶은 수 ·
계산 시각 · 분모 없음)이 실제로 실리는지를 본다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.api.conftest import Signed, finish_job
from tests.api.test_datasources import (  # noqa: F401 — plm 은 픽스처
    FakeOData,
    _source,
    _sync,
    _vendor_type,
    plm,
)
from tests.api.test_linked_fields import _relate
from tests.api.test_ontology import (
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)


def _slug(base: str) -> str:
    # 6자리면 한 번의 전체 시험에서 만드는 수천 개끼리 부딪친다(2026-10-05, 409) — 12자리.
    return f"{base}_{uuid.uuid4().hex[:12]}"


def _prop(
    client: TestClient, admin: Signed, type_slug: str, key: str, label: str, **kw: Any
) -> None:
    _make_property(client, admin, type_slug, key=key, label=label, **kw)


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    """기록 ─model→ SKU ─base→ 기본 모델. 판매(기본 모델 x 월 x 대수)가 분모.

    기록 여덟 — 접수일 · 판매일 · 증상 · 교체 부품(여럿) · 비용 · 공장. 하나는 접수일이
    `2026-02-30`(못 읽는 값), 하나는 판매일이 없고, 하나는 **오늘** 접수됐다(열린 기간).
    """
    base = _make_type(client, admin, label="기본 모델")
    sku = _make_type(client, admin, label="SKU")
    _prop(client, admin, sku, "base", "기본 모델", data_type="object_ref", ref_type_slug=base)
    part = _make_type(client, admin, label="부품")
    case = _make_type(client, admin, label="기록", usage="log")
    _prop(client, admin, case, "model", "모델", data_type="object_ref", ref_type_slug=sku)
    _prop(client, admin, case, "received", "접수일", data_type="date")
    _prop(client, admin, case, "sold", "판매일", data_type="date")
    _prop(
        client,
        admin,
        case,
        "symptom",
        "증상",
        data_type="enum",
        enum_options=["소음", "발열", "누수"],
    )
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
    _prop(client, admin, case, "cost", "비용", data_type="number")
    _prop(client, admin, case, "factory", "공장", data_type="text")
    sales = _make_type(client, admin, label="판매", usage="log")
    _prop(
        client,
        admin,
        sales,
        "base_model",
        "기본 모델",
        data_type="object_ref",
        ref_type_slug=base,
    )
    _prop(client, admin, sales, "month", "판매월", data_type="date")
    _prop(client, admin, sales, "units", "대수", data_type="number")

    s_base = _make_object(client, admin, base, label="S기본")["id"]
    a_base = _make_object(client, admin, base, label="A기본")["id"]
    s1 = _make_object(client, admin, sku, label="S-1", properties={"base": s_base})["id"]
    s2 = _make_object(client, admin, sku, label="S-2", properties={"base": s_base})["id"]
    a1 = _make_object(client, admin, sku, label="A-1", properties={"base": a_base})["id"]
    p1 = _make_object(client, admin, part, label="P1")["id"]
    p2 = _make_object(client, admin, part, label="P2")["id"]
    for base_id, month, units in [
        (s_base, "2026-01-01", 100),
        (s_base, "2026-02-01", 50),
        (a_base, "2026-01-01", 200),
    ]:
        _make_object(
            client,
            admin,
            sales,
            label=f"판매 {month}",
            properties={"base_model": base_id, "month": month, "units": units},
        )
    today = date.today().isoformat()
    rows: list[tuple[str, str | None, str, str, list[str], float | None, str]] = [
        ("2026-01-15", "2026-01-03", s1, "소음", [p1], 10, "F1"),
        ("2026-02-10", "2026-01-20", s2, "발열", [p1, p2], 20, "F1"),
        ("2026-02-20", "2026-02-01", s1, "소음", [], 30, "F2"),
        ("2026-03-05", "2026-01-10", a1, "누수", [p2], None, "F2"),
        ("2026-03-15", "2026-02-14", a1, "소음", [p1], 40, "F1"),
        (
            "2026-02-28",
            "2026-01-05",
            s1,
            "발열",
            [],
            5,
            "F1",
        ),  # 접수일을 2026-02-30 으로 망가뜨린다
        ("2026-03-20", None, s2, "소음", [p2], 15, "F2"),
        (today, today, s1, "소음", [], 1, "F1"),
    ]
    ids: list[str] = []
    for index, (received, sold, model, symptom, parts, cost, factory) in enumerate(rows, 1):
        properties: dict[str, Any] = {
            "model": model,
            "received": received,
            "symptom": symptom,
            "parts": parts,
            "factory": factory,
        }
        if sold:
            properties["sold"] = sold
        if cost is not None:
            properties["cost"] = cost
        ids.append(
            _make_object(client, admin, case, label=f"건{index}", properties=properties)["id"]
        )
    from app.database import SessionLocal
    from app.modules.objects.models import ObjectInstance

    with SessionLocal() as db:
        row = db.get(ObjectInstance, uuid.UUID(ids[5]))
        assert row is not None
        row.properties = {**row.properties, "received": "2026-02-30"}
        db.commit()
    return {
        "base": base,
        "sku": sku,
        "part": part,
        "case": case,
        "sales": sales,
        "s_base": s_base,
        "a_base": a_base,
        "p1": p1,
        "p2": p2,
        "today": today,
    }


def _sales_spec() -> dict[str, Any]:
    return {
        "measure": "sum",
        "measure_field": "properties.units",
        "time": {"address": "properties.month", "grain": "month"},
        "dimensions": [{"name": "base_model", "address": "properties.base_model"}],
    }


def _cases_spec(sales_slug: str) -> dict[str, Any]:
    return {
        "measure": "count",
        "time": {"address": "properties.received", "grain": "month"},
        "cohort": {"address": "properties.sold", "grain": "month"},
        "dimensions": [
            {"name": "base_model", "address": "ref.model.base"},
            {"name": "symptom", "address": "properties.symptom"},
            {"name": "factory", "address": "properties.factory"},
        ],
        "denominator": {
            "metric": sales_slug,
            "on": ["base_model"],
            "time": "cohort",
            "per": 100,
        },
        "settle_days": 30,
    }


def _define(
    client: TestClient,
    admin: Signed,
    *,
    source: str,
    spec: dict[str, Any],
    label: str = "지표",
    recompute: bool = True,
    **kw: Any,
) -> dict[str, Any]:
    body = {"slug": _slug("m"), "label": label, "source_type_slug": source, "spec": spec, **kw}
    made = client.post(
        "/api/metrics", params={"recompute": recompute}, json=body, headers=admin.headers
    )
    assert made.status_code == 201, made.text
    saved = dict(made.json())
    if not recompute:
        return dict(saved["metric"])
    done = finish_job(client, admin, saved["job"])
    assert done["status"] == "done", done
    # 계산이 끝난 뒤의 정의 — 계산 시각 · 셀 수 · 상태가 실린 것.
    got = client.get(f"/api/metrics/{saved['metric']['slug']}", headers=admin.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _two(
    client: TestClient, admin: Signed, w: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    sales = _define(client, admin, source=w["sales"], spec=_sales_spec(), label="판매 대수")
    cases = _define(
        client, admin, source=w["case"], spec=_cases_spec(sales["slug"]), label="인입"
    )
    return sales, cases


def _read(
    client: TestClient, who: Signed, slug: str, shape: str = "values", **params: Any
) -> dict[str, Any]:
    got = client.get(f"/api/metrics/{slug}/{shape}", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _listed(client: TestClient, who: Signed, type_slug: str, params: dict[str, str]) -> int:
    got = client.get(f"/api/objects/{type_slug}", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return int(got.json()["total"])


# --- 정의 · 계획 ------------------------------------------------------------------


def test_정의와_계획은_시스템_관리자만(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    w = _world(client, admin)
    body = {
        "slug": _slug("m"),
        "label": "x",
        "source_type_slug": w["sales"],
        "spec": _sales_spec(),
    }
    assert (
        client.post("/api/metrics/plan", json=body, headers=member.headers).status_code == 403
    )
    assert client.post("/api/metrics", json=body, headers=member.headers).status_code == 403
    # 정의 목록은 누구나 — 값이 보이는 것만 더해지므로 정의 자체는 비밀이 아니다.
    assert client.get("/api/metrics", headers=member.headers).status_code == 200


def test_계획이_거절_사유를_모아_말한다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    spec = {
        "measure": "sum",  # 숫자 칸 없이
        "time": {"address": "properties.symptom", "grain": "month"},  # 날짜 칸이 아니다
        "dimensions": [
            {"name": "period", "address": "properties.factory"},  # 예약어
            {"name": "gone", "address": "properties.nope"},  # 없는 칸
            {"name": "when", "address": "properties.received"},  # 날짜인데 단위가 없다
        ],
        "filters": [{"field": "symptom", "op": "gt", "value": "x"}],  # 고를 값에 범위
        "denominator": {"metric": "nope", "on": [], "time": None},
    }
    got = client.post(
        "/api/metrics/plan",
        json={"source_type_slug": w["case"], "spec": spec},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    plan = got.json()
    assert plan["ok"] is False
    joined = " / ".join(plan["errors"])
    for needle in (
        "숫자 칸을 골라야",
        "날짜 칸이 아닙니다",
        "예약어",
        "없는 칸",
        "기간 단위",
        "못 겁니다",
        "분모 지표가 없습니다",
    ):
        assert needle in joined, (needle, joined)
    # 저장도 같은 검사다 — 계획이 통과하지 않으면 422 에 사유가 실린다.
    denied = client.post(
        "/api/metrics",
        json={"slug": _slug("m"), "label": "x", "source_type_slug": w["case"], "spec": spec},
        headers=admin.headers,
    )
    assert denied.status_code == 422
    assert "예약어" in denied.json()["error"]["message"]
    # 기준 아홉 — 모양부터 막는다(여덟까지, ADR 0023).
    nine = {
        "measure": "count",
        "dimensions": [{"name": f"d{i}", "address": "properties.factory"} for i in range(9)],
    }
    assert (
        client.post(
            "/api/metrics/plan",
            json={"source_type_slug": w["case"], "spec": nine},
            headers=admin.headers,
        ).status_code
        == 422
    )


def test_계획이_셀_수를_어림하고_겹침을_말한다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    sales = _define(client, admin, source=w["sales"], spec=_sales_spec(), recompute=False)
    spec = _cases_spec(sales["slug"])
    spec["dimensions"].append({"name": "parts", "address": "properties.parts"})
    plan = client.post(
        "/api/metrics/plan",
        json={"source_type_slug": w["case"], "spec": spec},
        headers=admin.headers,
    ).json()
    assert plan["ok"] is True, plan
    assert plan["rows"] == 8
    assert plan["overlap"] is True
    assert any("겹침" in one for one in plan["warnings"])
    dims = {one["name"]: one for one in plan["dims"]}
    assert dims["base_model"]["label"] == "모델 › 기본 모델"
    assert dims["base_model"]["kind"] == "object_ref" and dims["base_model"]["distinct"] == 2
    assert dims["symptom"]["distinct"] == 3 and dims["parts"]["multi"] is True
    assert plan["period_from"] == "2026-01-01"
    # 셀은 펼친 기록 줄보다 많을 수 없다 — 부품이 둘인 기록 하나가 줄 하나를 더한다(8 + 1).
    assert plan["estimated_cells"] == 9


def test_분모는_같은_종류의_값으로만_짝짓는다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    sales = _define(client, admin, source=w["sales"], spec=_sales_spec(), recompute=False)
    spec = _cases_spec(sales["slug"])
    # 분자의 base_model 을 글자 칸으로 바꾸면 분모(기본 모델 참조)와 종류가 다르다.
    spec["dimensions"][0] = {"name": "base_model", "address": "properties.factory"}
    plan = client.post(
        "/api/metrics/plan",
        json={"source_type_slug": w["case"], "spec": spec},
        headers=admin.headers,
    ).json()
    assert plan["ok"] is False and any(
        "값 종류가 분모와 다릅니다" in one for one in plan["errors"]
    )
    # 단위가 다르면(분모는 월, 분자의 코호트를 분기로) 거절.
    spec = _cases_spec(sales["slug"])
    spec["time"]["grain"] = spec["cohort"]["grain"] = "quarter"
    plan = client.post(
        "/api/metrics/plan",
        json={"source_type_slug": w["case"], "spec": spec},
        headers=admin.headers,
    ).json()
    assert plan["ok"] is False and any("기간 단위" in one for one in plan["errors"])


# --- 계산 · 읽기 -----------------------------------------------------------------


def test_세어_둔_수가_통계_목록과_같다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    assert cases["last_status"] == "ok" and cases["cells"] > 0 and cases["overlap"] is False
    assert cases["stale"] is False and cases["broken"] is None
    assert [one["label"] for one in cases["dims"]] == ["모델 › 기본 모델", "증상", "공장"]

    table = _read(client, admin, cases["slug"], dims="symptom")
    counts = {one["labels"]["symptom"]: one["count"] for one in table["cells"]}
    assert counts == {"소음": 5, "발열": 2, "누수": 1}
    assert table["total_count"] == 8
    summary = client.get(
        f"/api/objects/{w['case']}/summary",
        params={"group_by": "properties.symptom"},
        headers=admin.headers,
    ).json()
    assert {one["label"]: one["count"] for one in summary["buckets"]} == counts
    # 접수일을 못 읽은 한 건은 숨기지 않는다.
    assert table["unbucketed"] == 1 and table["unbucketed_cohort"] == 1
    assert table["computed_at"] is not None and table["stale"] is False
    # 셀의 건 보기 조건으로 목록을 부르면 같은 수.
    noise = next(one for one in table["cells"] if one["labels"]["symptom"] == "소음")
    assert noise["drill"]["type_slug"] == w["case"] and noise["drill"]["partial"] == []
    assert _listed(client, admin, w["case"], noise["drill"]["params"]) == 5

    runs = client.get(f"/api/metrics/{cases['slug']}/runs", headers=admin.headers).json()
    assert len(runs) == 1 and runs[0]["status"] == "ok" and runs[0]["rows"] == 8
    assert runs[0]["stats"] == {"unbucketed": 1, "unbucketed_cohort": 1, "negative_age": 0}


def test_기간별_표와_추이(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    table = _read(client, admin, cases["slug"], by="period", period_to="2026-04-01")
    by_period = {one["period_label"]: one["count"] for one in table["cells"]}
    assert by_period == {"2026-01": 1, "2026-02": 2, "2026-03": 3}
    february = next(one for one in table["cells"] if one["period_label"] == "2026-02")
    assert february["period"] == "2026-02-01" and february["closed"] is True
    assert february["drill"]["params"] == {
        "f.received.gte": "2026-02-01",
        "f.received.lt": "2026-03-01",
    }
    assert _listed(client, admin, w["case"], february["drill"]["params"]) == 2
    # 기간을 안 걸면 못 읽은 날짜의 셀이 「(비어 있음)」 으로 선다.
    whole = _read(client, admin, cases["slug"], by="period")
    empty = next(one for one in whole["cells"] if one["period"] is None)
    assert empty["count"] == 1 and empty["period_label"] == "(비어 있음)"
    assert empty["drill"]["partial"] == ["period"]

    series = _read(client, admin, cases["slug"], "series", period_to="2026-04-01")
    assert series["grain"] == "month" and len(series["lines"]) == 1
    points = series["lines"][0]["points"]
    assert [one["label"] for one in points] == ["2026-01", "2026-02", "2026-03"]
    assert [one["count"] for one in points] == [1, 2, 3]
    assert [one["prev"] for one in points] == [None, 1.0, 2.0]
    assert all(one["closed"] for one in points)
    # 오늘 접수된 건의 기간은 아직 열려 있고, 그 사이 빈 달은 0 으로 채워진다.
    full = _read(client, admin, cases["slug"], "series")["lines"][0]["points"]
    assert full[-1]["closed"] is False and full[-1]["count"] == 1
    assert len(full) >= 4 and all(one["count"] == 0 for one in full[3:-1])
    # 세부 기준으로 선을 나눈다.
    split = _read(
        client, admin, cases["slug"], "series", split="symptom", period_to="2026-04-01"
    )
    assert split["lines"][0]["label"] == "소음" and len(split["lines"]) == 3
    assert [one["count"] for one in split["lines"][0]["points"]] == [1, 1, 2]


def test_기간_범위는_기간_경계로_맞추고_분모도_같은_범위다(
    client: TestClient, admin: Signed
) -> None:
    """화면의 날짜 칸은 아무 날이나 받는다 — 월 단위 지표에 `01-15` 부터를 주면 추이의 점이
    셀과 하나도 안 맞아 전부 0 이었다. 그리고 기간으로 묶지 않은 표의 비율은 분모가 전
    기간의 합이라 몇 배 작았다(2026-10-08)."""
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    aligned = _read(
        client,
        admin,
        cases["slug"],
        "series",
        period_from="2026-01-01",
        period_to="2026-04-01",
    )
    loose = _read(
        client,
        admin,
        cases["slug"],
        "series",
        period_from="2026-01-15",
        period_to="2026-03-10",
    )
    counts = [one["count"] for one in aligned["lines"][0]["points"]]
    assert counts == [1, 2, 3]
    assert [one["count"] for one in loose["lines"][0]["points"]] == counts

    # 판매월 1월 코호트만 — 분모도 1월 판매(S기본 100 · A기본 200)여야 한다(전 기간이면 S 150).
    table = _read(
        client,
        admin,
        cases["slug"],
        dims="base_model",
        cohort_from="2026-01-01",
        cohort_to="2026-02-01",
    )
    ratios = {one["labels"]["base_model"]: one["ratio"] for one in table["cells"]}
    assert ratios == {"S기본": 3.0, "A기본": 0.5}, table["cells"]


def test_코호트_행렬_누적_비율(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    found = _read(
        client, admin, cases["slug"], "cohort", cumulative="true", cohort_to="2026-03-01"
    )
    assert found["cohort_grain"] == "month" and found["ages"] == [0, 1, 2]
    assert (
        found["denominator"]["metric"] is not None and found["denominator"]["time"] == "cohort"
    )
    rows = {one["label"]: one for one in found["rows"]}
    jan, feb = rows["2026-01"], rows["2026-02"]
    # 1월 판매 코호트: 0 · 1 · 2개월째 한 건씩, 누적 1 · 2 · 3. 분모는 1월 판매 전부(300).
    assert [one["count"] for one in jan["cells"]] == [1, 1, 1]
    assert [one["cumulative"] for one in jan["cells"]] == [1.0, 2.0, 3.0]
    assert jan["denominator"] == 300.0 and jan["cells"][2]["ratio"] == 1.0
    assert [one["count"] for one in feb["cells"]] == [1, 1, 0]
    assert feb["denominator"] == 50.0 and feb["cells"][1]["ratio"] == 4.0
    # 셀의 건 보기는 코호트 범위 + 그 경과의 기간 범위다.
    assert jan["cells"][1]["drill"]["params"] == {
        "f.received.gte": "2026-02-01",
        "f.received.lt": "2026-03-01",
        "f.sold.gte": "2026-01-01",
        "f.sold.lt": "2026-02-01",
    }
    assert _listed(client, admin, w["case"], jan["cells"][1]["drill"]["params"]) == 1
    # 기준으로 거르면 분모도 그 기준으로 거른다 — S 모델만: 1월 코호트 둘, 분모 100.
    only_s = _read(
        client,
        admin,
        cases["slug"],
        "cohort",
        cumulative="true",
        cohort_to="2026-03-01",
        **{"d.base_model": w["s_base"]},
    )
    jan_s = next(one for one in only_s["rows"] if one["label"] == "2026-01")
    assert jan_s["denominator"] == 100.0
    # 경과 축은 거른 셀들이 정한다 — S 는 2개월째가 없어 0 · 1 뿐이다.
    assert only_s["ages"] == [0, 1] and [one["count"] for one in jan_s["cells"]] == [1, 1]
    assert jan_s["cells"][1]["ratio"] == 2.0


def test_분모가_펼쳐지고_없으면_비율이_없다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    table = _read(
        client,
        admin,
        cases["slug"],
        dims="base_model,symptom",
        by="cohort",
        cohort_to="2026-03-01",
    )
    cells = {
        (one["labels"]["base_model"], one["labels"]["symptom"], one["cohort_label"]): one
        for one in table["cells"]
    }
    # 판매 대수는 기본 모델 x 월뿐 — 증상마다 같은 값이 펼쳐진다.
    assert cells[("S기본", "소음", "2026-01")]["denominator"] == 100.0
    assert cells[("S기본", "소음", "2026-01")]["ratio"] == 1.0
    assert cells[("S기본", "발열", "2026-01")]["ratio"] == 2.0  # 건2 · 건6
    assert cells[("A기본", "누수", "2026-01")]["ratio"] == 0.5
    # A 모델의 2월 판매가 없다 — 비율은 비고, 그 수를 말한다.
    assert cells[("A기본", "소음", "2026-02")]["ratio"] is None
    assert table["denominator"]["missing"] >= 1
    # 걸음 너머 기준의 값은 id, 이름은 상대의 이름 — 조건도 그 주소 그대로.
    cell = cells[("S기본", "소음", "2026-01")]
    assert cell["dims"]["base_model"] == w["s_base"]
    assert cell["drill"]["params"]["f.ref.model.base.eq"] == w["s_base"]
    assert _listed(client, admin, w["case"], cell["drill"]["params"]) == cell["count"] == 1


def test_여러_값_기준은_겹침을_말한다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    spec = {
        "measure": "count",
        "time": {"address": "properties.received", "grain": "month"},
        "dimensions": [{"name": "part", "address": "properties.parts"}],
    }
    metric = _define(client, admin, source=w["case"], spec=spec, label="부품 교체")
    assert metric["overlap"] is True
    table = _read(client, admin, metric["slug"], dims="part")
    assert table["overlap"] is True
    counts = {one["labels"]["part"]: one["count"] for one in table["cells"]}
    assert counts == {"P1": 3, "P2": 3, "(비어 있음)": 3}
    assert table["total_count"] == 9  # 기록은 여덟 — 겹침
    empty = next(one for one in table["cells"] if one["dims"]["part"] is None)
    assert empty["drill"]["params"] == {"f.parts.empty": "1"}
    assert _listed(client, admin, w["case"], empty["drill"]["params"]) == 3


def test_합과_평균은_숫자로_읽힌_값에서만(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    spec = {
        "measure": "avg",
        "measure_field": "properties.cost",
        "time": {"address": "properties.received", "grain": "month"},
        "dimensions": [{"name": "symptom", "address": "properties.symptom"}],
    }
    metric = _define(client, admin, source=w["case"], spec=spec, label="평균 비용")
    table = _read(client, admin, metric["slug"], dims="symptom")
    cells = {one["labels"]["symptom"]: one for one in table["cells"]}
    # 누수 한 건은 비용이 없다 — 건수는 1, 평균의 분모(value_count)는 0.
    assert cells["누수"]["count"] == 1 and cells["누수"]["value_count"] == 0
    assert cells["누수"]["avg"] is None and cells["누수"]["value"] is None
    assert cells["소음"]["value_count"] == 5 and cells["소음"]["sum"] == 96.0
    assert cells["소음"]["avg"] == 96.0 / 5 and cells["소음"]["min"] == 1.0


def test_보이는_부서의_셀만_더한다(client: TestClient, admin: Signed, manager: Signed) -> None:
    w = _world(client, admin)
    other = client.post(
        "/api/workspaces",
        json={"slug": f"other-{uuid.uuid4().hex[:6]}", "name": "다른 팀"},
        headers=admin.headers,
    )
    assert other.status_code == 201, other.text
    for index in range(2):
        _make_object(
            client,
            admin,
            w["case"],
            label=f"남의 건{index}",
            workspace_slug=other.json()["slug"],
            properties={"received": "2026-01-20", "symptom": "누수"},
        )
    _, cases = _two(client, admin, w)
    mine = _read(client, manager, cases["slug"], dims="symptom")
    theirs = _read(client, admin, cases["slug"], dims="symptom")
    assert theirs["total_count"] == 10 and mine["total_count"] == 8
    assert {one["labels"]["symptom"]: one["count"] for one in mine["cells"]}["누수"] == 1
    assert {one["labels"]["symptom"]: one["count"] for one in theirs["cells"]}["누수"] == 3
    # 기준 값 목록도 같은 규칙.
    values = _read(client, manager, cases["slug"], "dims", name="symptom")["values"]
    assert {one["label"]: one["count"] for one in values} == {"소음": 5, "발열": 2, "누수": 1}


def test_두_번_세면_셀이_한_벌이고_실패하면_옛_값이_남는다(
    client: TestClient, admin: Signed
) -> None:
    from app.database import SessionLocal
    from app.modules.metrics import services
    from app.modules.metrics.models import MetricDef, MetricValue

    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    first = cases["current_run_id"]
    again = client.post(f"/api/metrics/{cases['slug']}/recompute", headers=admin.headers)
    assert again.status_code == 202, again.text
    assert finish_job(client, admin, again.json())["status"] == "done"
    now = client.get(f"/api/metrics/{cases['slug']}", headers=admin.headers).json()
    assert now["current_run_id"] != first
    with SessionLocal() as db:
        metric = db.get(MetricDef, uuid.UUID(now["id"]))
        assert metric is not None
        assert services.values_count(db, metric) == metric.cells
        stale_rows = (
            db.query(MetricValue).filter(MetricValue.run_id == uuid.UUID(first)).count()
        )
        assert stale_rows == 0
        # 칸이 지워진 것처럼 정의를 망가뜨린다 — 화면으로는 계획이 막으므로 표에서 직접.
        spec = dict(metric.spec)
        spec["dimensions"] = [{"name": "gone", "address": "properties.nope"}]
        metric.spec = spec
        db.commit()
    failed = client.post(f"/api/metrics/{cases['slug']}/recompute", headers=admin.headers)
    done = finish_job(client, admin, failed.json())
    assert done["status"] == "failed" and "없는 칸" in str(done["error"])
    broken = client.get(f"/api/metrics/{cases['slug']}", headers=admin.headers).json()
    assert broken["last_status"] == "failed" and "없는 칸" in broken["last_error"]
    assert broken["broken"] is not None and broken["current_run_id"] == now["current_run_id"]
    runs = client.get(f"/api/metrics/{cases['slug']}/runs", headers=admin.headers).json()
    assert runs[0]["status"] == "failed" and runs[0]["error"]
    # 홈의 「남은 일」 이 실패한 지표를 말한다 — 시스템 관리자에게.
    items = {
        one["key"]: one
        for one in client.get("/api/server/maintenance", headers=admin.headers).json()
    }
    assert (
        items["metric_failed"]["count"] >= 1
        and items["metric_failed"]["severity"] == "warning"
    )


def test_적재_뒤_훅이_작업을_넣고_중복은_안_넣는다(
    client: TestClient,
    admin: Signed,
    plm: FakeOData,  # noqa: F811 — 픽스처
) -> None:
    from app.database import SessionLocal
    from app.modules.jobs import services as job_services
    from app.modules.metrics import services
    from app.modules.ontology.models import ObjectType

    vendor = _vendor_type(client, admin)
    # 가짜 서버의 이름을 바꿔 둔다 — 「ANSYS Inc.」 를 더 만들면 검색 시험(`search("ansys")`)의
    # 상한 안에서 그 줄이 밀려난다(실측: 전체 시험에서만 깨졌다).
    for row in plm.rows:
        row["Name"] = f"벤더 {row['VendorNo']}"
    spec = {
        "measure": "count",
        "dimensions": [{"name": "country", "address": "properties.country"}],
    }
    metric = _define(
        client, admin, source=vendor, spec=spec, label="나라별 공급사", recompute=False
    )
    assert metric["stale"] is True and metric["last_run_at"] is None
    source = _source(client, admin, vendor)
    assert _sync(client, admin, source["slug"], apply=True).status_code == 200
    with SessionLocal() as db:
        pending = services.pending_recompute(db, metric["slug"])
        assert pending is not None and pending.requested_by_id is None
        assert pending.params["reason"] == f"datasource:{source['slug']}"
        # 같은 지표의 작업이 줄에 있으면 더 넣지 않는다.
        type_row = db.scalar(select(ObjectType).where(ObjectType.slug == vendor))
        assert type_row is not None
        assert services.enqueue_for_type(db, type_row.id, reason="again") == []
        job_id = str(pending.id)
    done = finish_job(client, admin, {"id": job_id})
    assert done["status"] == "done", done
    assert done["result"]["runs"][0]["status"] == "ok"
    table = _read(client, admin, metric["slug"], dims="country")
    assert {one["labels"]["country"]: one["count"] for one in table["cells"]} == {
        "미국": 2,
        "한국": 1,
    }
    # 시간 칸이 없는 지표는 추이를 못 낸다 — 그 사실을 말한다.
    denied = client.get(f"/api/metrics/{metric['slug']}/series", headers=admin.headers)
    assert denied.status_code == 422 and "시간 칸" in denied.json()["error"]["message"]
    # **바뀐 것이 없는 동기화는 다시 세지 않는다** — 몇 분마다 도는 소스가 매번 200만 건
    # 지표를 다시 세게 하면 DB 가 쉬지 못한다.
    again = _sync(client, admin, source["slug"], apply=True)
    assert again.status_code == 200 and again.json()["counts"]["create"] == 0
    with SessionLocal() as db:
        assert services.pending_recompute(db, metric["slug"]) is None
    # 한 줄이라도 바뀌면 다시 들어간다.
    plm.rows[0]["Rating"] = 99
    _sync(client, admin, source["slug"], apply=True)
    with SessionLocal() as db:
        assert services.pending_recompute(db, metric["slug"]) is not None
    while job_services.process_one("test-worker"):
        pass


def test_타이머가_돌릴_차례(client: TestClient, admin: Signed) -> None:
    """타이머는 **매시간** 깬다. 하루 미만 주기는 그 시간이 지났으면 아무 때나, 「매일 밤 ·
    N일마다 밤」 은 밤 시간에만 — 그리고 **타이머가 넣은 밤**에서 센다(끝난 시각이 아니다).

    예전에는 끝난 시각에서 「24시간이 지났나」 를 봐서, 오늘 밤 타이머가 어제 끝난 시각보다 몇
    분 일찍 깨면 건너뛰어 이틀에 한 번꼴로 셀 수 있었다. 낮에 적재로 다시 센 날도 밤 계산이
    밀렸다.
    """
    from app.database import SessionLocal
    from app.modules.metrics import services
    from app.modules.metrics.models import MetricDef

    w = _world(client, admin)
    nightly = _define(client, admin, source=w["sales"], spec=_sales_spec(), recompute=False)
    hourly = _define(
        client, admin, source=w["sales"], spec=_sales_spec(), recompute=False, interval_hours=6
    )
    weekly = _define(
        client,
        admin,
        source=w["sales"],
        spec=_sales_spec(),
        recompute=False,
        interval_hours=168,
    )
    manual = _define(
        client, admin, source=w["sales"], spec=_sales_spec(), recompute=False, interval_hours=0
    )
    night = datetime(2026, 10, 8, 2, 35, tzinfo=UTC)

    def due(now: datetime, *, at_night: bool) -> set[str]:
        with SessionLocal() as db:
            mine = {nightly["slug"], hourly["slug"], weekly["slug"], manual["slug"]}
            return {one.slug for one in services.due(db, now, nightly=at_night)} & mine

    def set_times(slug: str, **values: Any) -> None:
        with SessionLocal() as db:
            row = db.scalar(select(MetricDef).where(MetricDef.slug == slug))
            assert row is not None
            for key, value in values.items():
                setattr(row, key, value)
            db.commit()

    # 한 번도 안 셌다 — 시간마다는 바로, 밤마다는 다음 밤에. 손으로만은 안 센다.
    assert due(night - timedelta(hours=12), at_night=False) == {hourly["slug"]}
    assert due(night, at_night=True) == {nightly["slug"], hourly["slug"], weekly["slug"]}

    # 밤에 넣고 몇 분 뒤 끝났다. 다음 밤 타이머가 **몇 분 일찍** 깨어도 차례다.
    for one in (nightly, weekly):
        set_times(one["slug"], scheduled_at=night, last_run_at=night + timedelta(minutes=6))
    set_times(hourly["slug"], last_run_at=night + timedelta(minutes=6))
    next_night = night + timedelta(days=1) - timedelta(minutes=4)
    assert nightly["slug"] in due(next_night, at_night=True)
    assert weekly["slug"] not in due(next_night, at_night=True)  # 7밤마다
    assert weekly["slug"] in due(night + timedelta(days=7, minutes=-4), at_night=True)
    # 같은 밤에 두 번은 아니다 · 낮에는 밤마다가 안 돈다(무거운 계산이 낮에 안 돈다).
    assert nightly["slug"] not in due(night + timedelta(minutes=50), at_night=True)
    assert nightly["slug"] not in due(night + timedelta(hours=12), at_night=False)
    # **낮에 적재로 다시 셌어도** 밤 계산은 그대로 — 끝난 시각과 견주지 않는다.
    set_times(nightly["slug"], last_run_at=night + timedelta(hours=12))
    assert nightly["slug"] in due(next_night, at_night=True)

    # 시간마다 — 그 시간이 지났으면 낮에도. 타이머가 몇 분 일찍 깨어도 차례다.
    assert hourly["slug"] not in due(night + timedelta(hours=3), at_night=False)
    assert hourly["slug"] in due(night + timedelta(hours=6, minutes=-4), at_night=False)

    # 밤을 놓쳤으면(서버가 꺼져 있었다) 아침에 따라잡는다.
    assert nightly["slug"] in due(night + timedelta(days=1, hours=7), at_night=False)


def test_하루_이상_주기는_날_단위다(client: TestClient, admin: Signed) -> None:
    """36시간 같은 값은 「밤에 센다」 와 「시간마다 센다」 어느 쪽인지 말이 안 된다."""
    w = _world(client, admin)
    made = client.post(
        "/api/metrics",
        json={
            "slug": f"m_{uuid.uuid4().hex[:6]}",
            "label": "주기 틀림",
            "source_type_slug": w["sales"],
            "spec": _sales_spec(),
            "interval_hours": 36,
        },
        headers=admin.headers,
    )
    assert made.status_code == 422, made.text
    assert "날 단위" in made.text


def test_고치기와_지우기(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    sales, cases = _two(client, admin, w)
    # 정의를 고칠 때도 계획이 막는다.
    bad = dict(_cases_spec(sales["slug"]))
    bad["dimensions"] = [{"name": "x", "address": "properties.nope"}]
    denied = client.patch(
        f"/api/metrics/{cases['slug']}", json={"spec": bad}, headers=admin.headers
    )
    assert denied.status_code == 422
    renamed = client.patch(
        f"/api/metrics/{cases['slug']}", json={"label": "월별 인입"}, headers=admin.headers
    )
    assert renamed.status_code == 200 and renamed.json()["metric"]["label"] == "월별 인입"
    # 분모로 쓰이는 지표는 못 지운다.
    assert (
        client.delete(f"/api/metrics/{sales['slug']}", headers=admin.headers).status_code
        == 409
    )
    assert (
        client.delete(f"/api/metrics/{cases['slug']}", headers=admin.headers).status_code
        == 204
    )
    assert (
        client.delete(f"/api/metrics/{sales['slug']}", headers=admin.headers).status_code
        == 204
    )
    assert (
        client.get(f"/api/metrics/{cases['slug']}", headers=admin.headers).status_code == 404
    )


def test_모르는_기준과_축은_거절한다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    got = client.get(
        f"/api/metrics/{cases['slug']}/values", params={"dims": "nope"}, headers=admin.headers
    )
    assert got.status_code == 422 and "없는 기준" in got.json()["error"]["message"]
    got = client.get(
        f"/api/metrics/{cases['slug']}/values", params={"by": "week"}, headers=admin.headers
    )
    assert got.status_code == 422
    got = client.get(
        f"/api/metrics/{cases['slug']}/values",
        params={"period_from": "2026-13"},
        headers=admin.headers,
    )
    assert got.status_code == 422
    got = client.get(
        f"/api/metrics/{cases['slug']}/dims", params={"name": "nope"}, headers=admin.headers
    )
    assert got.status_code == 422


def test_타이머_스크립트는_차례인_것만_넣고_겹치지_않는다(
    client: TestClient, admin: Signed, capsys: Any
) -> None:
    """`scripts/recompute_metrics.py --due` — 지표마다 작업 하나, 같은 지표가 줄에 있으면 안
    넣는다."""
    import importlib.util
    from argparse import Namespace
    from pathlib import Path

    from app.database import SessionLocal

    script = Path(__file__).resolve().parents[2] / "scripts" / "recompute_metrics.py"
    spec = importlib.util.spec_from_file_location("recompute_metrics_under_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    w = _world(client, admin)
    every = _define(client, admin, source=w["sales"], spec=_sales_spec(), recompute=False)
    manual = _define(
        client, admin, source=w["sales"], spec=_sales_spec(), recompute=False, interval_hours=0
    )
    # 밤 시간으로 본다 — 「매일 밤」 지표는 밤에만 차례다.
    due = Namespace(due=True, all=False, slug=None, now=False, nightly=True)
    with SessionLocal() as db:
        assert module.enqueue(db, due) == 0
        first = capsys.readouterr().out
        assert f"{every['slug']}: 작업" in first and manual["slug"] not in first
        # 두 번째 — 같은 밤에 또 차례가 아니다(타이머가 넣은 때를 적었다).
        assert module.enqueue(db, due) == 0
        assert every["slug"] not in capsys.readouterr().out
        # 하나만 — 꺼진 주기의 지표도 손으로는 넣는다.
        assert (
            module.enqueue(db, Namespace(due=False, all=False, slug=manual["slug"], now=False))
            == 0
        )
        assert f"{manual['slug']}: 작업" in capsys.readouterr().out
        assert module.enqueue(db, Namespace(due=False, all=False, slug="nope", now=False)) == 1
    # 넣어 둔 작업을 여기서 비운다 — 남겨 두면 뒤의 시험이 `process_one` 으로 **남의 작업**을
    # 집어 자기 작업이 안 돈 줄 안다(실측: 웹훅 시험 둘이 그렇게 깨졌다).
    from app.modules.jobs import services as job_services

    while job_services.process_one("test-worker"):
        pass


def test_SKU_와_기본_모델을_관계로_이어도_분모와_짝이_맞는다(
    client: TestClient, admin: Signed
) -> None:
    """사내에서는 SKU → 기본 모델을 **관계로** 잇는다. 기록은 `ref.model.out.<관계>` 로 기본
    모델에 닿고 판매 집계는 기본 모델 **참조 칸**으로 닿는다 — 값이 같은 id 라 짝이 맞아야
    한다."""
    base = _make_type(client, admin, label="기본 모델")
    sku = _make_type(client, admin, label="SKU")
    rel = _make_relation(
        client,
        admin,
        label="기본 모델",
        src_type_slugs=[sku],
        dst_type_slugs=[base],
        directed=True,
        cardinality="many_to_one",
    )
    case = _make_type(client, admin, label="기록", usage="log")
    _prop(client, admin, case, "model", "모델", data_type="object_ref", ref_type_slug=sku)
    _prop(client, admin, case, "received", "접수일", data_type="date")
    _prop(client, admin, case, "sold", "판매일", data_type="date")
    sales = _make_type(client, admin, label="판매", usage="log")
    _prop(
        client,
        admin,
        sales,
        "base_model",
        "기본 모델",
        data_type="object_ref",
        ref_type_slug=base,
    )
    _prop(client, admin, sales, "month", "판매월", data_type="date")
    _prop(client, admin, sales, "units", "대수", data_type="number")
    s_base = _make_object(client, admin, base, label="S기본")["id"]
    s1 = _make_object(client, admin, sku, label="S-1")["id"]
    _relate(client, admin, sku, s1, rel, s_base)
    _make_object(
        client,
        admin,
        sales,
        label="판매 1월",
        properties={"base_model": s_base, "month": "2026-01-01", "units": 50},
    )
    for received in ("2026-01-20", "2026-02-10"):
        _make_object(
            client,
            admin,
            case,
            label=received,
            properties={"model": s1, "received": received, "sold": "2026-01-05"},
        )
    sales_metric = _define(client, admin, source=sales, spec=_sales_spec(), label="판매 대수")
    spec = {
        "measure": "count",
        "time": {"address": "properties.received", "grain": "month"},
        "cohort": {"address": "properties.sold", "grain": "month"},
        "dimensions": [{"name": "base_model", "address": f"ref.model.out.{rel}"}],
        "denominator": {
            "metric": sales_metric["slug"],
            "on": ["base_model"],
            "time": "cohort",
            "per": 100,
        },
    }
    cases = _define(client, admin, source=case, spec=spec, label="인입(관계)")
    assert cases["dims"][0]["kind"] == "related" and cases["broken"] is None

    found = _read(client, admin, cases["slug"], "cohort", cumulative="true")
    row = found["rows"][0]
    assert row["label"] == "2026-01" and row["denominator"] == 50.0
    assert [one["cumulative"] for one in row["cells"]] == [1.0, 2.0]
    assert row["cells"][1]["ratio"] == 4.0
    # 이름은 관계의 상대에서, 건 보기는 같은 주소의 조건으로.
    table = _read(client, admin, cases["slug"], dims="base_model")
    cell = table["cells"][0]
    assert cell["labels"]["base_model"] == "S기본" and cell["dims"]["base_model"] == s_base
    assert cell["drill"]["params"] == {f"f.ref.model.out.{rel}.eq": s_base}
    assert _listed(client, admin, case, cell["drill"]["params"]) == cell["count"] == 2


def test_거른_읽기의_건_보기도_그_거르기를_건다(client: TestClient, admin: Signed) -> None:
    """**셀의 수 = 그 셀의 건 보기 목록 수** — 읽을 때 건 거르기(`d.<기준>`)와 묶지 않은 기간 ·
    코호트 범위까지 조건이 돼야 한다. 0.4.32 는 기본 모델로 거른 코호트 · 추이에서 셀을 누르면
    전체 기본 모델의 기록을 열었다(거르지 않은 경우만 시험했다)."""
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    s_filter = {"d.base_model": w["s_base"]}

    def check(items: list[tuple[int, dict[str, Any]]]) -> int:
        seen = 0
        for count, drill in items:
            if count == 0 or drill["partial"]:
                continue
            assert _listed(client, admin, w["case"], drill["params"]) == count, drill
            seen += 1
        return seen

    cohort = _read(client, admin, cases["slug"], "cohort", cohort_to="2026-03-01", **s_filter)
    assert cohort["rows"], cohort
    first = cohort["rows"][0]["cells"][0]["drill"]["params"]
    assert first["f.ref.model.base.eq"] == w["s_base"]
    assert check([(c["count"], c["drill"]) for r in cohort["rows"] for c in r["cells"]]) >= 2

    series = _read(
        client, admin, cases["slug"], "series", period_to="2026-04-01", **{"d.symptom": "소음"}
    )
    points = series["lines"][0]["points"]
    assert points[0]["drill"]["params"]["f.symptom.eq"] == "소음"
    assert check([(p["count"], p["drill"]) for p in points]) >= 2

    # 기간 범위만 건 표 — 기간으로 묶지 않아도 그 범위가 조건이 된다.
    ranged = _read(
        client,
        admin,
        cases["slug"],
        dims="symptom",
        period_from="2026-02-01",
        period_to="2026-04-01",
        **s_filter,
    )
    cell = ranged["cells"][0]["drill"]["params"]
    assert cell["f.received.gte"] == "2026-02-01" and cell["f.received.lt"] == "2026-04-01"
    assert check([(c["count"], c["drill"]) for c in ranged["cells"]]) >= 1
