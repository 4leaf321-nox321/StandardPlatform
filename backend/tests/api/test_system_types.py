"""`system` 타입 — **행 없이 원 표를 비춘다.**

부서를 온톨로지에 넣겠다고 `objects` 에 행을 복제하면 두 벌이 되고 두 벌은 반드시
갈린다. 여기서 보는 것: 투영이 원 표 그대로인가, 참조가 원 표에서 풀리는가, 그
끝단의 선(`object_links`)이 관계와 같은 규칙을 지키는가, 그리고 승격이 id 를 보존하는가.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectLink, ObjectRelation
from app.modules.ontology import promotion
from app.modules.ontology.models import ObjectType
from app.modules.workspaces.models import Workspace
from app.shared import system_sources
from tests.api.conftest import Signed, export_file
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)


def _dept_type(client: TestClient, admin: Signed) -> str:
    return _make_type(
        client, admin, "dept", label="부서", kind_class="system", system_source="workspace"
    )


# --- 정의 --------------------------------------------------------------------


def test_투영_타입은_등록된_원_표를_적어야_한다(client: TestClient, admin: Signed) -> None:
    """빈 목록을 돌려주면 「없다」 로 읽히고, 사람은 없는 것을 새로 만든다."""
    missing = client.post(
        "/api/ontology/types",
        json={"slug": f"d_{uuid.uuid4().hex[:6]}", "label": "부서", "kind_class": "system"},
        headers=admin.headers,
    )
    assert missing.status_code == 422
    assert "system_source" in missing.json()["error"]["message"]

    unknown = client.post(
        "/api/ontology/types",
        json={
            "slug": f"d_{uuid.uuid4().hex[:6]}",
            "label": "부서",
            "kind_class": "system",
            "system_source": "nope",
        },
        headers=admin.headers,
    )
    assert unknown.status_code == 422
    assert "workspace" in unknown.json()["error"]["message"]

    stray = client.post(
        "/api/ontology/types",
        json={"slug": f"p_{uuid.uuid4().hex[:6]}", "label": "부품", "system_source": "user"},
        headers=admin.headers,
    )
    assert stray.status_code == 422


def test_스키마가_원_표_목록을_말한다(client: TestClient, admin: Signed) -> None:
    got = client.get("/api/ontology/schema", headers=admin.headers).json()
    assert {one["key"] for one in got["system_sources"]} >= {"workspace", "user"}


def test_객체가_있는_타입은_투영으로_못_돌린다(client: TestClient, admin: Signed) -> None:
    """행이 있는데 투영으로 바꾸면 그 행이 **화면에서 사라진다** — 지워진 게 아닌데."""
    part = _make_type(client, admin, label="부품")
    _make_object(client, admin, part, label="볼트")
    denied = client.patch(
        f"/api/ontology/types/{part}",
        json={"kind_class": "system", "system_source": "workspace"},
        headers=admin.headers,
    )
    assert denied.status_code == 409
    assert "승격" in denied.json()["error"]["message"]


# --- 투영 --------------------------------------------------------------------


def test_목록과_상세가_원_표_그대로다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    dept = _dept_type(client, admin)
    listed = client.get(f"/api/objects/{dept}", headers=member.headers).json()
    # 시험 DB 는 스위트가 함께 쓴다 — 부서가 한 쪽을 넘을 수 있으니 전체 수로 본다.
    assert listed["total"] >= 1 and len(listed["items"]) <= listed["limit"]

    searched = client.get(
        f"/api/objects/{dept}", params={"q": admin.workspace}, headers=member.headers
    ).json()
    assert [row["key"] for row in searched["items"]] == [admin.workspace]
    mine = searched["items"][0]
    assert mine["type_slug"] == dept and mine["properties"] == {}

    profile = client.get(f"/api/objects/{dept}/{mine['id']}", headers=member.headers).json()
    assert profile["object"]["label"] == mine["label"]
    assert profile["can_edit"] is False and profile["properties_schema"] == []

    gone = client.get(f"/api/objects/{dept}/{uuid.uuid4()}", headers=member.headers)
    assert gone.status_code == 404


def test_투영에는_파일로_넣지도_내보내지도_않는다(client: TestClient, admin: Signed) -> None:
    dept = _dept_type(client, admin)
    assert (
        client.get(f"/api/objects/{dept}/template", headers=admin.headers).status_code == 403
    )
    assert client.post(f"/api/objects/{dept}/export", headers=admin.headers).status_code == 403
    planned = client.post(
        f"/api/objects/{dept}/import-rows",
        json={"rows": [{"label": "x"}], "apply": False},
        headers=admin.headers,
    )
    assert planned.status_code == 200 and planned.json()["errors"]


# --- 참조 --------------------------------------------------------------------


def _part_with_owner(client: TestClient, admin: Signed) -> tuple[str, str]:
    dept = _dept_type(client, admin)
    part = _make_type(client, admin, label="부품", key_policy="optional")
    _make_property(
        client,
        admin,
        part,
        key="owner",
        label="담당 부서",
        data_type="object_ref",
        ref_type_slug=dept,
    )
    return dept, part


def test_참조가_원_표에서_풀린다(
    client: TestClient, admin: Signed, db: Session, workspace: Workspace
) -> None:
    """존재 확인도 이름도 **원 표**에서 — 부서 이름을 바꾸면 그 칸도 바뀐다."""
    _dept, part = _part_with_owner(client, admin)
    bolt = _make_object(
        client, admin, part, label="볼트", properties={"owner": str(workspace.id)}
    )
    first = client.get(f"/api/objects/{part}/{bolt['id']}", headers=admin.headers).json()
    assert first["object"]["ref_labels"][str(workspace.id)] == workspace.name

    workspace.name = "새 이름"
    db.commit()
    again = client.get(f"/api/objects/{part}/{bolt['id']}", headers=admin.headers).json()
    assert again["object"]["ref_labels"][str(workspace.id)] == "새 이름"

    missing = client.post(
        f"/api/objects/{part}",
        json={
            "label": "너트",
            "workspace_slug": admin.workspace,
            "properties": {"owner": str(uuid.uuid4())},
        },
        headers=admin.headers,
    )
    assert missing.status_code == 422
    assert "가리키는 객체를 찾을 수 없습니다" in missing.json()["error"]["message"]

    # 조건 거르기도 그대로 — 값은 원 표의 id 다.
    filtered = client.get(
        f"/api/objects/{part}", params={"f.owner.eq": str(workspace.id)}, headers=admin.headers
    ).json()
    assert [row["label"] for row in filtered["items"]] == ["볼트"]


def test_파일의_부서는_slug_로_적는다(
    client: TestClient, admin: Signed, workspace: Workspace, db: Session
) -> None:
    """식별자(slug)로도 이름으로도 — 이름은 **하나에만 맞을 때.**"""
    _dept, part = _part_with_owner(client, admin)
    # 시험 DB 는 스위트가 함께 쓴다 — 같은 이름의 부서가 여럿이면 이름 풀이가 (맞게) 거절된다.
    workspace.name = f"유일한 부서 {uuid.uuid4().hex[:6]}"
    db.commit()
    done = client.post(
        f"/api/objects/{part}/import-rows",
        json={
            "rows": [
                {"label": "볼트", "owner": workspace.slug},
                {"label": "너트", "owner": workspace.name},
            ],
            "workspace_slug": admin.workspace,
            "apply": True,
        },
        headers=admin.headers,
    ).json()
    assert done["applied"] is True, done
    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()
    assert {row["properties"]["owner"] for row in listed["items"]} == {str(workspace.id)}


# --- 선 ----------------------------------------------------------------------


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    dept = _dept_type(client, admin)
    part = _make_type(client, admin, label="부품", key_policy="optional")
    kind = _make_relation(
        client,
        admin,
        "owned_by",
        label="담당",
        inverse_label="담당함",
        cardinality="many_to_one",
        src_type_slugs=[part],
        dst_type_slugs=[dept],
    )
    bolt = _make_object(client, admin, part, label="볼트")
    return {"dept": dept, "part": part, "kind": kind, "bolt": bolt}


def test_객체와_부서를_잇는_선은_관계와_같이_보인다(
    client: TestClient, admin: Signed, member: Signed, db: Session, workspace: Workspace
) -> None:
    w = _world(client, admin)
    made = _link(
        client,
        admin,
        w["part"],
        w["bolt"]["id"],
        w["kind"],
        str(workspace.id),
        evidence_note="조직도",
    )
    assert made.status_code == 201, made.text
    row = made.json()
    assert row["object_label"] == workspace.name and row["object_type_slug"] == w["dept"]
    assert row["label"] == "담당" and row["evidence_note"] == "조직도"
    assert db.scalar(__import__("sqlalchemy").select(ObjectLink)) is not None

    # 객체 쪽 상세에도, 부서 쪽 상세에도 — 방향에 맞는 말로.
    part_side = client.get(
        f"/api/objects/{w['part']}/{w['bolt']['id']}", headers=member.headers
    ).json()
    assert [r["label"] for r in part_side["related"]] == ["담당"]
    dept_side = client.get(
        f"/api/objects/{w['dept']}/{workspace.id}", headers=member.headers
    ).json()
    assert [(r["label"], r["object_label"]) for r in dept_side["related"]] == [
        ("담당함", "볼트")
    ]

    # 같은 선은 한 번, 개수 제약은 두 표를 합쳐 본다.
    twice = _link(client, admin, w["part"], w["bolt"]["id"], w["kind"], str(workspace.id))
    assert twice.status_code == 409
    other = Workspace(slug=f"o-{uuid.uuid4().hex[:6]}", name="다른 부서")
    db.add(other)
    db.commit()
    second = _link(client, admin, w["part"], w["bolt"]["id"], w["kind"], str(other.id))
    assert second.status_code == 409 and "하나만" in second.json()["error"]["message"]

    # 이력에 남고, 끊으면 사라진다.
    history = client.get(
        f"/api/objects/{w['part']}/{w['bolt']['id']}/history", headers=admin.headers
    ).json()
    assert history[0]["kind"] == "relation"
    cut = client.delete(
        f"/api/objects/{w['part']}/{w['bolt']['id']}/relations/{row['relation_id']}",
        headers=admin.headers,
    )
    assert cut.status_code == 204
    assert (
        client.get(
            f"/api/objects/{w['part']}/{w['bolt']['id']}", headers=admin.headers
        ).json()["related"]
        == []
    )


def test_허용하지_않은_원_표로는_못_잇는다(
    client: TestClient, admin: Signed, workspace: Workspace
) -> None:
    """관계 종류가 도착 타입을 정해 두지 않았으면 원 표를 뒤지지 않는다 — 「부서 id 를
    넣었더니 계정이 걸렸다」 같은 일을 막는다."""
    _dept_type(client, admin)
    part = _make_type(client, admin, label="부품")
    loose = _make_relation(client, admin, "related_to", label="관련", src_type_slugs=[part])
    bolt = _make_object(client, admin, part, label="볼트")
    denied = _link(client, admin, part, bolt["id"], loose, str(workspace.id))
    assert denied.status_code == 404


def test_지우기_전_확인과_비우기_합치기가_선을_본다(
    client: TestClient, admin: Signed, workspace: Workspace, db: Session
) -> None:
    w = _world(client, admin)
    assert (
        _link(
            client, admin, w["part"], w["bolt"]["id"], w["kind"], str(workspace.id)
        ).status_code
        == 201
    )

    refs = client.get(
        f"/api/objects/{w['part']}/{w['bolt']['id']}/references", headers=admin.headers
    ).json()
    assert [r["other_label"] for r in refs["relations"]] == [workspace.name]
    blocked = client.delete(
        f"/api/objects/{w['part']}/{w['bolt']['id']}", headers=admin.headers
    )
    assert blocked.status_code == 409

    # 합치면 이긴 쪽으로 옮겨 간다.
    nut = _make_object(client, admin, w["part"], label="너트")
    merged = client.post(
        f"/api/objects/{w['part']}/{w['bolt']['id']}/merge",
        json={"into": nut["id"]},
        headers=admin.headers,
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["relations_moved"] == 1
    nut_side = client.get(
        f"/api/objects/{w['part']}/{nut['id']}", headers=admin.headers
    ).json()
    assert [r["object_label"] for r in nut_side["related"]] == [workspace.name]

    # 비우고 지우면 선이 없어진다.
    detached = client.delete(
        f"/api/objects/{w['part']}/{nut['id']}",
        params={"mode": "detach"},
        headers=admin.headers,
    )
    assert detached.status_code == 204
    from sqlalchemy import func, select

    remaining = db.scalar(
        select(func.count())
        .select_from(ObjectLink)
        .where(ObjectLink.src_id.in_([uuid.UUID(w["bolt"]["id"]), uuid.UUID(nut["id"])]))
    )
    assert (remaining or 0) == 0


def test_관계_파일의_도착점도_부서_slug_다(
    client: TestClient, admin: Signed, workspace: Workspace
) -> None:
    w = _world(client, admin)
    rows = [
        {
            "src": "볼트",
            "relation": w["kind"],
            "dst": workspace.slug,
            "evidence_note": "조직도",
        }
    ]
    done = client.post(
        f"/api/objects/{w['part']}/relations/import-rows",
        json={"rows": rows, "apply": True},
        headers=admin.headers,
    ).json()
    assert done["applied"] is True, done
    again = client.post(
        f"/api/objects/{w['part']}/relations/import-rows",
        json={"rows": rows, "apply": False},
        headers=admin.headers,
    ).json()
    assert [r["action"] for r in again["rows"]] == ["unchanged"]

    exported = export_file(
        client, admin, f"{w['part']}/relations/export", {"format": "json"}
    ).json()
    assert exported["rows"] == rows


def test_선이_걸린_객체는_고아가_아니다(
    client: TestClient, admin: Signed, workspace: Workspace
) -> None:
    w = _world(client, admin)
    before = client.get("/api/objects/quality/report", headers=admin.headers).json()
    assert any(
        f["kind"] == "orphan" and f["type_slug"] == w["part"] for f in before["findings"]
    )
    _link(client, admin, w["part"], w["bolt"]["id"], w["kind"], str(workspace.id))
    after = client.get("/api/objects/quality/report", headers=admin.headers).json()
    assert not any(
        f["kind"] == "orphan" and f["type_slug"] == w["part"] for f in after["findings"]
    )


def test_부서를_지우기_전에_이은_객체를_센다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    other = Workspace(slug=f"o-{uuid.uuid4().hex[:6]}", name="다른 부서")
    db.add(other)
    db.commit()
    w = _world(client, admin)
    _link(client, admin, w["part"], w["bolt"]["id"], w["kind"], str(other.id))
    refs = client.get(f"/api/workspaces/{other.slug}/references", headers=admin.headers).json()
    assert any(r["table"] == "object_links" and r["count"] == 1 for r in refs)


# --- 승격 --------------------------------------------------------------------


@pytest.fixture
def ledger_source() -> Any:
    """전용 표 흉내 — 승격 시험이 「같은 id 로 채워진 표」 를 갖게 한다."""
    rows: dict[uuid.UUID, system_sources.SystemRef] = {}

    def search(db: Session, viewer: Any, q: str | None, limit: int, offset: int) -> Any:
        found = [r for r in rows.values() if not q or q in r.label]
        return found[offset : offset + limit], len(found)

    source = system_sources.SystemSource(
        key="ledger",
        label="장부",
        search=search,
        lookup=lambda db, ids: {one: rows[one] for one in ids if one in rows},
        list_all=lambda db: list(rows.values()),
    )
    system_sources.register_system_source(source)
    try:
        yield rows
    finally:
        system_sources._sources.pop("ledger", None)


def test_승격은_id_를_보존하고_관계를_선으로_옮긴다(
    client: TestClient, admin: Signed, db: Session, ledger_source: dict[uuid.UUID, Any]
) -> None:
    """다른 타입의 참조 칸에는 이 타입 객체의 uuid 가 그대로 있다. 전용 표가 같은 id 로
    답해야 그 칸이 「(지워짐)」 이 되지 않는다."""
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    part = _make_type(client, admin, label="부품", key_policy="optional")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    kind = _make_relation(
        client,
        admin,
        "supplied_by",
        label="공급받음",
        src_type_slugs=[part],
        dst_type_slugs=[vendor],
    )
    acme = _make_object(client, admin, vendor, label="ACME")
    bolt = _make_object(client, admin, part, label="볼트", properties={"vendor": acme["id"]})
    assert _link(client, admin, part, bolt["id"], kind, acme["id"]).status_code == 201

    vendor_type = db.scalar(
        __import__("sqlalchemy").select(ObjectType).where(ObjectType.slug == vendor)
    )
    assert vendor_type is not None
    from app.modules.accounts.models import User

    actor = db.scalar(__import__("sqlalchemy").select(User).where(User.email == admin.email))
    assert actor is not None

    # 전용 표가 비었으면 아무것도 안 한다.
    short = promotion.plan(db, vendor_type, "ledger")
    assert short.missing_ids == [acme["id"]] and not short.ok

    ledger_source[uuid.UUID(acme["id"])] = system_sources.SystemRef(
        id=uuid.UUID(acme["id"]), key="ACME-1", label="ACME (장부)"
    )
    done = promotion.apply(db, actor, vendor_type, "ledger")
    db.commit()
    assert done.ok and done.objects == 1 and done.relations == 1

    from sqlalchemy import func, select

    # 시험 DB 는 스위트가 함께 쓴다 — 이 시험이 만든 것만 본다.
    acme_id = uuid.UUID(acme["id"])
    assert (
        db.scalar(
            select(func.count())
            .select_from(ObjectRelation)
            .where(ObjectRelation.dst_object_id == acme_id)
        )
        or 0
    ) == 0
    link = db.scalar(select(ObjectLink).where(ObjectLink.dst_id == acme_id))
    assert link is not None and link.dst_type == vendor and link.src_type == part

    # 화면은 같은 slug·같은 id 로 계속 본다 — 이름은 이제 전용 표의 것이다.
    schema = client.get("/api/ontology/schema", headers=admin.headers).json()
    promoted = next(t for t in schema["types"] if t["slug"] == vendor)
    assert promoted["kind_class"] == "system" and promoted["system_source"] == "ledger"
    profile = client.get(f"/api/objects/{part}/{bolt['id']}", headers=admin.headers).json()
    assert profile["object"]["ref_labels"][acme["id"]] == "ACME (장부)"
    assert [r["object_label"] for r in profile["related"]] == ["ACME (장부)"]
    listed = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()
    assert [row["key"] for row in listed["items"]] == ["ACME-1"]


# --- 그래프 --------------------------------------------------------------------


def test_그래프가_원_표와_이은_선을_본다(
    client: TestClient, admin: Signed, workspace: Workspace
) -> None:
    """「담당 부서」 로만 이어진 객체가 그림에서 외톨이로 보이면 그것은 「관계없음」 으로
    읽힌다. 정의 그림의 굵기·이웃 펼치기·찾기·한 타입 전부가 전부 링크를 알아야 한다."""
    w = _world(client, admin)
    assert (
        _link(
            client, admin, w["part"], w["bolt"]["id"], w["kind"], str(workspace.id)
        ).status_code
        == 201
    )

    overview = client.get("/api/graph/overview", headers=admin.headers).json()
    edge = next(e for e in overview["edges"] if e["relation"] == w["kind"])
    assert (
        edge["count"] == 1 and edge["src_type"] == w["part"] and edge["dst_type"] == w["dept"]
    )
    dept_node = next(n for n in overview["nodes"] if n["slug"] == w["dept"])
    assert dept_node["count"] >= 1

    # 객체에서 출발하면 부서가 이웃으로 온다.
    hood = client.get(
        "/api/graph/neighborhood", params={"focus": w["bolt"]["id"]}, headers=admin.headers
    ).json()
    assert {n["type_slug"] for n in hood["nodes"]} == {w["part"], w["dept"]}
    assert [e["relation"] for e in hood["edges"]] == [w["kind"]]
    assert next(n for n in hood["nodes"] if n["id"] == w["bolt"]["id"])["degree"] == 1

    # 부서에서 출발해도 된다 — 원 표의 행이 시작점.
    from_dept = client.get(
        "/api/graph/neighborhood", params={"focus": str(workspace.id)}, headers=admin.headers
    ).json()
    assert from_dept["focus"] == str(workspace.id)
    assert {n["label"] for n in from_dept["nodes"]} >= {workspace.name, "볼트"}

    # 찾기에 부서도 걸린다.
    hits = client.get(
        "/api/graph/search", params={"q": workspace.slug}, headers=admin.headers
    ).json()
    assert any(h["id"] == str(workspace.id) and h["type_slug"] == w["dept"] for h in hits)

    # 한 타입 전부 — 원 표 타입만 골라도, 객체 타입과 함께 골라도 선이 있다.
    # 시험 DB 는 스위트가 함께 쓴다 — 부서가 한 쪽을 넘을 수 있으니 찾기로 좁힌다.
    only = client.get(
        "/api/graph/subgraph",
        params={"types": w["dept"], "q": workspace.slug},
        headers=admin.headers,
    ).json()
    assert any(n["id"] == str(workspace.id) for n in only["nodes"]) and only["edges"] == []
    both = client.get(
        "/api/graph/subgraph",
        params={"types": f"{w['part']},{w['dept']}"},
        headers=admin.headers,
    ).json()
    assert [e["relation"] for e in both["edges"]] == [w["kind"]]
