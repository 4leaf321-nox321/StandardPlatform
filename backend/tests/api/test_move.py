"""객체를 다른 타입으로 옮기기 · 타입 합치기(ADR 0094).

- 객체 id 가 그대로라 참조 칸 · 관계 · 별칭이 따라온다. 칸은 같은 키 · 같은 이름 · 새로
  만들기 · 버림으로 대응한다. 이 타입을 가리키던 참조 속성과 관계 종류의 끝은 대상으로.
- 모양이 다른 칸 · 대상에 없는 선택 값 · 겹치는 식별자 · 같은 별칭 · 식별자 정책은 막는다.
  겹치는 식별자는 `merge_duplicates` 로 병합한다.
- 이력은 옮기기 전 기록을 대상의 키로 읽는다. 값이 많으면 작업으로.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEntry
from app.modules.objects.models import ObjectAlias, ObjectInstance
from tests.api.conftest import Signed, finish_job
from tests.api.test_ontology import _link, _make_object, _make_property, _make_type


def _hex() -> str:
    return uuid.uuid4().hex[:10]


def _made(
    client: TestClient, admin: Signed, slug: str, aliases: list[str] | None = None, **kw: Any
) -> dict[str, Any]:
    made = _make_object(client, admin, slug, **kw)
    if aliases:
        put = client.put(
            f"/api/objects/{slug}/{made['id']}/aliases",
            json={"aliases": aliases},
            headers=admin.headers,
        )
        assert put.status_code == 200, put.text
    return made


def _move(client: TestClient, admin: Signed, slug: str, to: str, **body: Any) -> Any:
    return client.post(
        f"/api/ontology/types/{slug}/move", json={"to": to, **body}, headers=admin.headers
    )


def _world(client: TestClient, admin: Signed) -> dict[str, str]:
    """협력사(국가 · 무게 · 메모) → 공급사(국가 · 중량 「무게」), 부품이 협력사를 참조한다."""
    vendor = _make_type(client, admin, label=f"협력사 {_hex()}", key_policy="optional")
    _make_property(client, admin, vendor, key="country", label="국가", data_type="text")
    _make_property(client, admin, vendor, key="w", label="무게", data_type="number", unit="kg")
    _make_property(client, admin, vendor, key="memo", label="메모", data_type="text")
    supplier = _make_type(client, admin, label=f"공급사 {_hex()}", key_policy="optional")
    _make_property(client, admin, supplier, key="country", label="국가", data_type="text")
    _make_property(
        client, admin, supplier, key="weight", label="무게", data_type="number", unit="kg"
    )
    part = _make_type(client, admin, label=f"부품 {_hex()}")
    _make_property(
        client,
        admin,
        part,
        key="maker",
        label="만든 곳",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    relation = f"rel_{_hex()}"
    made = client.post(
        "/api/ontology/relation-types",
        json={
            "slug": relation,
            "label": f"거래 {_hex()}",
            "src_type_slugs": [part],
            "dst_type_slugs": [vendor],
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    acme = _made(
        client,
        admin,
        vendor,
        label="에이컴",
        key=f"V-{_hex()}",
        properties={"country": "한국", "w": 3, "memo": "주"},
        aliases=["ACME"],
    )
    beta = _make_object(client, admin, vendor, label="베타", properties={"country": "미국"})
    bolt = _make_object(client, admin, part, label="볼트", properties={"maker": acme["id"]})
    linked = _link(client, admin, part, bolt["id"], relation, acme["id"])
    assert linked.status_code == 201, linked.text
    return {
        "vendor": vendor,
        "supplier": supplier,
        "part": part,
        "relation": relation,
        "acme": acme["id"],
        "acme_key": acme["key"],
        "beta": beta["id"],
        "bolt": bolt["id"],
    }


def _object(client: TestClient, admin: Signed, slug: str, oid: str) -> dict[str, Any]:
    got = client.get(f"/api/objects/{slug}/{oid}", headers=admin.headers)
    assert got.status_code == 200, got.text
    return dict(got.json()["object"])


def test_옮기면_칸_참조_관계_별칭이_따라오고_원_타입은_비고_이력은_대상의_키로_읽는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    w = _world(client, admin)
    vendor, supplier = w["vendor"], w["supplier"]
    changed = client.patch(
        f"/api/objects/{vendor}/{w['acme']}",
        json={"properties": {"w": 4}},
        headers=admin.headers,
    )
    assert changed.status_code == 200, changed.text
    before = db.scalar(select(ObjectInstance.updated_at).where(ObjectInstance.id == w["acme"]))

    plan = _move(client, admin, vendor, supplier)
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["errors"] == [] and body["applied"] is False and body["objects"] == 2, body
    fields = {one["source"]: (one["action"], one["target"]) for one in body["fields"]}
    assert fields == {
        "country": ("same", "country"),
        "w": ("map", "weight"),
        "memo": ("create", "memo"),
    }
    places = {one["what"]: one["count"] for one in body["places"]}
    assert places["이 타입을 가리키던 참조 속성(대상으로)"] == 1
    assert places["관계 종류의 끝(대상을 더함)"] == 1
    assert _object(client, admin, vendor, w["acme"])["label"] == "에이컴", "계획은 안 바꾼다"

    done = _move(client, admin, vendor, supplier, apply=True)
    assert done.status_code == 200, done.text
    assert done.json()["applied"] is True and done.json()["moved"] == 2
    acme = _object(client, admin, supplier, w["acme"])
    assert acme["properties"] == {"country": "한국", "weight": 4, "memo": "주"}
    db.expire_all()
    after = db.scalar(select(ObjectInstance.updated_at).where(ObjectInstance.id == w["acme"]))
    assert after == before
    kinds = client.get("/api/ontology/types", headers=admin.headers).json()
    supplier_id = next(one for one in kinds if one["slug"] == supplier)["id"]
    alias = db.scalars(select(ObjectAlias).where(ObjectAlias.object_id == w["acme"])).one()
    assert str(alias.type_id) == supplier_id, "별칭도 대상 타입의 것이 된다"
    listed = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()
    assert listed["total"] == 0, "원 타입은 빈 채로 남는다"
    props = client.get(f"/api/ontology/types/{w['part']}/properties", headers=admin.headers)
    maker = next(one for one in props.json() if one["key"] == "maker")
    assert maker["ref_type_slug"] == supplier
    relations = client.get("/api/ontology/relation-types", headers=admin.headers).json()
    ends = next(one for one in relations if one["slug"] == w["relation"])["dst_type_slugs"]
    assert supplier in ends
    bolt = _object(client, admin, w["part"], w["bolt"])
    assert bolt["properties"]["maker"] == w["acme"], "참조 칸은 id 로 그대로 이어진다"
    audit = db.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "ontology.type.move",
            AuditEntry.target_label == f"{vendor} → {supplier}",
        )
    ).one()
    assert audit.changes["keys"] == {"w": "weight"}

    history = client.get(f"/api/objects/{supplier}/{w['acme']}/history", headers=admin.headers)
    assert history.status_code == 200, history.text
    snapshots = [one["snapshot"] for one in history.json() if one.get("snapshot")]
    assert all("w" not in one["properties"] for one in snapshots), snapshots
    assert {one["properties"].get("weight") for one in snapshots} >= {3, 4}


def test_막는_것과_버림(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    vendor = w["vendor"]
    other = _make_type(client, admin, label=f"다른 곳 {_hex()}", key_policy="optional")
    _make_property(client, admin, other, key="country", label="국가", data_type="number")
    _make_property(
        client,
        admin,
        other,
        key="memo",
        label="메모",
        data_type="enum",
        enum_options=["가"],
    )
    body = _move(client, admin, vendor, other).json()
    notes = {one["source"]: one["note"] for one in body["fields"] if one["blocked"]}
    assert "모양이 다릅니다" in notes["country"]
    assert "모양이 다릅니다" in notes["memo"]
    assert body["errors"], body

    # 버림 — 사람이 적은 것만. 대상의 다른 키로도.
    dropped = _move(
        client, admin, vendor, other, mapping={"country": None, "memo": None}
    ).json()
    assert all(not one["blocked"] for one in dropped["fields"]), dropped
    actions = {one["source"]: one["action"] for one in dropped["fields"]}
    assert actions["country"] == "drop" and actions["memo"] == "drop"
    assert any("되돌릴 수 없습니다" in one["note"] for one in dropped["fields"])

    required = _make_type(client, admin, label=f"필수 식별자 {_hex()}", key_policy="required")
    keyless = _move(client, admin, vendor, required).json()
    assert any("식별자가 필수인데" in one for one in keyless["errors"]), keyless
    same = _move(client, admin, vendor, vendor).json()
    assert same["errors"] == ["같은 타입입니다."]


def test_겹치는_식별자는_병합하고_서로_다른_객체의_같은_별칭은_막는다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    vendor, supplier = w["vendor"], w["supplier"]
    twin = _made(
        client,
        admin,
        supplier,
        label="에이컴(공급사)",
        key=w["acme_key"],
        properties={"country": "한국"},
        aliases=["ACME"],
    )
    stranger = _made(client, admin, supplier, label="감마", aliases=["BETA-X"])
    refused = _move(client, admin, vendor, supplier).json()
    assert any("같은 식별자의 객체가 1건" in one for one in refused["errors"]), refused

    merged = _move(client, admin, vendor, supplier, merge_duplicates=True)
    assert merged.status_code == 200, merged.text
    assert merged.json()["errors"] == [] and merged.json()["duplicates"] == 1
    done = _move(client, admin, vendor, supplier, merge_duplicates=True, apply=True).json()
    assert done["applied"] is True and done["merged"] == 1, done
    # 부품의 참조 칸은 병합된 대상 객체를 가리킨다.
    bolt = _object(client, admin, w["part"], w["bolt"])
    assert bolt["properties"]["maker"] == twin["id"]

    # 서로 다른 객체가 같은 별칭 — 막는다.
    again = _make_type(client, admin, label=f"협력사2 {_hex()}", key_policy="optional")
    _made(client, admin, again, label="베타 X", aliases=["BETA-X"])
    clash = _move(client, admin, again, supplier).json()
    assert any("같은 별칭" in one for one in clash["errors"]), clash
    assert stranger["id"]


def test_많으면_작업으로_옮긴다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.ontology import move

    monkeypatch.setattr(move, "INLINE_OBJECTS", 1)
    w = _world(client, admin)
    vendor, supplier = w["vendor"], w["supplier"]
    big = _move(client, admin, vendor, supplier, apply=True)
    assert big.status_code == 409 and "ONTOLOGY-0106" in big.text, big.text
    job_path = big.json()["error"]["details"]["job_path"]
    made = client.post(job_path, json={"to": supplier}, headers=admin.headers)
    assert made.status_code == 202, made.text
    plan = finish_job(client, admin, made.json())
    assert plan["status"] == "done" and plan["result"]["applied"] is False, plan
    applied = client.post(f"/api/jobs/{plan['id']}/apply", headers=admin.headers)
    assert applied.status_code == 202, applied.text
    done = finish_job(client, admin, applied.json())
    assert done["status"] == "done" and done["result"]["moved"] == 2, done
    assert _object(client, admin, supplier, w["beta"])["label"] == "베타"
