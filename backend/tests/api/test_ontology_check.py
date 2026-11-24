"""정의 점검(ADR 0092) — 지우거나 묶을 후보를 센다. 아무것도 안 바꾼다.

시험 DB 는 스위트가 함께 쓴다 — 전체 수가 아니라 **이 시험이 만든 것**이 서는지만 본다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.ontology.models import ObjectType, RelationType
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _hex() -> str:
    return uuid.uuid4().hex[:10]


def _check(client: TestClient, admin: Signed, **params: str) -> dict[str, Any]:
    got = client.get("/api/ontology/check", params=params, headers=admin.headers)
    assert got.status_code == 200, got.text
    body: dict[str, Any] = got.json()
    return body


def _rows(body: dict[str, Any], kind: str, slug: str) -> list[dict[str, Any]]:
    return [one for one in body["items"] if one["kind"] == kind and one["slug"] == slug]


def _age(db: Session, model: Any, slug: str, days: int = 40) -> None:
    """만든 지 오래된 것으로 — 갓 만든 것은 비어 있어도 후보가 아니다."""
    db.execute(
        update(model)
        .where(model.slug == slug)
        .values(created_at=datetime.now(UTC) - timedelta(days=days))
    )
    db.commit()


def test_아무도_안_채우고_안_쓰는_속성은_삭제_후보다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    kind = _make_type(client, admin, label=f"점검 부품 {_hex()}")
    _make_property(client, admin, kind, key="filled", label="채운 칸", data_type="text")
    _make_property(client, admin, kind, key="empty", label="빈 칸", data_type="text")
    _make_property(client, admin, kind, key="prefix", label="앞머리 칸", data_type="text")
    # 앞머리로 쓰는 칸은 비어 있어도 후보가 아니다(쓰는 곳).
    patched = client.patch(
        f"/api/ontology/types/{kind}",
        json={"label_prefix": "properties.prefix"},
        headers=admin.headers,
    )
    assert patched.status_code == 200, patched.text
    _make_object(client, admin, kind, label="하나", properties={"filled": "값"})

    body = _check(client, admin)
    keys = {one["key"] for one in _rows(body, "unused_field", kind)}
    assert keys == {"empty"}, body["items"]
    [row] = _rows(body, "unused_field", kind)
    assert row["action"] == "delete_property" and row["estimated"] is False
    assert f'ontology_delete("property", "{kind}", key="empty")' in row["next"]
    assert "객체 1건 중 0건" in row["reason"]
    assert body["kinds"]["unused_field"] == "사용하지 않는 속성"


def test_같은_키가_따로_있으면_인터페이스_후보_종류가_다르면_따로_말한다(
    client: TestClient, admin: Signed
) -> None:
    same, mixed = f"same_{_hex()}", f"mixed_{_hex()}"
    one = _make_type(client, admin, label=f"점검 가 {_hex()}")
    two = _make_type(client, admin, label=f"점검 나 {_hex()}")
    for kind in (one, two):
        _make_property(
            client, admin, kind, key=same, label="무게", data_type="number", unit="kg"
        )
    _make_property(client, admin, one, key=mixed, label="등급", data_type="text")
    _make_property(client, admin, two, key=mixed, label="등급", data_type="number")

    body = _check(client, admin)
    [candidate] = _rows(body, "interface_candidate", same)
    assert sorted(candidate["types"]) == sorted([one, two]) and candidate["count"] == 2
    assert "숫자 · kg" in candidate["reason"]
    [mismatch] = _rows(body, "field_mismatch", mixed)
    assert "글 (한 줄)" in mismatch["reason"] and "숫자" in mismatch["reason"]
    assert not _rows(body, "interface_candidate", mixed)


def test_아무도_안_고른_선택_값을_한_값_여러_값_모두에서_센다(
    client: TestClient, admin: Signed
) -> None:
    kind = _make_type(client, admin, label=f"점검 시험 {_hex()}")
    _make_property(
        client,
        admin,
        kind,
        key="grade",
        label="판정",
        data_type="enum",
        enum_options=["합격", "불합격", "보류"],
    )
    _make_property(
        client,
        admin,
        kind,
        key="tags",
        label="꼬리표",
        data_type="enum",
        multi=True,
        enum_options=["외관", "기능", "치수"],
    )
    for at in range(20):
        _make_object(
            client,
            admin,
            kind,
            label=f"시험 {at}",
            properties={"grade": "합격" if at % 2 else "불합격", "tags": ["외관"]},
        )

    rows = {one["key"]: one for one in _rows(_check(client, admin), "unused_option", kind)}
    assert rows["grade"]["count"] == 1 and "「보류」" in rows["grade"]["reason"]
    assert rows["tags"]["count"] == 2 and "「기능」 · 「치수」" in rows["tags"]["reason"]


def test_오래_빈_타입과_관계_종류_설명_없는_공개_타입(
    client: TestClient, admin: Signed, db: Session
) -> None:
    old, fresh = _make_type(client, admin), _make_type(client, admin)
    _age(db, ObjectType, old)
    relation = f"rel_{_hex()}"
    made = client.post(
        "/api/ontology/relation-types",
        json={"slug": relation, "label": f"점검 관계 {_hex()}"},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    _age(db, RelationType, relation)
    core = _make_type(client, admin, label=f"점검 공개 {_hex()}", core=True)

    body = _check(client, admin)
    [empty] = _rows(body, "empty_type", old)
    assert empty["action"] == "delete_type" and "30일" in empty["reason"]
    assert not _rows(body, "empty_type", fresh)
    assert _rows(body, "unused_relation", relation)[0]["action"] == "delete_relation"
    assert _rows(body, "undescribed_core", core)
    # 공개 타입은 비어 있어도 삭제 후보로 세지 않는다(끄는 것부터가 알리는 일이다).
    assert not _rows(body, "empty_type", core)

    # 원천이 잠근 정의는 그 원천의 이름(source) · 잠금 해제를 함께 말한다.
    db.execute(update(ObjectType).where(ObjectType.slug == old).values(managed_by="relay-q"))
    db.commit()
    [locked] = _rows(_check(client, admin, kind="empty_type"), "empty_type", old)
    assert locked["managed_by"] == "relay-q" and "source=「relay-q」" in locked["reason"]


def test_한_종류만_보기_모르는_종류_권한(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    old = _make_type(client, admin)
    _age(db, ObjectType, old)
    only = _check(client, admin, kind="empty_type")
    assert {one["kind"] for one in only["items"]} == {"empty_type"}
    assert set(only["counts"]) == {"empty_type"}
    wrong = client.get("/api/ontology/check", params={"kind": "nope"}, headers=admin.headers)
    assert wrong.status_code == 422 and "ONTOLOGY-0103" in wrong.text, wrong.text
    assert client.get("/api/ontology/check", headers=member.headers).status_code == 403
