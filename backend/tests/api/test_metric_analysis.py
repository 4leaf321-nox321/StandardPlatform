"""분석(ADR 0014) — 진짜 앱으로. 레시피의 수식은 단위 시험(`tests/unit/test_recipe_*`)이 정답과
견주고, 여기서는 **경로 · 거르기 · 거절 · 주의 · 건 보기**가 선다는 것을 본다.

건 보기는 언제나 「그 수 = 그 목록의 수」 다 — 분석의 숫자도 근거로 돌아가야 한다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.modules.metrics.recipes import groups as groups_recipe
from tests.api.conftest import Signed
from tests.api.test_metrics import _define, _listed, _two, _world
from tests.api.test_ontology import _make_object


def _analysis(
    client: TestClient, who: Signed, slug: str, recipe: str, **params: Any
) -> dict[str, Any]:
    got = client.get(
        f"/api/metrics/{slug}/analysis/{recipe}", params=params, headers=who.headers
    )
    assert got.status_code == 200, got.text
    return dict(got.json())


def _refused(
    client: TestClient, who: Signed, slug: str, recipe: str, **params: Any
) -> dict[str, Any]:
    got = client.get(
        f"/api/metrics/{slug}/analysis/{recipe}", params=params, headers=who.headers
    )
    assert got.status_code == 422, got.text
    return dict(got.json()["error"])


def test_지표가_되는_분석과_안_되는_이유를_말한다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    found = {one["recipe"]: one for one in cases["analyses"]}
    assert found["pareto"]["ok"] is True and found["pareto"]["label"] == "파레토 · 집중도"
    average = _define(
        client,
        admin,
        source=w["case"],
        spec={
            "measure": "avg",
            "measure_field": "properties.cost",
            "dimensions": [{"name": "symptom", "address": "properties.symptom"}],
        },
        label="평균 비용",
        recompute=False,
    )
    pareto = next(one for one in average["analyses"] if one["recipe"] == "pareto")
    assert pareto["ok"] is False and "건수 · 합계" in pareto["reason"]


def test_파레토는_몫과_집중도와_건_보기를_준다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    found = _analysis(client, admin, cases["slug"], "pareto", dim="symptom")
    assert found["recipe"] == "pareto" and found["basis"] == "records"
    assert found["method"].startswith("파레토") and found["computed_at"] is not None
    assert [(one["label"], one["count"]) for one in found["items"]] == [
        ("소음", 5),
        ("발열", 2),
        ("누수", 1),
    ]
    assert [one["cls"] for one in found["items"]] == ["A", "A", "B"]
    assert found["items"][0]["share"] == pytest.approx(5 / 8)
    assert found["items"][-1]["cumulative"] == pytest.approx(1.0)
    concentration = found["concentration"]
    assert concentration["hhi"] == pytest.approx((25 + 4 + 1) / 64)
    assert concentration["categories"] == 3 and concentration["vital_few"] == 2
    for item in found["items"]:
        assert item["drill"]["partial"] == []
        assert _listed(client, admin, w["case"], item["drill"]["params"]) == item["count"]
    # 기본 모델로 거르면 그 기본 모델의 몫이고, 건 보기도 그 거르기를 건다.
    only_s = _analysis(
        client, admin, cases["slug"], "pareto", dim="symptom", **{"d.base_model": w["s_base"]}
    )
    assert [(one["label"], one["count"]) for one in only_s["items"]] == [
        ("소음", 4),
        ("발열", 2),
    ]
    for item in only_s["items"]:
        assert item["drill"]["params"]["f.ref.model.base.eq"] == w["s_base"]
        assert _listed(client, admin, w["case"], item["drill"]["params"]) == item["count"]
    # 기간별 추이 — 기간마다 집중도.
    trend = _analysis(
        client,
        admin,
        cases["slug"],
        "pareto",
        dim="symptom",
        by_period="true",
        period_to="2026-04-01",
    )
    assert [one["label"] for one in trend["trend"]] == ["2026-01", "2026-02", "2026-03"]
    assert trend["params"]["by_period"] is True


def test_여러_값_기준은_묶거나_걸러야_한다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    metric = _define(
        client,
        admin,
        source=w["case"],
        spec={
            "measure": "count",
            "dimensions": [
                {"name": "symptom", "address": "properties.symptom"},
                {"name": "part", "address": "properties.parts"},
            ],
        },
        label="증상 x 부품",
    )
    # 부품을 묶지도 거르지도 않고 증상의 몫을 보면 부품 둘인 기록이 두 번 센다 — 거절.
    refused = _refused(client, admin, metric["slug"], "pareto", dim="symptom")
    assert refused["code"].endswith("METRICS-0021") and "d.part=" in refused["message"]
    one_part = _analysis(
        client, admin, metric["slug"], "pareto", dim="symptom", **{"d.part": w["p1"]}
    )
    assert sum(one["count"] for one in one_part["items"]) == 3
    by_part = _analysis(client, admin, metric["slug"], "pareto", dim="part")
    assert by_part["basis"] == "occurrences"
    assert "overlap_basis" in {one["code"] for one in by_part["caveats"]}
    assert {one["label"]: one["count"] for one in by_part["items"]} == {"P1": 3, "P2": 3}
    assert by_part["empty_count"] == 3
    unknown = _refused(client, admin, metric["slug"], "pareto", dim="nope")
    assert unknown["code"].endswith("METRICS-0023")


def test_셀이_상한에서_잘리면_거절한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    monkeypatch.setattr(get_settings(), "metrics_max_read_cells", 1)
    refused = _refused(client, admin, cases["slug"], "pareto", dim="symptom")
    assert refused["code"].endswith("METRICS-0020") and "잘렸습니다" in refused["message"]


def test_일부_부서만_보이면_그렇게_말한다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
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
    mine = _analysis(client, manager, cases["slug"], "pareto", dim="symptom")
    assert mine["visible_share"] == pytest.approx(8 / 10)
    assert "partial_visibility" in {one["code"] for one in mine["caveats"]}
    everything = _analysis(client, admin, cases["slug"], "pareto", dim="symptom")
    assert everything["visible_share"] == 1.0
    assert "partial_visibility" not in {one["code"] for one in everything["caveats"]}


# --- ② 수명 ------------------------------------------------------------------------


def _plant(slug: str, cells: list[dict[str, Any]], watermark: datetime) -> None:
    """지표의 지금 계산을 **심은 셀**로 바꾼다 — 기록 수만 건을 만들지 않고 정답을 아는 셀
    위에서 경로 · 분모 결합 · 닫힘을 본다(건 보기는 기록이 없어 여기서 안 본다)."""
    from app.database import SessionLocal
    from app.modules.metrics.models import MetricDef, MetricRun, MetricValue

    with SessionLocal() as db:
        metric = db.scalars(select(MetricDef).where(MetricDef.slug == slug)).one()
        run = MetricRun(
            metric_id=metric.id,
            status="ok",
            watermark=watermark,
            finished_at=watermark,
            rows=sum(one["count"] for one in cells),
            cells=len(cells),
        )
        db.add(run)
        db.flush()
        for one in cells:
            db.add(
                MetricValue(
                    metric_id=metric.id,
                    run_id=run.id,
                    cell_hash=uuid.uuid4(),
                    workspace_id=None,
                    period=one.get("period"),
                    cohort=one.get("cohort"),
                    age=one.get("age"),
                    dims=one["dims"],
                    count=one["count"],
                    value_count=one.get("value_count", 0),
                    sum=one.get("sum"),
                    min=one.get("sum"),
                    max=one.get("sum"),
                )
            )
        metric.current_run_id = run.id
        db.commit()


def _month(start: date, months: int) -> date:
    index = start.year * 12 + start.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _weibull_steps(ages: int, beta: float, eta: float, p: float, units: float) -> list[int]:
    """경과 a 의 기대 건수 — 판매일이 달 안에 고르면 P(경과 = a) = G(a) - G(a-1),
    G(a) = ∫_a^{a+1} W. 가는 사다리꼴로 적분하고 반올림한다."""
    grid = np.linspace(0.0, 1.0, 2001)
    mass = [
        float(np.trapezoid(-np.expm1(-(((a + grid) / eta) ** beta)), a + grid))
        for a in range(ages)
    ]
    previous = [0.0, *mass[:-1]]
    return [
        round(units * p * (now - before)) for now, before in zip(mass, previous, strict=True)
    ]


def test_수명은_닫힌_경과로_맞추고_B10_을_지어내지_않는다(
    client: TestClient, admin: Signed
) -> None:
    """판매의 4.4%만 고장 나는 제품(형상 1.5 · 척도 400일) — 심은 셀에서 결함 모형을 고르고
    정답을 되찾는다. 닫히지 않은 경과 · 코호트, 판매가 덜 들어온 달, 분모 없는 달은 빼고
    그 수를 말한다."""
    w = _world(client, admin)
    sales, cases = _two(client, admin, w)
    dims = {"base_model": w["s_base"], "symptom": "소음", "factory": "F1"}
    first = date(2026, 1, 1)
    units = 20000.0
    steps = _weibull_steps(36, 1.5, 400 / 30.4375, 0.044, units)
    planted: list[dict[str, Any]] = []
    # 2026-01 ~ 2028-11 코호트 — 2028-11 까지 닫혔다(계산 2029-01-15, 닫힘 30일).
    for i in range(35):
        start = _month(first, i)
        horizon = 34 - i
        for age in range(horizon + 1):
            if steps[age]:
                planted.append(
                    {
                        "period": _month(start, age),
                        "cohort": start,
                        "age": age,
                        "dims": dims,
                        "count": steps[age],
                    }
                )
        # 아직 들어오는 중인 달(2028-12)의 기록 — 맞춤에서 뺀다.
        planted.append(
            {
                "period": _month(start, horizon + 1),
                "cohort": start,
                "age": horizon + 1,
                "dims": dims,
                "count": 3,
            }
        )
    # 닫히지 않은 코호트(2028-12), 판매가 덜 들어온 달(2029-01), 판매 대수가 없는 달(2025-12).
    for month, count in (
        (date(2028, 12, 1), 4),
        (date(2029, 1, 1), 6),
        (date(2025, 12, 1), 5),
    ):
        planted.append(
            {"period": month, "cohort": month, "age": 0, "dims": dims, "count": count}
        )
    _plant(cases["slug"], planted, datetime(2029, 1, 15, tzinfo=UTC))
    # 판매 대수 — 2026-01 ~ 2029-01. 판매 계산이 2029-01-20 이라 2029-01 판매는 덜 들어왔다.
    _plant(
        sales["slug"],
        [
            {
                "period": _month(first, i),
                "dims": {"base_model": w["s_base"]},
                "count": 1,
                "value_count": 1,
                "sum": units,
            }
            for i in range(37)
        ],
        datetime(2029, 1, 20, tzinfo=UTC),
    )

    found = _analysis(client, admin, cases["slug"], "life")
    assert found["recipe"] == "life" and found["method"].startswith("와이블")
    assert found["chosen"] == "defective" and found["time_unit"] == "month"
    assert found["lrt_p_value"] < 1e-10
    fits = {one["model"]: one for one in found["fits"]}
    assert fits["defective"]["beta"] == pytest.approx(1.5, rel=0.08)
    assert fits["defective"]["eta_days"] == pytest.approx(400, rel=0.08)
    assert fits["defective"]["p"] == pytest.approx(0.044, rel=0.08)
    assert fits["weibull"]["beta"] < 1.0  # 평평한 곡선을 따라간 표준 모형
    lives = found["b_lives"]
    assert [one["status"] for one in lives] == ["observed", "unreachable", "unreachable"]
    assert lives[2]["age"] is None and lives[2]["age_days"] is None and lives[2]["ci"] is None
    assert lives[2]["conditional_age"] is not None
    assert lives[0]["age"] is not None and lives[0]["observed_age"] is not None
    # 쓰는 코호트는 2026-01 ~ 2028-11 — 2028-11 은 경과 0 하나만 닫혔다.
    assert found["cohorts_used"] == 35 and found["max_age"] == 34
    assert found["excluded"] == {
        "missing_denominator": 5,
        "open_denominator": 6,
        "open_cohort": 4,
        "open_cells": 3 * 35,
    }
    codes = {one["code"] for one in found["caveats"]}
    assert {"missing_denominator", "open_cells_excluded", "records_not_units"} <= codes
    assert "plateau_unknown" not in codes
    assert found["units"] == units * 35
    assert len(found["points"]) == 35 and found["points"][0]["age"] == 0
    assert found["points"][-1]["cohorts"] == 1  # 경과 34 는 2026-01 코호트만 봤다
    assert len(found["cohort_rows"]) == 35 and found["cohort_rows"][0]["horizon"] == 34
    assert found["curve"] and found["gof_p_value"] is not None

    compact = _analysis(client, admin, cases["slug"], "life", compact="true")
    assert compact["curve"] == [] and compact["cohort_rows"] == []
    assert [one["status"] for one in compact["b_lives"]] == [
        "observed",
        "unreachable",
        "unreachable",
    ]
    # 표준 모형을 억지로 고르면 관측 밖을 읽고, 「결국 고장 나는 비율」 을 모른다고 말한다.
    standard = _analysis(client, admin, cases["slug"], "life", model="weibull")
    assert standard["chosen"] == "weibull" and len(standard["fits"]) == 1
    assert [one["status"] for one in standard["b_lives"]][1:] == ["extrapolated"] * 2
    assert "plateau_unknown" in {one["code"] for one in standard["caveats"]}
    # 경과를 12 개로 자르면 그 뒤의 건은 뺀 수로 말한다.
    short = _analysis(client, admin, cases["slug"], "life", max_age=12)
    assert short["max_age"] == 11
    assert short["excluded"]["beyond_max_age"] > 0


def test_수명의_코호트_건_보기는_닫힌_경과까지의_기록이다(
    client: TestClient, admin: Signed
) -> None:
    """기록이 적으면 맞추지 않고 비모수 곡선만 — 그래도 코호트마다의 수는 근거로 돌아간다."""
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    found = _analysis(client, admin, cases["slug"], "life")
    assert found["fits"] == [] and found["chosen"] is None
    assert "too_few_failures" in {one["code"] for one in found["caveats"]}
    # 오늘 판 오늘 접수 건은 판매 대수가 없는 달이다.
    assert found["excluded"]["missing_denominator"] == 1
    rows = {one["label"]: one for one in found["cohort_rows"]}
    assert set(rows) == {"2026-01", "2026-02"}
    assert (rows["2026-01"]["units"], rows["2026-01"]["failures"]) == (300.0, 3)
    assert (rows["2026-02"]["units"], rows["2026-02"]["failures"]) == (50.0, 2)
    for row in rows.values():
        assert row["drill"]["partial"] == []
        assert _listed(client, admin, w["case"], row["drill"]["params"]) == row["failures"]
    assert found["b_lives"][0]["status"] == "observed"
    assert [one["status"] for one in found["b_lives"]][1:] == ["none", "none"]
    # 기본 모델로 거르면 분모도 그 모델의 판매 대수, 건 보기도 그 거르기를 건다.
    only_s = _analysis(client, admin, cases["slug"], "life", **{"d.base_model": w["s_base"]})
    rows_s = {one["label"]: one for one in only_s["cohort_rows"]}
    assert (rows_s["2026-01"]["units"], rows_s["2026-01"]["failures"]) == (100.0, 2)
    for row in rows_s.values():
        assert row["drill"]["params"]["f.ref.model.base.eq"] == w["s_base"]
        assert _listed(client, admin, w["case"], row["drill"]["params"]) == row["failures"]


def test_수명이_안_되는_지표는_이유와_함께_거절한다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    sales, cases = _two(client, admin, w)
    assert next(one for one in cases["analyses"] if one["recipe"] == "life")["ok"] is True
    reason = next(one for one in sales["analyses"] if one["recipe"] == "life")
    assert reason["ok"] is False and "건수" in reason["reason"]
    refused = _refused(client, admin, sales["slug"], "life")
    assert refused["code"].endswith("METRICS-0024")


# --- ③ 관리도 ----------------------------------------------------------------------


def test_관리도는_코호트_창의_비율이고_건_보기가_창의_기록이다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    found = _analysis(client, admin, cases["slug"], "control", split="base_model")
    assert found["axis"] == "cohort" and found["window"] == 3 and found["kind"] == "u"
    assert found["per"] == 100 and found["split_label"] == "모델 › 기본 모델"
    assert [one["number"] for one in found["rules"]] == [1, 2, 3, 5]
    charts = {one["label"]: one for one in found["charts"]}
    assert set(charts) == {"S기본", "A기본"}
    s_points = {one["label"]: one for one in charts["S기본"]["points"]}
    # S 의 1월 판매 코호트: 출고 3개월 안 두 건 / 100대(x 100).
    assert s_points["2026-01"]["count"] == 2 and s_points["2026-01"]["exposure"] == 100.0
    assert s_points["2026-01"]["rate"] == pytest.approx(2.0)
    assert charts["S기본"]["center"] == pytest.approx(3 / 150 * 100)
    for chart in charts.values():
        for point in chart["points"]:
            assert point["drill"]["partial"] == []
            assert (
                _listed(client, admin, w["case"], point["drill"]["params"]) == point["count"]
            )
    assert s_points["2026-01"]["drill"]["params"]["f.received.lt"] == "2026-04-01"
    # A 의 2월 · S 의 오늘 달은 판매 대수가 없다.
    assert found["excluded"]["missing_denominator"] == 2
    codes = {one["code"] for one in found["caveats"]}
    assert {"window_basis", "few_subgroups", "missing_denominator"} <= codes
    # 분모가 공장으로 나뉘지 않으니 공장으로 나누면 비율이 틀린다 — 거절.
    refused = _refused(client, admin, cases["slug"], "control", split="factory")
    assert refused["code"].endswith("METRICS-0026") and "d.factory=" in refused["message"]


def test_분모가_축과_짝이_아니면_건수_관리도라고_말한다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    found = _analysis(client, admin, cases["slug"], "control", axis="period")
    assert found["kind"] == "c" and found["per"] == 1.0
    assert "count_basis" in {one["code"] for one in found["caveats"]}
    (chart,) = found["charts"]
    points = {one["label"]: one for one in chart["points"]}
    assert [points[label]["count"] for label in ("2026-01", "2026-02", "2026-03")] == [1, 2, 3]
    assert points["2026-04"]["count"] == 0 and points["2026-04"]["closed"] is True
    # 오늘 접수한 건은 열린 달 — 그리되 한계 · 규칙에서 뺀다.
    assert found["excluded"]["open"] == 1
    for point in chart["points"]:
        assert point["exposure"] is None
        assert _listed(client, admin, w["case"], point["drill"]["params"]) == point["count"]


def _monthly(
    client: TestClient, admin: Signed, w: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(판매 대수, 월 인입률) — 접수 월마다의 건수를 그 달의 판매 대수와 짝짓는다."""
    sales, _ = _two(client, admin, w)
    monthly = _define(
        client,
        admin,
        source=w["case"],
        spec={
            "measure": "count",
            "time": {"address": "properties.received", "grain": "month"},
            "dimensions": [{"name": "base_model", "address": "ref.model.base"}],
            "denominator": {
                "metric": sales["slug"],
                "on": ["base_model"],
                "time": "period",
                "per": 1000,
            },
            "settle_days": 30,
        },
        label="월 인입률",
    )
    return sales, monthly


