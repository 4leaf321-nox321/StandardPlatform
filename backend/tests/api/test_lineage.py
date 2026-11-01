"""파생 · 계보 관계와 개정 고정 — 낡은 파생물 알림(ADR 0041).

    도면(개정 칸 rev) ──만든 것──▶ 메시 ◀──사용한 메시── 결과
    (만든 것: 원천 끝이 출발)          (사용한 메시: 원천 끝이 도착 — 두 방향을 섞는다)

선을 맺는 순간 원천의 지금 개정(개정 칸이 있으면)과 원천의 바뀐 때를 선에 박는다(DB 트리거 —
넣는 길이 열 곳이 넘는다). 원천이 고정 뒤에 바뀌면 그 파생물이 **바로 낡음**, 그 아래는
**위에서 낡음**이다. 판정은 `objects/lineage.py` 한 곳(식은 DB 함수 `sp_lineage_stale`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectInstance, ObjectRelation
from tests.api.conftest import Signed, clock_settled
from tests.api.test_ontology import _link, _make_object, _make_property, _make_type


def _uniq(base: str) -> str:
    return f"{base}_{uuid.uuid4().hex[:10]}"


def _kind(
    client: TestClient,
    admin: Signed,
    *,
    source: str = "src",
    label: str = "만든 것",
    **kw: Any,
) -> str:
    slug = _uniq("made")
    got = client.post(
        "/api/ontology/relation-types",
        json={"slug": slug, "label": label, "lineage_source": source, **kw},
        headers=admin.headers,
    )
    assert got.status_code == 201, got.text
    assert got.json()["lineage_source"] == source
    return slug


@dataclass(frozen=True)
class Chain:
    cad: str
    mesh: str
    result: str
    made: str
    """원천이 출발인 계보 종류 — 도면 → 메시."""
    used_by: str
    """원천이 도착인 계보 종류 — 결과 → 메시(「결과가 메시를 썼다」)."""


def _chain(client: TestClient, admin: Signed) -> Chain:
    cad = _make_type(client, admin, "cad", label="도면", key_policy="optional")
    _make_property(client, admin, cad, key="rev", label="개정", data_type="text")
    _make_property(client, admin, cad, key="memo", label="메모", data_type="text")
    got = client.patch(
        f"/api/ontology/types/{cad}", json={"revision_key": "rev"}, headers=admin.headers
    )
    assert got.status_code == 200, got.text
    assert got.json()["revision_key"] == "rev"
    mesh = _make_type(client, admin, "mesh", label="메시", key_policy="optional")
    _make_property(client, admin, mesh, key="memo", label="메모", data_type="text")
    result = _make_type(client, admin, "result", label="결과", key_policy="optional")
    made = _kind(
        client,
        admin,
        source="src",
        label="만든 것",
        src_type_slugs=[cad],
        dst_type_slugs=[mesh],
    )
    used_by = _kind(
        client,
        admin,
        source="dst",
        label="사용한 메시",
        src_type_slugs=[result],
        dst_type_slugs=[mesh],
    )
    return Chain(cad=cad, mesh=mesh, result=result, made=made, used_by=used_by)


def _patch(client: TestClient, who: Signed, type_slug: str, object_id: str, **kw: Any) -> None:
    got = client.patch(f"/api/objects/{type_slug}/{object_id}", json=kw, headers=who.headers)
    assert got.status_code == 200, got.text


def _lineage(
    client: TestClient, who: Signed, type_slug: str, object_id: str
) -> dict[str, Any]:
    got = client.get(f"/api/objects/{type_slug}/{object_id}/lineage", headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _edge(db: Session, relation_id: str) -> ObjectRelation:
    db.expire_all()
    found = db.get(ObjectRelation, uuid.UUID(relation_id))
    assert found is not None
    return found


def test_맺을_때_원천의_개정과_바뀐_때를_박는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    world = _chain(client, admin)
    cad = _make_object(client, admin, world.cad, label="브래킷", properties={"rev": "B"})
    mesh = _make_object(client, admin, world.mesh, label="M-12")
    result = _make_object(client, admin, world.result, label="R-7")

    # 개정 칸 있음 — 개정과 바뀐 때.
    first = _link(client, admin, world.cad, cad["id"], world.made, mesh["id"])
    assert first.status_code == 201, first.text
    edge = _edge(db, first.json()["relation_id"])
    source = db.get(ObjectInstance, uuid.UUID(cad["id"]))
    assert source is not None
    assert edge.pinned_revision == "B"
    assert edge.pinned_at == source.updated_at

    # 원천이 도착인 종류 — 결과 → 메시. 메시는 개정 칸이 없어 바뀐 때만.
    second = _link(client, admin, world.result, result["id"], world.used_by, mesh["id"])
    assert second.status_code == 201, second.text
    edge = _edge(db, second.json()["relation_id"])
    mesh_row = db.get(ObjectInstance, uuid.UUID(mesh["id"]))
    assert mesh_row is not None
    assert edge.pinned_revision is None
    assert edge.pinned_at == mesh_row.updated_at

    # 맺기만 해서는 아무것도 안 낡는다(원천의 바뀐 때가 오르지 않는다).
    for type_slug, row in ((world.mesh, mesh), (world.result, result)):
        seen = _lineage(client, admin, type_slug, row["id"])
        assert seen["state"] == "fresh", seen


def test_개정이_바뀌면_바로_낡고_그_아래는_위에서_낡는다(
    client: TestClient, admin: Signed
) -> None:
    world = _chain(client, admin)
    cad = _make_object(client, admin, world.cad, label="브래킷", properties={"rev": "B"})
    mesh = _make_object(client, admin, world.mesh, label="M-12")
    result = _make_object(client, admin, world.result, label="R-7")
    assert (
        _link(client, admin, world.cad, cad["id"], world.made, mesh["id"]).status_code == 201
    )
    assert (
        _link(client, admin, world.result, result["id"], world.used_by, mesh["id"]).status_code
        == 201
    )

    # 개정 칸이 아닌 칸만 고치면 안 낡는다.
    clock_settled()
    _patch(client, admin, world.cad, cad["id"], properties={"memo": "주석"})
    assert _lineage(client, admin, world.mesh, mesh["id"])["state"] == "fresh"

    clock_settled()
    _patch(client, admin, world.cad, cad["id"], properties={"rev": "C"})
    seen = _lineage(client, admin, world.mesh, mesh["id"])
    assert seen["state"] == "direct", seen
    assert any("B → C" in one for one in seen["reasons"]), seen["reasons"]
    below = _lineage(client, admin, world.result, result["id"])
    assert below["state"] == "upstream", below
    assert below["up"][0]["source"]["state"] == "direct"

    # 원천의 영향 범위 — 이것으로 만든 것 둘(메시 · 결과), 둘 다 낡음.
    top = _lineage(client, admin, world.cad, cad["id"])
    assert top["impact"] == {"derived": 2, "stale": 2}

    # 목록 「낡은 것만」 · 낡음 표시.
    listed = client.get(
        f"/api/objects/{world.result}", params={"f.lineage.eq": "stale"}, headers=admin.headers
    )
    assert listed.status_code == 200, listed.text
    assert [one["id"] for one in listed.json()["items"]] == [result["id"]]
    assert listed.json()["lineage"] == {result["id"]: "upstream"}
    direct_only = client.get(
        f"/api/objects/{world.result}",
        params={"f.lineage.eq": "direct"},
        headers=admin.headers,
    )
    assert direct_only.json()["total"] == 0


def _world(client: TestClient, admin: Signed) -> tuple[Chain, dict[str, dict[str, Any]]]:
    """도면 C(B) → 메시 M → 결과 R 하나씩 — 결과 → 메시는 원천이 도착인 종류."""
    world = _chain(client, admin)
    cad = _make_object(client, admin, world.cad, label="브래킷", properties={"rev": "B"})
    mesh = _make_object(client, admin, world.mesh, label="M-12")
    result = _make_object(client, admin, world.result, label="R-7")
    assert (
        _link(client, admin, world.cad, cad["id"], world.made, mesh["id"]).status_code == 201
    )
    assert (
        _link(client, admin, world.result, result["id"], world.used_by, mesh["id"]).status_code
        == 201
    )
    return world, {"cad": cad, "mesh": mesh, "result": result}


def _repin(client: TestClient, who: Signed, type_slug: str, object_id: str, **kw: Any) -> Any:
    return client.post(
        f"/api/objects/{type_slug}/{object_id}/lineage/repin", json=kw, headers=who.headers
    )


def test_다시_고정은_풀고_감사에_남고_위_낡음은_그대로다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    world, ids = _world(client, admin)
    clock_settled()
    _patch(client, admin, world.cad, ids["cad"]["id"], properties={"rev": "C"})

    # 결과를 다시 고정해도 원천(메시)은 안 바뀐다 — 위에서 낡음은 그대로.
    done = _repin(client, admin, world.result, ids["result"]["id"], basis="checked")
    assert done.status_code == 200, done.text
    assert done.json()["changed"] == []
    assert done.json()["lineage"]["state"] == "upstream"

    # 메시를 다시 고정하면 메시와 그 아래가 함께 풀린다.
    done = _repin(
        client, admin, world.mesh, ids["mesh"]["id"], basis="checked", note="치수 변화 없음"
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert [
        (one["before"]["revision"], one["after"]["revision"]) for one in body["changed"]
    ] == [("B", "C")]
    assert body["unchanged"] == 0
    assert body["lineage"]["state"] == "fresh"
    assert _lineage(client, admin, world.result, ids["result"]["id"])["state"] == "fresh"

    # 감사 — 선마다 `lineage.repin`, 앞뒤 개정과 근거. 양 끝의 이력에 선다.
    from app.modules.audit.models import AuditEntry

    entry = db.scalar(
        select(AuditEntry)
        .where(AuditEntry.action == "lineage.repin")
        .where(AuditEntry.target_id == uuid.UUID(body["changed"][0]["relation_id"]))
    )
    assert entry is not None
    assert entry.changes["changed"]["pinned_revision"] == {"before": "B", "after": "C"}
    assert "영향 없음 확인" in (entry.reason or "") and "치수 변화 없음" in (
        entry.reason or ""
    )
    for type_slug, row in ((world.mesh, ids["mesh"]), (world.cad, ids["cad"])):
        history = client.get(
            f"/api/objects/{type_slug}/{row['id']}/history", headers=admin.headers
        ).json()
        assert any(one["action"] == "lineage.repin" for one in history), type_slug

    # 원천을 고쳐 다시 만들면(메시 수정) 그 아래가 바로 낡음 — 메시는 개정 칸이 없어 바뀐 때로.
    clock_settled()
    _patch(client, admin, world.mesh, ids["mesh"]["id"], properties={"memo": "다시 만듦"})
    seen = _lineage(client, admin, world.result, ids["result"]["id"])
    assert seen["state"] == "direct", seen
    assert any("고정 뒤에 수정됨" in one for one in seen["reasons"]), seen["reasons"]


def test_다시_고정은_파생물을_고칠_수_있는_사람만(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    world, ids = _world(client, admin)
    clock_settled()
    _patch(client, admin, world.cad, ids["cad"]["id"], properties={"rev": "C"})
    denied = _repin(client, member, world.mesh, ids["mesh"]["id"], basis="checked")
    assert denied.status_code == 403, denied.text
    # 이 객체의 원천 선이 아닌 것은 고를 수 없다.
    stranger = _repin(
        client, admin, world.mesh, ids["mesh"]["id"], relation_ids=[str(uuid.uuid4())]
    )
    assert stranger.status_code == 404, stranger.text


def test_관계를_맺기만_해서는_원천의_바뀐_때가_안_오르고_안_낡는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """개정 칸이 없는 원천(메시)은 바뀐 때로 판정한다 — 관계를 맺거나 끊는 일이 원천 객체의
    행을 건드리면 그 아래가 모두 거짓 「낡음」 이 된다."""
    world, ids = _world(client, admin)
    mesh_id = uuid.UUID(ids["mesh"]["id"])
    db.expire_all()
    before = db.get(ObjectInstance, mesh_id)
    assert before is not None
    stamp = before.updated_at
    other = _make_object(client, admin, world.result, label="R-8")
    plain = client.post(
        "/api/ontology/relation-types",
        json={"slug": _uniq("plain"), "label": "참고"},
        headers=admin.headers,
    ).json()["slug"]
    clock_settled()
    # 원천에서 나가는 다른 계보 선 · 계보 아닌 선 · 관계 파일 일괄 입력(같은 선 = 바뀐 것
    # 없음).
    assert (
        _link(client, admin, world.result, other["id"], world.used_by, ids["mesh"]["id"])
    ).status_code == 201
    assert (
        _link(client, admin, world.mesh, ids["mesh"]["id"], plain, other["id"]).status_code
        == 201
    )
    again = client.post(
        f"/api/objects/{world.cad}/relations/import-rows",
        json={
            "rows": [
                {
                    "src": ids["cad"]["label"],
                    "relation": world.made,
                    "dst": ids["mesh"]["label"],
                }
            ],
            "apply": True,
        },
        headers=admin.headers,
    )
    assert again.status_code == 200, again.text
    # 객체 일괄 입력도 — 바뀐 것 없는 줄은 바뀐 때를 올리지 않는다.
    rows = client.post(
        f"/api/objects/{world.mesh}/import-rows",
        json={"rows": [{"label": "M-12"}], "apply": True, "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert rows.status_code == 200, rows.text
    db.expire_all()
    after = db.get(ObjectInstance, mesh_id)
    assert after is not None
    assert after.updated_at == stamp
    assert _lineage(client, admin, world.result, ids["result"]["id"])["state"] == "fresh"


def test_관계_파일로_맺어도_고정된다(client: TestClient, admin: Signed, db: Session) -> None:
    world = _chain(client, admin)
    cad = _make_object(client, admin, world.cad, label="CAD-F", properties={"rev": "R1"})
    mesh = _make_object(client, admin, world.mesh, label="MESH-F")
    done = client.post(
        f"/api/objects/{world.cad}/relations/import-rows",
        json={
            "rows": [{"src": "CAD-F", "relation": world.made, "dst": "MESH-F"}],
            "apply": True,
        },
        headers=admin.headers,
    )
    assert done.status_code == 200, done.text
    db.expire_all()
    edge = db.scalar(
        select(ObjectRelation).where(
            ObjectRelation.src_object_id == uuid.UUID(cad["id"]),
            ObjectRelation.dst_object_id == uuid.UUID(mesh["id"]),
        )
    )
    assert edge is not None
    assert edge.pinned_revision == "R1"
    assert edge.pinned_at is not None


def test_묶음은_박힌_고정을_그대로_싣고_되돌리기는_그대로_되살린다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    from tests.api.conftest import bundle_export, bundle_import, finish_job

    world, ids = _world(client, admin)
    clock_settled()
    _patch(client, admin, world.cad, ids["cad"]["id"], properties={"rev": "C"})

    # 묶음 내보내기 — 계보 선의 고정과 판정이 실린다(개정 칸이 없는 선은 낡음 표시).
    group = client.post(
        "/api/ontology/groups",
        json={"slug": _uniq("grp"), "label": "계보 묶음"},
        headers=admin.headers,
    ).json()["slug"]
    for slug in (world.cad, world.mesh, world.result):
        assert (
            client.patch(
                f"/api/ontology/types/{slug}",
                json={"nav_group_slug": group},
                headers=admin.headers,
            ).status_code
            == 200
        )
    sent = bundle_export(client, admin, group)
    rows = [row for batch in sent["relations"] for row in batch["rows"]]
    made = next(row for row in rows if row["relation"] == world.made)
    assert made["pinned_revision"] == "B" and made.get("pinned_stale") is True
    assert (
        next(t for t in sent["ontology"]["types"] if t["slug"] == world.cad)["revision_key"]
        == "rev"
    )
    assert (
        next(r for r in sent["ontology"]["relation_types"] if r["slug"] == world.used_by)[
            "lineage_source"
        ]
        == "dst"
    )

    # 선을 끊고 그 묶음 줄로 다시 이으면 — 새로 박지 않는다(B 그대로, 낡음 그대로).
    edge_id = _lineage(client, admin, world.mesh, ids["mesh"]["id"])["up"][0]["relation_id"]
    gone = client.delete(
        f"/api/objects/{world.cad}/{ids['cad']['id']}/relations/{edge_id}",
        headers=admin.headers,
    )
    assert gone.status_code == 204, gone.text
    loaded = bundle_import(
        client,
        admin,
        {"relations": [{"type_slug": world.cad, "rows": [made]}], "apply": True},
    )
    assert loaded["applied"] is True, loaded
    seen = _lineage(client, admin, world.mesh, ids["mesh"]["id"])
    assert seen["state"] == "direct", seen
    assert seen["up"][0]["pinned_revision"] == "B"

    # 묶음 「맞춤」 이 그 선을 끊고(다른 메시로 바꿈), 그 판을 되돌리면 — 끊을 때의
    # 고정(B)으로.
    _make_object(client, admin, world.mesh, label="M-99")
    cut = bundle_import(
        client,
        admin,
        {
            "relations": [
                {
                    "type_slug": world.cad,
                    "mode": "replace",
                    "rows": [
                        {"src": ids["cad"]["label"], "relation": world.made, "dst": "M-99"}
                    ],
                }
            ],
            "apply": True,
        },
    )
    assert cut["applied"] is True, cut
    assert _lineage(client, admin, world.mesh, ids["mesh"]["id"])["up"] == [], cut
    started = client.post(
        f"/api/bundles/runs/{cut['run_id']}/undo",
        params={"apply": True},
        headers=admin.headers,
    )
    assert started.status_code == 202, started.text
    assert finish_job(client, admin, started.json())["status"] == "done"
    seen = _lineage(client, admin, world.mesh, ids["mesh"]["id"])
    assert seen["up"] and seen["up"][0]["pinned_revision"] == "B", seen
    assert seen["state"] == "direct"


def test_묶음의_시각_고정은_이_설치의_시계로_판정을_옮긴다(
    client: TestClient, admin: Signed
) -> None:
    """개정 칸이 없는 원천의 고정 시각은 그 설치의 시계다 — 허브의 시각을 그대로 두면 받는 쪽
    원천의 바뀐 때(가져온 때)가 늘 뒤라 모든 선이 낡음이 된다. 묶음은 판정(`pinned_stale`)을
    싣고, 받는 쪽은 그 판정을 옮긴다."""
    from tests.api.conftest import bundle_import

    world, ids = _world(client, admin)
    other = _make_object(client, admin, world.result, label="R-OLD")
    old = "2001-01-01T00:00:00+00:00"
    loaded = bundle_import(
        client,
        admin,
        {
            "relations": [
                {
                    "type_slug": world.result,
                    "rows": [
                        # 허브에서 낡지 않았던 선 — 옛 시각이어도 낡지 않음.
                        {
                            "src": "R-OLD",
                            "relation": world.used_by,
                            "dst": "M-12",
                            "pinned_at": old,
                        },
                    ],
                }
            ],
            "apply": True,
        },
    )
    assert loaded["applied"] is True, loaded
    assert _lineage(client, admin, world.result, other["id"])["state"] == "fresh"
    # 허브에서 낡았던 선 — 받는 쪽에서도 낡음.
    third = _make_object(client, admin, world.result, label="R-STALE")
    loaded = bundle_import(
        client,
        admin,
        {
            "relations": [
                {
                    "type_slug": world.result,
                    "rows": [
                        {
                            "src": "R-STALE",
                            "relation": world.used_by,
                            "dst": "M-12",
                            "pinned_at": old,
                            "pinned_stale": True,
                        },
                    ],
                }
            ],
            "apply": True,
        },
    )
    assert loaded["applied"] is True, loaded
    assert _lineage(client, admin, world.result, third["id"])["state"] == "direct"
    assert ids["result"]


def test_개정_칸_검사(client: TestClient, admin: Signed) -> None:
    world = _chain(client, admin)
    _make_property(client, admin, world.cad, key="due", label="기한", data_type="date")
    _make_property(
        client, admin, world.cad, key="tags", label="꼬리표", data_type="text", multi=True
    )

    def set_key(key: str) -> Any:
        return client.patch(
            f"/api/ontology/types/{world.cad}",
            json={"revision_key": key},
            headers=admin.headers,
        )

    for key in ("nope", "due", "tags"):
        got = set_key(key)
        assert got.status_code == 422, (key, got.text)
        assert got.json()["error"]["code"].endswith("ONTOLOGY-0098")

    # 쓰이는 개정 칸은 삭제 · 여러 값 · 종류 변경 못 한다.
    gone = client.delete(
        f"/api/ontology/types/{world.cad}/properties/rev", headers=admin.headers
    )
    assert gone.status_code == 422, gone.text
    assert gone.json()["error"]["code"].endswith("ONTOLOGY-0099")
    multi = client.patch(
        f"/api/ontology/types/{world.cad}/properties/rev",
        json={"label": "개정", "data_type": "text", "multi": True},
        headers=admin.headers,
    )
    assert multi.status_code == 422, multi.text
    retype = client.post(
        f"/api/ontology/types/{world.cad}/properties/rev/retype",
        json={"data_type": "date"},
        headers=admin.headers,
    )
    assert retype.status_code == 422, retype.text

    # 정의 가져오기도 같은 검사 — 계획의 오류로.
    plan = client.post(
        "/api/ontology/import",
        json={
            "types": [
                {
                    "slug": world.cad,
                    "label": "도면",
                    "properties": [{"key": "rev", "label": "개정", "data_type": "date"}],
                }
            ]
        },
        params={"dry_run": "true"},
        headers=admin.headers,
    )
    assert plan.status_code in (200, 422), plan.text
    said = plan.text
    assert "개정 칸" in said, said
    wrong = client.post(
        "/api/ontology/import",
        json={"types": [{"slug": world.mesh, "label": "메시", "revision_key": "nope"}]},
        params={"dry_run": "true"},
        headers=admin.headers,
    )
    assert "개정 칸" in wrong.text, wrong.text

    # 비우면 된다(그 뒤에는 지울 수 있다).
    assert set_key("").status_code == 200


def test_원천_끝은_방향_있는_종류만(client: TestClient, admin: Signed) -> None:
    refused = client.post(
        "/api/ontology/relation-types",
        json={
            "slug": _uniq("sim"),
            "label": "비슷함",
            "directed": False,
            "lineage_source": "src",
        },
        headers=admin.headers,
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"].endswith("ONTOLOGY-0097")
    kind = _kind(client, admin, source="src")
    undirected = client.patch(
        f"/api/ontology/relation-types/{kind}", json={"directed": False}, headers=admin.headers
    )
    assert undirected.status_code == 422, undirected.text
    odd = client.patch(
        f"/api/ontology/relation-types/{kind}",
        json={"lineage_source": "both"},
        headers=admin.headers,
    )
    assert odd.status_code == 422, odd.text


def test_계보를_켜거나_끝을_바꾸면_있는_선을_지금_값으로_고정한다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    world = _chain(client, admin)
    cad = _make_object(client, admin, world.cad, label="CAD-K", properties={"rev": "K1"})
    mesh = _make_object(client, admin, world.mesh, label="MESH-K")
    plain = client.post(
        "/api/ontology/relation-types",
        json={"slug": _uniq("later"), "label": "나중에 계보"},
        headers=admin.headers,
    ).json()["slug"]
    edge_id = _link(client, admin, world.cad, cad["id"], plain, mesh["id"]).json()[
        "relation_id"
    ]
    assert _edge(db, edge_id).pinned_at is None
    on = client.patch(
        f"/api/ontology/relation-types/{plain}",
        json={"lineage_source": "src"},
        headers=admin.headers,
    )
    assert on.status_code == 200, on.text
    assert _edge(db, edge_id).pinned_revision == "K1"
    # 끝을 바꾸면 그 끝(메시 — 개정 칸 없음)으로 다시.
    flip = client.patch(
        f"/api/ontology/relation-types/{plain}",
        json={"lineage_source": "dst"},
        headers=admin.headers,
    )
    assert flip.status_code == 200, flip.text
    edge = _edge(db, edge_id)
    assert edge.pinned_revision is None and edge.pinned_at is not None
    off = client.patch(
        f"/api/ontology/relation-types/{plain}",
        json={"lineage_source": ""},
        headers=admin.headers,
    )
    assert off.status_code == 200, off.text
    edge = _edge(db, edge_id)
    assert edge.pinned_revision is None and edge.pinned_at is None


def test_개정_칸을_바꾸면_판정을_지키며_고정을_옮긴다(
    client: TestClient, admin: Signed
) -> None:
    """개정 칸을 새로 정하면 그때까지 바뀐 때로 박힌 선(고정 개정 없음)이 전부 「개정이 다름」
    으로 낡아 보인다 — 트리거가 낡지 않았던 선은 새 칸의 지금 값으로, 낡았던 선은 낡은 채로
    옮긴다."""
    world = _chain(client, admin)
    _make_property(client, admin, world.mesh, key="ver", label="판", data_type="number")
    fresh_mesh = _make_object(client, admin, world.mesh, label="MV-1", properties={"ver": 1})
    stale_mesh = _make_object(client, admin, world.mesh, label="MV-2", properties={"ver": 1})
    fresh = _make_object(client, admin, world.result, label="RV-1")
    stale = _make_object(client, admin, world.result, label="RV-2")
    for result, mesh in ((fresh, fresh_mesh), (stale, stale_mesh)):
        assert (
            _link(
                client, admin, world.result, result["id"], world.used_by, mesh["id"]
            ).status_code
            == 201
        )
    clock_settled()
    _patch(client, admin, world.mesh, stale_mesh["id"], properties={"memo": "고침"})
    assert _lineage(client, admin, world.result, stale["id"])["state"] == "direct"

    got = client.patch(
        f"/api/ontology/types/{world.mesh}",
        json={"revision_key": "ver"},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    first = _lineage(client, admin, world.result, fresh["id"])
    assert first["state"] == "fresh", first
    assert first["up"][0]["pinned_revision"] == "1"
    assert _lineage(client, admin, world.result, stale["id"])["state"] == "direct"
    # 이제 개정 칸으로 판정한다 — 개정 아닌 칸을 고쳐도 안 낡고, 판을 올리면 낡는다.
    clock_settled()
    _patch(client, admin, world.mesh, fresh_mesh["id"], properties={"memo": "주석"})
    assert _lineage(client, admin, world.result, fresh["id"])["state"] == "fresh"
    _patch(client, admin, world.mesh, fresh_mesh["id"], properties={"ver": 2})
    seen = _lineage(client, admin, world.result, fresh["id"])
    assert seen["state"] == "direct" and any("1 → 2" in one for one in seen["reasons"])

    # 개정 칸을 비우면 바뀐 때로 돌아가되 판정은 그대로.
    assert (
        client.patch(
            f"/api/ontology/types/{world.mesh}",
            json={"revision_key": ""},
            headers=admin.headers,
        ).status_code
        == 200
    )
    assert _lineage(client, admin, world.result, fresh["id"])["state"] == "direct"
    assert _lineage(client, admin, world.result, stale["id"])["state"] == "direct"


def test_지운_원천은_치지_않는다(client: TestClient, admin: Signed) -> None:
    world, ids = _world(client, admin)
    clock_settled()
    _patch(client, admin, world.cad, ids["cad"]["id"], properties={"rev": "C"})
    assert _lineage(client, admin, world.mesh, ids["mesh"]["id"])["state"] == "direct"
    from app.database import SessionLocal

    with SessionLocal() as other:
        row = other.get(ObjectInstance, uuid.UUID(ids["cad"]["id"]))
        assert row is not None
        from datetime import UTC, datetime

        row.deleted_at = datetime.now(UTC)
        other.commit()
    seen = _lineage(client, admin, world.mesh, ids["mesh"]["id"])
    assert seen["state"] == "fresh", seen
    assert seen["up"] == []
    assert _lineage(client, admin, world.result, ids["result"]["id"])["state"] == "fresh"


def test_유효_기간이_끝난_계보_선은_치지_않는다(client: TestClient, admin: Signed) -> None:
    world = _chain(client, admin)
    dated = _kind(client, admin, source="src", effectivity=True)
    cad = _make_object(client, admin, world.cad, label="CAD-P", properties={"rev": "P1"})
    mesh = _make_object(client, admin, world.mesh, label="MESH-P")
    made = _link(client, admin, world.cad, cad["id"], dated, mesh["id"], valid_to="2001-01-01")
    assert made.status_code == 201, made.text
    clock_settled()
    _patch(client, admin, world.cad, cad["id"], properties={"rev": "P2"})
    assert _lineage(client, admin, world.mesh, mesh["id"])["state"] == "fresh"


def test_두_판정이_같은_것을_낡음으로_본다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """전체 집합(SQL — 목록 거르기 · 영향 범위)과 위 사슬 접기(파이썬 — 계보 화면 · 목록
    표시)가 섞인 사슬에서 같은 답을 낸다. 다이아몬드 · 두 방향 종류 · 깊은 사슬 · 개정과 바뀐
    때."""
    from app.modules.objects import lineage

    world = _chain(client, admin)
    cads = [
        _make_object(client, admin, world.cad, label=f"D{n}", properties={"rev": "A"})
        for n in range(3)
    ]
    meshes = [_make_object(client, admin, world.mesh, label=f"MS{n}") for n in range(4)]
    results = [_make_object(client, admin, world.result, label=f"RS{n}") for n in range(4)]
    deep = _kind(client, admin, source="src", label="다음 결과")
    for cad_n, mesh_n in ((0, 0), (0, 1), (1, 1), (1, 2), (2, 3)):
        made = _link(
            client, admin, world.cad, cads[cad_n]["id"], world.made, meshes[mesh_n]["id"]
        )
        assert made.status_code == 201, made.text
    for result_n, mesh_n in ((0, 0), (1, 1), (2, 2), (3, 3)):
        used = _link(
            client,
            admin,
            world.result,
            results[result_n]["id"],
            world.used_by,
            meshes[mesh_n]["id"],
        )
        assert used.status_code == 201, used.text
    chain = [results[2]]
    for n in range(4):
        nxt = _make_object(client, admin, world.result, label=f"RD{n}")
        assert (
            _link(client, admin, world.result, chain[-1]["id"], deep, nxt["id"]).status_code
            == 201
        )
        chain.append(nxt)
    clock_settled()
    _patch(client, admin, world.cad, cads[1]["id"], properties={"rev": "B"})
    _patch(client, admin, world.mesh, meshes[3]["id"], properties={"memo": "고침"})

    every = [*cads, *meshes, *results, *chain[1:]]
    ids = [uuid.UUID(one["id"]) for one in every]
    db.expire_all()
    from sqlalchemy import text as sql_text

    whole = {
        row[0]
        for row in db.execute(
            sql_text(
                f"SELECT id FROM ({lineage.stale_sql(lineage.kinds(db))}) s "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [str(one) for one in ids]},
        )
    }
    folded = {one for one, state in lineage.states_of(db, ids).items() if state.stale}
    assert whole == folded
    expected = {
        meshes[1]["id"],
        meshes[2]["id"],
        results[1]["id"],
        results[2]["id"],
        results[3]["id"],
        *(one["id"] for one in chain[1:]),
    }
    assert {str(one) for one in whole} == expected
    direct = {
        row[0]
        for row in db.execute(
            sql_text(
                f"SELECT id FROM ({lineage.stale_sql(lineage.kinds(db), direct_only=True)}) s "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [str(one) for one in ids]},
        )
    }
    assert {str(one) for one in direct} == {meshes[1]["id"], meshes[2]["id"], results[3]["id"]}
    # 다이아몬드의 결과 하나 — 원천 메시 하나만 낡아도 위에서 낡음.
    assert (
        lineage.states_of(db, [uuid.UUID(results[1]["id"])])[uuid.UUID(results[1]["id"])].name
        == "upstream"
    )


def test_통계와_내보내기도_같은_조건을_받는다(client: TestClient, admin: Signed) -> None:
    world, ids = _world(client, admin)
    clock_settled()
    _patch(client, admin, world.cad, ids["cad"]["id"], properties={"rev": "C"})
    got = client.get(
        f"/api/objects/{world.result}/summary",
        params={"f.lineage.eq": "stale"},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    assert got.json()["total"] == 1, got.json()
    fresh = client.get(
        f"/api/objects/{world.result}",
        params={"f.lineage.eq": "fresh"},
        headers=admin.headers,
    )
    assert fresh.json()["total"] == 0
    wrong = client.get(
        f"/api/objects/{world.result}", params={"f.lineage.eq": "odd"}, headers=admin.headers
    )
    assert wrong.status_code == 422, wrong.text
    assert wrong.json()["error"]["code"].endswith("OBJECTS-0132")


def test_검토함은_바로_낡은_파생물마다_한_항목이고_일괄_확인과_되돌리기(
    client: TestClient, admin: Signed
) -> None:

    world, ids = _world(client, admin)
    clock_settled()
    _patch(client, admin, world.cad, ids["cad"]["id"], properties={"rev": "C"})
    listed = client.get(
        "/api/review/items",
        params={"kind": "stale_lineage", "type": world.mesh},
        headers=admin.headers,
    )
    assert listed.status_code == 200, listed.text
    items = [one for one in listed.json()["items"] if one["kind"] == "stale_lineage"]
    # 바로 낡은 메시만 — 위에서 낡은 결과는 항목이 아니다.
    assert [one["id"] for one in items] == [ids["mesh"]["id"]]
    item = items[0]
    assert item["recommended"] == "checked" and item["confidence"] is None
    assert any("B → C" in one for one in item["evidence"])

    planned = client.post(
        "/api/review/plan",
        json={
            "mode": "decisions",
            "decisions": [
                {"kind": "stale_lineage", "item_id": item["id"], "action": "accept"}
            ],
        },
        headers=admin.headers,
    )
    assert planned.status_code == 200, planned.text
    plan = planned.json()
    applied = client.post(
        "/api/review/apply",
        json={
            "decisions": plan["decisions"],
            "fingerprint": plan["fingerprint"],
            "mode": "selected",
        },
        headers=admin.headers,
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["counts"].get("done") == 1, result
    assert _lineage(client, admin, world.mesh, ids["mesh"]["id"])["state"] == "fresh"
    assert _lineage(client, admin, world.result, ids["result"]["id"])["state"] == "fresh"

    undone = client.post(
        f"/api/review/batches/{result['batch_id']}/undo",
        json={"apply": True},
        headers=admin.headers,
    )
    assert undone.status_code == 200, undone.text
    seen = _lineage(client, admin, world.mesh, ids["mesh"]["id"])
    assert seen["state"] == "direct", (seen, result)
    assert seen["up"][0]["pinned_revision"] == "B"


def test_정의를_내보내고_가져오면_계보와_개정_칸이_돌아온다(
    client: TestClient, admin: Signed
) -> None:
    import json

    world = _chain(client, admin)
    got = client.get("/api/ontology/export?format=json", headers=admin.headers)
    assert got.status_code == 200
    body = json.loads(got.content)
    kind = next(one for one in body["relation_types"] if one["slug"] == world.used_by)
    cad = next(one for one in body["types"] if one["slug"] == world.cad)
    assert kind["lineage_source"] == "dst" and cad["revision_key"] == "rev"
    for path, field in (
        (f"/api/ontology/relation-types/{world.used_by}", "lineage_source"),
        (f"/api/ontology/types/{world.cad}", "revision_key"),
    ):
        assert client.patch(path, json={field: ""}, headers=admin.headers).status_code == 200
    again = client.post(
        "/api/ontology/import",
        json={"relation_types": [kind], "types": [cad]},
        params={"dry_run": "false"},
        headers=admin.headers,
    )
    assert again.status_code == 200, again.text
    kinds = client.get("/api/ontology/relation-types", headers=admin.headers).json()
    assert (
        next(one for one in kinds if one["slug"] == world.used_by)["lineage_source"] == "dst"
    )
    types = client.get("/api/ontology/types", headers=admin.headers).json()
    assert next(one for one in types if one["slug"] == world.cad)["revision_key"] == "rev"


def test_RDF_정의는_계보를_PROV_에_잇고_데이터는_고정을_싣는다(
    client: TestClient, admin: Signed
) -> None:
    from rdflib import Graph, Namespace, URIRef
    from rdflib.namespace import OWL, RDFS

    prov = Namespace("http://www.w3.org/ns/prov#")
    world, ids = _world(client, admin)
    got = client.get("/api/rdf/schema", headers=admin.headers)
    assert got.status_code == 200, got.text
    graph = Graph().parse(data=got.text, format="turtle")

    def node(suffix: str) -> URIRef:
        return next(
            one
            for one in set(graph.subjects())
            if isinstance(one, URIRef) and str(one).endswith(suffix)
        )

    # 원천이 도착 — 그 속성이 바로 wasDerivedFrom 의 하위 속성.
    used = node(f"rel.{world.used_by}")
    assert (used, RDFS.subPropertyOf, prov.wasDerivedFrom) in graph
    # 원천이 출발 — 역방향 속성이 잇는다(속성 자신은 아니다).
    made = node(f"rel.{world.made}")
    assert (made, RDFS.subPropertyOf, prov.wasDerivedFrom) not in graph
    inverse = next(graph.subjects(OWL.inverseOf, made))
    assert (inverse, RDFS.subPropertyOf, prov.wasDerivedFrom) in graph

    data = client.get("/api/rdf/data", params={"type": world.cad}, headers=admin.headers)
    assert data.status_code == 200, data.text
    triples = Graph().parse(data=data.text, format="turtle")
    pinned = [
        str(value)
        for subject, predicate, value in triples
        if str(predicate).endswith("pinnedRevision")
    ]
    assert pinned == ["B"], pinned
    assert ids
