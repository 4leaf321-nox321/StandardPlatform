"""눈여겨볼 것(ADR 0032) — 플랫폼이 스스로 지표를 훑어 결론을 줄 세운다.

심은 셀(정답을 아는 증상별 월 인입률) 위에서: 훑기가 증상마다 변화점을 찾고, 오래된 변화는
점수가 줄고, 다시 훑어도 같은 결론은 한 줄이고, 결론은 그 지표의 셀이 든 부서를 모두 보는
사람에게만 보이고, 「그만 보기」 · 끄기는 시스템 관리자만 한다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.jobs import services as job_services
from app.modules.metrics import insights
from app.modules.metrics.models import MetricDef, MetricInsight
from app.modules.workspaces.models import Workspace
from app.shared.errors import code
from tests.api.conftest import Signed
from tests.api.test_metric_analysis import _month, _plant, _symptom_months
from tests.api.test_metrics import _world


def _setup(client: TestClient, admin: Signed) -> tuple[dict[str, Any], str]:
    """기본 모델 하나 · 증상 셋 · 48달. 발열은 오래전(18달 전)에 오르고, 누수는 끝 넉 달에
    오른다(잠정), 소음은 그대로."""
    w = _world(client, admin)
    sales, monthly = _symptom_months(client, admin, w)
    first, watermark = date(2026, 1, 1), datetime(2030, 2, 15, tzinfo=UTC)
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

    def count(symptom: str, i: int) -> int:
        if symptom == "발열":
            return 210 if i >= 30 else 150
        if symptom == "누수":
            return 150 if i >= 44 else 100
        return 200

    _plant(
        monthly["slug"],
        [
            {
                "period": _month(first, i),
                "dims": {"base_model": w["s_base"], "symptom": symptom},
                "count": count(symptom, i),
            }
            for i in range(48)
            for symptom in ("소음", "발열", "누수")
        ],
        watermark,
    )
    return w, monthly["slug"]


def _scan(client: TestClient, admin: Signed, slug: str) -> None:
    queued = client.post("/api/insights/scan", json={"metric": slug}, headers=admin.headers)
    assert queued.status_code == 202, queued.text
    assert queued.json() == {"queued": 1}
    while job_services.process_one("test-worker"):
        pass


def _mine(client: TestClient, who: Signed, slug: str, **params: Any) -> list[dict[str, Any]]:
    got = client.get("/api/insights", params={"metric": slug, **params}, headers=who.headers)
    assert got.status_code == 200, got.text
    return list(got.json()["items"])


def test_증상마다_변화점을_찾고_오래된_것은_점수가_준다(
    client: TestClient, admin: Signed
) -> None:
    _, slug = _setup(client, admin)
    _scan(client, admin, slug)
    found = _mine(client, admin, slug)
    by_symptom = {
        one["detail"].get("value"): one
        for one in found
        if one["kind"] == "change" and one["detail"].get("by") == "symptom"
    }
    assert {"누수", "발열"} <= set(by_symptom), [one["title"] for one in found]
    leak, heat = by_symptom["누수"], by_symptom["발열"]
    assert leak["detail"]["at"] == "2029-09-01"
    assert leak["detail"]["provisional"] is True
    assert heat["detail"]["at"] == "2028-07-01"

    # 점수 = 세기 x 근거. 세기는 적게 잡은 비(비의 구간 중 1 에 가까운 끝). 끝 넉 달의 누수는
    # 잠정이라 절반, 18달 전의 발열은 오래돼 반감(자료의 마지막 기간에서 잰다 — 오늘이 아니라).
    def full(one: dict[str, Any]) -> float:
        low, high = one["detail"]["ratio_ci"]
        assert abs(one["detail"]["ratio_bound"] - low) < 1e-3  # 오른 것은 아래 끝
        return insights.score_of(
            one["detail"]["ratio"], one["detail"]["support"], interval=(low, high)
        )

    leak_full, heat_full = full(leak), full(heat)
    assert (
        0 < leak_full < insights.score_of(leak["detail"]["ratio"], leak["detail"]["support"])
    )
    assert abs(leak["score"] - leak_full / 2) < 1e-3
    assert 0.45 * heat_full < heat["score"] < 0.6 * heat_full
    assert found == sorted(found, key=lambda one: -one["score"])
    # 링크는 그 분석 그 인자 — 증상마다 훑기.
    assert leak["link"].startswith(f"/metrics/{slug}?tab=analysis&recipe=changes")
    assert "by=symptom" in leak["link"]
    assert leak["kind_label"] == "변화점" and leak["past"] is False


def test_다시_훑어도_같은_결론은_한_줄이고_그만_보기는_관리자만(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    _, slug = _setup(client, admin)
    _scan(client, admin, slug)
    first = _mine(client, admin, slug)
    _scan(client, admin, slug)
    again = _mine(client, admin, slug)
    assert {one["id"] for one in again} == {one["id"] for one in first}
    rows = db.scalars(select(MetricInsight).join(MetricDef).where(MetricDef.slug == slug))
    assert len(list(rows)) == len(first)

    target = first[0]["id"]
    denied = client.put(
        f"/api/insights/{target}/dismissed", json={"dismissed": True}, headers=member.headers
    )
    assert denied.status_code == 403
    done = client.put(
        f"/api/insights/{target}/dismissed", json={"dismissed": True}, headers=admin.headers
    )
    assert done.status_code == 200 and done.json()["dismissed"] is True
    assert target not in {one["id"] for one in _mine(client, admin, slug)}
    assert target in {one["id"] for one in _mine(client, admin, slug, include_dismissed=True)}
    _scan(client, admin, slug)  # 다시 훑어도 그만 본 것은 그대로
    assert target not in {one["id"] for one in _mine(client, admin, slug)}


def test_셀이_든_부서를_모두_보는_사람에게만_보인다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    _, slug = _setup(client, admin)
    _scan(client, admin, slug)
    assert _mine(client, member, slug), "전역 셀뿐이면 모두에게 보인다"

    other = Workspace(slug=f"o-{uuid.uuid4().hex[:6]}", name="다른 부서")
    db.add(other)
    db.commit()
    metric = db.scalar(select(MetricDef).where(MetricDef.slug == slug))
    assert metric is not None
    # 한 칸이 남의 부서 기록이 된다 — 훑기는 전체의 눈이라 그 수가 결론에 섞인다.
    db.execute(
        text(
            "UPDATE metric_values SET workspace_id = :ws WHERE ctid IN (SELECT ctid FROM "
            "metric_values WHERE metric_id = :m AND run_id = :r LIMIT 1)"
        ),
        {"ws": other.id, "m": metric.id, "r": metric.cells_run_id},
    )
    db.commit()
    _scan(client, admin, slug)
    assert _mine(client, member, slug) == []
    assert _mine(client, admin, slug), "시스템 관리자는 늘 본다"


def test_끄면_훑지_않고_작업은_지표마다_하나만_든다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    _, slug = _setup(client, admin)
    metric = db.scalar(select(MetricDef).where(MetricDef.slug == slug))
    assert metric is not None
    first = insights.request(db, metric)
    second = insights.request(db, metric)
    db.commit()
    assert first is not None and second is not None and first.id == second.id
    while job_services.process_one("test-worker"):
        pass

    assert (
        client.put(
            f"/api/insights/metrics/{slug}", json={"off": True}, headers=member.headers
        ).status_code
        == 403
    )
    off = client.put(
        f"/api/insights/metrics/{slug}", json={"off": True}, headers=admin.headers
    )
    assert off.json() == {"slug": slug, "off": True}
    refused = client.post("/api/insights/scan", json={"metric": slug}, headers=admin.headers)
    assert refused.status_code == 404 and refused.json()["error"]["code"] == code(
        "METRICS", 91
    )
    db.refresh(metric)
    assert insights.request(db, metric) is None


def test_개인_토큰은_읽기만_한다(client: TestClient, admin: Signed) -> None:
    """`/api/insights` 는 범위 표에 없다 — 토큰으로는 읽기만(「모르는 것은 막는다」)."""
    made = client.post(
        "/api/auth/tokens",
        json={"name": "읽기", "scopes": ["read", "objects:write", "ontology:write"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    token = {"Authorization": f"Bearer {made.json()['token']}"}
    assert client.get("/api/insights", headers=token).status_code == 200
    refused = client.post("/api/insights/scan", json={}, headers=token)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == code("AUTH", 105)


def test_훑기_작업은_다른_작업이_없을_때_집는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """지표를 다시 셀 때마다 훑기가 하나씩 들어온다 — 그것이 쌓여 뒤에 넣은 다시 세기 ·
    동기화가 줄을 서면 안 된다."""
    _, slug = _setup(client, admin)
    metric = db.scalar(select(MetricDef).where(MetricDef.slug == slug))
    assert metric is not None
    while job_services.process_one("test-worker"):
        pass
    scan_job = insights.request(db, metric)
    db.commit()
    other = job_services.enqueue(
        db, kind="metrics_recompute", params={"slugs": [slug]}, user=None, workspace_id=None
    )
    db.commit()
    claimed = job_services.claim(db, "test-worker")
    assert claimed is not None and claimed.id == other.id
    claimed.status = "cancelled"
    db.commit()
    assert scan_job is not None
    following = job_services.claim(db, "test-worker")
    assert following is not None and following.id == scan_job.id
    following.status = "cancelled"
    db.commit()