def _plant_months(
    w: dict[str, Any],
    sales: dict[str, Any],
    monthly: dict[str, Any],
    counts: list[int],
    units: float,
    watermark: datetime,
) -> None:
    first = date(2026, 1, 1)
    dims = {"base_model": w["s_base"]}
    _plant(
        monthly["slug"],
        [
            {"period": _month(first, i), "dims": dims, "count": count}
            for i, count in enumerate(counts)
        ],
        watermark,
    )
    _plant(
        sales["slug"],
        [
            {
                "period": _month(first, i),
                "dims": dims,
                "count": 1,
                "value_count": 1,
                "sum": units,
            }
            for i in range(len(counts))
        ],
        watermark,
    )


def test_관리도는_큰_부분군을_라니로_넓히고_튄_달을_잡는다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    sales, monthly = _monthly(client, admin, w)
    first = date(2026, 1, 1)
    dims = {"base_model": w["s_base"]}
    # 30 달 — 대수 2만에 건수 200 언저리(달마다 ±10% 흔들림), 스무째 달에 320 으로 튄다.
    counts = [round(200 * (1 + 0.1 * np.sin(i * 1.7))) for i in range(30)]
    counts[20] = 320
    _plant(
        monthly["slug"],
        [
            {"period": _month(first, i), "dims": dims, "count": count}
            for i, count in enumerate(counts)
        ],
        datetime(2028, 8, 15, tzinfo=UTC),
    )
    _plant(
        sales["slug"],
        [
            {
                "period": _month(first, i),
                "dims": dims,
                "count": 1,
                "value_count": 1,
                "sum": 20000.0,
            }
            for i in range(30)
        ],
        datetime(2028, 8, 15, tzinfo=UTC),
    )
    found = _analysis(client, admin, monthly["slug"], "control")
    assert found["axis"] == "period" and found["kind"] == "u" and found["per"] == 1000
    (chart,) = found["charts"]
    assert chart["subgroups"] == 30 and chart["sigma_z_raw"] > 1.0
    flagged = {one["label"]: one["signals"] for one in chart["points"] if one["signals"]}
    assert 1 in flagged["2027-09"]
    assert chart["center"] == pytest.approx(sum(counts) / (20000 * 30) * 1000)
    # 앞 18 달로 한계를 잡는다.
    early = _analysis(client, admin, monthly["slug"], "control", baseline_to="2027-07-01")
    (early_chart,) = early["charts"]
    assert early_chart["baseline_points"] == 18
    assert early_chart["center"] == pytest.approx(sum(counts[:18]) / (20000 * 18) * 1000)
    # 압축 — 끝 12 점과 신호만.
    compact = _analysis(client, admin, monthly["slug"], "control", compact="true")
    labels = [one["label"] for one in compact["charts"][0]["points"]]
    assert "2027-09" in labels and len(labels) <= 13


