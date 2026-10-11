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
from sqlalchemy import Table, event, func, select, text
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.audit.models import AuditEntry
from app.modules.bundles import journal
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


# --- 고친 줄 — 덩어리 UPDATE 는 flush 와 같은 것을 남긴다 ---------------------------------


def _flush_path(patch: pytest.MonkeyPatch) -> None:
    """**예전 쓰기 길** — 고친 줄을 flush 가 줄마다 보내고, 계획이 검증한 속성을 적용이 다시
    검증한다(2026-10-10 전)."""
    patch.setattr(copyin, "update_rows", lambda _db, _rows: 0)
    planned = bulk.plan_objects

    def plan_again(*args: Any, **kw: Any) -> bulk.Plan:
        plan = planned(*args, **kw)
        if isinstance(plan.index, bulk._Index):
            plan.index.checked.clear()
        return plan

    patch.setattr(bulk, "plan_objects", plan_again)


class _Statements:
    """적용 동안 보낸 UPDATE — (표, 줄 수, 덩어리 문장인가). executemany 는 줄 수만큼."""

    def __init__(self, db: Session) -> None:
        self.engine = db.get_bind()
        self.seen: list[tuple[str, int, bool]] = []
        self.defs = 0
        """속성 정의를 물은 수 — 줄마다 묻지 않는지 본다."""

    def __enter__(self) -> _Statements:
        event.listen(self.engine, "before_cursor_execute", self._heard)
        return self

    def __exit__(self, *_exc: object) -> None:
        event.remove(self.engine, "before_cursor_execute", self._heard)

    def _heard(
        self, _c: Any, _cur: Any, statement: str, params: Any, _ctx: Any, many: bool
    ) -> None:
        if "FROM property_defs" in statement:
            self.defs += 1
        if statement.lstrip().upper().startswith("UPDATE"):
            batched = "FROM unnest(" in statement
            self.seen.append((statement.split()[1], len(params) if many else 1, batched))


def _snapshot_world(
    db: Session, type_ids: list[uuid.UUID], since: int, run_id: uuid.UUID, slug: str
) -> dict[str, Any]:
    """한 세계의 결과를 **id · slug 없이** — 두 세계를 그대로 견줄 수 있게."""
    db.expire_all()
    rows = list(db.scalars(select(ObjectInstance).where(ObjectInstance.type_id.in_(type_ids))))
    names = {str(one.id): one.key or one.label for one in rows}

    def plain(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: plain(one) for key, one in value.items()}
        if isinstance(value, list):
            return [plain(one) for one in value]
        if isinstance(value, uuid.UUID):
            return names.get(str(value), "?")
        if isinstance(value, str):
            return names.get(value, value.replace(slug, "<slug>"))
        return value

    logged = _since(db, since)
    undo = list(
        db.scalars(
            select(BundleUndoEntry)
            .where(BundleUndoEntry.run_id == run_id)
            .order_by(BundleUndoEntry.seq)
        )
    )
    refs = db.execute(
        select(ObjectRef.src_id, ObjectRef.key, ObjectRef.dst_id).where(
            ObjectRef.src_id.in_([one.id for one in rows])
        )
    )
    return {
        "objects": sorted(
            (
                one.key,
                one.label,
                one.description,
                one.status,
                plain(one.properties),
                sorted(one.human_edits),
                list(one.previous_keys),
                aliases.human_of(db, [one.id]).get(one.id, []),
            )
            for one in rows
        ),
        "audit": [
            (one.action, plain(one.target_id), plain(one.target_label), plain(one.changes))
            for one in logged
        ],
        "undo": [
            (one.seq, one.table_name, one.action, plain(one.target_id), plain(one.before))
            for one in undo
        ],
        "undo_after": [plain(one.after) for one in undo],
        "refs": sorted((plain(src), key, plain(dst)) for src, key, dst in refs),
    }


