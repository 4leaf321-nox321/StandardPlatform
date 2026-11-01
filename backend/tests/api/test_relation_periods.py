"""관계의 유효 기간과 시점 질의(ADR 0039).

    P(제품) ← A1(어셈블리, ~2026-03-01) ← BOLT
    P       ← A2(어셈블리, 2026-03-01~)
    기록 R1 → A1(교체 부품 칸)

「3월 1일부터 A1 이 A2 로 바뀜」 — A1 의 끝날 = A2 의 시작일 = 3월 1일(반열린 [from, to)). 2월
기준일에는 P 아래 A1 · BOLT, 4월 기준일에는 P 아래 A2. 같은 기준일이 트리 · 하위 포함 · 조건의
「하위 전체」 · 계층 축 · 롤업 · 연결 경로 · 이웃 그림 · 관련 객체 · 조건의 관계 걸음 · 통계에
**같은 뜻으로** 걸리는지 본다 — 판정은 `edges.alive` 한 곳이다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEntry
from app.modules.objects import edges
from app.modules.objects.models import ObjectRelation, ObjectRelationTombstone
from tests.api.conftest import Signed, bundle_import, finish_job
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)

FEB = "2026-02-15"
MAR = "2026-03-01"
APR = "2026-04-15"


def _periodic_tree(client: TestClient, admin: Signed) -> tuple[str, str]:
    """유효 기간을 받는 트리 관계와 부품 타입(무게 칸 · 롤업 합)."""
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
        effectivity=True,
        src_type_slugs=[part],
        dst_type_slugs=[part],
    )
    got = client.patch(
        f"/api/ontology/types/{part}",
        json={
            "list_view": {
                "tree": {"relation": kind, "parent": "dst"},
                "rollups": [{"property": "weight", "fn": "sum"}],
            }
        },
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    return part, kind


@pytest.fixture
def bom(client: TestClient, admin: Signed) -> dict[str, Any]:
    part, kind = _periodic_tree(client, admin)
    made = {
        name: _make_object(client, admin, part, label=name, key=name, properties=props)
        for name, props in (
            ("P", {}),
            ("A1", {"weight": 1}),
            ("A2", {"weight": 2}),
            ("BOLT", {"weight": 0.5}),
        )
    }
    links: dict[str, str] = {}
    for child, parent, period in (
        ("A1", "P", {"valid_to": MAR}),
        ("A2", "P", {"valid_from": MAR}),
        ("BOLT", "A1", {}),
    ):
        got = _link(client, admin, part, made[child]["id"], kind, made[parent]["id"], **period)
        assert got.status_code == 201, got.text
        links[child] = got.json()["relation_id"]

    svc = _make_type(client, admin, label="서비스 기록", usage="log")
    _make_property(
        client,
        admin,
        svc,
        key="part",
        label="교체 부품",
        data_type="object_ref",
        ref_type_slug=part,
    )
    record = _make_object(
        client, admin, svc, label="R1", properties={"part": made["A1"]["id"]}
    )
    return {"part": part, "kind": kind, "svc": svc, "made": made, "links": links, "r1": record}


def _ids(rows: list[dict[str, Any]], made: dict[str, Any]) -> set[str]:
    names = {one["id"]: name for name, one in made.items()}
    return {names.get(one["id"], one["id"]) for one in rows}


def _tree(client: TestClient, who: Signed, part: str, **params: Any) -> dict[str, Any]:
    got = client.get(f"/api/objects/{part}/tree", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _listed(client: TestClient, who: Signed, slug: str, **params: Any) -> dict[str, Any]:
    got = client.get(f"/api/objects/{slug}", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


# --- 정의와 쓰기 -----------------------------------------------------------------


def test_꺼진_종류는_예전_그대로_기간을_거절하고_같은_선은_하나(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "uses", label="씀")
    a = _make_object(client, admin, part, label="A")
    b = _make_object(client, admin, part, label="B")

    dated = _link(client, admin, part, a["id"], kind, b["id"], valid_from="2026-01-01")
    assert dated.status_code == 422, dated.text
    assert "유효 기간을 받지 않는" in dated.json()["error"]["message"]

    assert _link(client, admin, part, a["id"], kind, b["id"]).status_code == 201
    again = _link(client, admin, part, a["id"], kind, b["id"])
    assert again.status_code == 409 and "이미 이어져" in again.json()["error"]["message"]

    # 관련 객체의 줄은 기간 없이 「지금」 이다.
    profile = client.get(f"/api/objects/{part}/{a['id']}", headers=admin.headers).json()
    row = next(one for one in profile["related"] if one["relation"] == kind)
    assert row["valid_from"] is None and row["valid_to"] is None
    assert row["period_state"] == "current"


def test_기간은_시작이_끝보다_앞이어야_한다(client: TestClient, admin: Signed) -> None:
    part, kind = _periodic_tree(client, admin)
    a = _make_object(client, admin, part, label="A")
    b = _make_object(client, admin, part, label="B")
    for valid_from, valid_to in (("2026-03-01", "2026-03-01"), ("2026-04-01", "2026-03-01")):
        got = _link(
            client,
            admin,
            part,
            a["id"],
            kind,
            b["id"],
            valid_from=valid_from,
            valid_to=valid_to,
        )
        assert got.status_code == 422, got.text
        assert "끝날" in got.json()["error"]["message"]


def test_기간이_겹치는_같은_선은_거절하고_다르면_여럿이다(
    client: TestClient, admin: Signed
) -> None:
    """BOM 에서 같은 부품이 기간을 바꿔 다시 들어온다 — 같은 두 객체 사이의 선이 여럿이다.
    맞닿은 기간(앞의 끝날 = 뒤의 시작일)은 겹치지 않는다(반열린)."""
    part, kind = _periodic_tree(client, admin)
    a = _make_object(client, admin, part, label="A")
    b = _make_object(client, admin, part, label="B")

    def link(**period: Any) -> Any:
        return _link(client, admin, part, a["id"], kind, b["id"], **period)

    assert link(valid_from="2026-01-01", valid_to=MAR).status_code == 201
    assert link(valid_from=MAR, valid_to="2026-06-01").status_code == 201  # 맞닿음
    assert link(valid_from="2026-09-01").status_code == 201  # 다시 들어온다

    overlap = link(valid_from="2026-05-01", valid_to="2026-07-01")
    assert overlap.status_code == 409, overlap.text
    assert "겹치는" in overlap.json()["error"]["message"]
    # 기간이 빈 선은 늘 유효 — 무엇과도 겹친다.
    assert link().status_code == 409

    profile = client.get(
        f"/api/objects/{part}/{a['id']}", params={"all_periods": True}, headers=admin.headers
    ).json()
    periods = sorted(
        (one["valid_from"], one["valid_to"]) for one in profile["related"] if one["outgoing"]
    )
    assert periods == [
        ("2026-01-01", MAR),
        (MAR, "2026-06-01"),
        ("2026-09-01", None),
    ]


def test_기간이_빈_선이_있으면_기간_있는_같은_선을_못_넣는다(
    client: TestClient, admin: Signed
) -> None:
    part, kind = _periodic_tree(client, admin)
    a = _make_object(client, admin, part, label="A")
    b = _make_object(client, admin, part, label="B")
    assert _link(client, admin, part, a["id"], kind, b["id"]).status_code == 201
    later = _link(client, admin, part, a["id"], kind, b["id"], valid_from="2030-01-01")
    assert later.status_code == 409, later.text


def test_DB_가_겹침을_막는다_앱의_검사를_지나도(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """같은 순간의 두 요청은 서로를 못 본다 — 그때 막는 것은 배제 제약이다."""
    part, kind = _periodic_tree(client, admin)
    a = _make_object(client, admin, part, label="A")
    b = _make_object(client, admin, part, label="B")
    assert (
        _link(client, admin, part, a["id"], kind, b["id"], valid_to="2026-06-01").status_code
        == 201
    )
    src, dst = uuid.UUID(a["id"]), uuid.UUID(b["id"])
    db.add(
        ObjectRelation(
            src_object_id=src,
            dst_object_id=dst,
            relation=kind,
            valid_from=date(2026, 5, 1),
        )
    )
    with pytest.raises(Exception) as caught:
        edges.flush(db)
    assert "겹치는" in str(caught.value)
    db.rollback()

    # 꺼진 종류에 기간을 적으면 트리거가 거절한다.
    plain = _make_relation(client, admin, "uses", label="씀")
    db.add(
        ObjectRelation(
            src_object_id=src, dst_object_id=dst, relation=plain, valid_from=date(2026, 1, 1)
        )
    )
    with pytest.raises(Exception) as refused:
        edges.flush(db)
    assert "유효 기간" in str(refused.value)
    db.rollback()


def test_개수_제약은_같은_때에_하나(client: TestClient, admin: Signed) -> None:
    """「한 부품의 공급사는 하나」 — 3월부터 공급사가 바뀌면 두 선이 함께 있어도 같은 때에는
    하나다."""
    part = _make_type(client, admin, label="부품")
    vendor = _make_type(client, admin, label="공급사")
    kind = _make_relation(
        client, admin, "supplied_by", label="공급", cardinality="many_to_one", effectivity=True
    )
    bolt = _make_object(client, admin, part, label="볼트")
    s1, s2, s3 = (
        _make_object(client, admin, vendor, label=name) for name in ("S1", "S2", "S3")
    )
    assert (
        _link(client, admin, part, bolt["id"], kind, s1["id"], valid_to=MAR).status_code == 201
    )
    assert (
        _link(client, admin, part, bolt["id"], kind, s2["id"], valid_from=MAR).status_code
        == 201
    )
    third = _link(client, admin, part, bolt["id"], kind, s3["id"], valid_from="2026-05-01")
    assert third.status_code == 409 and "하나만" in third.json()["error"]["message"]


def test_유효_기간을_끄려면_기간_든_선이_없어야_한다(
    client: TestClient, admin: Signed
) -> None:
    part, kind = _periodic_tree(client, admin)
    a = _make_object(client, admin, part, label="A")
    b = _make_object(client, admin, part, label="B")
    linked = _link(client, admin, part, a["id"], kind, b["id"], valid_to="2030-01-01")
    assert linked.status_code == 201

    off = client.patch(
        f"/api/ontology/relation-types/{kind}",
        json={"effectivity": False},
        headers=admin.headers,
    )
    assert off.status_code == 409 and "기간이 든 관계가 1개" in off.json()["error"]["message"]

    cleared = client.patch(
        f"/api/objects/{part}/{a['id']}/relations/{linked.json()['relation_id']}",
        json={"valid_to": None},
        headers=admin.headers,
    )
    assert cleared.status_code == 200, cleared.text
    off = client.patch(
        f"/api/ontology/relation-types/{kind}",
        json={"effectivity": False},
        headers=admin.headers,
    )
    assert off.status_code == 200 and off.json()["effectivity"] is False
    # 꺼진 종류는 예전처럼 같은 선 하나 — 사본(트리거)이 맞춰졌다.
    assert _link(client, admin, part, a["id"], kind, b["id"]).status_code == 409
    assert (
        _link(client, admin, part, a["id"], kind, b["id"], valid_from="2031-01-01").status_code
        == 422
    )


# --- 기준일 -----------------------------------------------------------------------


def test_기준일이_트리와_하위_포함에_같게_걸린다(
    client: TestClient, admin: Signed, bom: dict[str, Any]
) -> None:
    part, made = bom["part"], bom["made"]
    p = made["P"]["id"]

    def children(as_of: str) -> set[str]:
        body = _tree(client, admin, part, parent=p, as_of=as_of)
        assert body["as_of"] == as_of
        return _ids(body["nodes"], made)

    assert children(FEB) == {"A1"}
    # 경계일 — 끝날은 제외, 시작일은 포함.
    assert children("2026-02-28") == {"A1"}
    assert children(MAR) == {"A2"}
    assert children(APR) == {"A2"}

    # 뿌리 — 4월에는 A1 이 P 아래가 아니라 뿌리(BOLT 를 거느린)다.
    roots = _ids(_tree(client, admin, part, as_of=APR)["nodes"], made)
    assert roots == {"P", "A1"}
    assert _ids(_tree(client, admin, part, as_of=FEB)["nodes"], made) == {"P"}

    def under(as_of: str) -> set[str]:
        return _ids(_listed(client, admin, part, under=p, as_of=as_of)["items"], made)

    assert under(FEB) == {"P", "A1", "BOLT"}
    assert under(APR) == {"P", "A2"}

    # 조건의 「하위 전체」 — 목록 · 통계 · 지표가 같은 말을 쓴다.
    by_condition = _listed(client, admin, part, **{"f.id.under": p, "as_of": FEB})
    assert _ids(by_condition["items"], made) == {"P", "A1", "BOLT"}
    by_ref = _listed(client, admin, bom["svc"], **{"f.part.under": p, "as_of": FEB})
    assert by_ref["total"] == 1
    assert (
        _listed(client, admin, bom["svc"], **{"f.part.under": p, "as_of": APR})["total"] == 0
    )


def test_기준일이_관계_걸음_조건과_통계에_걸린다(
    client: TestClient, admin: Signed, bom: dict[str, Any]
) -> None:
    part, kind, made = bom["part"], bom["kind"], bom["made"]
    p = made["P"]["id"]
    field = f"f.out.{kind}.eq"
    assert _ids(_listed(client, admin, part, **{field: p, "as_of": FEB})["items"], made) == {
        "A1"
    }
    assert _ids(_listed(client, admin, part, **{field: p, "as_of": APR})["items"], made) == {
        "A2"
    }

    def bars(as_of: str, group_by: str, slug: str = part) -> dict[str, int]:
        got = client.get(
            f"/api/objects/{slug}/summary",
            params={"group_by": group_by, "as_of": as_of},
            headers=admin.headers,
        )
        assert got.status_code == 200, got.text
        return {one["label"]: one["count"] for one in got.json()["buckets"]}

    # 관계 기준 — 상위가 P 인 것.
    assert bars(FEB, f"out.{kind}").get("P") == 1
    feb_by_parent = bars(FEB, f"out.{kind}")
    apr_by_parent = bars(APR, f"out.{kind}")
    assert feb_by_parent.get("A1") == 1  # BOLT
    assert "A1" in apr_by_parent  # BOLT 는 늘 A1 아래
    # 계층 축 — 기록의 교체 부품을 뿌리로 접는다.
    assert bars(FEB, "properties.part@0", bom["svc"]) == {"P": 1}
    assert bars(APR, "properties.part@0", bom["svc"]) == {"A1": 1}


def test_기준일이_롤업_경로_이웃에_걸린다(
    client: TestClient, admin: Signed, bom: dict[str, Any]
) -> None:
    part, made = bom["part"], bom["made"]
    p, bolt = made["P"]["id"], made["BOLT"]["id"]

    def rollup(as_of: str) -> dict[str, Any]:
        got = client.get(
            f"/api/objects/{part}/{p}/rollup", params={"as_of": as_of}, headers=admin.headers
        )
        assert got.status_code == 200, got.text
        return dict(got.json()[0])

    assert rollup(FEB)["value"] == 1.5 and rollup(FEB)["descendants"] == 2
    assert rollup(APR)["value"] == 2 and rollup(APR)["descendants"] == 1

    def path(as_of: str) -> dict[str, Any]:
        got = client.get(
            "/api/graph/path",
            params={"source": bolt, "target": p, "as_of": as_of},
            headers=admin.headers,
        )
        assert got.status_code == 200, got.text
        return dict(got.json())

    feb = path(FEB)
    assert feb["found"] is True and feb["length"] == 2 and feb["as_of"] == FEB
    a1_edge = next(one for one in feb["edges"] if one["dst"] == p)
    assert a1_edge["valid_to"] == MAR and a1_edge["valid_from"] is None
    assert path(APR)["found"] is False

    def neighbors(as_of: str) -> set[str]:
        got = client.get(
            "/api/graph/neighborhood",
            params={"focus": p, "as_of": as_of},
            headers=admin.headers,
        )
        assert got.status_code == 200, got.text
        return _ids(got.json()["nodes"], made) - {"P"}

    assert neighbors(FEB) == {"A1"}
    assert neighbors(APR) == {"A2"}


def test_관련_객체는_기본이_오늘이고_지난_앞으로의_선을_포함할_수_있다(
    client: TestClient, admin: Signed
) -> None:
    part, kind = _periodic_tree(client, admin)
    today = date.today()
    hub = _make_object(client, admin, part, label="허브")
    past = _make_object(client, admin, part, label="지난")
    now = _make_object(client, admin, part, label="지금")
    later = _make_object(client, admin, part, label="앞으로")
    for row, period in (
        (past, {"valid_to": today.isoformat()}),  # 끝날 = 오늘 → 오늘은 무효(제외)
        (now, {"valid_from": today.isoformat()}),  # 시작일 = 오늘 → 오늘부터 유효(포함)
        (later, {"valid_from": (today + timedelta(days=1)).isoformat()}),
    ):
        assert (
            _link(client, admin, part, row["id"], kind, hub["id"], **period).status_code == 201
        )

    def related(**params: Any) -> dict[str, str]:
        body = client.get(
            f"/api/objects/{part}/{hub['id']}", params=params, headers=admin.headers
        ).json()
        return {one["object_label"]: one["period_state"] for one in body["related"]}

    assert related() == {"지금": "current"}
    assert related(all_periods=True) == {"지난": "past", "지금": "current", "앞으로": "future"}
    # 기본 트리도 오늘 — 앞으로의 선 · 지난 선은 안 따라간다.
    tree = _tree(client, admin, part, parent=hub["id"])
    assert {one["label"] for one in tree["nodes"]} == {"지금"}
    assert tree["as_of"] == today.isoformat()


def test_기간만_고쳐도_감사와_이력에_앞뒤가_남는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    part, kind = _periodic_tree(client, admin)
    a = _make_object(client, admin, part, label="A")
    b = _make_object(client, admin, part, label="B")
    linked = _link(client, admin, part, a["id"], kind, b["id"], valid_from="2026-01-01")
    assert linked.status_code == 201
    relation_id = linked.json()["relation_id"]

    patched = client.patch(
        f"/api/objects/{part}/{a['id']}/relations/{relation_id}",
        json={"valid_to": "2026-06-01"},
        headers=admin.headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["valid_from"] == "2026-01-01"
    assert patched.json()["valid_to"] == "2026-06-01"

    entry = db.scalars(
        select(AuditEntry)
        .where(
            AuditEntry.target_id == uuid.UUID(relation_id),
            AuditEntry.action == "object.relation.update",
        )
        .order_by(AuditEntry.seq.desc())
    ).first()
    assert entry is not None
    assert entry.changes["changed"] == {"valid_to": {"before": None, "after": "2026-06-01"}}
    assert entry.changes["valid_from"] == "2026-01-01"

    history = client.get(f"/api/objects/{part}/{a['id']}/history", headers=admin.headers)
    assert history.status_code == 200, history.text
    lines = [one["relation"] for one in history.json() if one["kind"] == "relation"]
    assert lines[0]["changed"] == {"valid_to": {"before": None, "after": "2026-06-01"}}
    assert lines[0]["valid_to"] == "2026-06-01"


def test_선을_끊으면_무덤에_기간이_남는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    part, kind = _periodic_tree(client, admin)
    a = _make_object(client, admin, part, label="A")
    b = _make_object(client, admin, part, label="B")
    linked = _link(client, admin, part, a["id"], kind, b["id"], valid_to=MAR)
    gone = client.delete(
        f"/api/objects/{part}/{a['id']}/relations/{linked.json()['relation_id']}",
        headers=admin.headers,
    )
    assert gone.status_code == 204
    grave = db.scalar(
        select(ObjectRelationTombstone).where(
            ObjectRelationTombstone.src_object_id == uuid.UUID(a["id"])
        )
    )
    assert (
        grave is not None and grave.valid_to == date(2026, 3, 1) and grave.valid_from is None
    )


# --- 파일 · 묶음 · 되돌리기 ----------------------------------------------------------------


def test_관계_파일에_기간_열이_있다(client: TestClient, admin: Signed) -> None:
    part, kind = _periodic_tree(client, admin)
    _make_object(client, admin, part, label="A", key="A")
    _make_object(client, admin, part, label="B", key="B")
    rows = [
        {"src": "A", "relation": kind, "dst": "B", "valid_to": MAR},
        {"src": "A", "relation": kind, "dst": "B", "valid_from": "2026.3.1"},
    ]

    def send(body_rows: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        got = client.post(
            f"/api/objects/{part}/relations/import-rows",
            json={"rows": body_rows, "apply": True, **extra},
            headers=admin.headers,
        )
        assert got.status_code == 200, got.text
        return dict(got.json())

    first = send(rows)
    assert first["counts"].get("create") == 2, first
    assert send(rows)["counts"].get("unchanged") == 2  # 같은 파일 다시 — 두 겹이 안 된다

    # 내려받아 끝날을 적어 다시 넣는 길 — 겹치는 있는 선 하나의 기간을 고친다.
    edited = send(
        [
            {
                "src": "A",
                "relation": kind,
                "dst": "B",
                "valid_from": MAR,
                "valid_to": "2026-09-01",
            }
        ]
    )
    assert edited["counts"].get("update") == 1, edited

    # 파일 안에서 기간이 겹치면 그 줄의 오류다.
    clash = client.post(
        f"/api/objects/{part}/relations/import-rows",
        json={
            "rows": [
                {"src": "A", "relation": kind, "dst": "B", "valid_from": "2027-01-01"},
                {"src": "A", "relation": kind, "dst": "B", "valid_from": "2027-06-01"},
            ]
        },
        headers=admin.headers,
    ).json()
    assert any("겹치는" in (one.get("message") or "") for one in clash["rows"]), clash

    exported = client.post(f"/api/objects/{part}/relations/export", headers=admin.headers)
    assert exported.status_code == 202, exported.text


def test_묶음이_기간을_싣고_되돌리기가_기간을_되살린다(
    client: TestClient, admin: Signed
) -> None:
    part, kind = _periodic_tree(client, admin)
    _make_object(client, admin, part, label="A", key="A")
    _make_object(client, admin, part, label="B", key="B")
    loaded = bundle_import(
        client,
        admin,
        {
            "relations": [
                {
                    "type_slug": part,
                    "rows": [
                        {"src": "A", "relation": kind, "dst": "B", "valid_to": MAR},
                        {"src": "A", "relation": kind, "dst": "B", "valid_from": MAR},
                    ],
                }
            ],
            "apply": True,
        },
    )
    assert loaded["applied"] is True, loaded

    # 「파일대로 맞춤」 — 한 기간만 남기면 다른 기간의 선이 끊긴다(기간까지가 선의 정체다).
    replaced = bundle_import(
        client,
        admin,
        {
            "relations": [
                {
                    "type_slug": part,
                    "mode": "replace",
                    "rows": [{"src": "A", "relation": kind, "dst": "B", "valid_to": MAR}],
                }
            ],
            "apply": True,
        },
    )
    assert replaced["applied"] is True, replaced
    a = next(one for one in _listed(client, admin, part)["items"] if one["key"] == "A")

    def periods() -> set[tuple[str | None, str | None]]:
        body = client.get(
            f"/api/objects/{part}/{a['id']}",
            params={"all_periods": True},
            headers=admin.headers,
        ).json()
        return {
            (one["valid_from"], one["valid_to"]) for one in body["related"] if one["outgoing"]
        }

    assert periods() == {(None, MAR)}

    undone = client.post(
        f"/api/bundles/runs/{replaced['run_id']}/undo",
        params={"apply": True},
        headers=admin.headers,
    )
    assert undone.status_code == 202, undone.text
    done = finish_job(client, admin, undone.json())
    assert done["status"] == "done" and done["result"]["ok"] is True, done
    # 끊었던 선이 **그 기간 그대로** 돌아온다.
    assert periods() == {(None, MAR), (MAR, None)}


# --- 바깥으로 나가는 것 ---------------------------------------------------------------


def test_코어_API_는_오늘_유효한_선과_기간을_싣고_경계일을_알린다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    tag = uuid.uuid4().hex[:6]
    part = _make_type(client, admin, label=f"부품{tag}", key_policy="optional")
    for slug in (part,):
        got = client.patch(
            f"/api/ontology/types/{slug}", json={"core": True}, headers=admin.headers
        )
        assert got.status_code == 200, got.text
    kind = _make_relation(
        client,
        admin,
        "bom_of",
        label="구성",
        src_type_slugs=[part],
        dst_type_slugs=[part],
        effectivity=True,
    )
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    rows = {
        name: _make_object(client, admin, part, label=name, key=name)
        for name in ("HUB", "OLD", "NEW", "LATER")
    }
    hub = rows["HUB"]["id"]
    for name, period in (
        ("OLD", {"valid_to": yesterday}),  # 어제 끝났다
        ("NEW", {"valid_from": yesterday}),  # 어제 시작했다
        ("LATER", {"valid_from": (today + timedelta(days=30)).isoformat()}),
    ):
        got = _link(client, admin, part, rows[name]["id"], kind, hub, **period)
        assert got.status_code == 201, got.text

    catalog = client.get("/api/core", headers=admin.headers).json()
    entry = next(one for one in catalog["types"] if one["slug"] == part)
    promise = next(one for one in entry["relation_kinds"] if one["slug"] == kind)
    assert promise["effectivity"] is True

    first = client.get(f"/api/core/{part}/relations", headers=admin.headers).json()
    assert {one["src"]: one["valid_from"] for one in first["items"]} == {"NEW": yesterday}

    # 선이 바뀐 지는 오래고, 그 사이에 경계일이 지났다 — 시각이 아니라 날짜가 일이다.
    long_ago = datetime.now(UTC) - timedelta(days=5)
    db.execute(
        update(ObjectRelation)
        .where(ObjectRelation.relation == kind)
        .values(updated_at=long_ago)
    )
    db.commit()
    since = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    feed = client.get(
        f"/api/core/{part}/relations", params={"since": since}, headers=admin.headers
    ).json()
    state = {one["src"]: one["deleted"] for one in feed["items"]}
    assert state == {"OLD": True, "NEW": False}, feed


def test_RDF_는_오늘_유효한_선만_싣고_기간을_진술에_단다(
    client: TestClient, admin: Signed, bom: dict[str, Any]
) -> None:
    from rdflib import RDF, Graph

    got = client.get("/api/rdf/data", params={"type": bom["part"]}, headers=admin.headers)
    assert got.status_code == 200, got.text
    graph = Graph().parse(data=got.text, format="turtle")
    predicate = next(
        one for one in set(graph.predicates()) if str(one).endswith(f"rel.{bom['kind']}")
    )
    pairs = {
        (str(s).rsplit("/", 1)[-1], str(o).rsplit("/", 1)[-1])
        for s, o in graph.subject_objects(predicate)
    }
    today = date.today().isoformat()
    expected = {("BOLT", "A1")} | ({("A2", "P")} if today >= MAR else {("A1", "P")})
    assert pairs == expected
    stated = list(graph.subjects(RDF.type, RDF.Statement))
    assert len(stated) == 1  # 기간이 든 선 하나만(오늘 유효한 것)


# --- 판정 한 곳 -----------------------------------------------------------------------


def test_ORM_판정과_SQL_판정이_같은_줄을_고른다(
    client: TestClient, admin: Signed, db: Session, bom: dict[str, Any]
) -> None:
    """두 표기(`alive` · `alive_sql`)는 같은 규칙이다 — 뒤에 규칙을 더할 때 한쪽만 고치면
    트리와 롤업이 다른 선을 센다."""
    kind = bom["kind"]
    for day in (
        None,
        date(2026, 2, 28),
        date(2026, 3, 1),
        date(2026, 4, 15),
        date(2099, 1, 1),
    ):
        by_orm = set(
            db.scalars(
                select(ObjectRelation.id).where(
                    ObjectRelation.relation == kind, edges.alive(ObjectRelation, day)
                )
            )
        )
        by_sql = set(
            db.scalars(
                text(
                    f"SELECT r.id FROM object_relations r WHERE r.relation = :k "
                    f"AND {edges.alive_sql('r', day)}"
                ),
                {"k": kind},
            )
        )
        assert by_orm == by_sql, day


def test_경계일이_지났는지_증분이_본다(
    client: TestClient, admin: Signed, db: Session, bom: dict[str, Any]
) -> None:
    kind = bom["kind"]
    assert edges.crossed(db, [kind], after=date(2026, 2, 28), until=date(2026, 3, 1))
    assert not edges.crossed(db, [kind], after=date(2026, 3, 1), until=date(2026, 3, 31))
    assert not edges.crossed(db, [kind], after=date(2026, 3, 1), until=date(2026, 3, 1))