# --- ⑩ 계절 · 변화점 ----------------------------------------------------------------


def test_변화점은_계절을_빼고_수준이_바뀐_달을_찾는다(
    client: TestClient, admin: Signed
) -> None:
    """4 년 — 대수 2만, 계절(±20%)을 타는 인입이 2028-01 부터 30% 오른다(잡음 없는 셀)."""
    w = _world(client, admin)
    sales, monthly = _monthly(client, admin, w)
    season = [1 + 0.2 * np.sin(2 * np.pi * i / 12) for i in range(12)]
    counts = [round(200 * season[i % 12] * (1.3 if i >= 24 else 1.0)) for i in range(48)]
    _plant_months(w, sales, monthly, counts, 20000.0, datetime(2030, 2, 15, tzinfo=UTC))
    found = _analysis(client, admin, monthly["slug"], "changes")
    assert found["recipe"] == "changes" and found["kind"] == "rate" and found["per"] == 1000
    assert found["season_length"] == 12 and len(found["seasonal"]) == 12
    assert found["seasonal"][3]["label"] == "4월"
    assert found["seasonal"][3]["index"] == pytest.approx(
        season[3] / np.exp(np.mean(np.log(season))), rel=0.02
    )
    (change,) = found["changes"]
    assert change["label"] == "2028-01" and change["provisional"] is False
    assert change["ratio"] == pytest.approx(1.3, rel=0.02)
    assert change["ratio_ci"][0] < 1.3 < change["ratio_ci"][1]
    assert [one["points"] for one in found["segments"]] == [24, 24]
    assert len(found["points"]) == 48 and all(one["closed"] for one in found["points"])
    point = found["points"][30]
    assert point["level"] == pytest.approx(change["after"])
    assert point["adjusted"] == pytest.approx(point["rate"] / point["seasonal"])
    # 끝의 넉 달만 오른 줄 — 변화점은 잡되 잠정이라고 말한다.
    late = [round(200 * season[i % 12] * (1.5 if i >= 44 else 1.0)) for i in range(48)]
    _plant_months(w, sales, monthly, late, 20000.0, datetime(2030, 2, 15, tzinfo=UTC))
    provisional = _analysis(client, admin, monthly["slug"], "changes", compact="true")
    (last,) = provisional["changes"]
    assert last["label"] == "2029-09" and last["provisional"] is True
    assert "provisional_change" in {one["code"] for one in provisional["caveats"]}
    assert len(provisional["points"]) == 24
    # 코호트 칸이 없는 지표에 코호트 축을 물으면 거절한다.
    refused = _refused(client, admin, monthly["slug"], "changes", axis="cohort")
    assert refused["code"].endswith("METRICS-0026")