def _edit_once(
    client: TestClient, admin: Signed, db: Session
) -> tuple[dict[str, Any], dict[str, Any]]:
    """객체를 고치는 파일 한 장 — 참조 아닌 칸 · 참조 칸 · 목록 참조 · 그대로 · 사람이 고친
    칸 · 별칭만 · 설명 · 같은 객체를 두 줄이(id 로 하나, 식별자로 하나) · 새로 하나."""
    model = _make_type(client, admin, label="개발모델", key_policy="required")
    case = _make_type(client, admin, label="서비스", key_policy="required")
    for key, label, more in (
        ("model", "모델", {"data_type": "object_ref", "ref_type_slug": model}),
        ("parts", "부품", {"data_type": "object_ref", "ref_type_slug": model, "multi": True}),
        ("cost", "비용", {"data_type": "number"}),
        ("note", "메모", {"data_type": "text"}),
    ):
        _make_property(client, admin, case, key=key, label=label, **more)
    one, two = (
        _make_object(client, admin, model, key=f"M-{n}", label=f"모델 {n}")["id"]
        for n in (1, 2)
    )
    made = {
        key: _make_object(client, admin, case, key=key, label=label, properties=props)
        for key, label, props in (
            ("C-1", "하나", {"model": one, "parts": [one, two], "cost": 1}),
            ("C-2", "둘", {"model": one, "cost": 2}),
            ("C-3", "그대로", {"model": one, "cost": 3}),
            ("C-4", "옛 이름", {"cost": 4}),
            ("C-5", "다섯", {"model": two}),
            ("C-6", "여섯", {}),
            ("C-7", "일곱", {"cost": 7}),
        )
    }
    # 사람이 화면에서 이름을 고쳤다 — 파일의 이름은 비켜 간다.
    edited = client.patch(
        f"/api/objects/{case}/{made['C-4']['id']}",
        json={"label": "사람 이름"},
        headers=admin.headers,
    )
    assert edited.status_code == 200, edited.text
    rows: list[dict[str, Any]] = [
        {"key": "C-1", "cost": "5"},
        {"key": "C-2", "model": "M-2"},
        {"key": "C-3", "label": "그대로", "cost": "3"},
        {"key": "C-4", "label": "파일 이름", "cost": "9"},
        {"key": "C-5", "aliases": "다섯째"},
        {"key": "C-6", "description": "설명", "parts": "M-1;M-2"},
        {"id": made["C-7"]["id"], "key": "C-7", "cost": "11"},
        {"key": "C-7", "note": "둘째 줄"},
        {"key": "C-8", "label": "새 것", "model": "M-1"},
    ]
    object_type = db.scalar(select(ObjectType).where(ObjectType.slug == case))
    model_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == model))
    user = db.scalar(select(User).where(User.email == admin.email))
    owner = db.scalar(select(Workspace.id).where(Workspace.slug == admin.workspace))
    assert object_type is not None and model_id is not None
    assert user is not None and owner is not None
    stamped = {
        one.key: one.updated_at
        for one in db.scalars(
            select(ObjectInstance).where(ObjectInstance.type_id == object_type.id)
        )
    }
    c1 = uuid.UUID(made["C-1"]["id"])
    kept_refs = db.execute(
        text("SELECT ctid, xmin::text FROM object_refs WHERE src_id = :s ORDER BY ctid"),
        {"s": c1},
    ).all()
    since = _last_seq(db)
    heard: list[events.ChangeEvent] = []
    events.register_listener(heard.extend)
    run_id = journal.start(db, user, source="시험")
    try:
        with _Statements(db) as sent:
            plan = bulk.apply_objects(db, user, object_type, rows, owner_workspace_id=owner)
    finally:
        events._listeners.remove(heard.extend)
        journal.drop(db)
    assert plan.ok, [(one.action, one.message) for one in plan.rows]
    world = _snapshot_world(db, [object_type.id, model_id], since, run_id, case)
    world["actions"] = [one.action for one in plan.rows]
    world["heard"] = [(one.action, world_key(db, one.target_id)) for one in heard]
    world["touched"] = sorted(
        one.key
        for one in db.scalars(
            select(ObjectInstance).where(ObjectInstance.type_id == object_type.id)
        )
        if one.key in stamped and one.updated_at != stamped[one.key]
    )
    facts = {
        "statements": sent.seen,
        "kept_refs_same": kept_refs
        == db.execute(
            text("SELECT ctid, xmin::text FROM object_refs WHERE src_id = :s ORDER BY ctid"),
            {"s": c1},
        ).all(),
    }
    return world, facts


