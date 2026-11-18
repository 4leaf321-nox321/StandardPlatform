"""날짜 칸 색인을 목록의 날짜 조건이 탄다(ADR 0084) — 「빈 날」 0건을 세느라 2,000만 건 타입을
훑지 않게. 실행 계획으로 본다(작은 시험 표라 순차 훑기를 끄고 — 플래너가 색인을 **고를 수
있는가** 를 본다)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.metrics import timeindex
from app.modules.objects import conditions
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import ObjectType, PropertyDef
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_property, _make_type


def _plan(db: Session, stmt: Any) -> str:
    """앱과 같은 길로(키 · 값을 매개변수로) 보낸 질의의 실행 계획."""
    compiled = stmt.compile(dialect=db.get_bind().dialect)
    connection = db.connection()
    connection.exec_driver_sql("SET LOCAL enable_seqscan = off")
    rows = connection.exec_driver_sql(f"EXPLAIN {compiled}", compiled.params)
    return "\n".join(str(one[0]) for one in rows)


def test_날짜_범위_조건이_날짜_칸_색인을_탄다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    kind = _make_type(client, admin, label="서비스")
    _make_property(client, admin, kind, key="served", label="서비스일", data_type="date")
    type_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == kind))
    assert type_id is not None
    db.add_all(
        ObjectInstance(
            type_id=type_id,
            label=f"건{index}",
            properties={"served": f"2026-{1 + index % 12:02d}-{1 + index % 28:02d}"},
        )
        for index in range(300)
    )
    db.commit()
    name = timeindex.name_of(type_id, "served")
    # 시험의 트랜잭션 안이라 CONCURRENTLY 없이 — 식은 타이머가 세우는 것과 글자 그대로 같다.
    db.execute(
        text(timeindex.create_sql(name, type_id, "served").replace("CONCURRENTLY ", ""))
    )
    db.commit()
    try:
        definition = db.scalar(
            select(PropertyDef).where(
                PropertyDef.owner_id == type_id, PropertyDef.key == "served"
            )
        )
        found = conditions.clause(
            conditions.Condition(field="served", op="gte", value="2031-01-01"), definition
        )
        stmt = (
            select(func.count())
            .select_from(ObjectInstance)
            .where(
                ObjectInstance.type_id == type_id,
                ObjectInstance.deleted_at.is_(None),
                found,
            )
        )
        assert name in _plan(db, stmt)
        db.rollback()
        # 세면 0 — 색인을 타도 답은 같다.
        assert db.scalar(stmt) == 0
    finally:
        db.rollback()
        db.execute(text(f"DROP INDEX IF EXISTS {name}"))
        db.commit()


def test_설정의_날짜_칸이_세울_색인에_든다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind = _make_type(client, admin, label="서비스")
    _make_property(client, admin, kind, key="served", label="서비스일", data_type="date")
    _make_property(client, admin, kind, key="memo", label="메모", data_type="text")
    type_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == kind))
    assert type_id is not None
    monkeypatch.setattr(
        get_settings(), "date_index_fields", f"{kind}.served, {kind}.memo, nope.served, 잘못"
    )
    # 날짜 칸만 · 있는 타입만.
    assert timeindex.configured(db, get_settings().date_index_fields) == {(type_id, "served")}
    # 작은 타입은 세우지 않는다(5만 건 문턱) — 문턱을 낮추면 든다.
    assert timeindex.name_of(type_id, "served") not in timeindex.wanted(db)
    assert timeindex.name_of(type_id, "served") in timeindex.wanted(db, min_rows=0)