def test_변화점의_건_보기는_그_기간의_기록이다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    found = _analysis(client, admin, cases["slug"], "changes", axis="period")
    assert found["kind"] == "count"
    codes = {one["code"] for one in found["caveats"]}
    assert "count_basis" in codes
    assert found["excluded"]["open"] == 1
    assert found["points"][-1]["closed"] is False
    for point in found["points"]:
        assert _listed(client, admin, w["case"], point["drill"]["params"]) == point["count"]


# --- ④ 순차 검정 --------------------------------------------------------------------


def test_순차_검정은_전작의_경과별_비율로_기대_건수를_낸다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    found = _analysis(
        client, admin, cases["slug"], "sprt", target=w["s_base"], reference=w["a_base"]
    )
    assert found["dim"] == "base_model"
    assert (found["target_label"], found["reference_label"]) == ("S기본", "A기본")
    # 전작 A: 1월 코호트 200대에 경과 2 한 건 — 경과 2 의 비율 0.005, 나머지 0.
    rates = {one["age"]: one["rate"] for one in found["reference_rates"]}
    assert rates[2] == pytest.approx(0.005) and rates[0] == 0 and rates[1] == 0
    # 새 모델 S: 1월 100대 · 2월 50대 → 기대 0.5 + 0.25, 관측 세 건.
    assert found["observed"] == 3 and found["expected"] == pytest.approx(0.75)
    assert found["decision"] == "continue" and found["decided_at"] is None
    assert found["to_not_worse"] is not None and found["to_worse"] is not None
    assert {"small_expected", "one_sided"} <= {one["code"] for one in found["caveats"]}
    assert found["excluded"]["missing_denominator"] == 1  # 오늘 판 건
    assert found["excluded"]["reference_missing_denominator"] == 1  # A 의 2월
    rows = {one["label"]: one for one in found["cohort_rows"]}
    assert (rows["2026-01"]["observed"], rows["2026-02"]["observed"]) == (2, 1)
    for row in rows.values():
        assert row["drill"]["params"]["f.ref.model.base.eq"] == w["s_base"]
        assert _listed(client, admin, w["case"], row["drill"]["params"]) == row["observed"]

    # 전작을 새 모델 객체의 칸으로 찾는다.
    from tests.api.test_metrics import _prop

    _prop(
        client,
        admin,
        w["base"],
        "predecessor",
        "전작",
        data_type="object_ref",
        ref_type_slug=w["base"],
    )
    patched = client.patch(
        f"/api/objects/{w['base']}/{w['s_base']}",
        json={"properties": {"predecessor": w["a_base"]}},
        headers=admin.headers,
    )
    assert patched.status_code == 200, patched.text
    via = _analysis(
        client, admin, cases["slug"], "sprt", target=w["s_base"], reference_via="predecessor"
    )
    assert via["reference"] == w["a_base"] and via["expected"] == pytest.approx(0.75)
    # 거절 — 전작 없음 · 같은 모델 · 모델 거르기를 함께 줌 · 칸이 빈 객체.
    assert _refused(client, admin, cases["slug"], "sprt", target=w["s_base"])["code"].endswith(
        "METRICS-0029"
    )
    assert _refused(
        client, admin, cases["slug"], "sprt", target=w["s_base"], reference=w["s_base"]
    )["code"].endswith("METRICS-0029")
    assert _refused(
        client,
        admin,
        cases["slug"],
        "sprt",
        target=w["s_base"],
        reference=w["a_base"],
        **{"d.base_model": w["s_base"]},
    )["code"].endswith("METRICS-0029")
    assert _refused(
        client, admin, cases["slug"], "sprt", target=w["a_base"], reference_via="predecessor"
    )["code"].endswith("METRICS-0029")


