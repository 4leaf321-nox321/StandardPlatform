"""경보(ADR 0016) — 진짜 앱으로. 만들 때 있던 것은 알리지 않고, 계산 뒤 **처음 보는 결론만
한 번** 알리고, 실패는 계산을 멈추지 않고 처음만 알리고, 남의 경보는 없는 것이다.

계산 뒤의 확인은 `alerts.after_recompute` 를 직접 부른다 — 진짜 계산은 심은 셀을 기록에서 다시
세어 덮으므로, 정답을 아는 셀 위에서 보려면 계산 다음 단계만 부른다(계산에서 부르는 것은 실패
시험이 `run_recompute` 로 본다).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.api.conftest import Signed
from tests.api.test_metric_analysis import _month, _monthly, _plant, _plant_months
from tests.api.test_metrics import _prop, _two, _world


def _create(
    client: TestClient,
    who: Signed,
    slug: str,
    recipe: str,
    params: dict[str, str],
    name: str = "경보",
) -> dict[str, Any]:
    made = client.post(
        f"/api/metrics/{slug}/alerts",
        json={"name": name, "recipe": recipe, "params": params},
        headers=who.headers,
    )
    assert made.status_code == 201, made.text
    return dict(made.json())


def _refused(
    client: TestClient, who: Signed, slug: str, recipe: str, params: dict[str, str]
) -> str:
    got = client.post(
        f"/api/metrics/{slug}/alerts",
        json={"name": "x", "recipe": recipe, "params": params},
        headers=who.headers,
    )
    assert got.status_code == 422, got.text
    return str(got.json()["error"]["code"])


def _after(slug: str) -> dict[str, int]:
    """계산이 커밋된 뒤의 단계 — 그 지표의 켜진 경보를 차례로."""
    from app.database import SessionLocal
    from app.modules.metrics import alerts
    from app.modules.metrics.models import MetricDef

    with SessionLocal() as db:
        metric_id = db.scalars(select(MetricDef.id).where(MetricDef.slug == slug)).one()
        return alerts.after_recompute(db, metric_id)


def _notes(client: TestClient, who: Signed, kind: str) -> list[dict[str, Any]]:
    got = client.get("/api/notifications", headers=who.headers)
    assert got.status_code == 200, got.text
    return [one for one in got.json() if one["kind"] == kind]


def _sprt_world(
    client: TestClient, admin: Signed
) -> tuple[dict[str, Any], dict[str, Any], Callable[[int], None]]:
    """전작 A — 2024~2025 코호트 1,000대씩 경과마다 2건. 새 모델 S — 2026~2027 코호트
    1,000대씩, 경과마다 `per_cell` 건. 4 면 두 배라 셋째 기간에 「나쁨」, 2 면 다섯째 기간에
    「나쁘지 않음」."""
    w = _world(client, admin)
    sales, cases = _two(client, admin, w)
    dims_a = {"base_model": w["a_base"], "symptom": "소음", "factory": "F1"}
    dims_s = {"base_model": w["s_base"], "symptom": "소음", "factory": "F1"}
    watermark = datetime(2028, 2, 15, tzinfo=UTC)
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

    return w, cases, plant


def test_경보는_처음_있던_것을_알리지_않고_새로_선_결론만_한_번_알린다(
    client: TestClient, admin: Signed
) -> None:
    w, cases, plant = _sprt_world(client, admin)
    slug = cases["slug"]
    params = {"target": w["s_base"], "reference": w["a_base"]}
    plant(2)  # 같다 — 「나쁘지 않음」 은 고르지 않으면 알리지 않는다
    made = _create(client, admin, slug, "sprt", params, name="S 초기 고장")
    assert made["baseline"]["findings"] == [] and made["events"] == 0
    assert made["last_status"] == "ok" and made["recipe_label"].startswith("순차 검정")
    assert made["link"].startswith(f"/metrics/{slug}?tab=analysis&recipe=sprt&")
    assert f"target={w['s_base']}" in made["link"]

    plant(4)  # 두 배 — 「나쁨」 이 선다
    assert _after(slug) == {"alerts": 1, "new": 1}
    (note,) = _notes(client, admin, "metric.alert")
    assert note["title"] == "S 초기 고장: 새 결과 1건" and note["link"] == made["link"]
    assert "S기본: 전작 A기본 보다 나쁨" in note["body"]
    # 결론은 누적이라 다음 계산에서도 서 있다 — 같은 것을 다시 알리지 않는다.
    assert _after(slug) == {"alerts": 1, "new": 0}
    assert len(_notes(client, admin, "metric.alert")) == 1

    events = client.get("/api/metrics/alerts/events", headers=admin.headers).json()
    (event,) = [one for one in events if one["alert_id"] == made["id"]]
    assert event["key"] == f"worse:{w['s_base']}" and event["baseline"] is False
    assert event["detail"]["smr"] == pytest.approx(2.0)
    assert event["detail"]["decided_at"] == "2026-03" and event["link"] == made["link"]

    # 지금 확인 — 적지도 알리지도 않고, 이미 본 결론은 새것이 아니라고 말한다.
    checked = client.post(
        f"/api/metrics/{slug}/alerts/{made['id']}/check", headers=admin.headers
    )
    assert checked.status_code == 200, checked.text
    assert [one["new"] for one in checked.json()["findings"]] == [False]

    # 「나쁨」 이 이미 선 뒤에 만든 경보 — 그것은 처음부터 있던 것이다.
    again = _create(client, admin, slug, "sprt", params, name="복제")
    (found,) = again["baseline"]["findings"]
    assert found["key"] == f"worse:{w['s_base']}" and found["new"] is False
    assert again["events"] == 1
    assert _after(slug) == {"alerts": 2, "new": 0}
    assert len(_notes(client, admin, "metric.alert")) == 1
    shown = client.get("/api/metrics/alerts/events", headers=admin.headers).json()
    assert again["id"] not in {one["alert_id"] for one in shown}
    with_baseline = client.get(
        "/api/metrics/alerts/events", params={"baseline": "true"}, headers=admin.headers
    ).json()
    assert {made["id"], again["id"]} <= {one["alert_id"] for one in with_baseline}


def test_관리도_경보는_끝_부분군의_신호만_새로_본다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    sales, monthly = _monthly(client, admin, w)
    slug = monthly["slug"]
    counts = [round(200 * (1 + 0.1 * np.sin(i * 1.7))) for i in range(30)]
    counts[20] = 320  # 2027-09 — 신호지만 끝 부분군이 아니다
    _plant_months(w, sales, monthly, counts, 20000.0, datetime(2028, 8, 15, tzinfo=UTC))
    made = _create(client, admin, slug, "control", {}, name="월 인입률 관리도")
    assert made["baseline"]["findings"] == []

    counts.append(400)  # 2028-07 이 닫히며 튄다
    _plant_months(w, sales, monthly, counts, 20000.0, datetime(2028, 9, 15, tzinfo=UTC))
    after = _after(slug)
    assert after["alerts"] == 1 and after["new"] >= 1
    events = client.get(
        f"/api/metrics/{slug}/alerts/{made['id']}/events", headers=admin.headers
    ).json()
    keys = {one["key"] for one in events}
    assert "-:2028-07-01:1" in keys and not any("2027-09" in key for key in keys)
    (note,) = _notes(client, admin, "metric.alert")
    assert "2028-07" in note["body"] and "한계 밖" in note["body"]


def test_경보가_실패해도_계산은_서고_처음_실패만_알린다(
    client: TestClient, admin: Signed
) -> None:
    from app.database import SessionLocal
    from app.modules.metrics import services
    from app.modules.metrics.models import MetricAlert

    w = _world(client, admin)
    sales, monthly = _monthly(client, admin, w)
    slug = monthly["slug"]
    counts = [round(200 * (1 + 0.1 * np.sin(i * 1.7))) for i in range(30)]
    _plant_months(w, sales, monthly, counts, 20000.0, datetime(2028, 8, 15, tzinfo=UTC))
    made = _create(client, admin, slug, "control", {})
    # 정의에서 기준이 사라진 것과 같다 — 없는 기준으로 거른다.
    with SessionLocal() as db:
        alert = db.get(MetricAlert, uuid.UUID(made["id"]))
        assert alert is not None
        alert.params = {**alert.params, "d.nothing": "x"}
        db.commit()
    with SessionLocal() as db:
        result = services.run_recompute(db, [slug], job_id=None, progress=lambda *_: None)
    (run,) = result["runs"]
    assert run["status"] == "ok" and run["alerts"] == {"alerts": 1, "new": 0}
    (failed,) = _notes(client, admin, "metric.alert.failed")
    assert "nothing" in (failed["body"] or "") and failed["link"].startswith(
        f"/metrics/{slug}?"
    )
    _after(slug)
    assert len(_notes(client, admin, "metric.alert.failed")) == 1
    (listed,) = client.get(f"/api/metrics/{slug}/alerts", headers=admin.headers).json()
    assert listed["last_status"] == "failed" and "METRICS-0008" in listed["last_error"]


def test_남의_경보는_없는_것이고_틀린_인자는_만들기_전에_거절한다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    w, cases, plant = _sprt_world(client, admin)
    slug = cases["slug"]
    plant(2)
    params = {"target": w["s_base"], "reference": w["a_base"]}
    made = _create(client, admin, slug, "sprt", params)
    base = f"/api/metrics/{slug}/alerts"
    assert client.get(base, headers=member.headers).json() == []
    assert client.get("/api/metrics/alerts/events", headers=member.headers).json() == []
    one = f"{base}/{made['id']}"
    assert client.patch(one, json={"name": "y"}, headers=member.headers).status_code == 404
    assert client.delete(one, headers=member.headers).status_code == 404
    assert client.post(f"{one}/check", headers=member.headers).status_code == 404
    assert client.get(f"{one}/events", headers=member.headers).status_code == 404

    assert _refused(client, admin, slug, "pareto", {}).endswith("METRICS-0040")
    assert _refused(client, admin, slug, "sprt", {**params, "oops": "1"}).endswith(
        "METRICS-0041"
    )
    assert _refused(client, admin, slug, "sprt", {"reference": w["a_base"]}).endswith(
        "METRICS-0042"
    )
    # 분석이 거절하는 것은 분석의 말 그대로 — 새 모델과 전작이 같다.
    same = {"target": w["s_base"], "reference": w["s_base"]}
    assert _refused(client, admin, slug, "sprt", same).endswith("METRICS-0029")
    assert _refused(client, admin, slug, "sprt", {**params, "d.nothing": "x"}).endswith(
        "METRICS-0008"
    )

    # 멤버도 자기 경보를 건다 — 자기 눈으로 돈다.
    mine = _create(client, member, slug, "sprt", params, name="내 것")
    assert [one["id"] for one in client.get(base, headers=member.headers).json()] == [
        mine["id"]
    ]
    off = client.patch(one, json={"is_active": False}, headers=admin.headers)
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert _after(slug) == {"alerts": 1, "new": 0}
    assert client.delete(one, headers=admin.headers).status_code == 204
    assert client.get(base, headers=admin.headers).json() == []


def test_새_모델_훑기는_최근_출시_모델마다_전작과_견준다(
    client: TestClient, admin: Signed
) -> None:
    w, cases, plant = _sprt_world(client, admin)
    slug = cases["slug"]
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
    plant(2)
    # 판매는 2027-12 까지 — 24 달 안에 처음 팔린 것은 S 하나(A 는 2024 부터).
    made = _create(
        client, admin, slug, "sprt", {"launched_within": "24", "reference_via": "predecessor"}
    )
    assert made["baseline"]["findings"] == []
    (note,) = made["baseline"]["notes"]
    assert "모델 1개" in note
    plant(4)
    assert _after(slug) == {"alerts": 1, "new": 1}
    (event,) = client.get(
        f"/api/metrics/{slug}/alerts/{made['id']}/events", headers=admin.headers
    ).json()
    assert event["key"] == f"worse:{w['s_base']}"
    # 여섯 달 안에 처음 팔린 모델은 없다.
    narrow = _create(
        client, admin, slug, "sprt", {"launched_within": "6", "reference_via": "predecessor"}
    )
    assert narrow["baseline"]["findings"] == []
    assert narrow["baseline"]["notes"] == ["최근 6기간 안에 처음 팔린 모델이 없습니다."]