def world_key(db: Session, object_id: uuid.UUID | None) -> str | None:
    if object_id is None:
        return None
    found = db.get(ObjectInstance, object_id)
    return None if found is None else found.key


def test_고친_줄은_덩어리_UPDATE_로_쓰고_남는_것은_flush_와_같다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 파일을 **예전 길(flush 가 줄마다)** 과 새 길(덩어리 UPDATE)로 각각 다른 세계에
    넣고 남는 것을 견준다 — 객체(값 · 바뀐 때 · 사람이 고친 칸 · 별칭) · 감사 기록과 차례 ·
    되돌릴 기록과 차례 · 바깥 알림 · 참조 색인."""
    with monkeypatch.context() as patch:
        _flush_path(patch)
        before, before_facts = _edit_once(client, admin, db)
    after, facts = _edit_once(client, admin, db)
    assert after == before

    # 무엇이 남았나 — 두 세계가 같으니 한쪽만 본다.
    assert after["actions"] == [
        "update",
        "update",
        "unchanged",
        "update",
        "update",
        "update",
        "update",
        "update",
        "create",
    ]
    objects = {one[0]: one for one in after["objects"]}
    assert objects["C-1"][4] == {"model": "M-1", "parts": ["M-1", "M-2"], "cost": 5}
    assert objects["C-2"][4] == {"model": "M-2", "cost": 2}
    # 사람이 고친 이름은 그대로, 비용은 고친다.
    assert objects["C-4"][1] == "사람 이름" and objects["C-4"][4] == {"cost": 9}
    assert objects["C-4"][5] == ["label"]
    assert objects["C-5"][7] == ["다섯째"] and objects["C-5"][4] == {"model": "M-2"}
    assert objects["C-6"][2] == "설명" and objects["C-6"][4] == {"parts": ["M-1", "M-2"]}
    # 같은 객체를 두 줄이 — 뒷줄은 앞줄이 고친 값 위에.
    assert objects["C-7"][4] == {"cost": 11, "note": "둘째 줄"}
    # 바뀐 때는 고친 줄만(별칭만 바뀐 줄도), 그대로인 줄은 그대로.
    assert after["touched"] == ["C-1", "C-2", "C-4", "C-5", "C-6", "C-7"]
    assert ("C-2", "model", "M-2") in after["refs"] and ("C-2", "model", "M-1") not in after[
        "refs"
    ]
    assert ("C-8", "model", "M-1") in after["refs"]
    assert [one[0] for one in after["audit"]] == [
        *(["object.update"] * 7),
        "object.create",
        "object.import",
    ]
    assert after["heard"][:2] == [("object.update", "C-1"), ("object.update", "C-2")]
    assert [one[0] for one in after["undo"]] == list(range(1, len(after["undo"]) + 1))

    # 새 길은 고친 줄을 **덩어리로** — 별칭 앞에서 앞줄 셋을 한 문장(별칭 맞추기는 끝에서
    # flush 한다), 그 줄의 「바뀐 때」 는 그 flush 가, 남은 둘은 바뀐 칸이 달라 두 문장.
    # 예전 길은 고친 줄마다 한 문장이었다.
    assert facts["statements"] == [
        ("objects", 1, True),
        ("objects", 1, False),
        ("objects", 1, True),
        ("objects", 1, True),
    ]
    assert not [one for one in before_facts["statements"] if one[2]]
    assert sum(one[1] for one in before_facts["statements"]) == 6
    # 참조 아닌 칸(비용)만 고친 줄의 색인 줄은 지웠다 다시 넣지 않는다.
    assert facts["kept_refs_same"] is True


def test_고친_선도_덩어리_UPDATE_로_쓰고_남는_것은_flush_와_같다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def once() -> tuple[list[Any], _Statements]:
        cause = _make_type(client, admin, label="원인", key_policy="required")
        effect = _make_type(client, admin, label="결과", key_policy="required")
        kind = _make_relation(
            client, admin, label="일으킴", src_type_slugs=[cause], dst_type_slugs=[effect]
        )
        _make_object(client, admin, cause, key="C-1", label="진동")
        for n in (1, 2, 3, 4):
            _make_object(client, admin, effect, key=f"E-{n}", label=f"결과 {n}")
        object_type = db.scalar(select(ObjectType).where(ObjectType.slug == cause))
        user = db.scalar(select(User).where(User.email == admin.email))
        assert object_type is not None and user is not None
        first = bulk.apply_relations(
            db,
            user,
            object_type,
            [
                {"src": "C-1", "relation": kind, "dst": f"E-{n}", "evidence_note": "가"}
                for n in (1, 2, 3, 4)
            ],
        )
        assert first.ok
        edges = {
            one.id: one
            for one in db.scalars(
                select(ObjectRelation).where(ObjectRelation.relation == kind)
            )
        }
        stamped = {key: one.updated_at for key, one in edges.items()}
        since = _last_seq(db)
        with _Statements(db) as sent:
            again = bulk.apply_relations(
                db,
                user,
                object_type,
                [
                    {"src": "C-1", "relation": kind, "dst": f"E-{n}", "evidence_note": note}
                    for n, note in ((1, "나"), (2, "가"), (3, "나"), (4, "나"))
                ],
            )
        actions = [one.action for one in again.rows]
        assert actions == ["update", "unchanged", "update", "update"]
        db.expire_all()
        shown = sorted(
            (
                one.evidence_note,
                one.properties,
                one.updated_at != stamped[one.id],
            )
            for one in db.scalars(
                select(ObjectRelation).where(ObjectRelation.relation == kind)
            )
        )
        logged = [(one.action, one.changes) for one in _since(db, since)]
        return [shown, logged], sent

    with monkeypatch.context() as patch:
        _flush_path(patch)
        before, _ = once()
    after, sent = once()
    assert after == before
    assert after[0] == [("가", {}, False), *([("나", {}, True)] * 3)]
    assert sent.seen == [("object_relations", 1, True)]  # 고친 선 셋을 한 문장으로
    # 속성 정의가 없는 종류도 줄마다 정의를 다시 묻지 않는다(빈 목록을 「없음」 으로 읽던 것).
    assert sent.defs <= 2, sent.defs


def test_덩어리_UPDATE_는_flush_와_같은_칸만_쓴다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """같은 값을 다시 넣은 칸은 안 쓴다(그런 칸뿐이면 줄도, 바뀐 때도 그대로) · 바뀐 칸만 ·
    별칭만 바뀐 줄의 「바뀐 때」(`now()`) · 그 밖의 SQL 식은 flush 에 맡긴다."""
    tool = _make_type(client, admin, label="툴", key_policy="required")
    _make_property(client, admin, tool, key="note", label="메모", data_type="text")
    made = [
        _make_object(
            client, admin, tool, key=f"T-{n}", label=f"툴 {n}", properties={"note": "옛"}
        )
        for n in range(4)
    ]
    rows = [db.get(ObjectInstance, uuid.UUID(one["id"])) for one in made]
    assert all(one is not None for one in rows)
    same, changed, touched, by_sql = cast("list[ObjectInstance]", rows)
    stamped = {one.id: one.updated_at for one in (same, changed, touched, by_sql)}
    same.label = "툴 0"
    same.properties = {"note": "옛"}
    changed.properties = {"note": "새"}
    touched.updated_at = func.now()
    by_sql.label = ObjectInstance.label + "!"
    with _Statements(db) as sent:
        assert copyin.update_rows(db, [same, changed, touched, by_sql, changed]) == 2
        assert not db.is_modified(changed) and not db.is_modified(touched)
        db.flush()
    assert sent.seen == [("objects", 1, True), ("objects", 1, True), ("objects", 1, False)]
    db.commit()
    db.expire_all()
    shown = {
        one.key: (one.label, one.properties, one.updated_at != stamped[one.id])
        for one in db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.id.in_([uuid.UUID(one["id"]) for one in made])
            )
        )
    }
    assert shown == {
        "T-0": ("툴 0", {"note": "옛"}, False),
        "T-1": ("툴 1", {"note": "새"}, True),
        "T-2": ("툴 2", {"note": "옛"}, True),
        "T-3": ("툴 3!", {"note": "옛"}, True),
    }