def test_순차_검정은_두_배면_나쁨_같으면_나쁘지_않음으로_선다(
    client: TestClient, admin: Signed
) -> None:
    """전작 A — 2024~2025 코호트 1,000대씩, 경과마다 2건(비율 0.002). 새 모델 S — 2026~2027
    코호트 1,000대씩. 기간 k 의 기대는 2(k+1) 씩 쌓인다."""
    w = _world(client, admin)
    sales, cases = _two(client, admin, w)
    dims_a = {"base_model": w["a_base"], "symptom": "소음", "factory": "F1"}
    dims_s = {"base_model": w["s_base"], "symptom": "소음", "factory": "F1"}
    watermark = datetime(2028, 2, 15, tzinfo=UTC)  # 닫힘 30일 — 2027-12 까지 닫혔다

    def plant(per_cell: int) -> None:
        cells: list[dict[str, Any]] = []
        for i in range(24):
            start = _month(date(2024, 1, 1), i)
            for age in range(min(36, 48 - i)):
                cells.append(
                    {
                        "period": _month(start, age),
                        "cohort": start,
                        "age": age,
                        "dims": dims_a,
                        "count": 2,
                    }
                )
            target = _month(date(2026, 1, 1), i)
            for age in range(24 - i):
                cells.append(
                    {
                        "period": _month(target, age),
                        "cohort": target,
                        "age": age,
                        "dims": dims_s,
                        "count": per_cell,
                    }
                )
        _plant(cases["slug"], cells, watermark)

    _plant(
        sales["slug"],
        [
            {
                "period": _month(date(2024, 1, 1), i),
                "dims": {"base_model": base},
                "count": 1,
                "value_count": 1,
                "sum": 1000.0,
            }
            for base, offset in ((w["a_base"], 0), (w["s_base"], 24))
            for i in range(offset, offset + 24)
        ],
        watermark,
    )
    plant(4)
    worse = _analysis(
        client, admin, cases["slug"], "sprt", target=w["s_base"], reference=w["a_base"]
    )
    # Λ = 2E·ln 1.5 - 0.5E — E 가 9.3 을 넘는 셋째 기간(E = 12)에 「나쁨」.
    assert worse["decision"] == "worse" and worse["decided_at"] == "2026-03"
    assert worse["smr"] == pytest.approx(2.0) and worse["smr_low"] > 1.5
    looks = {one["label"]: one for one in worse["looks"]}
    assert looks["2026-03"]["expected"] == pytest.approx(12.0)
    assert looks["2026-03"]["after_decision"] is False and looks["2026-04"]["after_decision"]
    assert worse["to_worse"] is None
    compact = _analysis(
        client,
        admin,
        cases["slug"],
        "sprt",
        target=w["s_base"],
        reference=w["a_base"],
        compact="true",
    )
    assert "2026-03" in {one["label"] for one in compact["looks"]}
    assert len(compact["looks"]) <= 13 and compact["reference_rates"] == []

    plant(2)
    same = _analysis(
        client, admin, cases["slug"], "sprt", target=w["s_base"], reference=w["a_base"]
    )
    # Λ = -0.0945E — E 가 23.8 을 넘는 다섯째 기간(E = 30)에 「나쁘지 않음」.
    assert same["decision"] == "not_worse" and same["decided_at"] == "2026-05"
    assert same["smr"] == pytest.approx(1.0)

    # 재방문으로 기록이 대수보다 많아도(코호트마다 2,400건 > 1,000대) 대수당 건수로 견준다 —
    # 수명과 달리 그 코호트를 빼지 않는다.
    plant(100)
    revisits = _analysis(
        client, admin, cases["slug"], "sprt", target=w["s_base"], reference=w["a_base"]
    )
    assert revisits["decision"] == "worse" and revisits["decided_at"] == "2026-01"
    assert "inconsistent_denominator" not in revisits["excluded"]


def test_순차_검정이_안_되는_지표는_이유를_말한다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    _, monthly = _monthly(client, admin, w)
    reason = next(one for one in monthly["analyses"] if one["recipe"] == "sprt")
    assert reason["ok"] is False and "코호트" in reason["reason"]
    refused = _refused(client, admin, monthly["slug"], "sprt", target=w["s_base"])
    assert refused["code"].endswith("METRICS-0028")


# --- ⑨ 연관 · 묶음 · 파레토 두 기간 비교 ------------------------------------------------


def _symptom_parts(client: TestClient, admin: Signed, w: dict[str, Any]) -> dict[str, Any]:
    return _define(
        client,
        admin,
        source=w["case"],
        spec={
            "measure": "count",
            "time": {"address": "properties.received", "grain": "month"},
            "dimensions": [
                {"name": "symptom", "address": "properties.symptom"},
                {"name": "part", "address": "properties.parts"},
            ],
        },
        label="증상 x 부품",
    )


def test_연관은_심은_덩어리의_짝과_묶음을_찾는다(client: TestClient, admin: Signed) -> None:
    """증상 넷 x 부품 넷 — 증상 a · b 는 부품 1 · 2 와, c · d 는 3 · 4 와 자주 함께 나온다."""
    w = _world(client, admin)
    metric = _symptom_parts(client, admin, w)
    strong = {
        ("a", "1"),
        ("a", "2"),
        ("b", "1"),
        ("b", "2"),
        ("c", "3"),
        ("c", "4"),
        ("d", "3"),
        ("d", "4"),
    }
    cells: list[dict[str, Any]] = [
        {
            "period": date(2026, 1, 1),
            "dims": {"symptom": f"S-{row}", "part": f"P-{col}"},
            "count": 40 if (row, col) in strong else 4,
        }
        for row in "abcd"
        for col in "1234"
    ]
    cells.append(
        {"period": date(2026, 1, 1), "dims": {"symptom": "S-a", "part": None}, "count": 9}
    )
    _plant(metric["slug"], cells, datetime(2026, 9, 1, tzinfo=UTC))
    found = _analysis(client, admin, metric["slug"], "assoc", rows="symptom", cols="part")
    assert found["basis"] == "occurrences" and found["total"] == 8 * 40 + 8 * 4
    assert found["excluded"] == {"no_value": 9}
    pairs = {(one["row"]["key"], one["col"]["key"]) for one in found["pairs"]}
    assert pairs and all((row[-1] in "ab") == (col[-1] in "12") for row, col in pairs)
    first = found["pairs"][0]
    assert first["lift"] == pytest.approx(40 / (88 * 88 / 352))
    assert first["q_value"] < 0.05 and first["drill"]["params"]
    groups = [
        sorted(one["key"] for one in cluster["members"]) for cluster in found["clusters"]
    ]
    assert sorted(groups) == [["S-a", "S-b"], ["S-c", "S-d"]]
    assert found["silhouette"] > 0.5
    assert found["map_explained"] > 0.5 and len(found["map_rows"]) == 4
    # 원인분산도 — 증상마다 부품 넷 중 둘에 몰렸다(40 · 40 · 4 · 4): 유효 원인 수 2.4.
    spread = {one["row"]["key"]: one for one in found["dispersion"]}
    assert set(spread) == {"S-a", "S-b", "S-c", "S-d"}
    assert spread["S-a"]["effective"] == pytest.approx(
        1 / (2 * (40 / 88) ** 2 + 2 * (4 / 88) ** 2)
    )
    assert spread["S-a"]["top"]["key"] in {"P-1", "P-2"} and spread["S-a"]["causes"] == 4
    assert found["overall_effective"] == pytest.approx(4.0)  # 전체로는 넷에 고르다
    codes = {one["code"] for one in found["caveats"]}
    assert {"overlap_basis", "association", "multiple_testing", "dispersion"} <= codes
    compact = _analysis(
        client, admin, metric["slug"], "assoc", rows="symptom", cols="part", compact="true"
    )
    assert compact["map_rows"] == [] and len(compact["pairs"]) <= 15


