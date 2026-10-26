"""타입별 객체 수의 계수표와 그래프 선 굵기의 찍어 둔 값(ADR 0038).

계수표는 읽을 때 세지 않는다 — 그래서 **세었을 때와 늘 같아야** 한다. 객체가 생기고
지워지는 길(만들기 · 여러 줄 한 번에 · 지우기 · 되살리기 · 합치기 · 타입 옮기기 · 영구
삭제)마다 센 값과 견준다. 선 굵기는 찍어 둔 값이라 처음에는 「세는 중」, 작업이 돈 뒤에는 그
값, 낡으면 다시 센다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.modules.jobs import services as job_services
from app.modules.jobs.models import Job
from app.modules.objects import graphcounts, typecounts
from app.modules.objects.models import GraphEdgeSnapshot, ObjectInstance
from tests.api.conftest import Signed
from tests.api.test_ontology import _link, _make_object, _make_relation, _make_type


def _type_id(db: Session, slug: str) -> uuid.UUID:
    from app.modules.ontology.models import ObjectType

    found = db.scalar(select(ObjectType.id).where(ObjectType.slug == slug))
    assert found is not None
    return found


def _both(
    db: Session, ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    """(계수표, 센 값) — 그 타입들만."""
    db.expire_all()
    counted = {key: value for key, value in typecounts.counts(db).items() if key in ids}
    actual = {
        type_id: int(n)
        for type_id, n in db.execute(
            select(ObjectInstance.type_id, func.count())
            .where(ObjectInstance.type_id.in_(ids), ObjectInstance.deleted_at.is_(None))
            .group_by(ObjectInstance.type_id)
        )
    }
    return counted, actual


def test_계수표는_객체가_생기고_지워지는_길마다_센_값과_같다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    a = _make_type(client, admin, label="부품")
    b = _make_type(client, admin, label="공급사")
    ids = [_type_id(db, a), _type_id(db, b)]
    made = [_make_object(client, admin, a, label=f"부품{i}") for i in range(3)]
    _make_object(client, admin, b, label="공급사0")

    def same(expected: dict[uuid.UUID, int]) -> None:
        counted, actual = _both(db, ids)
        assert counted == actual == expected

    same({ids[0]: 3, ids[1]: 1})

    # 여러 줄 한 번에(문장 하나에 여러 행 — 문장 트리거가 한 번에 센다).
    db.add_all(
        ObjectInstance(type_id=ids[1], label=f"묶음{i}", properties={}) for i in range(5)
    )
    db.commit()
    same({ids[0]: 3, ids[1]: 6})

    # 지우기(목록에서 내려감) · 되살리기.
    gone = client.delete(f"/api/objects/{a}/{made[0]['id']}", headers=admin.headers)
    assert gone.status_code == 204, gone.text
    same({ids[0]: 2, ids[1]: 6})
    db.execute(
        text("UPDATE objects SET deleted_at = NULL WHERE id = :id"), {"id": made[0]["id"]}
    )
    db.commit()
    same({ids[0]: 3, ids[1]: 6})

    # 합치기 — 진 쪽이 내려간다.
    merged = client.post(
        f"/api/objects/{a}/{made[1]['id']}/merge",
        json={"into": made[2]["id"]},
        headers=admin.headers,
    )
    assert merged.status_code == 200, merged.text
    same({ids[0]: 2, ids[1]: 6})

    # 타입 옮기기 · 속성만 고치기(계수표가 안 움직인다) · 영구 삭제.
    db.execute(
        text("UPDATE objects SET type_id = :b WHERE id = :id"),
        {"b": ids[1], "id": made[0]["id"]},
    )
    db.execute(
        text("UPDATE objects SET properties = '{\"x\": 1}' WHERE type_id = :b"), {"b": ids[1]}
    )
    db.commit()
    same({ids[0]: 1, ids[1]: 7})
    db.execute(
        text("DELETE FROM objects WHERE type_id = :b AND label LIKE '묶음%'"), {"b": ids[1]}
    )
    db.execute(
        text("DELETE FROM objects WHERE id = :id"), {"id": made[1]["id"]}
    )  # 이미 내려감
    db.commit()
    same({ids[0]: 1, ids[1]: 2})


def test_증감_줄이_쌓이면_읽는_쪽이_타입마다_한_줄로_합친다(db: Session) -> None:
    ghost = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO object_type_counts (type_id, delta) "
            "SELECT :t, CASE WHEN n % 2 = 0 THEN 2 ELSE -1 END "
            "FROM generate_series(1, :n) AS n"
        ),
        {"t": ghost, "n": typecounts.COMPACT_AT + 2},
    )
    db.commit()
    expected = (typecounts.COMPACT_AT + 2) // 2  # +2 · -1 이 쌍으로
    assert typecounts.counts(db)[ghost] == expected
    db.expire_all()
    rows = db.scalar(
        text("SELECT count(*) FROM object_type_counts WHERE type_id = :t"), {"t": ghost}
    )
    assert rows == 1  # 합쳤다
    assert typecounts.counts(db)[ghost] == expected


def _overview(client: TestClient, who: Signed) -> dict[str, Any]:
    got = client.get("/api/graph/overview", headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _edge(body: dict[str, Any], relation: str) -> dict[str, Any]:
    return next(one for one in body["edges"] if one["relation"] == relation)


def test_그래프_선_굵기는_찍어_둔_값이고_없거나_낡으면_다시_센다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    part = _make_type(client, admin, label="부품")
    vendor = _make_type(client, admin, label="공급사")
    kind = _make_relation(
        client, admin, "supplied_by", src_type_slugs=[part], dst_type_slugs=[vendor]
    )
    bolt = _make_object(client, admin, part, label="볼트")
    acme = _make_object(client, admin, vendor, label="ACME")
    assert _link(client, admin, part, bolt["id"], kind, acme["id"]).status_code == 201
    db.execute(text("DELETE FROM graph_edge_snapshots"))
    db.execute(text("DELETE FROM jobs WHERE kind = :k"), {"k": graphcounts.JOB_KIND})
    db.commit()

    # 처음 — 아직 안 셌다: 「세는 중」 이고 세는 작업이 하나 든다(두 번 열어도 하나).
    first = _overview(client, admin)
    assert first["counting"] is True and first["counted_at"] is None
    assert _edge(first, kind)["count"] == 0
    _overview(client, admin)
    waiting = db.scalars(select(Job).where(Job.kind == graphcounts.JOB_KIND)).all()
    assert len(waiting) == 1 and waiting[0].status == "queued"
    while job_services.process_one("test-worker"):
        pass

    counted = _overview(client, admin)
    assert counted["counting"] is False and counted["counted_at"]
    assert _edge(counted, kind)["count"] == 1
    # 노드 수는 계수표 — 찍어 두지 않아도 지금 수다.
    assert {one["slug"]: one["count"] for one in counted["nodes"]}[part] == 1

    # 낡으면 있는 값을 그리면서 다시 세는 작업을 넣는다.
    db.execute(
        text("UPDATE graph_edge_snapshots SET counted_at = :at"),
        {"at": datetime.now(UTC) - graphcounts.STALE_AFTER - timedelta(minutes=1)},
    )
    db.commit()
    stale = _overview(client, admin)
    assert stale["counting"] is False and _edge(stale, kind)["count"] == 1
    assert (
        db.scalar(
            select(func.count()).where(
                Job.kind == graphcounts.JOB_KIND, Job.status == "queued"
            )
        )
        == 1
    )
    assert db.get(GraphEdgeSnapshot, 1) is not None


@pytest.mark.usefixtures("department_scope")
def test_부서로_조회를_좁힌_설치의_멤버는_그_자리에서_센다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """사람마다 보이는 수가 달라 찍어 둔 값(전부를 본 수)을 못 쓴다 — 남의 부서 수가 샌다."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "near", src_type_slugs=[part], dst_type_slugs=[part])
    one = _make_object(client, admin, part, label="하나", workspace_slug=member.workspace)
    two = _make_object(client, admin, part, label="둘", workspace_slug=member.workspace)
    assert _link(client, admin, part, one["id"], kind, two["id"]).status_code == 201

    body = _overview(client, member)
    assert body["counting"] is False and body["counted_at"] is None
    assert _edge(body, kind)["count"] == 1
