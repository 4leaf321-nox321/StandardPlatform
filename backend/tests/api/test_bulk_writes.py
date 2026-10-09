"""일괄 입력의 **쓰기 길** — 새 줄은 ORM 을 거치지 않고 COPY 로 들어간다(`shared/copyin.py`).

빨라진 대신 지켜야 할 것: ORM 으로 넣던 줄과 **같은 모양**인가(기본값 · 시각 · 이력 표시),
참조 색인 트리거가 도나, 감사 기록이 줄마다 같은 말로 **같은 차례**로 남나(묶음 한 줄이 맨
뒤), 바깥 알림(웹훅 · 지켜보기)이 줄마다 그대로 나가나, 되돌릴 기록의 차례가 이어지나.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.audit.models import AuditEntry
from app.modules.bundles.models import BundleUndoEntry
from app.modules.objects import aliases, bulk
from app.modules.objects.models import ObjectInstance, ObjectRef, ObjectRelation
from app.modules.ontology.models import ObjectType
from app.modules.workspaces.models import Workspace
from app.shared import copyin, events
from tests.api.conftest import Signed, bundle_import
from tests.api.test_ontology import _make_object, _make_property, _make_relation, _make_type


def _since(db: Session, seq: int) -> list[AuditEntry]:
    return list(
        db.scalars(select(AuditEntry).where(AuditEntry.seq > seq).order_by(AuditEntry.seq))
    )


def _last_seq(db: Session) -> int:
    return int(db.scalar(select(func.coalesce(func.max(AuditEntry.seq), 0))) or 0)


def test_새로_만든_객체는_ORM_으로_넣던_것과_같은_모양이다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    company = _make_type(client, admin, label="기업", key_policy="required")
    vendor = _make_object(client, admin, company, key="C-1", label="미국사")
    tool = _make_type(client, admin, label="툴", key_policy="required")
    _make_property(
        client,
        admin,
        tool,
        key="vendor",
        label="개발사",
        data_type="object_ref",
        ref_type_slug=company,
    )
    _make_property(
        client,
        admin,
        tool,
        key="grade",
        label="등급",
        data_type="enum",
        enum_options=["A", "B"],
        default_value="A",
    )
    _make_property(client, admin, tool, key="weight", label="무게", data_type="number")
    old = _make_object(client, admin, tool, key="T-0", label="옛 툴", properties={"weight": 1})
    object_type = db.scalar(select(ObjectType).where(ObjectType.slug == tool))
    user = db.scalar(select(User).where(User.email == admin.email))
    owner = db.scalar(select(Workspace.id).where(Workspace.slug == admin.workspace))
    assert object_type is not None and user is not None and owner is not None
    rows: list[dict[str, Any]] = [
        {
            "key": "T-1",
            "label": "해석기",
            "vendor": "C-1",
            "weight": "2.5",
            "aliases": "솔버;해석 툴",
        },
        {"key": "T-0", "label": "옛 툴", "weight": "4"},
        {
            "key": "T-2",
            "label": "메셔",
            "description": "격자",
            "status": "deprecated",
            "valid_from_year": "2020",
            "valid_to_year": "2024",
        },
    ]
    before = _last_seq(db)
    heard: list[events.ChangeEvent] = []

    def listen(staged: list[events.ChangeEvent]) -> None:
        heard.extend(staged)

    events.register_listener(listen)
    try:
        plan = bulk.apply_objects(db, user, object_type, rows, owner_workspace_id=owner)
    finally:
        events._listeners.remove(listen)
    assert plan.ok, [one.message for one in plan.rows]
    assert [one.action for one in plan.rows] == ["create", "update", "create"]

    made = {
        one.key: one
        for one in db.scalars(
            select(ObjectInstance).where(ObjectInstance.type_id == object_type.id)
        )
    }
    first, second = made["T-1"], made["T-2"]
    assert plan.rows[0].object_id == first.id and plan.rows[2].object_id == second.id
    # 계획이 검증한 그대로 — 기본값까지 들어간다.
    assert first.properties == {"vendor": vendor["id"], "grade": "A", "weight": 2.5}
    assert second.properties == {"grade": "A"}
    # ORM 의 파이썬 기본값과 같은 것이 DB 기본값으로 선다.
    for one in (first, second):
        assert one.owner_workspace_id == owner
        assert one.created_by_id == user.id
        assert one.human_edits == {} and one.previous_keys == []
        assert one.merged_into_id is None and one.deleted_at is None
        # 같은 트랜잭션의 now() — 별칭을 붙여도 바뀐 때가 따로 서지 않는다.
        assert one.updated_at == one.created_at
    assert (first.description, first.status) == ("", "active")
    assert (first.valid_from_year, first.valid_to_year) == (None, None)
    assert (second.description, second.status) == ("격자", "deprecated")
    assert (second.valid_from_year, second.valid_to_year) == (2020, 2024)
    assert made["T-0"].properties["weight"] == 4
    # 참조 색인 트리거가 돌았다(COPY 도 INSERT 트리거를 부른다).
    refs = list(db.scalars(select(ObjectRef).where(ObjectRef.src_id == first.id)))
    assert [(one.key, str(one.dst_id)) for one in refs] == [("vendor", vendor["id"])]
    # 별칭은 객체가 들어간 뒤에 붙는다.
    assert aliases.human_of(db, [first.id])[first.id] == ["솔버", "해석 툴"]

    # 감사 기록 — 줄마다 하나, **줄 차례대로**, 묶음 한 줄이 맨 뒤.
    logged = _since(db, before)
    assert [(one.action, one.target_id) for one in logged] == [
        ("object.create", first.id),
        ("object.update", uuid.UUID(old["id"])),
        ("object.create", second.id),
        ("object.import", None),
    ]
    for entry in logged[:3]:
        assert entry.reason == "일괄 가져오기"
        assert entry.actor_id == user.id
        assert entry.actor_label == (user.display_name or user.email)
        assert entry.workspace_id == owner
        assert entry.created_at is not None
    assert logged[0].target_label == f"{tool}:해석기" and logged[0].changes == {}
    assert logged[1].changes["properties"]["after"]["weight"] == 4
    assert logged[3].changes["create"] == 2 and logged[3].changes["update"] == 1

    # 바깥 알림 — 줄마다 그대로(`record_rows` 처럼 한 줄로 줄지 않는다), 같은 차례로.
    assert [(one.action, one.target_id) for one in heard] == [
        (one.action, one.target_id) for one in logged
    ]
    assert heard[0].target_label == f"{tool}:해석기" and heard[0].actor_id == user.id


def test_관계도_같은_모양으로_들어간다(client: TestClient, admin: Signed, db: Session) -> None:
    cause = _make_type(client, admin, label="원인", key_policy="required")
    effect = _make_type(client, admin, label="결과", key_policy="required")
    kind = _make_relation(
        client, admin, label="일으킴", src_type_slugs=[cause], dst_type_slugs=[effect]
    )
    src = _make_object(client, admin, cause, key="C-1", label="진동")
    dsts = [
        _make_object(client, admin, effect, key=f"E-{n}", label=f"결과 {n}") for n in (1, 2)
    ]
    object_type = db.scalar(select(ObjectType).where(ObjectType.slug == cause))
    user = db.scalar(select(User).where(User.email == admin.email))
    assert object_type is not None and user is not None
    before = _last_seq(db)
    plan = bulk.apply_relations(
        db,
        user,
        object_type,
        [
            {"src": "C-1", "relation": kind, "dst": "E-1", "evidence_note": "현장 보고"},
            {"src": "C-1", "relation": kind, "dst": "E-2"},
        ],
        mark={"_source": "시험"},
    )
    assert plan.ok, [one.message for one in plan.rows]
    edges = {
        str(one.dst_object_id): one
        for one in db.scalars(
            select(ObjectRelation).where(ObjectRelation.src_object_id == uuid.UUID(src["id"]))
        )
    }
    assert set(edges) == {dsts[0]["id"], dsts[1]["id"]}
    first = edges[dsts[0]["id"]]
    assert first.id == plan.rows[0].object_id
    assert (first.relation, first.evidence_note, first.properties) == (kind, "현장 보고", {})
    assert first.created_by_id == user.id and first.datasource_id is None
    assert first.updated_at == first.created_at
    assert edges[dsts[1]["id"]].evidence_note == ""

    logged = _since(db, before)
    assert [one.action for one in logged] == [
        "object.relation.add",
        "object.relation.add",
        "object.relation.import",
    ]
    assert logged[0].target_id == first.id
    # 객체 이력이 이 키로 선을 찾는다(`changes.src` · `changes.dst`) — 모양이 그대로여야 한다.
    assert logged[0].changes == {
        "relation": kind,
        "src": src["id"],
        "dst": dsts[0]["id"],
        "src_label": "진동",
        "dst_label": "결과 1",
        "_source": "시험",
    }


def test_묶음의_되돌릴_기록은_차례가_이어진다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """새 줄의 되돌릴 기록은 모았다가 넣는다 — **차례(`seq`)는 부른 차례**여야 되돌리기가
    거꾸로 밟는다. 고친 줄(ORM 으로 바로 넣는다)과 섞여도 끊기지 않는다."""
    tool = _make_type(client, admin, label="툴", key_policy="required")
    _make_property(client, admin, tool, key="note", label="메모", data_type="text")
    _make_object(client, admin, tool, key="T-0", label="옛 툴", properties={"note": "옛"})
    loaded = bundle_import(
        client,
        admin,
        {
            "objects": [
                {
                    "type_slug": tool,
                    "workspace_slug": admin.workspace,
                    "rows": [
                        {"key": "T-1", "label": "하나"},
                        {"key": "T-0", "label": "옛 툴", "note": "새"},
                        {"key": "T-2", "label": "둘"},
                    ],
                }
            ],
            "apply": True,
        },
    )
    assert loaded["applied"] is True, loaded
    run_id = uuid.UUID(loaded["run_id"])
    entries = list(
        db.scalars(
            select(BundleUndoEntry)
            .where(BundleUndoEntry.run_id == run_id)
            .order_by(BundleUndoEntry.seq)
        )
    )
    assert [one.seq for one in entries] == [1, 2, 3]
    assert [(one.table_name, one.action, one.label) for one in entries] == [
        ("objects", "create", f"{tool}:하나"),
        ("objects", "update", f"{tool}:옛 툴"),
        ("objects", "create", f"{tool}:둘"),
    ]
    type_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == tool))
    made = {
        one.key: one.id
        for one in db.scalars(select(ObjectInstance).where(ObjectInstance.type_id == type_id))
    }
    assert entries[0].target_id == made["T-1"] and entries[2].target_id == made["T-2"]
    assert entries[0].before == {} and entries[0].after == {}


def test_끝점_글자가_수만_개여도_한_질의로_묻는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """관계 파일의 출발점이 2만 개쯤을 넘으면 끝점 질의가 바인드 한도(65,535)를 넘어 계획이
    통째로 죽었다(5만 줄, 2026-10-09). 목록은 배열 하나로 보낸다(`batches.any_of`)."""
    user = db.scalar(select(User).where(User.email == admin.email))
    assert user is not None
    texts = {f"없는-{n}" for n in range(70_000)}
    assert bulk._ends_of(db, user, None, texts) == {}


def test_COPY_는_DB_기본값이_없는_칸을_빼면_거절한다(db: Session) -> None:
    """파이썬 기본값(`default=uuid.uuid4`)은 COPY 에 안 붙는다 — 빼면 조용히 NULL(또는 오류)이
    아니라 **넣기 전에** 거절한다."""
    with pytest.raises(ValueError, match="id"):
        copyin.copy_rows(
            db,
            cast("Table", AuditEntry.__table__),
            [{"action": "x", "actor_label": "x", "target_table": "x", "target_label": "x"}],
        )
    assert copyin.copy_rows(db, cast("Table", AuditEntry.__table__), []) == 0