def test_연관은_기준_둘을_고르고_건_보기는_그_짝이다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    metric = _symptom_parts(client, admin, w)
    found = _analysis(
        client, admin, metric["slug"], "assoc", rows="symptom", cols="part", min_count=1
    )
    # 부품이 빈 기록 셋(건3 · 건6 · 건8)은 뺀다, 짝 다섯을 검정했다(유의한 것은 없다).
    assert found["excluded"] == {"no_value": 3} and found["tested"] == 5
    assert found["pairs"] == []
    same = _refused(client, admin, metric["slug"], "assoc", rows="symptom", cols="symptom")
    assert same["code"].endswith("METRICS-0034")
    filtered = _refused(
        client,
        admin,
        metric["slug"],
        "assoc",
        rows="symptom",
        cols="part",
        **{"d.part": w["p1"]},
    )
    assert filtered["code"].endswith("METRICS-0034")
    _, cases = _two(client, admin, w)
    one_dim = _define(
        client,
        admin,
        source=w["case"],
        spec={
            "measure": "count",
            "dimensions": [{"name": "symptom", "address": "properties.symptom"}],
        },
        label="증상만",
    )
    reason = next(one for one in one_dim["analyses"] if one["recipe"] == "assoc")
    assert reason["ok"] is False and "둘 이상" in reason["reason"]
    assert next(one for one in cases["analyses"] if one["recipe"] == "assoc")["ok"] is True


def test_파레토는_두_기간의_몫을_견준다(client: TestClient, admin: Signed) -> None:
    """앞 기간(1월) 소음 500 · 발열 300 · 누수 200, 뒤 기간(3월) 소음 500 · 발열 200 ·
    누수 300."""
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    dims = {"base_model": w["s_base"], "factory": "F1"}
    cells = [
        {"period": period, "dims": {**dims, "symptom": symptom}, "count": count}
        for period, counts in (
            (date(2026, 1, 1), {"소음": 500, "발열": 300, "누수": 200}),
            (date(2026, 3, 1), {"소음": 500, "발열": 200, "누수": 300}),
        )
        for symptom, count in counts.items()
    ]
    _plant(cases["slug"], cells, datetime(2026, 9, 1, tzinfo=UTC))
    found = _analysis(
        client,
        admin,
        cases["slug"],
        "pareto",
        dim="symptom",
        period_from="2026-01-01",
        period_to="2026-02-01",
        compare_from="2026-03-01",
        compare_to="2026-04-01",
    )
    comparison = found["comparison"]
    assert comparison["label_a"] == "2026-01-01 ~ 2026-02-01"
    assert comparison["total_a"] == comparison["total_b"] == 1000
    assert comparison["df"] == 2 and comparison["p_value"] < 1e-6
    items = {one["key"]: one for one in comparison["items"]}
    assert items["누수"]["residual"] > 3 and items["누수"]["notable"] is True
    assert items["발열"]["residual"] < -3 and items["소음"]["notable"] is False
    assert items["누수"]["share_a"] == pytest.approx(0.2)
    assert items["누수"]["share_b"] == pytest.approx(0.3)
    assert found["params"]["compare_from"] == "2026-03-01"
    # 비교를 안 주면 없다.
    plain = _analysis(client, admin, cases["slug"], "pareto", dim="symptom")
    assert plain["comparison"] is None


# --- ④ · ⑩ 값마다 훑기 --------------------------------------------------------------------


