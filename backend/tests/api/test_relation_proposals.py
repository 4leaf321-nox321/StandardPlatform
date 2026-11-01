"""제안된 관계(ADR 0042) — 선으로 만들기 전에 남기는 「이 둘은 이어져야 할 것 같다」.

    제안   양 끝을 볼 수만 있으면(권한 없는 사람 · AI 의 길)
    확정   출발 객체를 고칠 수 있는 사람 — 「관계 추가」 와 **같은 길**(`relations.add_edge`)
    거절   같은 사람 — 같은 제안이 다시 오면 「언제 누가 왜」 만 돌려준다

제안은 따로 둔 표(`relation_proposals`)라 트리 · 롤업 · 목록 조건 · 코어 API 가 볼 길이 없다 —
확정해야 선이 된다. 확정은 개수 제약 · 순환 · 기간 겹침 · 계보 고정 트리거 · 감사를 그대로
탄다.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEntry
from app.modules.objects.models import ObjectRelation, RelationProposal
from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed, _make_user, maintenance_counts
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)


def _propose(
    client: TestClient,
    who: Signed,
    type_slug: str,
    src: str,
    relation: str,
    dst: str,
    **kw: Any,
) -> dict[str, Any]:
    got = client.post(
        f"/api/objects/{type_slug}/{src}/relation-proposals",
        json={"relation": relation, "dst_object_id": dst, **kw},
        headers=who.headers,
    )
    assert got.status_code == 200, got.text
    return dict(got.json())


def _decide(client: TestClient, who: Signed, proposal_id: str, what: str, **body: Any) -> Any:
    return client.post(
        f"/api/objects/relation-proposals/{proposal_id}/{what}", json=body, headers=who.headers
    )


def _of(
    client: TestClient, who: Signed, type_slug: str, object_id: str
) -> list[dict[str, Any]]:
    got = client.get(
        f"/api/objects/{type_slug}/{object_id}/relation-proposals", headers=who.headers
    )
    assert got.status_code == 200, got.text
    return list(got.json())


def _related(client: TestClient, who: Signed, type_slug: str, object_id: str) -> set[str]:
    got = client.get(f"/api/objects/{type_slug}/{object_id}", headers=who.headers)
    assert got.status_code == 200, got.text
    return {
        one["object_id"] for one in got.json()["related"] if one["stored_as"] == "relation"
    }


def _row(db: Session, proposal_id: str) -> RelationProposal:
    db.expire_all()
    found = db.get(RelationProposal, uuid.UUID(proposal_id))
    assert found is not None
    return found


def _pair(client: TestClient, admin: Signed, **kind: Any) -> tuple[str, str, str]:
    """부품 · 공급사 타입과 그 사이 관계 종류 하나."""
    part = _make_type(client, admin, label="부품", key_policy="optional")
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    relation = _make_relation(
        client,
        admin,
        "supplied_by",
        label="공급받음",
        inverse_label="공급함",
        src_type_slugs=[part],
        dst_type_slugs=[vendor],
        **kind,
    )
    return part, vendor, relation


# --- 권한 -------------------------------------------------------------------------


def test_볼_수만_있는_사람이_제안하고_확정은_출발_객체를_고칠_수_있는_사람만(
    client: TestClient, admin: Signed, manager: Signed, member: Signed, db: Session
) -> None:
    part, vendor, kind = _pair(client, admin)
    bolt = _make_object(client, manager, part, label="볼트")
    acme = _make_object(client, manager, vendor, label="ACME")

    # 멤버는 축 객체를 못 고친다 — 관계 추가는 막히고, 제안은 된다.
    denied = _link(client, member, part, bolt["id"], kind, acme["id"])
    assert denied.status_code == 403, denied.text
    made = _propose(
        client,
        member,
        part,
        bolt["id"],
        kind,
        acme["id"],
        evidence_note="공급 계약서 3쪽",
        confidence=0.8,
        origin="mcp",
        origin_ref="대화 #12",
    )
    assert made["status"] == "created", made
    proposal = made["proposal"]
    assert proposal["status"] == "proposed" and proposal["confidence"] == 0.8
    assert proposal["can_decide"] is False and proposal["can_withdraw"] is True
    assert proposal["proposed_by_label"] == "member"

    # 양쪽 객체 화면에 열린 제안으로 — 선은 아직 없다.
    assert [one["id"] for one in _of(client, member, part, bolt["id"])] == [proposal["id"]]
    seen_from_vendor = _of(client, manager, vendor, acme["id"])
    assert seen_from_vendor[0]["outgoing"] is False
    assert seen_from_vendor[0]["label"] == "공급함"
    assert seen_from_vendor[0]["can_decide"] is True
    assert acme["id"] not in _related(client, admin, part, bolt["id"])

    # 확정은 출발 객체를 고칠 수 있는 사람만.
    # 「관계 추가」 와 같은 판정이라 같은 말(부서 관리자만)로 거절한다.
    refused = _decide(client, member, proposal["id"], "accept")
    assert refused.status_code == 403, refused.text
    assert "관리자" in refused.json()["error"]["message"]
    accepted = _decide(client, manager, proposal["id"], "accept", note="계약서 확인")
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["status"] == "accepted" and body["relation_id"]
    assert body["decided_by_label"] == "manager"
    assert acme["id"] in _related(client, admin, part, bolt["id"])
    assert _of(client, manager, part, bolt["id"]) == []

    # 선은 「관계 추가」 와 같은 감사를 남기고 근거를 이어받는다. 제안 · 확정은 양 끝의 이력에.
    edge = db.get(ObjectRelation, uuid.UUID(body["relation_id"]))
    assert edge is not None and edge.evidence_note == "공급 계약서 3쪽"
    actions = set(
        db.scalars(
            select(AuditEntry.action).where(
                AuditEntry.changes["src"].astext == bolt["id"],
                AuditEntry.changes["dst"].astext == acme["id"],
            )
        )
    )
    assert {"relation.propose", "relation.proposal.accept", "object.relation.add"} <= actions
    history = client.get(
        f"/api/objects/{vendor}/{acme['id']}/history", headers=admin.headers
    ).json()
    seen = {one["action"] for one in history}
    assert {"relation.propose", "relation.proposal.accept"} <= seen, seen


def test_못_보는_객체로는_제안하지_못한다(
    client: TestClient,
    admin: Signed,
    member: Signed,
    db: Session,
    department_scope: None,
) -> None:
    part, vendor, kind = _pair(client, admin)
    other = Workspace(slug=f"far-{uuid.uuid4().hex[:8]}", name="먼 팀")
    db.add(other)
    db.commit()
    boss = _make_user(db, other, label="far-boss", is_system_admin=False, role="manager")
    from tests.api.conftest import _login

    far = Signed(email=boss.email, token=_login(client, boss.email), workspace=other.slug)
    hidden = _make_object(client, far, vendor, label="남의 공급사")
    bolt = _make_object(client, admin, part, label="볼트")
    got = client.post(
        f"/api/objects/{part}/{bolt['id']}/relation-proposals",
        json={"relation": kind, "dst_object_id": hidden["id"]},
        headers=member.headers,
    )
    assert got.status_code == 404, got.text


def test_부서_계정_끝은_제안으로_받지_않는다(
    client: TestClient, admin: Signed, workspace: Workspace
) -> None:
    part = _make_type(client, admin, label="과제")
    dept = _make_type(
        client,
        admin,
        label=f"부서투영{uuid.uuid4().hex[:6]}",
        kind_class="system",
        system_source="workspace",
    )
    kind = _make_relation(
        client,
        admin,
        "owned_by",
        label="담당 부서",
        src_type_slugs=[part],
        dst_type_slugs=[dept],
    )
    task = _make_object(client, admin, part, label="과제 1")
    got = client.post(
        f"/api/objects/{part}/{task['id']}/relation-proposals",
        json={"relation": kind, "dst_object_id": str(workspace.id)},
        headers=admin.headers,
    )
    assert got.status_code == 409, got.text
    assert got.json()["error"]["code"].endswith("OBJECTS-0138")


def test_철회는_제안한_사람만(
    client: TestClient, admin: Signed, manager: Signed, member: Signed
) -> None:
    part, vendor, kind = _pair(client, admin)
    bolt = _make_object(client, admin, part, label="볼트")
    acme = _make_object(client, admin, vendor, label="ACME")
    proposal = _propose(client, member, part, bolt["id"], kind, acme["id"])["proposal"]
    denied = client.delete(
        f"/api/objects/relation-proposals/{proposal['id']}", headers=manager.headers
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"].endswith("OBJECTS-0144")
    done = client.delete(
        f"/api/objects/relation-proposals/{proposal['id']}", headers=member.headers
    )
    assert done.status_code == 204, done.text
    assert _of(client, admin, part, bolt["id"]) == []


# --- 같은 것이 이미 있으면 ------------------------------------------------------------


def test_이미_이어진_선_열린_제안_거절된_적_있음을_돌려준다(
    client: TestClient, admin: Signed, manager: Signed, member: Signed
) -> None:
    part, vendor, kind = _pair(client, admin)
    bolt = _make_object(client, admin, part, label="볼트")
    acme = _make_object(client, admin, vendor, label="ACME")
    beta = _make_object(client, admin, vendor, label="BETA")
    gamma = _make_object(client, admin, vendor, label="GAMMA")

    # 이미 이어진 선 — 만들지 않고 그 선을 말한다.
    linked = _link(client, admin, part, bolt["id"], kind, acme["id"])
    assert linked.status_code == 201
    same = _propose(client, member, part, bolt["id"], kind, acme["id"])
    assert same["status"] == "connected" and same["proposal"] is None
    assert same["relation_id"] == linked.json()["relation_id"]
    assert "이미" in same["message"]

    # 열린 제안 — 낮은 확신도는 그대로, 높은 확신도면 근거 · 확신도를 새 것으로.
    first = _propose(client, member, part, bolt["id"], kind, beta["id"], confidence=0.6)
    assert first["status"] == "created"
    lower = _propose(
        client,
        member,
        part,
        bolt["id"],
        kind,
        beta["id"],
        confidence=0.4,
        evidence_note="약함",
    )
    assert lower["status"] == "open" and lower["updated"] is False
    assert lower["proposal"]["id"] == first["proposal"]["id"]
    assert lower["proposal"]["confidence"] == 0.6
    higher = _propose(
        client,
        member,
        part,
        bolt["id"],
        kind,
        beta["id"],
        confidence=0.9,
        evidence_note="강함",
    )
    assert higher["status"] == "open" and higher["updated"] is True
    assert higher["proposal"]["confidence"] == 0.9
    assert higher["proposal"]["evidence_note"] == "강함"

    # 거절된 적 있음 — 언제 누가 왜. `again` 으로만 다시.
    made = _propose(client, member, part, bolt["id"], kind, gamma["id"])["proposal"]
    rejected = _decide(client, manager, made["id"], "reject", note="단종된 공급사")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    again = _propose(client, member, part, bolt["id"], kind, gamma["id"])
    assert again["status"] == "rejected_before", again
    assert "단종된 공급사" in again["message"] and "manager" in again["message"]
    forced = _propose(client, member, part, bolt["id"], kind, gamma["id"], again=True)
    assert forced["status"] == "created"
    assert forced["proposal"]["id"] != made["id"]


def test_여러_건_제안은_줄마다_결과를_낸다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    part, vendor, kind = _pair(client, admin)
    bolt = _make_object(client, admin, part, label="볼트")
    nut = _make_object(client, admin, part, label="너트")
    acme = _make_object(client, admin, vendor, label="ACME")
    assert _link(client, admin, part, nut["id"], kind, acme["id"]).status_code == 201
    got = client.post(
        "/api/objects/relation-proposals",
        json={
            "rows": [
                {"src_object_id": bolt["id"], "relation": kind, "dst_object_id": acme["id"]},
                {"src_object_id": bolt["id"], "relation": kind, "dst_object_id": acme["id"]},
                {"src_object_id": nut["id"], "relation": kind, "dst_object_id": acme["id"]},
                {
                    "src_object_id": bolt["id"],
                    "relation": "no_such",
                    "dst_object_id": acme["id"],
                },
                # 끝 타입이 안 맞는다 — 공급사 → 부품.
                {"src_object_id": acme["id"], "relation": kind, "dst_object_id": bolt["id"]},
            ]
        },
        headers=member.headers,
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert [one["status"] for one in body["rows"]] == [
        "created",
        "open",
        "connected",
        "error",
        "error",
    ]
    assert body["rows"][3]["error_code"].endswith("OBJECTS-0020")
    assert body["rows"][4]["error_code"].endswith("OBJECTS-0022")
    assert body["counts"] == {
        "created": 1,
        "open": 1,
        "connected": 1,
        "rejected_before": 0,
        "error": 2,
    }
    # 틀린 줄이 앞 줄을 안 걷어낸다.
    assert len(_of(client, admin, part, bolt["id"])) == 1


# --- 확정은 보통의 선 만들기와 같은 길 ------------------------------------------------


def test_확정은_개수_제약_순환_기간_겹침에_걸린다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    # 개수 제약 — 부품의 공급사는 하나.
    part, vendor, single = _pair(client, admin, cardinality="many_to_one")
    bolt = _make_object(client, admin, part, label="볼트")
    acme = _make_object(client, admin, vendor, label="ACME")
    beta = _make_object(client, admin, vendor, label="BETA")
    assert _link(client, admin, part, bolt["id"], single, acme["id"]).status_code == 201
    # 만들 때는 모양만 — 개수 제약은 확정할 때.
    late = _propose(client, admin, part, bolt["id"], single, beta["id"])["proposal"]
    refused = _decide(client, admin, late["id"], "accept")
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"].endswith("OBJECTS-0143") and error["details"]["cause"].endswith(
        "OBJECTS-0023"
    )
    assert "하나만" in error["message"]
    assert _row(db, late["id"]).status == "proposed"

    # 순환 — A ⊂ B 가 있는데 B ⊂ A 를 제안.
    tree = _make_type(client, admin, label="조립품")
    within = _make_relation(
        client,
        admin,
        "part_of",
        label="속함",
        transitive=True,
        acyclic=True,
        src_type_slugs=[tree],
        dst_type_slugs=[tree],
    )
    a = _make_object(client, admin, tree, label="A")
    b = _make_object(client, admin, tree, label="B")
    assert _link(client, admin, tree, a["id"], within, b["id"]).status_code == 201
    back = _propose(client, admin, tree, b["id"], within, a["id"])["proposal"]
    cycle = _decide(client, admin, back["id"], "accept")
    assert cycle.status_code == 409
    assert cycle.json()["error"]["details"]["cause"].endswith("OBJECTS-0026")

    # 기간 겹침 — 제안 뒤에 겹치는 선이 생겼다.
    dated = _make_relation(
        client,
        admin,
        "bom_of",
        label="구성",
        effectivity=True,
        src_type_slugs=[tree],
        dst_type_slugs=[tree],
    )
    c = _make_object(client, admin, tree, label="C")
    proposed = _propose(client, admin, tree, c["id"], dated, a["id"], valid_from="2026-02-01")[
        "proposal"
    ]
    assert proposed["valid_from"] == "2026-02-01"
    got = _link(
        client,
        admin,
        tree,
        c["id"],
        dated,
        a["id"],
        valid_from="2026-01-01",
        valid_to="2026-03-01",
    )
    assert got.status_code == 201, got.text
    overlap = _decide(client, admin, proposed["id"], "accept")
    assert overlap.status_code == 409
    assert overlap.json()["error"]["details"]["cause"].endswith("OBJECTS-0128")


def test_계보_종류로_확정하면_원천의_개정이_고정된다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    cad = _make_type(client, admin, label="도면", key_policy="optional")
    _make_property(client, admin, cad, key="rev", label="개정", data_type="text")
    got = client.patch(
        f"/api/ontology/types/{cad}", json={"revision_key": "rev"}, headers=admin.headers
    )
    assert got.status_code == 200, got.text
    mesh = _make_type(client, admin, label="메시", key_policy="optional")
    made = _make_relation(
        client,
        admin,
        "made",
        label="만든 것",
        lineage_source="src",
        src_type_slugs=[cad],
        dst_type_slugs=[mesh],
    )
    drawing = _make_object(client, admin, cad, label="브래킷", properties={"rev": "B"})
    m12 = _make_object(client, admin, mesh, label="M-12")
    proposal = _propose(client, admin, cad, drawing["id"], made, m12["id"], confidence=0.97)
    accepted = _decide(client, admin, proposal["proposal"]["id"], "accept")
    assert accepted.status_code == 200, accepted.text
    db.expire_all()
    edge = db.get(ObjectRelation, uuid.UUID(accepted.json()["relation_id"]))
    assert edge is not None and edge.pinned_revision == "B" and edge.pinned_at is not None


def test_확정_전의_제안은_트리_롤업_목록_조건_코어에_안_보인다(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, label="부품", key_policy="optional")
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    kind = _make_relation(
        client,
        admin,
        "bom_of",
        label="구성",
        inverse_label="구성품",
        transitive=True,
        acyclic=True,
        src_type_slugs=[part],
        dst_type_slugs=[part],
    )
    got = client.patch(
        f"/api/ontology/types/{part}",
        json={
            "core": True,
            "list_view": {
                "tree": {"relation": kind, "parent": "dst"},
                "rollups": [{"property": "weight", "fn": "sum"}],
            },
        },
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    top = _make_object(client, admin, part, label="P", key="P")
    child = _make_object(client, admin, part, label="A1", key="A1", properties={"weight": 3})
    proposal = _propose(client, admin, part, child["id"], kind, top["id"], confidence=0.99)

    def views() -> dict[str, Any]:
        tree = client.get(f"/api/objects/{part}/tree", headers=admin.headers).json()
        roots = {one["id"]: one for one in tree["nodes"]}
        rollup = client.get(
            f"/api/objects/{part}/{top['id']}/rollup", headers=admin.headers
        ).json()[0]
        listed = client.get(
            f"/api/objects/{part}",
            params={f"f.out.{kind}.eq": top["id"]},
            headers=admin.headers,
        ).json()
        core = client.get(f"/api/core/{part}/relations", headers=admin.headers).json()
        return {
            "children": roots[top["id"]]["child_count"] if top["id"] in roots else None,
            "rollup": rollup["value"],
            "listed": listed["total"],
            "core": len(core["items"]),
            "related": top["id"] in _related(client, admin, part, child["id"]),
        }

    # 트리의 뿌리는 자식이 있는 것만 — 제안만 있는 P 는 아직 부모도 자식도 없는 것(뿌리 아님).
    assert views() == {
        "children": None,
        "rollup": None,
        "listed": 0,
        "core": 0,
        "related": False,
    }
    accepted = _decide(client, admin, proposal["proposal"]["id"], "accept")
    assert accepted.status_code == 200, accepted.text
    assert views() == {"children": 1, "rollup": 3, "listed": 1, "core": 1, "related": True}


# --- 되돌리기 · 검토함 ---------------------------------------------------------------


def test_확정을_되돌리면_선을_해제하고_다시_열지만_그_뒤_고친_선은_건너뛴다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    part, vendor, kind = _pair(client, admin)
    bolt = _make_object(client, admin, part, label="볼트")
    acme = _make_object(client, admin, vendor, label="ACME")
    beta = _make_object(client, admin, vendor, label="BETA")
    first = _propose(client, admin, part, bolt["id"], kind, acme["id"])["proposal"]
    accepted = _decide(client, admin, first["id"], "accept").json()
    reopened = client.post(
        f"/api/objects/relation-proposals/{first['id']}/reopen", headers=admin.headers
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["reopened"] is True
    assert reopened.json()["proposal"]["status"] == "proposed"
    db.expire_all()
    assert db.get(ObjectRelation, uuid.UUID(accepted["relation_id"])) is None

    # 확정 뒤 그 선을 고쳤다 — 되돌리기는 선도 제안도 건드리지 않는다.
    second = _propose(client, admin, part, bolt["id"], kind, beta["id"])["proposal"]
    edge_id = _decide(client, admin, second["id"], "accept").json()["relation_id"]
    patched = client.patch(
        f"/api/objects/{part}/{bolt['id']}/relations/{edge_id}",
        json={"evidence_note": "사람이 고침"},
        headers=admin.headers,
    )
    assert patched.status_code == 200, patched.text
    skipped = client.post(
        f"/api/objects/relation-proposals/{second['id']}/reopen", headers=admin.headers
    ).json()
    assert skipped["reopened"] is False and "수정됐습니다" in skipped["message"]
    db.expire_all()
    assert db.get(ObjectRelation, uuid.UUID(edge_id)) is not None
    assert _row(db, second["id"]).status == "accepted"


def _review_items(client: TestClient, who: Signed, type_slug: str) -> list[dict[str, Any]]:
    got = client.get(
        "/api/review/items",
        params={"kind": "proposed_relation", "type": type_slug},
        headers=who.headers,
    )
    assert got.status_code == 200, got.text
    return [one for one in got.json()["items"] if one["kind"] == "proposed_relation"]


def test_검토함은_제안마다_한_항목이고_추천대로_일괄_확정과_되돌리기(
    client: TestClient, admin: Signed, manager: Signed, member: Signed, db: Session
) -> None:
    part, vendor, kind = _pair(client, admin)
    bolt = _make_object(client, manager, part, label="볼트")
    vendors = {
        name: _make_object(client, manager, vendor, label=name) for name in ("V1", "V2", "V3")
    }
    before = maintenance_counts(client, manager).get("relation_proposals", 0)
    made = {
        name: _propose(
            client,
            member,
            part,
            bolt["id"],
            kind,
            vendors[name]["id"],
            confidence=score,
            evidence_note=f"근거 {name}",
            origin="mcp",
        )["proposal"]["id"]
        for name, score in (("V1", 0.95), ("V2", 0.3), ("V3", None))
    }
    assert maintenance_counts(client, manager).get("relation_proposals", 0) == before + 3

    items = {one["id"]: one for one in _review_items(client, manager, part)}
    assert set(made.values()) <= set(items)
    high, low, unknown = (items[made[name]] for name in ("V1", "V2", "V3"))
    assert high["recommended"] == "accept" and high["confidence"] == 0.95
    assert low["recommended"] is None and low["confidence"] is None
    assert unknown["recommended"] is None
    assert any("근거 V1" in line for line in high["evidence"])
    assert any("AI(MCP)" in line and "member" in line for line in high["evidence"])
    assert high["blocked"] is None

    # 「추천대로 모두」(문턱 0.9) — 확신도 높은 하나만.
    planned = client.post(
        "/api/review/plan",
        json={
            "mode": "recommended",
            "filters": {"kind": "proposed_relation", "type": part},
            "threshold": 0.9,
        },
        headers=manager.headers,
    )
    assert planned.status_code == 200, planned.text
    plan = planned.json()
    mine = [one for one in plan["decisions"] if one["item_id"] in set(made.values())]
    assert [one["item_id"] for one in mine] == [made["V1"]], plan
    result = _apply(client, manager, mine)
    assert result["counts"].get("done") == 1, result
    assert _row(db, made["V1"]).status == "accepted"
    assert vendors["V1"]["id"] in _related(client, admin, part, bolt["id"])

    # 거절도 검토함에서 — 그리고 묶음을 되돌리면 둘 다 다시 열린다(확정한 선은 해제).
    rejected = _apply(
        client,
        manager,
        [
            {
                "kind": "proposed_relation",
                "item_id": made["V2"],
                "action": "choose",
                "value": "reject",
            }
        ],
    )
    assert rejected["counts"].get("done") == 1, rejected
    assert _row(db, made["V2"]).status == "rejected"

    for batch in (result["batch_id"], rejected["batch_id"]):
        undone = client.post(
            f"/api/review/batches/{batch}/undo", json={"apply": True}, headers=manager.headers
        )
        assert undone.status_code == 200, undone.text
    assert _row(db, made["V1"]).status == "proposed"
    assert _row(db, made["V2"]).status == "proposed"
    assert vendors["V1"]["id"] not in _related(client, admin, part, bolt["id"])


def _apply(client: TestClient, who: Signed, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    planned = client.post(
        "/api/review/plan",
        json={"mode": "decisions", "decisions": decisions},
        headers=who.headers,
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
        headers=who.headers,
    )
    assert applied.status_code == 200, applied.text
    return dict(applied.json())


# --- 다른 기능과의 관계 ---------------------------------------------------------------


def test_끝을_지우면_목록과_검토함에서_빠지고_확정은_거절하되_거절은_받는다(
    client: TestClient, admin: Signed
) -> None:
    part, vendor, kind = _pair(client, admin)
    bolt = _make_object(client, admin, part, label="볼트")
    acme = _make_object(client, admin, vendor, label="ACME")
    beta = _make_object(client, admin, vendor, label="BETA")
    first = _propose(client, admin, part, bolt["id"], kind, acme["id"])["proposal"]
    second = _propose(client, admin, part, bolt["id"], kind, beta["id"])["proposal"]
    gone = client.delete(f"/api/objects/{vendor}/{acme['id']}", headers=admin.headers)
    assert gone.status_code == 204, gone.text

    assert [one["id"] for one in _of(client, admin, part, bolt["id"])] == [second["id"]]
    assert first["id"] not in {one["id"] for one in _review_items(client, admin, part)}
    refused = _decide(client, admin, first["id"], "accept")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"].endswith("OBJECTS-0141")
    cleared = _decide(client, admin, first["id"], "reject", note="공급사 삭제됨")
    assert cleared.status_code == 200, cleared.text


def test_합치면_제안이_이긴_쪽으로_옮겨지고_같은_제안은_하나로(
    client: TestClient, admin: Signed, db: Session
) -> None:
    part, vendor, kind = _pair(client, admin)
    loser = _make_object(client, admin, part, label="볼트(옛)")
    winner = _make_object(client, admin, part, label="볼트")
    acme = _make_object(client, admin, vendor, label="ACME")
    beta = _make_object(client, admin, vendor, label="BETA")
    same_on_loser = _propose(client, admin, part, loser["id"], kind, acme["id"])["proposal"]
    same_on_winner = _propose(client, admin, part, winner["id"], kind, acme["id"])["proposal"]
    moved = _propose(client, admin, part, loser["id"], kind, beta["id"])["proposal"]

    merged = client.post(
        f"/api/objects/{part}/{loser['id']}/merge",
        json={"into": winner["id"]},
        headers=admin.headers,
    )
    assert merged.status_code == 200, merged.text
    on_winner = {one["id"]: one for one in _of(client, admin, part, winner["id"])}
    assert set(on_winner) == {same_on_winner["id"], moved["id"]}
    assert on_winner[moved["id"]]["src_object_id"] == winner["id"]
    db.expire_all()
    assert db.get(RelationProposal, uuid.UUID(same_on_loser["id"])) is None
    entry = db.scalar(
        select(AuditEntry)
        .where(
            AuditEntry.action == "object.merge",
            AuditEntry.target_id == uuid.UUID(winner["id"]),
        )
        .order_by(AuditEntry.seq.desc())
    )
    assert entry is not None
    assert entry.changes["proposals_moved"] == 1 and entry.changes["proposals_dropped"] == 1


def test_관계_종류를_지우면_제안도_함께_지운다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    part, vendor, kind = _pair(client, admin)
    bolt = _make_object(client, admin, part, label="볼트")
    acme = _make_object(client, admin, vendor, label="ACME")
    proposal = _propose(client, admin, part, bolt["id"], kind, acme["id"])["proposal"]

    plan = client.get(
        "/api/ontology/delete-plan",
        params={"kind": "relation_type", "slug": kind},
        headers=admin.headers,
    )
    assert plan.status_code == 200, plan.text
    assert any("제안된 관계 1개" in line for line in plan.json()["removes"]), plan.json()
    gone = client.delete(f"/api/ontology/relation-types/{kind}", headers=admin.headers)
    assert gone.status_code == 204, gone.text
    db.expire_all()
    assert db.get(RelationProposal, uuid.UUID(proposal["id"])) is None


def test_지운_객체를_영구_삭제하면_그_제안도_미리_보기에_세고_지운다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """타입 삭제의 영구 삭제(ADR 0008) — 지운 객체에 걸린 제안은 FK(CASCADE)가 함께 지울 것도
    **명시적으로** 세고 지운다. 미리 보기와 사라지는 것이 맞아야 사람이 믿는다."""
    part = _make_type(client, admin, label="부품")
    vendor = _make_type(client, admin, label="공급사")
    kind = _make_relation(client, admin, "supplied_by", label="공급받음")
    bolt = _make_object(client, admin, part, label="볼트")
    acme = _make_object(client, admin, vendor, label="ACME")
    proposal = _propose(client, admin, part, bolt["id"], kind, acme["id"])["proposal"]
    gone = client.delete(f"/api/objects/{part}/{bolt['id']}", headers=admin.headers)
    assert gone.status_code == 204, gone.text

    plan = client.get(
        "/api/ontology/delete-plan",
        params={"kind": "type", "slug": part},
        headers=admin.headers,
    )
    assert plan.status_code == 200, plan.text
    assert "제안된 관계 1개" in plan.json()["removes"], plan.json()
    done = client.delete(
        f"/api/ontology/types/{part}", params={"purge_deleted": "true"}, headers=admin.headers
    )
    assert done.status_code == 204, done.text
    db.expire_all()
    assert db.get(RelationProposal, uuid.UUID(proposal["id"])) is None
