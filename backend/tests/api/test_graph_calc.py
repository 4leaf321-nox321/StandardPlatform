"""밤 그래프 계산(ADR 0067) — 판을 만들어 API 로 읽는다.

    모델 M1 ─ A1 ─ P1 · P2        (구성 = 이어지는 관계, 부모 → 자식)
       └─ P4
    모델 M2 ─ A1, A2 ─ P2 · P3

시험 DB 에는 다른 시험의 객체도 있다 — 셈은 그래프 전체로 하고, 견주기는 이 세계의 타입 ·
객체로만 한다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.modules.graph import calc
from app.modules.graph.models import GraphCalcRun
from app.modules.jobs.models import Job
from tests.api.conftest import Signed, finish_job
from tests.api.test_ontology import _link, _make_object, _make_relation, _make_type


@pytest.fixture(autouse=True)
def _cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "graph_calc_engine", "cpu")
    monkeypatch.setattr(get_settings(), "graph_calc_threads", 2)


@pytest.fixture
def world(client: TestClient, admin: Signed) -> dict[str, Any]:
    part = _make_type(client, admin, label="부품", key_policy="optional")
    model = _make_type(client, admin, label="모델", key_policy="optional")
    kind = _make_relation(
        client,
        admin,
        "bom",
        label="구성",
        transitive=True,
        acyclic=True,
        src_type_slugs=[model, part],
        dst_type_slugs=[part],
    )
    ids: dict[str, str] = {}
    for name in ("A1", "A2", "P1", "P2", "P3", "P4"):
        ids[name] = _make_object(client, admin, part, label=name)["id"]
    for name in ("M1", "M2"):
        ids[name] = _make_object(client, admin, model, label=name)["id"]
    slug_of = {name: (model if name.startswith("M") else part) for name in ids}
    for src, dst in (
        ("M1", "A1"),
        ("M1", "P4"),
        ("M2", "A1"),
        ("M2", "A2"),
        ("A1", "P1"),
        ("A1", "P2"),
        ("A2", "P2"),
        ("A2", "P3"),
    ):
        got = _link(client, admin, slug_of[src], ids[src], kind, ids[dst])
        assert got.status_code == 201, got.text
    return {"part": part, "model": model, "kind": kind, "ids": ids}


def _calc(client: TestClient, admin: Signed) -> dict[str, Any]:
    made = client.post("/api/graph/calc/run", headers=admin.headers)
    assert made.status_code == 202, made.text
    done = finish_job(client, admin, made.json())
    assert done["status"] == "done", done
    return dict(done["result"])


def test_기대는_수_순위는_끝까지_올라가_닿는_모델의_수다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    result = _calc(client, admin)
    assert result["engine"] == "cpu"
    assert world["kind"] in result["relations"]
    got = client.get(
        "/api/graph/calc/top",
        params={"metric": "depends_top", "relation": world["kind"], "type": world["part"]},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["run"]["engine"] == "cpu" and body["run"]["computed_at"]
    values = {one["label"]: one["value"] for one in body["items"]}
    assert values == {"A1": 2, "P1": 2, "P2": 2, "A2": 1, "P3": 1, "P4": 1}
    ranks = [one["rank"] for one in body["items"]]
    assert ranks == sorted(ranks)
    # 이어지는 관계가 아닌 것은 순위가 없다 — 무엇을 셌는지 말한다.
    wrong = client.get(
        "/api/graph/calc/top",
        params={"metric": "depends_top", "relation": "없는_관계"},
        headers=admin.headers,
    )
    assert wrong.status_code == 404
    assert world["kind"] in wrong.json()["error"]["message"]


def test_객체_하나는_지표_무리_비슷한_것을_함께_준다(
    client: TestClient, admin: Signed, member: Signed, world: dict[str, Any]
) -> None:
    _calc(client, admin)
    ids = world["ids"]
    got = client.get(f"/api/graph/calc/object/{ids['M1']}", headers=member.headers)
    assert got.status_code == 200, got.text
    body = got.json()
    similar = [(one["label"], one["score"], one["rank"]) for one in body["similar"]]
    assert similar == [("M2", 0.5, 1)]
    assert body["similar"][0]["relation"] == world["kind"]
    assert body["cluster"] is not None and body["cluster"]["size"] >= 2
    part = client.get(f"/api/graph/calc/object/{ids['P2']}", headers=member.headers).json()
    depends = [one for one in part["scores"] if one["metric"] == "depends_top"]
    assert depends and depends[0]["value"] == 2 and depends[0]["of"] >= 6
    # 같은 무리의 객체 — 무리 안에서 이음이 많은 것부터.
    cluster = part["cluster"]["cluster"]
    members = client.get(
        f"/api/graph/calc/clusters/{cluster}",
        params={"type": world["part"]},
        headers=member.headers,
    )
    assert members.status_code == 200, members.text
    labels = [one["label"] for one in members.json()["items"]]
    assert "P2" in labels
    listed = client.get("/api/graph/calc/clusters", headers=member.headers).json()
    assert any(one["cluster"] == cluster for one in listed["items"]) or listed["total"] > 50


def test_지금_계산은_시스템_관리자만_넣고_겹쳐_넣지_않는다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    assert client.post("/api/graph/calc/run", headers=member.headers).status_code == 403
    first = client.post("/api/graph/calc/run", headers=admin.headers)
    again = client.post("/api/graph/calc/run", headers=admin.headers)
    assert first.status_code == again.status_code == 202
    assert first.json()["id"] == again.json()["id"]
    finish_job(client, admin, first.json())
    status = client.get("/api/graph/calc", headers=member.headers).json()
    assert status["run"] is not None and status["pending"] is None
    assert status["engine_setting"] == "cpu"


def test_부서로_좁힌_설치에서는_모든_부서를_보는_사람에게만(
    client: TestClient, admin: Signed, member: Signed, department_scope: None
) -> None:
    assert client.get("/api/graph/calc", headers=member.headers).status_code == 403
    denied = client.get("/api/graph/calc/clusters", headers=member.headers)
    assert denied.status_code == 403
    assert "모든 부서" in denied.json()["error"]["message"]
    assert client.get("/api/graph/calc", headers=admin.headers).status_code == 200


def test_타이머는_처음_판을_넣고_실패한_뒤에는_멈춘다(
    db: Session, client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    db.execute(delete(Job).where(Job.kind == calc.KIND))
    db.execute(delete(GraphCalcRun))
    db.commit()
    with SessionLocal() as session:
        assert calc.schedule(session) == calc.KIND
    with SessionLocal() as session:
        # 기다리는 것이 있으면 또 넣지 않는다.
        assert calc.schedule(session) is None
        session.execute(delete(Job).where(Job.kind == calc.KIND))
        session.add(Job(kind=calc.KIND, status="failed", params={}))
        session.commit()
    with SessionLocal() as session:
        assert calc.schedule(session) is None
        assert "실패" in (calc.paused(session) or "")
    # 사람이 「지금 계산」 을 눌러 끝나면 다시 돈다.
    _calc(client, admin)
    with SessionLocal() as session:
        assert calc.paused(session) is None


def test_무리는_고른_관계로만_짓고_모르는_이름이면_말한다(
    client: TestClient,
    admin: Signed,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """무리는 고른 선에 따라 뜻이 바뀐다 — 기준정보 전체면 공급사 중심, 구성만이면 제품군
    (견주기). 고른 것을 판에 적는다. 모르는 이름은 조용히 빼지 않는다."""
    monkeypatch.setattr(get_settings(), "graph_calc_whole_relations", f" {world['kind']} ,")
    result = _calc(client, admin)
    assert result["whole_relations"] == [world["kind"]]
    ids = world["ids"]
    m1 = client.get(f"/api/graph/calc/object/{ids['M1']}", headers=admin.headers).json()
    assert m1["cluster"] is not None and m1["run"]["stats"]["whole_relations"] == [
        world["kind"]
    ]

    monkeypatch.setattr(get_settings(), "graph_calc_whole_relations", "없는_관계")
    made = client.post("/api/graph/calc/run", headers=admin.headers)
    failed = finish_job(client, admin, made.json())
    assert failed["status"] == "failed"
    assert "없는_관계" in failed["error"] and world["kind"] in failed["error"]
    # 비우면 전부 — 예전 그대로.
    monkeypatch.setattr(get_settings(), "graph_calc_whole_relations", "")
    assert _calc(client, admin)["whole_relations"] == []