def _symptom_months(
    client: TestClient, admin: Signed, w: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(판매 대수, 증상별 월 인입률) — 분모는 기본 모델로만 짝짓는다(증상은 대수를 안
    나눈다)."""
    sales, _ = _two(client, admin, w)
    monthly = _define(
        client,
        admin,
        source=w["case"],
        spec={
            "measure": "count",
            "time": {"address": "properties.received", "grain": "month"},
            "dimensions": [
                {"name": "base_model", "address": "ref.model.base"},
                {"name": "symptom", "address": "properties.symptom"},
            ],
            "denominator": {
                "metric": sales["slug"],
                "on": ["base_model"],
                "time": "period",
                "per": 1000,
            },
            "settle_days": 30,
        },
        label="증상별 월 인입률",
    )
    return sales, monthly


def test_변화점을_증상마다_훑으면_늘어난_증상이_먼저_선다(
    client: TestClient, admin: Signed
) -> None:
    """4 년 · 대수 2만 — 발열은 2028-07 부터 1.4배, 누수는 0.7배, 소음은 그대로(잡음 없는
    셀)."""
    w = _world(client, admin)
    sales, monthly = _symptom_months(client, admin, w)
    first, watermark = date(2026, 1, 1), datetime(2030, 2, 15, tzinfo=UTC)
    levels = {"소음": (200, 200), "발열": (150, 210), "누수": (100, 70)}
    _plant(
        monthly["slug"],
        [
            {
                "period": _month(first, i),
                "dims": {"base_model": w["s_base"], "symptom": symptom},
                "count": after if i >= 30 else before,
            }
            for i in range(48)
            for symptom, (before, after) in levels.items()
        ],
        watermark,
    )
    _plant(
        sales["slug"],
        [
            {
                "period": _month(first, i),
                "dims": {"base_model": w["s_base"]},
                "count": 1,
                "value_count": 1,
                "sum": 20000.0,
            }
            for i in range(48)
        ],
        watermark,
    )
    found = _analysis(client, admin, monthly["slug"], "changes/scan", by="symptom")
    assert found["scanned"] == 3 and found["extra_penalty"] == pytest.approx(2 * np.log(3))
    assert [one["key"] for one in found["items"]] == ["발열", "누수", "소음"]
    up, down, flat = found["items"]
    assert up["direction"] == "up" and up["last"]["label"] == "2028-07"
    assert up["last"]["ratio"] == pytest.approx(1.4, rel=0.02)
    assert up["level_now"] == pytest.approx(210 / 20000 * 1000, rel=0.02)
    assert down["direction"] == "down" and flat["direction"] == "flat"
    assert "발열" in up["drill"]["params"].values()
    assert len(found["periods"]) == 48 and found["kind"] == "rate"
    codes = {one["code"] for one in found["caveats"]}
    assert {"scan_penalty", "shared_exposure"} <= codes
    refused = _refused(
        client, admin, monthly["slug"], "changes/scan", by="symptom", **{"d.symptom": "소음"}
    )
    assert refused["code"].endswith("METRICS-0027")


def test_순차_검정을_증상마다_훑으면_나빠진_증상과_새로_생긴_증상이_먼저_선다(
    client: TestClient, admin: Signed
) -> None:
    """전작 A · 새 모델 S 1,000대씩 — 소음은 둘 다 경과마다 2건, 발열은 2 → 6건(세 배), 누수는
    전작에 없고 S 에 3건. 전작에 없던 증상도 0 건 코호트가 기대 0 을 내서 견준다."""
    w = _world(client, admin)
    sales, cases = _two(client, admin, w)
    watermark = datetime(2028, 2, 15, tzinfo=UTC)
    per = {"소음": (2, 2), "발열": (2, 6), "누수": (0, 3)}
    cells: list[dict[str, Any]] = []
    for i in range(24):
        a_start, s_start = _month(date(2024, 1, 1), i), _month(date(2026, 1, 1), i)
        for symptom, (a_count, s_count) in per.items():
            a_dims = {"base_model": w["a_base"], "symptom": symptom, "factory": "F1"}
            s_dims = {"base_model": w["s_base"], "symptom": symptom, "factory": "F1"}
            if a_count:
                cells += [
                    {
                        "period": _month(a_start, age),
                        "cohort": a_start,
                        "age": age,
                        "dims": a_dims,
                        "count": a_count,
                    }
                    for age in range(min(36, 48 - i))
                ]
            cells += [
                {
                    "period": _month(s_start, age),
                    "cohort": s_start,
                    "age": age,
                    "dims": s_dims,
                    "count": s_count,
                }
                for age in range(24 - i)
            ]
    _plant(cases["slug"], cells, watermark)
    _plant(
        sales["slug"],
        [
            {
                "period": _month(date(2024, 1, 1), i),
                "dims": {"base_model": base},
                "count": 1,
                "value_count": 1,
                "sum": 1000.0,
            }
            for base, offset in ((w["a_base"], 0), (w["s_base"], 24))
            for i in range(offset, offset + 24)
        ],
        watermark,
    )
    found = _analysis(
        client,
        admin,
        cases["slug"],
        "sprt/scan",
        by="symptom",
        target=w["s_base"],
        reference=w["a_base"],
    )
    assert found["scanned"] == 3 and found["alpha_each"] == pytest.approx(0.05 / 3)
    assert found["skipped"] == []
    items = {one["key"]: one for one in found["items"]}
    assert items["발열"]["decision"] == "worse" and items["발열"]["smr"] == pytest.approx(3.0)
    assert items["누수"]["decision"] == "worse" and items["누수"]["new"] is True
    assert items["누수"]["smr"] is None and items["누수"]["expected"] == 0
    assert items["소음"]["decision"] != "worse" and items["소음"]["smr"] == pytest.approx(1.0)
    assert found["items"][-1]["key"] == "소음"  # 「나쁨」 이 먼저
    codes = {one["code"] for one in found["caveats"]}
    assert {"multiple_testing", "shared_exposure", "new_values"} <= codes
    # 모델 기준으로는 훑지 않는다.
    refused = _refused(
        client,
        admin,
        cases["slug"],
        "sprt/scan",
        by="base_model",
        target=w["s_base"],
        reference=w["a_base"],
    )
    assert refused["code"].endswith("METRICS-0029")


# --- ⑤ 집단 비교 ------------------------------------------------------------------------


def test_집단_비교는_뜨거운_집단을_잡고_작은_집단의_우연을_줄인다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기본 모델 다섯 · 1 년 — B1 · B2 는 달마다 2만 대에 100건, B3 은 200건(두 배). B4 는 한
    달 150 대에 3 건뿐 — 그대로면 2%(전체의 세 배)라 가장 나빠 보인다. B5 는 달마다 2천 대에
    기록이 하나도 없다(가장 좋은 집단 — 기록이 없어도 대수가 있으면 든다)."""
    w = _world(client, admin)
    sales, monthly = _monthly(client, admin, w)
    first, watermark = date(2026, 1, 1), datetime(2027, 3, 15, tzinfo=UTC)
    plan = {
        "B1": (100, 20000.0),
        "B2": (100, 20000.0),
        "B3": (200, 20000.0),
        "B4": (3, 150.0),
        "B5": (0, 2000.0),
    }
    months = {"B1": 12, "B2": 12, "B3": 12, "B4": 1, "B5": 12}
    _plant(
        monthly["slug"],
        [
            {"period": _month(first, i), "dims": {"base_model": key}, "count": count}
            for key, (count, _) in plan.items()
            for i in range(months[key])
            if count
        ]
        # B4 의 판매가 없는 달의 기록(대수 없음 — 뺀다), B1 의 아직 열린 달(뺀다).
        + [
            {"period": _month(first, 5), "dims": {"base_model": "B4"}, "count": 7},
            {"period": date(2027, 3, 1), "dims": {"base_model": "B1"}, "count": 11},
        ],
        watermark,
    )
    _plant(
        sales["slug"],
        [
            {
                "period": _month(first, i),
                "dims": {"base_model": key},
                "count": 1,
                "value_count": 1,
                "sum": units,
            }
            for key, (_, units) in plan.items()
            for i in range(months[key])
        ]
        + [
            {
                "period": date(2027, 3, 1),
                "dims": {"base_model": "B1"},
                "count": 1,
                "value_count": 1,
                "sum": 20000.0,
            }
        ],
        watermark,
    )
    found = _analysis(client, admin, monthly["slug"], "groups", dim="base_model")
    assert found["recipe"] == "groups" and found["groups"] == 5 and found["per"] == 1000
    assert found["excluded"] == {"missing_denominator": 7, "open": 11}
    assert found["other_groups"] == 0
    assert found["heterogeneity_p"] < 1e-6 and found["spread"] > 0
    rows = {one["key"]: one for one in found["rows"]}
    assert found["rows"][0]["key"] == "B3" and rows["B3"]["flag"] == "high"
    assert rows["B3"]["count"] == 2400 and rows["B3"]["exposure"] == 240000
    small = rows["B4"]
    assert small["rate"] == pytest.approx(3 / 150 * 1000)
    assert small["rate"] > rows["B3"]["rate"]  # 그대로면 가장 나빠 보이지만
    assert (
        small["flag"] is None and small["shrinkage"] > 0.5 and small["shrunk"] < small["rate"]
    )
    assert rows["B1"]["count"] == 1200 and rows["B1"]["exposure"] == 240000
    none = rows["B5"]
    assert none["count"] == 0 and none["exposure"] == 24000 and none["flag"] == "low"
    assert found["rows"][-1]["key"] == "B5"
    assert found["flagged"] >= 2 and rows["B3"]["drill"]["params"]
    # 건 보기는 닫힌 범위로 좁힌다 — 열린 달의 기록은 근거에 없다.
    assert rows["B1"]["drill"]["params"]["f.received.lt"] == "2027-02-01"
    codes = {one["code"] for one in found["caveats"]}
    assert {"multiple_testing", "small_groups", "association", "open_excluded"} <= codes
    assert "missing_denominator" in codes
    # 자리가 모자라면 「다름」 을 전체와 먼 것부터(B5 · B3 이 B1 · B2 보다 멀다) — 계산에는
    # 모두 든다.
    monkeypatch.setattr(groups_recipe, "MAX_ROWS", 2)
    cut = _analysis(client, admin, monthly["slug"], "groups", dim="base_model")
    assert cut["groups"] == 5 and cut["other_groups"] == 3 and cut["flagged"] == 4
    assert [one["key"] for one in cut["rows"]] == ["B3", "B5"]
    assert cut["heterogeneity_p"] == found["heterogeneity_p"]
    assert "rows_shown" in {one["code"] for one in cut["caveats"]}
    # 집단마다 대수를 모르는 기준으로는 견주지 않는다.
    _, symptom_months = _symptom_months(client, admin, w)
    refused = _refused(client, admin, symptom_months["slug"], "groups", dim="symptom")
    assert refused["code"].endswith("METRICS-0035")
    listed = next(one for one in monthly["analyses"] if one["recipe"] == "groups")
    assert listed["ok"] is True and listed["label"] == "집단 비교"


# --- 전후 비교 ----------------------------------------------------------------------


def test_전후_비교는_적용일_앞뒤의_닫힌_부분군을_견주고_낀_달은_뺀다(
    client: TestClient, admin: Signed
) -> None:
    """S 기본 모델 · 2026 년 — 상반기 달마다 2만 대에 100건, 하반기 60건(4할 줄음). 계산은
    2027-03-15 라 2027-01 까지 닫혔고, 2027-02 는 열려 있다."""
    w = _world(client, admin)
    sales, monthly = _monthly(client, admin, w)
    watermark = datetime(2027, 3, 15, tzinfo=UTC)
    _plant_months(w, sales, monthly, [100] * 6 + [60] * 6 + [60, 60], 20000.0, watermark)
    model = {"d.base_model": w["s_base"]}

    found = _analysis(client, admin, monthly["slug"], "cutin", at="2026-07-01", **model)
    assert found["recipe"] == "cutin" and found["decision"] == "reduced"
    assert found["before"]["subgroups"] == 6 and found["before"]["count"] == 600
    assert found["after"]["subgroups"] == 7 and found["open_after"] == 1
    assert found["ratio"] == pytest.approx(0.6)
    assert found["ratio_high"] < 1 and found["before"]["rate"] == pytest.approx(5.0)
    assert found["before"]["first"] == "2026-01" and found["before"]["last"] == "2026-06"
    # 근거 — 앞의 범위(접수월 1~6월)와 그 모델.
    drill = found["before"]["drill"]["params"]
    assert drill["f.received.gte"] == "2026-01-01" and drill["f.received.lt"] == "2026-07-01"
    sides = [one["side"] for one in found["points"]]
    assert sides[:6] == ["before"] * 6 and sides[6:] == ["after"] * 8
    codes = {one["code"] for one in found["caveats"]}
    assert "association" in codes and "open_after" in codes and "unfiltered" not in codes

    # 적용일이 달 중간이면 그 달은 섞여 있어 뺀다. 처음 두 달은 출시 초기로 뺄 수 있다.
    mid = _analysis(
        client, admin, monthly["slug"], "cutin", at="2026-07-15", skip_first=2, **model
    )
    sides = [one["side"] for one in mid["points"]]
    assert sides[:2] == ["skipped"] * 2 and sides[6] == "boundary"
    assert mid["before"]["subgroups"] == 4 and mid["after"]["subgroups"] == 6
    assert "boundary" in {one["code"] for one in mid["caveats"]}

    # 적용일을 늦게 잡으면(11월) 앞쪽이 이미 내려가 있었다 — 「줄었다」 여도 그렇다고 말한다.
    # 거르지 않으면 그것도 말한다.
    late = _analysis(client, admin, monthly["slug"], "cutin", at="2026-11-01")
    assert late["pre_trend"]["change_per_period"] < 0 and late["pre_trend"]["p_value"] < 0.05
    codes = {one["code"] for one in late["caveats"]}
    assert "pre_trend" in codes and "unfiltered" in codes

    # 적용일 뒤가 아직 하나도 안 닫혔으면 「아직 이르다」.
    early = _analysis(client, admin, monthly["slug"], "cutin", at="2027-02-01", **model)
    assert early["decision"] == "too_early" and early["after"]["subgroups"] == 0
    assert "no_after" in {one["code"] for one in early["caveats"]}

    bad = client.get(
        f"/api/metrics/{monthly['slug']}/analysis/cutin",
        params={"at": "2026-07-01", "effect": 1.5},
        headers=admin.headers,
    )
    assert bad.status_code == 422
    listed = next(one for one in monthly["analyses"] if one["recipe"] == "cutin")
    assert listed["ok"] is True and listed["label"] == "전후 비교"


# --- 클레임 예측 ---------------------------------------------------------------------


def test_클레임_예측은_이미_판_코호트의_앞으로를_수명_맞춤으로_내고_되짚어_본다(
    client: TestClient, admin: Signed
) -> None:
    """판매 30개월(2026-01 ~ 2028-06) x 2만 대, 모두 고장 나는 와이블(형상 1.5 · 척도 13개월).
    계산 2028-08-15 · 닫힘 30일이라 2028-06 까지 닫혔다 — 예측은 2028-07 부터."""
    w = _world(client, admin)
    sales, cases = _two(client, admin, w)
    dims = {"base_model": w["s_base"], "symptom": "소음", "factory": "F1"}
    first, units, beta, eta = date(2026, 1, 1), 20000.0, 1.5, 13.0
    steps = _weibull_steps(40, beta, eta, 1.0, units)
    planted: list[dict[str, Any]] = []
    for i in range(30):
        start = _month(first, i)
        for age in range(30 - i):
            if steps[age]:
                planted.append(
                    {
                        "period": _month(start, age),
                        "cohort": start,
                        "age": age,
                        "dims": dims,
                        "count": steps[age],
                    }
                )
    watermark = datetime(2028, 8, 15, tzinfo=UTC)
    _plant(cases["slug"], planted, watermark)
    _plant(
        sales["slug"],
        [
            {
                "period": _month(first, i),
                "dims": {"base_model": w["s_base"]},
                "count": 1,
                "value_count": 1,
                "sum": units,
            }
            for i in range(30)
        ],
        watermark,
    )
    found = _analysis(
        client, admin, cases["slug"], "forecast", horizon=6, warranty=24, cost=50000
    )
    assert found["recipe"] == "forecast" and found["model"] == "weibull"
    assert found["start"] == "2028-07" and len(found["points"]) == 6
    assert found["beta"] == pytest.approx(beta, rel=0.03)
    assert found["eta"] == pytest.approx(eta, rel=0.03)
    assert found["units"] == 30 * units and found["cohorts"] == 30
    # 2028-07 의 정답 — 코호트마다 아직 안 본 경과(30 - c), 보증 24개월 안만.
    grid = np.linspace(0.0, 1.0, 2001)
    mass = [
        float(np.trapezoid(-np.expm1(-(((a + grid) / eta) ** beta)), a + grid))
        for a in range(40)
    ]
    pi = [now - before for now, before in zip(mass, [0.0, *mass[:-1]], strict=True)]
    truth = sum(units * pi[30 - c] for c in range(30) if 30 - c < 24)
    july = found["points"][0]
    assert july["expected"] == pytest.approx(truth, rel=0.03)
    assert july["low"] <= july["expected"] <= july["high"]
    assert july["low"] <= july["low80"] <= july["high80"] <= july["high"]
    # 보증 끝까지 남은 총량은 앞 6개월보다 크고, 비용은 건수 x 건당 비용이다.
    assert found["remaining"]["expected"] > found["total"]["expected"]
    assert found["total"]["cost"] == pytest.approx(found["total"]["expected"] * 50000)
    # 앞부분은 실제(닫힌 기간), 되짚어 보기는 6개월 전까지로 맞춰 그 뒤를 맞혔나.
    assert found["history"][-1]["label"] == "2028-06" and found["history"][-1]["actual"] > 0
    back = found["backtest"]
    assert back["start"] == "2028-01" and back["periods"] == 6
    assert back["within"] is True
    assert back["predicted"] == pytest.approx(back["actual"], rel=0.05)
    codes = {one["code"] for one in found["caveats"]}
    assert "already_sold" in codes and "records_not_units" in codes
    # 코호트가 없는 지표는 이유를 말하고 거절한다.
    _, monthly = _monthly(client, admin, w)
    refused = _refused(client, admin, monthly["slug"], "forecast")
    assert refused["code"].endswith("METRICS-0038")
