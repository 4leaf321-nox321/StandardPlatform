"""속성 키 · 타입 slug 바꾸기(ADR 0093) — 닿는 곳을 세어 보이고 한 번에 바꾼다.

- 계획은 아무것도 안 바꾸고 자리마다 건수를 말한다. 적용은 정의 · 객체 값 · 화면 설정 · 저장된
  뷰의 주소 · 참조 속성의 대상 · 관계 종류의 끝까지 바꾸고, 스냅샷 · 감사 기록을 남긴다.
- 객체의 `updated_at` 은 그대로다. 이력은 바꾸기 전 기록을 새 키로 읽는다.
- 막는 것: 같은 이름 · 이미 있는 키 · 남은 저장값 · 문서 속성 · 확장이 쓰는 타입 · 원천 잠금.
- 값이 많으면 작업으로 — 지문이 같을 때만 적용.
- 옛 slug 로 열면 새 slug 를 말한다.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEntry
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import ObjectType
from app.shared import extensions as extension_points
from tests.api.conftest import Signed, finish_job
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _hex() -> str:
    return uuid.uuid4().hex[:10]


def _rename_key(
    client: TestClient, admin: Signed, slug: str, key: str, to: str, **body: Any
) -> Any:
    return client.post(
        f"/api/ontology/types/{slug}/properties/{key}/rename-key",
        json={"to": to, **body},
        headers=admin.headers,
    )


def _world(client: TestClient, admin: Signed) -> dict[str, str]:
    """공급사(국가) ← 부품(무게 · 공급사 참조), 부품 화면 설정 · 뷰 둘."""
    vendor = _make_type(client, admin, label=f"공급사 {_hex()}")
    _make_property(client, admin, vendor, key="country", label="국가", data_type="text")
    part = _make_type(client, admin, label=f"부품 {_hex()}")
    _make_property(client, admin, part, key="w", label="무게", data_type="number", unit="kg")
    _make_property(client, admin, part, key="memo", label="메모", data_type="text")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    patched = client.patch(
        f"/api/ontology/types/{part}",
        json={"list_view": {"columns": ["properties.w", "properties.memo"]}},
        headers=admin.headers,
    )
    assert patched.status_code == 200, patched.text
    acme = _make_object(client, admin, vendor, label="에이컴", properties={"country": "한국"})
    bolt = _make_object(
        client, admin, part, label="볼트", properties={"w": 3, "vendor": acme["id"]}
    )
    for name, conditions in (
        ("무거운 것", [{"field": "w", "op": "gte", "value": "2"}]),
        ("한국산", [{"field": "ref.vendor.country", "op": "eq", "value": "한국"}]),
    ):
        made = client.post(
            f"/api/objects/{part}/views",
            json={"name": name, "query": {"q": "", "conditions": conditions}},
            headers=admin.headers,
        )
        assert made.status_code == 201, made.text
    return {"vendor": vendor, "part": part, "bolt": bolt["id"], "acme": acme["id"]}


def _views(client: TestClient, admin: Signed, slug: str) -> dict[str, list[str]]:
    rows = client.get(f"/api/objects/{slug}/views", headers=admin.headers).json()
    return {
        one["name"]: [cond["field"] for cond in one["query"]["conditions"]] for one in rows
    }


def _object(client: TestClient, admin: Signed, slug: str, oid: str) -> dict[str, Any]:
    got = client.get(f"/api/objects/{slug}/{oid}", headers=admin.headers)
    assert got.status_code == 200, got.text
    return dict(got.json()["object"])


def test_속성_키를_바꾸면_값_설정_뷰의_주소가_함께_바뀌고_이력은_새_키로_읽는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    w = _world(client, admin)
    part, vendor = w["part"], w["vendor"]
    # 바꾸기 전에 값을 한 번 고친다 — 이력에 옛 키 「w」 로 남는다.
    changed = client.patch(
        f"/api/objects/{part}/{w['bolt']}",
        json={"properties": {"w": 4}},
        headers=admin.headers,
    )
    assert changed.status_code == 200, changed.text
    before = db.scalar(select(ObjectInstance.updated_at).where(ObjectInstance.id == w["bolt"]))

    plan = _rename_key(client, admin, part, "w", "weight")
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["applied"] is False and body["errors"] == [] and body["objects"] == 1
    places = {one["what"]: one["count"] for one in body["places"]}
    assert places["객체 값"] == 1 and places["속성 정의"] == 1
    assert places["저장된 뷰(조건 · 통계 기준)"] == 1
    assert places["타입 목록 화면 설정(열 · 정렬 · 트리 · 롤업)"] == 1
    assert not any(line.startswith("바꾼 뒤에도 깨집니다") for line in body["warnings"])
    # 계획은 아무것도 안 바꾼다.
    assert "w" in _object(client, admin, part, w["bolt"])["properties"]

    done = _rename_key(client, admin, part, "w", "weight", apply=True)
    assert done.status_code == 200, done.text
    assert done.json()["applied"] is True and done.json()["snapshot_id"]
    got = _object(client, admin, part, w["bolt"])
    assert got["properties"].get("weight") == 4 and "w" not in got["properties"]
    db.expire_all()
    after = db.scalar(select(ObjectInstance.updated_at).where(ObjectInstance.id == w["bolt"]))
    assert after == before, "값이 바뀐 것이 아니라 updated_at 은 그대로다"
    assert _views(client, admin, part)["무거운 것"] == ["weight"]
    kind = client.get("/api/ontology/types", headers=admin.headers).json()
    row = next(one for one in kind if one["slug"] == part)
    assert row["list_view"]["columns"] == ["properties.weight", "properties.memo"]
    audit = db.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "ontology.property.rename",
            AuditEntry.target_label == f"{part}.w",
        )
    ).one()
    assert audit.changes["from"] == "w" and audit.changes["to"] == "weight"

    # 이력 — 바꾸기 전 기록도 새 키로 읽는다(그때의 값 3 → 4).
    history = client.get(f"/api/objects/{part}/{w['bolt']}/history", headers=admin.headers)
    assert history.status_code == 200, history.text
    snapshots = [one["snapshot"] for one in history.json() if one.get("snapshot")]
    assert all("w" not in one["properties"] for one in snapshots), snapshots
    assert {one["properties"].get("weight") for one in snapshots} >= {3, 4}

    # 다른 타입의 칸을 지나는 주소도 — 공급사.country → nation 이면 부품 뷰의
    # ref.vendor.country 가 바뀐다.
    moved = _rename_key(client, admin, vendor, "country", "nation", apply=True)
    assert moved.status_code == 200, moved.text
    assert _views(client, admin, part)["한국산"] == ["ref.vendor.nation"]


def test_막는_것과_잘못된_이름(client: TestClient, admin: Signed, db: Session) -> None:
    w = _world(client, admin)
    part = w["part"]
    same = _rename_key(client, admin, part, "w", "w")
    assert same.status_code == 422 and "ONTOLOGY-0104" in same.text, same.text
    wrong = _rename_key(client, admin, part, "w", "Bad Key")
    assert wrong.status_code == 422, wrong.text

    taken = _rename_key(client, admin, part, "w", "memo").json()
    assert any("이미 키 「memo」" in one for one in taken["errors"]), taken
    # 예전에 삭제한 속성의 값이 남은 키 — 섞이지 않게 막는다.
    db.execute(
        update(ObjectInstance)
        .where(ObjectInstance.id == w["bolt"])
        .values(properties=ObjectInstance.properties.op("||")({"old_w": 1}))
    )
    db.commit()
    orphan = _rename_key(client, admin, part, "w", "old_w").json()
    assert any("값이 남은 객체가 1건" in one for one in orphan["errors"]), orphan
    refused = _rename_key(client, admin, part, "w", "old_w", apply=True).json()
    assert refused["applied"] is False

    doc = _make_type(client, admin, label=f"문서 {_hex()}")
    _make_property(client, admin, doc, key="body", label="본문", data_type="document")
    blocked = _rename_key(client, admin, doc, "body", "text").json()
    assert any("문서 · 파일 속성" in one for one in blocked["errors"]), blocked

    db.execute(update(ObjectType).where(ObjectType.slug == part).values(managed_by="relay-r"))
    db.commit()
    locked = _rename_key(client, admin, part, "memo", "note").json()
    assert any("relay-r 가 관리하는 정의" in one for one in locked["errors"]), locked


def test_확장이_쓰는_타입은_막는다(client: TestClient, admin: Signed) -> None:
    held = _make_type(client, admin, label=f"확장 타입 {_hex()}")
    _make_property(client, admin, held, key="tools", label="도구", data_type="text")

    def guard(_db: Session) -> dict[str, str]:
        return {held: "확장 시험이 이 타입을 씁니다."}

    extension_points.register_type_guard(guard)
    try:
        key = _rename_key(client, admin, held, "tools", "tool_list").json()
        slug = client.post(
            f"/api/ontology/types/{held}/rename",
            json={"to": f"x_{_hex()}"},
            headers=admin.headers,
        ).json()
    finally:
        extension_points._type_guards.remove(guard)
    assert any("확장 시험이 이 타입을 씁니다." in one for one in key["errors"]), key
    assert any("확장 시험이 이 타입을 씁니다." in one for one in slug["errors"]), slug


def test_값이_많으면_작업으로_계획하고_적용한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.ontology import rename

    monkeypatch.setattr(rename, "INLINE_OBJECTS", 0)
    w = _world(client, admin)
    part = w["part"]
    inline = _rename_key(client, admin, part, "memo", "note", apply=True)
    # 값이 있는 객체가 없어도 문턱(0)을 못 넘는 것은 아니다 — memo 는 값이 없다.
    assert inline.status_code == 200, inline.text
    big = _rename_key(client, admin, part, "w", "weight", apply=True)
    assert big.status_code == 409 and "ONTOLOGY-0105" in big.text, big.text
    job_path = big.json()["error"]["details"]["job_path"]
    made = client.post(job_path, json={"to": "weight"}, headers=admin.headers)
    assert made.status_code == 202, made.text
    plan = finish_job(client, admin, made.json())
    assert plan["status"] == "done" and plan["result"]["applied"] is False, plan
    assert plan["result"]["fingerprint"]
    applied = client.post(f"/api/jobs/{plan['id']}/apply", headers=admin.headers)
    assert applied.status_code == 202, applied.text
    done = finish_job(client, admin, applied.json())
    assert done["status"] == "done" and done["result"]["applied"] is True, done
    assert _object(client, admin, part, w["bolt"])["properties"].get("weight") == 3


def test_타입_slug_를_바꾸면_참조_관계_끝_주소가_함께_바뀌고_옛_주소는_새_slug_를_말한다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    w = _world(client, admin)
    part, vendor = w["part"], w["vendor"]
    relation = f"supplies_{_hex()}"
    made = client.post(
        "/api/ontology/relation-types",
        json={
            "slug": relation,
            "label": f"공급 {_hex()}",
            "src_type_slugs": [vendor],
            "dst_type_slugs": [part],
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    # 공급사 쪽에서 「이 공급사를 가리키는 부품」 으로 거르는 뷰 — 주소에 부품 slug 가 든다.
    view = client.post(
        f"/api/objects/{vendor}/views",
        json={
            "name": "부품 있음",
            "query": {
                "q": "",
                "conditions": [{"field": f"in.{part}:vendor", "op": "notempty"}],
            },
        },
        headers=admin.headers,
    )
    assert view.status_code == 201, view.text

    to = f"component_{_hex()}"
    plan = client.post(
        f"/api/ontology/types/{part}/rename", json={"to": to}, headers=admin.headers
    ).json()
    assert plan["errors"] == [] and plan["applied"] is False, plan
    places = {one["what"]: one["count"] for one in plan["places"]}
    assert places["관계 종류의 끝"] == 1 and places["저장된 뷰(조건 · 통계 기준)"] == 1

    done = client.post(
        f"/api/ontology/types/{part}/rename",
        json={"to": to, "apply": True},
        headers=admin.headers,
    )
    assert done.status_code == 200, done.text
    assert _object(client, admin, to, w["bolt"])["label"] == "볼트"
    kinds = client.get("/api/ontology/relation-types", headers=admin.headers).json()
    assert next(one for one in kinds if one["slug"] == relation)["dst_type_slugs"] == [to]
    assert _views(client, admin, vendor)["부품 있음"] == [f"in.{to}:vendor"]

    old = client.get(f"/api/objects/{part}", headers=admin.headers)
    assert old.status_code == 404 and to in old.text, old.text
    assert old.json()["error"]["details"]["renamed_to"] == to

    taken = client.post(
        f"/api/ontology/types/{to}/rename", json={"to": vendor}, headers=admin.headers
    ).json()
    assert any("이미 「" in one for one in taken["errors"]), taken
    reserved = client.post(
        f"/api/ontology/types/{to}/rename", json={"to": "home"}, headers=admin.headers
    )
    assert reserved.status_code in (409, 422), reserved.text


def test_개정_칸의_키를_바꿔도_계보_선은_낡지_않고_낡은_것은_그대로다(
    client: TestClient, admin: Signed
) -> None:
    """개정 칸이면 복사 → 개정 칸 이름 변경 → 옛 키 삭제 — 트리거가 앞뒤 값을 견줘도 같다."""
    from tests.api.conftest import clock_settled
    from tests.api.test_lineage import _chain, _lineage, _patch
    from tests.api.test_ontology import _link

    world = _chain(client, admin)
    fresh = _make_object(client, admin, world.cad, label="브래킷", properties={"rev": "B"})
    stale = _make_object(client, admin, world.cad, label="커버", properties={"rev": "A"})
    meshes = {}
    for name, source in (("M-1", fresh), ("M-2", stale)):
        meshes[name] = _make_object(client, admin, world.mesh, label=name)
        made = _link(client, admin, world.cad, source["id"], world.made, meshes[name]["id"])
        assert made.status_code == 201, made.text
    clock_settled()
    _patch(client, admin, world.cad, stale["id"], properties={"rev": "A2"})
    assert _lineage(client, admin, world.mesh, meshes["M-2"]["id"])["state"] == "direct"

    done = _rename_key(client, admin, world.cad, "rev", "revision", apply=True)
    assert done.status_code == 200, done.text
    kinds = client.get("/api/ontology/types", headers=admin.headers).json()
    assert next(one for one in kinds if one["slug"] == world.cad)["revision_key"] == "revision"
    assert _lineage(client, admin, world.mesh, meshes["M-1"]["id"])["state"] == "fresh"
    assert _lineage(client, admin, world.mesh, meshes["M-2"]["id"])["state"] == "direct"
    # 바꾼 뒤의 개정 변경은 새 키로 낡는다.
    clock_settled()
    _patch(client, admin, world.cad, fresh["id"], properties={"revision": "C"})
    assert _lineage(client, admin, world.mesh, meshes["M-1"]["id"])["state"] == "direct"
