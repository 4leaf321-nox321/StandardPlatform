"""끊긴 질문(ADR 0031) — 답을 못 받고 끊긴 물음을 **모양만** 남기고, 같은 모양끼리 묶어 보인다.

무엇을 지표로 미리 세어 둘지, 트리플 스토어가 정말 필요한지를 짐작이 아니라 실제 물음으로
가르려고 남긴다. 조건의 값과 검색어는 남기지 않는다 — 요청 시간 기록이 물음표 뒤를 안 남기는
것과 같은 까닭이다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.audit.models import CutQuestion
from app.modules.objects import summary as summary_service
from app.modules.server import performance
from app.shared.errors import code
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _cut(client: TestClient, who: Signed) -> Any:
    got = client.get("/api/server/performance/cut", headers=who.headers)
    assert got.status_code == 200, got.text
    return got.json()


def test_시간_상한에_걸린_통계는_모양만_남고_같은_모양끼리_묶인다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind = _make_type(client, admin, label="부품")
    _make_property(client, admin, kind, key="grade", label="등급", data_type="text")
    _make_object(client, admin, kind, label="볼트", properties={"grade": "A"})

    def slow(db: Session, *args: Any, **kwargs: Any) -> Any:
        db.execute(text("SELECT pg_sleep(3)"))
        raise AssertionError("상한이 이 질의를 끊었어야 한다")

    monkeypatch.setattr(summary_service, "summarize", slow)
    monkeypatch.setattr(get_settings(), "read_query_seconds", 0.2)
    asked = {"group_by": "properties.grade", "f.grade.eq": "비밀값-A", "q": "검색어-볼트"}
    for _ in range(2):
        got = client.get(f"/api/objects/{kind}/summary", params=asked, headers=admin.headers)
        assert got.status_code == 504, got.text
    monkeypatch.undo()

    found = _cut(client, admin)
    mine = [one for one in found["items"] if one["shape"].get("type") == kind]
    assert len(mine) == 1, "같은 모양이면 한 줄이다"
    one = mine[0]
    assert (one["kind"], one["kind_label"], one["code"]) == (
        "timeout",
        "시간 상한",
        code("COMMON", 504),
    )
    assert one["route"] == "/api/objects/{type_slug}/summary"
    assert one["shape"] == {
        "type": kind,
        "group_by": "properties.grade",
        "q": True,
        "conditions": ["grade eq"],
    }
    assert (one["count"], one["users"]) == (2, 1)
    assert one["last_request_id"]
    # **값은 안 남는다** — 조건의 값도, 검색어도.
    dumped = json.dumps(found, ensure_ascii=False)
    assert "비밀값-A" not in dumped and "검색어-볼트" not in dumped


def test_SPARQL_의_거절은_질의문과_함께_남는다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _make_type(client, admin, label="모델", key_policy="required")
    case = _make_type(client, admin, label="기록", usage="log")
    _make_object(client, admin, model, key="M-1", label="M-1")
    _make_object(client, admin, model, key="M-2", label="M-2")

    records = f"SELECT ?x WHERE {{ ?x a sp:{case} }}"
    refused = client.post("/api/rdf/query", json={"query": records}, headers=admin.headers)
    assert refused.json()["error"]["code"] == code("RDF", 6)

    monkeypatch.setattr("app.modules.rdf.export.MAX_OBJECTS", 1)
    big = f"SELECT ?x WHERE {{ ?x a sp:{model} }}"
    too_big = client.post(
        "/api/rdf/query", json={"query": big, "types": [model]}, headers=admin.headers
    )
    assert too_big.json()["error"]["code"] == code("RDF", 5)

    rows = {
        row.kind: row
        for row in db.scalars(select(CutQuestion).where(CutQuestion.route == "/api/rdf/query"))
        if row.shape.get("query") in (records, big)
    }
    assert rows["records"].shape == {"types": [], "query": records}
    assert rows["records"].code == code("RDF", 6)
    assert rows["too_big"].shape == {"types": [model], "query": big}
    assert rows["too_big"].code == code("RDF", 5)


def test_끊긴_질문은_시스템_관리자만_보고_홈에_뜨고_보관_기간_뒤_지운다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    assert client.get("/api/server/performance/cut", headers=member.headers).status_code == 403
    db.add(
        CutQuestion(
            kind="timeout",
            code=code("COMMON", 504),
            method="GET",
            route="/api/objects/{type_slug}/summary",
            shape={"type": "홈시험"},
        )
    )
    db.commit()
    performance._cached.clear()
    home = client.get("/api/server/maintenance", headers=admin.headers).json()
    assert any(one["key"] == "cut_questions" and one["count"] >= 1 for one in home)

    old = CutQuestion(
        kind="timeout",
        code=code("COMMON", 504),
        method="GET",
        route="/api/objects/{type_slug}",
        shape={"type": "오래된것"},
    )
    db.add(old)
    db.commit()
    stale = datetime.now(UTC) - timedelta(days=get_settings().log_retention_days + 1)
    db.execute(
        text("UPDATE cut_questions SET created_at = :at WHERE id = :id"),
        {"at": stale, "id": old.id},
    )
    db.commit()
    gone_id = old.id
    performance.purge_old_timings(db)
    db.expire_all()  # 같은 세션이 들고 있는 줄이 아니라 표를 본다
    assert db.get(CutQuestion, gone_id) is None


def test_끊긴_통계에서_지표_초안을_짓고_그_초안은_계획을_통과한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0032 — 날짜 칸 기준은 시간 칸으로, 나머지 기준과 **조건의 칸**은 기준으로(값은
    안 남았다 — 읽을 때 `d.<기준>=값` 으로 거른다). 초안은 저장하지 않는다."""
    kind = _make_type(client, admin, label="수리 기록")
    _make_property(client, admin, kind, key="received", label="접수일", data_type="date")
    _make_property(client, admin, kind, key="grade", label="등급", data_type="text")
    _make_object(client, admin, kind, label="1건", properties={"received": "2026-01-05"})

    def slow(db: Session, *args: Any, **kwargs: Any) -> Any:
        db.execute(text("SELECT pg_sleep(3)"))
        raise AssertionError("상한이 이 질의를 끊었어야 한다")

    monkeypatch.setattr(summary_service, "summarize", slow)
    monkeypatch.setattr(get_settings(), "read_query_seconds", 0.2)
    asked = {"group_by": "properties.received", "grain": "month", "f.grade.eq": "A"}
    got = client.get(f"/api/objects/{kind}/summary", params=asked, headers=admin.headers)
    assert got.status_code == 504
    monkeypatch.undo()

    plain = _cut(client, admin)
    assert all(one.get("suggestion") is None for one in plain["items"]), "물어야 싣는다"
    found = client.get(
        "/api/server/performance/cut", params={"suggest": "true"}, headers=admin.headers
    ).json()
    (one,) = [row for row in found["items"] if row["shape"].get("type") == kind]
    draft = one["suggestion"]
    assert draft["source_type_slug"] == kind and draft["slug"].startswith("cut_")
    assert draft["spec"]["time"] == {"address": "properties.received", "grain": "month"}
    assert draft["spec"]["dimensions"] == [{"name": "grade", "address": "properties.grade"}]
    plan = client.post(
        "/api/metrics/plan",
        json={"slug": draft["slug"], "source_type_slug": kind, "spec": draft["spec"]},
        headers=admin.headers,
    )
    assert plan.status_code == 200, plan.text
    assert plan.json()["ok"] is True, plan.json()["errors"]
