"""지표의 **기록 끝** — 자료가 언제까지 들어왔나(`stats.data_through` ·
`stats.source_through`)와 그 뒤의 빈 닫힌 기간.

닫힌 기간의 경계는 계산 시각에서 나온다 — 원천이 멎어도 밤마다 앞으로 간다. 그러면 분석은
끊긴 뒤의 기간을 「닫힌 0건」 으로 읽고 가짜 하락을 낸다. 지키는 것:

- 실행마다 건수가 있는 마지막 기간(지표의 기록 끝)과 **원천 타입 전체**의 마지막 기간(원천
  기록 끝)을 남긴다 — 전량 · 증분 둘 다, 계산 시점의 기간보다 뒤로는 안 간다(날짜 칸의 오타),
  지운 기록은 안 센다. 머무는 기간 지표는 셀이 계산 시점까지 펼쳐져 있어 원천에서 잰다.
- 응답 머리가 그것과 「닫힌 기간이 통째로 비었나」 를 말한다 — **원천 기록 끝으로 판정한다**
  (좁은 지표 · 드문 지표의 끝은 원천이 살아 있어도 멈춘다), 한 기간 안의 지연은 넘어가고, 분모
  지표는 따로 본다(`own_gap` · `denominator.data_gap`). 그 칸이 없는 옛 실행은 모르는 것이지 빈
  것이 아니다.
- 분석은 주의(`data_gap`)로 말하고 **수는 바꾸지 않는다** — 끝쪽 0건이 진짜일 때도 있다. 읽은
  범위가 원천 기록 끝 안에서 끝나면 말하지 않는다.
- 원천 하나만 멎으면(다른 원천이 들어와 기록 끝이 새것) 머리의 신선도(`freshness`)와 분석의
  주의(`source_issue`)가 말한다. 꺼 둔 소스는 말하지 않는다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.api.conftest import Signed
from tests.api.test_metric_analysis import _analysis, _month, _monthly, _plant, _plant_months
from tests.api.test_metric_incremental import _recount
from tests.api.test_metrics import _define, _prop, _read, _world
from tests.api.test_ontology import _make_object, _make_type


def _runs(client: TestClient, admin: Signed, slug: str) -> list[dict[str, Any]]:
    got = client.get(f"/api/metrics/{slug}/runs", headers=admin.headers)
    assert got.status_code == 200, got.text
    return list(got.json())


def _stamp(slug: str, *, watermark: datetime | None = None, **stats: str | None) -> None:
    """지금 실행의 워터마크 · 기록 끝을 정답을 아는 값으로 — 오늘 날짜에 기대지 않고 경계를
    본다. 값이 None 이면 그 칸을 뺀다(이 칸이 생기기 전에 센 실행)."""
    from app.database import SessionLocal
    from app.modules.metrics.models import MetricDef, MetricRun

    with SessionLocal() as db:
        metric = db.scalars(select(MetricDef).where(MetricDef.slug == slug)).one()
        run = db.get(MetricRun, metric.current_run_id)
        assert run is not None
        merged = dict(run.stats or {})
        for key, value in stats.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        run.stats = merged
        if watermark is not None:
            run.watermark = watermark
        db.commit()


def _log(client: TestClient, admin: Signed) -> str:
    case = _make_type(client, admin, label="접수", usage="log")
    _prop(client, admin, case, "received", "접수일", data_type="date")
    _prop(client, admin, case, "symptom", "증상", data_type="text")
    return case


def _record(client: TestClient, admin: Signed, case: str, received: str) -> str:
    made = _make_object(
        client,
        admin,
        case,
        label=f"접수 {received}",
        properties={"received": received, "symptom": "소음"},
    )
    return str(made["id"])


def test_실행마다_기록_끝을_남기고_미래_날짜는_자른다(
    client: TestClient, admin: Signed
) -> None:
    case = _log(client, admin)
    for received in ("2026-03-10", "2026-04-02", "2026-05-20"):
        _record(client, admin, case, received)
    spec = {
        "measure": "count",
        "time": {"address": "properties.received", "grain": "month"},
        "dimensions": [{"name": "symptom", "address": "properties.symptom"}],
    }
    metric = _define(client, admin, source=case, spec=spec, label="접수")
    slug = metric["slug"]
    (first,) = _runs(client, admin, slug)
    assert first["mode"] == "full" and first["stats"]["data_through"] == "2026-05-01"
    assert first["stats"]["source_through"] == "2026-05-01"  # 거르기가 없으면 같다

    # 증분 — 뒤 달에 기록이 들어오면 끝이 따라간다.
    august = _record(client, admin, case, "2026-08-05")
    step = _recount(client, admin, slug)
    assert step["mode"] == "incremental", step["note"]
    assert step["stats"]["data_through"] == "2026-08-01"
    assert step["stats"]["source_through"] == "2026-08-01"

    # 날짜 칸의 오타(2062년)는 셀에는 들지만 끝을 미래로 밀지 않는다 — 밀면 멎은 원천이 늘
    # 「들어오는 중」 으로 보인다.
    _record(client, admin, case, "2062-09-01")
    typo = _recount(client, admin, slug)
    assert typo["mode"] == "incremental", typo["note"]
    assert typo["stats"]["data_through"] == "2026-08-01"
    assert typo["stats"]["source_through"] == "2026-08-01"
    table = _read(client, admin, slug, by="period")
    assert "2062-09-01" in {one["period"] for one in table["cells"]}
    assert table["data_through"] == "2026-08-01"
    assert table["source_through"] == "2026-08-01"

    # 전량도 같은 값이고, 끝 달의 기록을 삭제하면 증분이 끝을 되돌린다.
    full = _recount(client, admin, slug, reason="manual")
    assert full["mode"] == "full" and full["stats"] == typo["stats"]
    gone = client.delete(f"/api/objects/{case}/{august}", headers=admin.headers)
    assert gone.status_code == 204, gone.text
    back = _recount(client, admin, slug)
    assert back["mode"] == "incremental", back["note"]
    assert back["stats"]["data_through"] == "2026-05-01"
    # 원천 기록 끝도 지운 기록을 안 센다.
    assert back["stats"]["source_through"] == "2026-05-01"

    # 시간 칸이 없는 지표에는 기록 끝이 없다 — 머리도 null 이고 빈 기간을 말하지 않는다.
    flat = _define(
        client,
        admin,
        source=case,
        spec={"measure": "count", "dimensions": spec["dimensions"]},
        label="접수 합",
    )
    (only,) = _runs(client, admin, flat["slug"])
    assert "data_through" not in only["stats"] and "source_through" not in only["stats"]
    plain = _read(client, admin, flat["slug"], dims="symptom")
    assert plain["data_through"] is None and plain["source_through"] is None
    assert plain["data_gap"] is False and plain["own_gap"] is False


def test_좁은_지표는_원천이_살아_있으면_빈_기간이_아니다(
    client: TestClient, admin: Signed
) -> None:
    """「진동」 만 세는 지표 — 진동은 3월이 마지막인데 소음은 10월까지 들어온다. 지표의 끝으로
    판정하면 원천이 살아 있는데도 「아직 안 들어옴」 이 영구히 선다(끝난 모델 · 대책 뒤 정말
    0건 · 드문 지표도 같다). 원천 타입 전체(거르기 없이)의 끝으로 판정하고, 지표의 끝은
    보여 주기만 한다. 원천 타입이 통째로 멎으면 참이다."""
    case = _log(client, admin)
    for received in ("2026-01-10", "2026-03-02"):
        _make_object(
            client,
            admin,
            case,
            label=f"진동 {received}",
            properties={"received": received, "symptom": "진동"},
        )
    recent = [_record(client, admin, case, day) for day in ("2026-09-03", "2026-10-20")]
    narrow = _define(
        client,
        admin,
        source=case,
        spec={
            "measure": "count",
            "time": {"address": "properties.received", "grain": "month"},
            "dimensions": [{"name": "symptom", "address": "properties.symptom"}],
            "filters": [{"field": "symptom", "op": "eq", "value": "진동"}],
        },
        label="진동 접수",
    )
    slug = narrow["slug"]
    (run,) = _runs(client, admin, slug)
    assert run["stats"]["data_through"] == "2026-03-01"
    assert run["stats"]["source_through"] == "2026-10-01"

    # 11-12 — 지표의 끝(3월)으로는 빈 기간이 일곱 달이지만, 원천은 10월까지 들어왔다.
    _stamp(slug, watermark=_at("2026-11-12"))
    head = _read(client, admin, slug, by="period")
    assert head["data_through"] == "2026-03-01" and head["source_through"] == "2026-10-01"
    assert head["data_gap"] is False and head["own_gap"] is False
    control = _analysis(client, admin, slug, "pareto", dim="symptom")
    assert "data_gap" not in {one["code"] for one in control["caveats"]}

    # 원천 타입이 통째로 멎었다 — 9 · 10월 기록을 삭제하면 원천의 끝도 3월이다.
    for one in recent:
        gone = client.delete(f"/api/objects/{case}/{one}", headers=admin.headers)
        assert gone.status_code == 204, gone.text
    again = _recount(client, admin, slug, reason="manual")
    assert again["stats"]["source_through"] == "2026-03-01"
    _stamp(slug, watermark=_at("2026-11-12"))
    head = _read(client, admin, slug, by="period")
    assert head["data_gap"] is True and head["own_gap"] is True


def test_머무는_기간_지표는_원천에서_기록_끝을_잰다(client: TestClient, admin: Signed) -> None:
    """판매 2026-01 · 02 를 12달 머물게 세면 셀은 계산 시점의 달까지 펼쳐진다 — 셀의 끝으로
    재면 판매가 멎은 것을 모른다. 미래 날짜의 판매는 여기서도 자른다."""
    sales = _make_type(client, admin, label="판매", usage="log")
    _prop(client, admin, sales, "month", "판매월", data_type="date")
    _prop(client, admin, sales, "units", "대수", data_type="number")
    for month, units in (("2026-01-01", 100), ("2026-02-01", 50), ("2062-03-01", 9)):
        _make_object(
            client,
            admin,
            sales,
            label=f"판매 {month}",
            properties={"month": month, "units": units},
        )
    metric = _define(
        client,
        admin,
        source=sales,
        spec={
            "measure": "sum",
            "measure_field": "properties.units",
            "time": {"address": "properties.month", "grain": "month"},
            "stay": {"periods": 12},
        },
        label="보증 중 대수",
    )
    from app.database import SessionLocal
    from app.modules.metrics.models import MetricDef, MetricValue

    with SessionLocal() as db:
        found = db.scalars(select(MetricDef).where(MetricDef.slug == metric["slug"])).one()
        last_cell = db.scalar(
            select(func.max(MetricValue.period)).where(
                MetricValue.metric_id == found.id, MetricValue.run_id == found.current_run_id
            )
        )
    assert last_cell is not None and last_cell > date(2026, 6, 1)
    (run,) = _runs(client, admin, metric["slug"])
    assert run["stats"]["data_through"] == "2026-02-01"
    assert run["stats"]["source_through"] == "2026-02-01"  # 원천에서도 미래 날짜를 자른다


def _pair(client: TestClient, admin: Signed) -> tuple[str, str]:
    """(접수 — 닫힘 10일, 판매 대수 — 닫힘 0일). 분모는 기간끼리 짝."""
    case = _log(client, admin)
    _record(client, admin, case, "2026-09-10")
    sales = _make_type(client, admin, label="판매", usage="log")
    _prop(client, admin, sales, "month", "판매월", data_type="date")
    _prop(client, admin, sales, "units", "대수", data_type="number")
    _make_object(
        client, admin, sales, label="판매", properties={"month": "2026-09-01", "units": 100}
    )
    units = _define(
        client,
        admin,
        source=sales,
        spec={
            "measure": "sum",
            "measure_field": "properties.units",
            "time": {"address": "properties.month", "grain": "month"},
        },
        label="판매 대수",
    )
    rate = _define(
        client,
        admin,
        source=case,
        spec={
            "measure": "count",
            "time": {"address": "properties.received", "grain": "month"},
            "denominator": {"metric": units["slug"], "on": [], "time": "period", "per": 100},
            "settle_days": 10,
        },
        label="인입률",
    )
    return rate["slug"], units["slug"]


def _at(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T12:00:00").replace(tzinfo=UTC)


def test_머리가_기록_끝과_닫힌_기간이_통째로_빈_것을_말한다(
    client: TestClient, admin: Signed
) -> None:
    """월 단위 · 닫힘 10일 — 닫힌 기간의 경계는 워터마크 날짜 - 10일. 빈 구간(기록 끝의 다음
    달부터 경계까지)이 한 달보다 길어야 참이다: 9월까지 들어온 채로 11-11(경계 11-01, 10월이
    막 닫힘)은 아직 들어오는 중일 수 있고, 11-12 부터 빈 기간이다."""
    rate, units = _pair(client, admin)
    # 판정은 원천 기록 끝으로 — 지표의 기록 끝(보여 주는 값)은 그보다 앞서 멈춰 있어도 된다.
    _stamp(
        units,
        watermark=_at("2026-11-11"),
        data_through="2026-03-01",
        source_through="2026-10-01",
    )

    _stamp(
        rate,
        watermark=_at("2026-11-11"),
        data_through="2026-01-01",
        source_through="2026-09-01",
    )
    head = _read(client, admin, rate, by="period")
    assert head["data_through"] == "2026-01-01" and head["source_through"] == "2026-09-01"
    assert head["data_gap"] is False and head["own_gap"] is False
    assert head["denominator"]["data_through"] == "2026-03-01"
    assert head["denominator"]["source_through"] == "2026-10-01"
    assert head["denominator"]["data_gap"] is False
    assert head["denominator"]["grain"] == "month"  # 분모의 기록 끝을 읽는 단위

    _stamp(rate, watermark=_at("2026-11-12"))
    head = _read(client, admin, rate, by="period")
    assert head["data_gap"] is True and head["own_gap"] is True
    assert head["denominator"]["data_gap"] is False
    # 추이 · 표 어느 읽기든 같은 머리다.
    assert _read(client, admin, rate, "series")["data_gap"] is True

    # 10월까지 들어왔으면 12월 10일(경계 11-30)에도 한 기간 안의 지연이다.
    _stamp(rate, watermark=_at("2026-12-10"), source_through="2026-10-01")
    assert _read(client, admin, rate, by="period")["data_gap"] is False

    # 분모만 멎었다 — 판매 대수가 8월까지(닫힘 0일, 경계 11-11)면 머리는 참이고 분모가 말한다.
    _stamp(units, source_through="2026-08-01")
    head = _read(client, admin, rate, by="period")
    assert head["data_gap"] is True and head["own_gap"] is False
    assert head["source_through"] == "2026-10-01"
    assert head["denominator"]["source_through"] == "2026-08-01"
    assert head["denominator"]["data_gap"] is True

    # 둘 다 멎었다 — 화면이 두 문장을 내도록 분자 쪽만의 표시가 따로 선다.
    _stamp(rate, source_through="2026-08-01")
    head = _read(client, admin, rate, by="period")
    assert head["own_gap"] is True and head["denominator"]["data_gap"] is True

    # 지표의 기록 끝만 있고 원천 기록 끝이 없는 실행(이 칸이 생기기 전) — 모르는 것이지 빈 것이
    # 아니다. 지표의 끝이 오래돼도 판정에 안 쓴다.
    _stamp(units, source_through=None)
    _stamp(rate, watermark=_at("2027-06-01"), data_through="2026-01-01", source_through=None)
    head = _read(client, admin, rate, by="period")
    assert head["data_through"] == "2026-01-01" and head["source_through"] is None
    assert head["data_gap"] is False and head["own_gap"] is False
    assert head["denominator"]["source_through"] is None


def _without_gap(found: dict[str, Any]) -> dict[str, Any]:
    """분석 결과에서 기록 끝 · 빈 기간의 말을 뺀 것 — 나머지(수)는 한 줄도 달라지면 안 된다."""
    marks = ("data_through", "source_through", "data_gap", "own_gap")
    out = {k: v for k, v in found.items() if k != "caveats" and k not in marks}
    if out.get("denominator"):
        out["denominator"] = {k: v for k, v in out["denominator"].items() if k not in marks}
    out["caveats"] = [one for one in found["caveats"] if one["code"] != "data_gap"]
    return out


def test_분석은_빈_닫힌_기간을_주의로_말하고_수는_그대로_둔다(
    client: TestClient, admin: Signed
) -> None:
    """`test_부분군의_줄은_대수가_있는_끝쪽의_0건까지_잇는다` 와 같은 셀 — 7월 뒤 0건은 대책의
    효과일 수도, 원천이 멎은 것일 수도 있다. 기록 끝이 7월이면 주의를 달고, 수는 그대로다."""
    w = _world(client, admin)
    sales, monthly = _monthly(client, admin, w)
    first, watermark = date(2026, 1, 1), datetime(2027, 3, 15, tzinfo=UTC)
    dims = {"base_model": w["s_base"]}
    _plant(
        monthly["slug"],
        [
            {"period": _month(first, i), "dims": dims, "count": count}
            for i, count in enumerate([3] * 6 + [1])
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
                "sum": 1000.0,
            }
            for i in range(12)
        ],
        watermark,
    )
    model = {"d.base_model": w["s_base"]}
    asks: list[tuple[str, dict[str, Any]]] = [
        ("cutin", {"at": "2026-07-01", **model}),
        ("changes", model),
        ("control", model),
        ("pareto", {"dim": "base_model"}),
    ]
    # 심은 실행에는 기록 끝이 없다(옛 실행과 같다) — 주의가 없다.
    before = {
        recipe: _analysis(client, admin, monthly["slug"], recipe, **ask)
        for recipe, ask in asks
    }
    for found in before.values():
        assert "data_gap" not in {one["code"] for one in found["caveats"]}
        assert found["data_gap"] is False

    # 분자의 원천이 7월까지(닫힘 30일 — 경계 2027-02-13) · 판매는 2027-02 까지(경계 03-15, 한
    # 기간 안이라 분모는 아니다). 지표의 기록 끝만 오래된 것은 판정에 안 쓴다(앞의 실행).
    _stamp(monthly["slug"], data_through="2026-07-01", source_through="2026-07-01")
    _stamp(sales["slug"], source_through="2027-02-01")
    for recipe, ask in asks:
        found = _analysis(client, admin, monthly["slug"], recipe, **ask)
        assert found["data_gap"] is True and found["data_through"] == "2026-07-01"
        assert found["source_through"] == "2026-07-01" and found["own_gap"] is True
        assert found["denominator"]["data_gap"] is False
        (note,) = [one for one in found["caveats"] if one["code"] == "data_gap"]
        assert note["level"] == "warn" and note["message"] == (
            "2026-08 이후는 미반영 구간입니다(원천 최종 반영 기간 2026-07) — 이 구간의 0건 · "
            "감소는 수집 지연 때문일 수 있으므로, 데이터 소스의 수집 현황을 확인해야 합니다."
        )
        # 경고가 앞에 선다.
        assert found["caveats"][0]["level"] == "warn"
        assert _without_gap(found) == _without_gap(before[recipe]), recipe
    cutin = _analysis(client, admin, monthly["slug"], "cutin", **asks[0][1])
    assert cutin["decision"] == "reduced" and cutin["points"][-1]["count"] == 0

    # 읽은 범위가 원천 기록 끝의 다음 기간(8월) 안에서 끝나면 보여 준 기간에는 빈 기간이
    # 없다 — 주의를 빼고, 머리의 `data_gap`(지표 단위의 사실)은 그대로 둔다. 날짜는 기간
    # 경계로 올린다.
    for to in ("2026-08-01", "2026-07-20"):
        ranged = _analysis(client, admin, monthly["slug"], "control", period_to=to, **model)
        assert ranged["data_gap"] is True
        assert "data_gap" not in {one["code"] for one in ranged["caveats"]}, to
    ranged = _analysis(
        client, admin, monthly["slug"], "control", period_to="2026-09-01", **model
    )
    assert "data_gap" in {one["code"] for one in ranged["caveats"]}

    # 분모만 멎었다 — 그쪽 이름으로 말한다. 기간으로 짝지은 분석(관리도)은 그 뒤 기간의 분모를,
    # 짝짓지 않은 분석(파레토 — 범위의 합으로 나눈다)은 분모 합을 말한다.
    _stamp(monthly["slug"], source_through="2027-02-01")
    _stamp(sales["slug"], source_through="2026-10-01")
    found = _analysis(client, admin, monthly["slug"], "control", **model)
    assert found["data_gap"] is True and found["denominator"]["data_gap"] is True
    assert found["own_gap"] is False
    (note,) = [one for one in found["caveats"] if one["code"] == "data_gap"]
    assert note["message"] == (
        f"분모 「{sales['label']}」 의 2026-11 이후는 미반영 구간입니다(원천 최종 반영 기간 "
        "2026-10) — 이 구간의 비율은 분모가 빠졌거나 덜 반영된 값일 수 있으므로, 데이터 "
        "소스의 수집 현황을 확인해야 합니다."
    )
    assert _without_gap(found) == _without_gap(before["control"])
    pareto = _analysis(client, admin, monthly["slug"], "pareto", dim="base_model")
    assert pareto["denominator"]["time"] is None
    (note,) = [one for one in pareto["caveats"] if one["code"] == "data_gap"]
    assert note["message"] == (
        f"분모 「{sales['label']}」 의 2026-11 이후는 미반영 구간입니다(원천 최종 반영 기간 "
        "2026-10) — 분모 합이 덜 반영되었을 수 있으므로, 데이터 소스의 수집 현황을 확인해야 "
        "합니다."
    )


def test_원천_기록_끝은_날짜_칸_색인을_탄다(client: TestClient, admin: Signed) -> None:
    """원천 기록 끝은 원천 타입 전체의 최댓값이라 2,000만 건이면 색인 없이 25초다(실측). 날짜
    칸 색인(`timeindex`)과 **같은 식**이어야 플래너가 그 색인의 거꾸로 읽기 한 줄로 바꾼다 —
    어긋나면 색인은 서 있는데 아무도 안 쓴다(조용히 느려질 뿐이라 아무도 모른다). 이 지표의
    것 하나만 같은 문장(`create_sql`)으로 세우고 끝나면 지운다."""
    from sqlalchemy import text

    from app.database import SessionLocal, engine
    from app.modules.metrics import compute, timeindex
    from app.modules.metrics.models import MetricDef

    case = _log(client, admin)
    for received in ("2026-03-10", "2026-04-02", "2062-09-01"):
        _record(client, admin, case, received)
    metric = _define(
        client,
        admin,
        source=case,
        spec={
            "measure": "count",
            "time": {"address": "properties.received", "grain": "month"},
        },
        label="색인",
    )
    with SessionLocal() as db:
        found = db.scalars(select(MetricDef).where(MetricDef.slug == metric["slug"])).one()
        source = found.source_type_id
        name = timeindex.name_of(source, "received")
        db.rollback()  # 열린 트랜잭션이 있으면 CONCURRENTLY 가 그것을 기다린다
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("SET lock_timeout = '20s'"))
            connection.execute(text(timeindex.create_sql(name, source, "received")))
        try:
            found = db.scalars(select(MetricDef).where(MetricDef.slug == metric["slug"])).one()
            built = compute.built_of(db, found)
            seen = date(2026, 11, 8)
            sql = compute.source_through_statement(built, seen).compile(
                dialect=engine.dialect, compile_kwargs={"literal_binds": True}
            )
            db.execute(text("SET LOCAL enable_seqscan = off"))
            plan = "\n".join(db.scalars(text(f"EXPLAIN {sql}")))
            # 작은 표라 플래너는 색인을 앞으로 읽어 묶는다 — 큰 표면 같은 색인의 거꾸로 읽기
            # 한 줄(`LIMIT 1`)이다. 여기서 보는 것은 식 · 부분 조건이 맞물리나다.
            assert name in plan, plan
            # 미래 날짜(2062)는 자르고 기간 시작일로 내린다.
            assert compute.source_through(db, built, seen=seen) == date(2026, 4, 1)
        finally:
            db.rollback()
            with engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                connection.execute(text("SET lock_timeout = '20s'"))
                connection.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))


def _source_row(slug: str, *, name: str, **kw: Any) -> None:
    """그 지표의 원천 타입에 데이터 소스 하나를 바로 넣는다 — 상태(실패 · 멎음 · 따라잡는 중)를
    정답을 아는 값으로. 동기화는 안 돌린다."""
    from app.database import SessionLocal
    from app.modules.datasources.models import DataSource
    from app.modules.metrics.models import MetricDef

    with SessionLocal() as db:
        metric = db.scalars(select(MetricDef).where(MetricDef.slug == slug)).one()
        body: dict[str, Any] = {"kind": "odata", "options": {}, "mapping": {}, **kw}
        db.add(
            DataSource(
                slug=f"src_{uuid.uuid4().hex[:8]}",
                name=name,
                entity_set="Rows",
                type_id=metric.source_type_id,
                **body,
            )
        )
        db.commit()


def test_원천_하나만_멎으면_머리의_신선도와_분석의_주의가_말한다(
    client: TestClient, admin: Signed
) -> None:
    """원천이 여럿이면 하나가 멎어도 다른 것이 들어와 원천 기록 끝(최댓값)이 새것이라 빈 기간이
    안 선다. 머리가 원천 타입 · 분모 원천 타입의 신선도를 싣고(꺼 둔 것도), 분석은 실패 중 ·
    정해진 간격보다 오래 안 돎 · 따라잡는 중인 것을 한 줄로 말한다. 꺼 둔 소스는 말하지 않는다
    — 일부러 멈춘 것이다."""
    w = _world(client, admin)
    sales, monthly = _monthly(client, admin, w)
    _plant_months(w, sales, monthly, [30] * 12, 20000.0, datetime(2027, 1, 15, tzinfo=UTC))
    tag = uuid.uuid4().hex[:6]
    succeeded = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
    long_ago = datetime.now(UTC) - timedelta(days=2)
    _source_row(
        monthly["slug"],
        name=f"가 ERP {tag}",
        last_status="failed",
        last_success_at=succeeded,
        interval_minutes=60,
        last_run_at=long_ago,
    )
    _source_row(monthly["slug"], name=f"나 끔 {tag}", last_status="failed", is_active=False)
    _source_row(
        monthly["slug"],
        name=f"다 코어 {tag}",
        kind="sp_core",
        last_status="ok",
        last_success_at=succeeded,
        options={"_resume": {"next": "abc"}},
    )
    _source_row(
        monthly["slug"], name=f"라 MES {tag}", last_status="ok", last_success_at=succeeded
    )
    _source_row(
        sales["slug"],
        name=f"판매 SAP {tag}",
        last_status="ok",
        interval_minutes=60,
        last_run_at=long_ago,
    )

    head = _read(client, admin, monthly["slug"], by="period")
    mine = [one for one in head["freshness"] if tag in one["name"]]
    assert [one["name"] for one in mine] == [
        f"가 ERP {tag}",
        f"나 끔 {tag}",
        f"다 코어 {tag}",
        f"라 MES {tag}",
    ]
    by_name = {one["name"]: one for one in mine}
    assert by_name[f"가 ERP {tag}"]["failing"] is True
    assert by_name[f"나 끔 {tag}"]["active"] is False
    assert by_name[f"나 끔 {tag}"]["failing"] is False
    assert by_name[f"다 코어 {tag}"]["catching_up"] is True
    den_fresh = [one for one in head["denominator"]["freshness"] if tag in one["name"]]
    assert [(one["name"], one["stale"]) for one in den_fresh] == [(f"판매 SAP {tag}", True)]
    # 원천 기록 끝이 없으니(심은 실행) 빈 기간은 아니다 — 신선도만 따로 말한다.
    assert head["data_gap"] is False

    found = _analysis(
        client, admin, monthly["slug"], "control", **{"d.base_model": w["s_base"]}
    )
    (note,) = [one for one in found["caveats"] if one["code"] == "source_issue"]
    assert note["level"] == "warn"
    assert note["message"] == (
        f"원천 「가 ERP {tag}」 수집 실패(최종 수집 시각 2026-11-01 03:00 UTC) · "
        f"원천 「다 코어 {tag}」 적재 진행 중(최종 수집 시각 2026-11-01 03:00 UTC) · "
        f"분모 「{sales['label']}」 의 원천 「판매 SAP {tag}」 수집 지연(최종 수집 시각 없음) "
        "— 해당 원천의 새 기록이 집계에 빠졌을 수 있습니다."
    )
    assert "끔" not in note["message"] and "라 MES" not in note["message"]
