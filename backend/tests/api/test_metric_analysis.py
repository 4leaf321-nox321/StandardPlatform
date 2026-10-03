"""분석(ADR 0014) — 진짜 앱으로. 레시피의 수식은 단위 시험(`tests/unit/test_recipe_*`)이 정답과
견주고, 여기서는 **경로 · 거르기 · 거절 · 주의 · 건 보기**가 선다는 것을 본다.

건 보기는 언제나 「그 수 = 그 목록의 수」 다 — 분석의 숫자도 근거로 돌아가야 한다.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

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
