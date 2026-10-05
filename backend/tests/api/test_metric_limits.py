"""지표의 상한 — 실제 셀로 막는다(ADR 0023).

지키는 것: 표본이 전부일 때(기록이 표본보다 적다) 계획의 셀 어림이 **계산의 실제 셀 수와 같다**
(여러 값 기준의 펼침 · 코호트 · 참조 너머 기준까지) · 계산이 실제로 상한을 넘으면 그 계산을
버리고 옛 값을 둔다 · 기준은 여덟까지 · 화면에 돌려주는 읽기 상한과 분석이 받는 상한이 따로다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Signed, finish_job
from tests.api.test_metric_analysis import _analysis
from tests.api.test_metrics import _cases_spec, _define, _read, _sales_spec, _world


def _plan(
    client: TestClient, admin: Signed, source: str, spec: dict[str, Any]
) -> dict[str, Any]:
    got = client.post(
        "/api/metrics/plan",
        json={"source_type_slug": source, "spec": spec},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_표본이_전부면_계획의_셀_어림이_실제_셀과_같다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    sales = _define(client, admin, source=w["sales"], spec=_sales_spec(), label="판매 대수")
    parts = {
        "measure": "count",
        "time": {"address": "properties.received", "grain": "month"},
        "dimensions": [
            {"name": "part", "address": "properties.parts"},
            {"name": "base_model", "address": "ref.model.base"},
        ],
    }
    for source, spec in [
        (w["sales"], _sales_spec()),
        (w["case"], _cases_spec(sales["slug"])),
        (w["case"], parts),
    ]:
        plan = _plan(client, admin, source, spec)
        assert plan["ok"], plan["errors"]
        made = _define(client, admin, source=source, spec=spec)
        assert plan["estimated_cells"] == made["cells"], (spec, plan["estimated_cells"])


def test_계산이_상한을_넘으면_그_계산을_버리고_옛_값을_둔다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    w = _world(client, admin)
    sales = _define(client, admin, source=w["sales"], spec=_sales_spec(), label="판매 대수")
    cases = _define(client, admin, source=w["case"], spec=_cases_spec(sales["slug"]))
    before = cases["current_run_id"]
    assert cases["cells"] > 2
    counted = sum(one["count"] for one in _read(client, admin, cases["slug"])["cells"])
    monkeypatch.setattr(get_settings(), "metrics_max_cells", 2)
    job = client.post(f"/api/metrics/{cases['slug']}/recompute", headers=admin.headers)
    assert job.status_code == 202, job.text
    done = finish_job(client, admin, job.json())
    assert done["status"] == "failed"
    after = client.get(f"/api/metrics/{cases['slug']}", headers=admin.headers).json()
    assert after["current_run_id"] == before and after["cells"] == cases["cells"]
    assert after["last_status"] == "failed"
    assert "METRICS-0012" in after["last_error"] and "옛 값" in after["last_error"]
    # 옛 값은 그대로 읽힌다.
    assert sum(one["count"] for one in _read(client, admin, cases["slug"])["cells"]) == counted


def test_기준은_여덟까지(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    addresses = [
        "properties.symptom",
        "properties.factory",
        "ref.model.base",
        "properties.cost",
        "status",
        "workspace",
        "created_year",
        "label",
        "key",
    ]
    eight = {
        "measure": "count",
        "time": {"address": "properties.received", "grain": "month"},
        "dimensions": [
            {"name": f"d{index}", "address": one} for index, one in enumerate(addresses[:8])
        ],
    }
    plan = _plan(client, admin, w["case"], eight)
    assert plan["ok"], plan["errors"]
    nine = {
        **eight,
        "dimensions": [
            {"name": f"d{index}", "address": one} for index, one in enumerate(addresses)
        ],
    }
    got = client.post(
        "/api/metrics/plan",
        json={"source_type_slug": w["case"], "spec": nine},
        headers=admin.headers,
    )
    assert got.status_code == 422


def test_화면_읽기_상한과_분석이_받는_상한은_따로다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    w = _world(client, admin)
    sales = _define(client, admin, source=w["sales"], spec=_sales_spec(), label="판매 대수")
    cases = _define(client, admin, source=w["case"], spec=_cases_spec(sales["slug"]))
    whole = _read(client, admin, cases["slug"], dims="symptom")
    assert not whole["truncated"] and len(whole["cells"]) > 1
    monkeypatch.setattr(get_settings(), "metrics_max_read_cells", 1)
    table = _read(client, admin, cases["slug"], dims="symptom")
    assert table["truncated"] and len(table["cells"]) == 1
    # 분석은 계산하려고 받는 상한(20만)을 쓴다 — 화면 상한에 잘리지 않는다.
    pareto = _analysis(client, admin, cases["slug"], "pareto", dim="symptom")
    assert pareto["total"] == whole["total_count"] and not pareto["truncated"]
