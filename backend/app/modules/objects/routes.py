"""객체 라우터 — **보이는 것과 고칠 수 있는 것은 다른 축이다.**

보기는 `visible_owner_clause`(전역 + 내 부서), 고치기는 `require_owner_edit`
(소유 부서의 관리자 또는 시스템 관리자).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.files.models import Attachment
from app.modules.objects import graph
from app.modules.objects import relations as rel
from app.modules.objects.models import (
    OBJECT_STATUSES,
    ObjectInstance,
    ObjectRelation,
    ObjectYear,
)
from app.modules.objects.schemas import (
    AttachmentBrief,
    ObjectCreateRequest,
    ObjectOut,
    ObjectPatchRequest,
    ObjectProfileOut,
    RelatedObjectOut,
    RelationCreateRequest,
    RelationPatchRequest,
    TreeNodeOut,
    TreeOut,
)
from app.modules.objects.services import (
    apply_property_filters,
    apply_search,
    apply_sort,
    apply_year,
    count_of,
    normalize_key,
    properties_of,
    require_key_free,
    require_refs_exist,
)
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.modules.ontology.schemas import PropertyDefOut
from app.modules.ontology.services import (
    merge_properties,
    require_choice,
    validate_properties,
)
from app.modules.workspaces.models import Workspace
from app.shared import audit
from app.shared.auth import current_user
from app.shared.errors import Conflict, Forbidden, NotFound, code
from app.shared.pagination import Page, clamp_limit
from app.shared.permissions import (
    require_owner_edit,
    resolve_owner_workspace,
    visible_owner_clause,
)

router = APIRouter(prefix="/objects", tags=["objects"])


# --- 공통 -------------------------------------------------------------------


def _type(db: Session, slug: str) -> ObjectType:
    row = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
    if row is None:
        raise NotFound(code("OBJECTS", 10), f"타입을 찾을 수 없습니다: {slug}")
    return row


def _workspace_slugs(db: Session) -> dict[uuid.UUID, str]:
    return {row.id: row.slug for row in db.scalars(select(Workspace))}


def _ref_labels(
    db: Session, defs: list[PropertyDef], rows: list[ObjectInstance]
) -> dict[uuid.UUID, str]:
    """이 목록이 가리키는 객체들의 이름을 **한 번에** 읽는다.

    행마다 물으면 목록 한 쪽에 질의가 수십 개 붙는다. 어느 키가 참조인지는
    속성 정의가 알므로, 아무 문자열이나 uuid 로 넘겨짚지 않는다.
    """
    ref_keys = [d.key for d in defs if d.data_type == "object_ref"]
    if not ref_keys:
        return {}

    wanted: set[uuid.UUID] = set()
    for row in rows:
        values = row.properties or {}
        for key in ref_keys:
            raw = values.get(key)
            for item in raw if isinstance(raw, list) else [raw]:
                if not isinstance(item, str):
                    continue
                try:
                    wanted.add(uuid.UUID(item))
                except ValueError:  # pragma: no cover - 검증이 이미 막는다
                    continue
    if not wanted:
        return {}

    found = db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(wanted)))
    return {row.id: row.label for row in found}


def _out(
    row: ObjectInstance,
    type_slug: str,
    workspaces: dict[uuid.UUID, str],
    ref_labels: dict[uuid.UUID, str] | None = None,
) -> ObjectOut:
    return ObjectOut(
        id=row.id,
        type_slug=type_slug,
        key=row.key,
        label=row.label,
        description=row.description,
        properties=row.properties or {},
        ref_labels={str(k): v for k, v in (ref_labels or {}).items()},
        status=row.status,
        owner_workspace_slug=(
            workspaces.get(row.owner_workspace_id) if row.owner_workspace_id else None
        ),
        valid_from_year=row.valid_from_year,
        valid_to_year=row.valid_to_year,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _visible(
    db: Session, user: User, object_type: ObjectType, object_id: uuid.UUID
) -> ObjectInstance:
    row = db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == object_id,
            ObjectInstance.type_id == object_type.id,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    if row is None:
        # **없는 것과 안 보이는 것을 같은 말로 답한다.** 구별해 주면 남의 부서에
        # 무엇이 있는지 id 를 바꿔 가며 알아낼 수 있다.
        raise NotFound(code("OBJECTS", 11), "객체를 찾을 수 없습니다.")
    return row


def _can_edit(db: Session, user: User, row: ObjectInstance) -> bool:
    try:
        require_owner_edit(
            db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 12)
        )
    except Exception:
        return False
    return True


def _tree_spec(object_type: ObjectType) -> tuple[str | None, graph.ParentEnd]:
    """`list_view.tree` 가 정한 트리 관계와 부모 쪽 끝.

    안 정했으면 트리를 안 그린다 — **`transitive` 인 관계가 둘 이상일 수 있어서**
    아무거나 골라 그리면 그 트리는 무엇을 보여 주는지 말할 수 없다.
    """
    spec = (object_type.list_view or {}).get("tree") or {}
    relation = spec.get("relation")
    parent_end: graph.ParentEnd = "src" if spec.get("parent") == "src" else "dst"
    return (relation if isinstance(relation, str) and relation else None, parent_end)


# --- 트리 -------------------------------------------------------------------


@router.get("/{type_slug}/tree", response_model=TreeOut)
def object_tree(
    type_slug: str,
    parent: uuid.UUID | None = Query(default=None, description="펼칠 노드. 없으면 뿌리"),
    orphans: bool = Query(default=False, description="어디에도 안 걸린 것만"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TreeOut:
    """트리 한 단계.

    **통째로 안 불러온다.** 펼칠 때 그 단계만 읽는다 — 안 그러면 부품 5천 개짜리
    트리에서 첫 화면이 안 뜬다.
    """
    object_type = _type(db, type_slug)
    relation, parent_end = _tree_spec(object_type)
    if relation is None:
        return TreeOut(nodes=[], orphan_count=0)

    if parent is not None:
        ids = graph.child_ids(db, relation=relation, parent_id=parent, parent_end=parent_end)
        orphan_count = 0
    else:
        parentless = graph.parentless_ids(
            db, relation=relation, type_id=object_type.id, parent_end=parent_end
        )
        counts = graph.child_counts(
            db, relation=relation, parent_ids=parentless, parent_end=parent_end
        )
        roots = [one for one in parentless if counts.get(one, 0) > 0]
        orphan_ids = [one for one in parentless if counts.get(one, 0) == 0]
        ids = orphan_ids if orphans else roots
        orphan_count = len(orphan_ids)

    if not ids:
        return TreeOut(nodes=[], orphan_count=orphan_count)

    # **볼 수 있는 것만 준다.** 남의 부서 것은 트리에도 안 나온다.
    rows = list(
        db.scalars(
            select(ObjectInstance)
            .where(
                ObjectInstance.id.in_(ids),
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
            .order_by(ObjectInstance.label)
        )
    )
    counts = graph.child_counts(
        db, relation=relation, parent_ids=[row.id for row in rows], parent_end=parent_end
    )
    return TreeOut(
        nodes=[
            TreeNodeOut(
                id=row.id,
                label=row.label,
                key=row.key,
                status=row.status,
                child_count=counts.get(row.id, 0),
            )
            for row in rows
        ],
        orphan_count=orphan_count,
    )


# --- 목록 -------------------------------------------------------------------


@router.get("/{type_slug}", response_model=Page[ObjectOut])
def list_objects(
    type_slug: str,
    request: Request,
    q: str | None = Query(default=None, description="이름·식별자·검색 속성"),
    status: str | None = Query(default=None),
    under: uuid.UUID | None = Query(default=None, description="트리에서 고른 노드"),
    deep: bool = Query(default=True, description="아래 것까지 포함"),
    year: int | None = Query(
        default=None, ge=1900, le=2999, description="그 해에 해당하는 것만"
    ),
    limit: int | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Page[ObjectOut]:
    """이 타입의 객체 목록.

    거르기는 `?p.<속성키>=<값>` 으로 온다. `list_view` 가 열·정렬·검색 자리를
    정하고, 안 정해 뒀으면 기본형으로 떨어진다 — **빈 화면이 되지는 않는다.**
    """
    object_type = _type(db, type_slug)

    stmt = select(ObjectInstance).where(
        ObjectInstance.type_id == object_type.id,
        ObjectInstance.deleted_at.is_(None),
        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
    )
    if status:
        stmt = stmt.where(ObjectInstance.status == status)
    if q:
        stmt = apply_search(stmt, object_type, q)
    if year is not None:
        stmt = apply_year(db, stmt, object_type, year)
    if under is not None:
        # **기본은 「아래 것까지 포함」 이다.** 안 그러면 상위 노드를 눌렀을 때
        # 목록이 비고, 그 빈 목록은 「없다」 로 읽힌다.
        relation, parent_end = _tree_spec(object_type)
        if relation is None:
            raise Conflict(
                code("OBJECTS", 33),
                f"{object_type.label}에는 트리가 정의돼 있지 않습니다. "
                "타입의 「목록 화면」 에서 트리로 쓸 관계를 고르세요.",
            )
        wanted = [under]
        if deep:
            wanted += graph.descendant_ids(
                db, relation=relation, root_id=under, parent_end=parent_end
            )
        stmt = stmt.where(ObjectInstance.id.in_(wanted))

    filters = {
        key[2:]: value for key, value in request.query_params.items() if key.startswith("p.")
    }
    if filters:
        stmt = apply_property_filters(stmt, filters)

    total = count_of(db, stmt)
    capped = clamp_limit(limit)
    rows = db.scalars(apply_sort(stmt, object_type).limit(capped).offset(offset))

    found = list(rows)
    workspaces = _workspace_slugs(db)
    labels = _ref_labels(db, properties_of(db, object_type.id), found)
    return Page(
        items=[_out(row, object_type.slug, workspaces, labels) for row in found],
        total=total,
        limit=capped,
        offset=offset,
    )


# --- 하나 -------------------------------------------------------------------


@router.get("/{type_slug}/{object_id}", response_model=ObjectProfileOut)
def object_profile(
    type_slug: str,
    object_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ObjectProfileOut:
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)

    attachments = db.scalars(
        select(Attachment)
        .where(
            Attachment.owner_table == "objects",
            Attachment.owner_id == row.id,
            Attachment.deleted_at.is_(None),
        )
        .order_by(Attachment.created_at.desc())
    )

    defs = properties_of(db, object_type.id)
    return ObjectProfileOut(
        object=_out(row, object_type.slug, _workspace_slugs(db), _ref_labels(db, defs, [row])),
        type_label=object_type.label,
        properties_schema=[PropertyDefOut.model_validate(p) for p in defs],
        attachments=[AttachmentBrief.model_validate(a) for a in attachments],
        related=_related(db, user, row),
        can_edit=_can_edit(db, user, row),
    )


@router.post("/{type_slug}", response_model=ObjectOut, status_code=201)
def create_object(
    type_slug: str,
    payload: ObjectCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ObjectOut:
    object_type = _type(db, type_slug)
    if not object_type.is_active:
        raise Forbidden(
            code("OBJECTS", 13), f"{object_type.label}은 지금 쓰지 않는 타입입니다."
        )
    if object_type.kind_class == "system":
        # system 축은 **원 표를 투영**한다. 여기에 행을 만들면 두 벌이 되고,
        # 두 벌은 반드시 갈린다.
        raise Forbidden(
            code("OBJECTS", 14),
            f"{object_type.label}은 다른 표를 비추는 타입이라 여기서 만들지 않습니다.",
        )

    owner_workspace_id = resolve_owner_workspace(
        db, user, payload.workspace_slug, what="객체", code_value=code("OBJECTS", 15)
    )
    key = normalize_key(object_type, payload.key)
    require_key_free(db, object_type, key, owner_workspace_id=owner_workspace_id)

    defs = properties_of(db, object_type.id)
    properties = validate_properties(defs, payload.properties)
    require_refs_exist(db, defs, properties)

    row = ObjectInstance(
        type_id=object_type.id,
        key=key,
        label=payload.label,
        description=payload.description,
        properties=properties,
        owner_workspace_id=owner_workspace_id,
        valid_from_year=payload.valid_from_year,
        valid_to_year=payload.valid_to_year,
        created_by_id=user.id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="object.create",
        actor=user,
        target_table="objects",
        target_id=row.id,
        target_label=f"{object_type.slug}:{row.label}",
        workspace_id=owner_workspace_id,
    )
    db.commit()
    db.refresh(row)
    return _out(row, object_type.slug, _workspace_slugs(db))


@router.patch("/{type_slug}/{object_id}", response_model=ObjectOut)
def update_object(
    type_slug: str,
    object_id: uuid.UUID,
    payload: ObjectPatchRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ObjectOut:
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 16)
    )

    before: dict[str, Any] = {
        "key": row.key,
        "label": row.label,
        "status": row.status,
        "properties": dict(row.properties or {}),
    }

    if payload.key is not None:
        key = normalize_key(object_type, payload.key)
        require_key_free(
            db, object_type, key, owner_workspace_id=row.owner_workspace_id, exclude_id=row.id
        )
        row.key = key
    if payload.label is not None:
        row.label = payload.label
    if payload.description is not None:
        row.description = payload.description
    if payload.status is not None:
        row.status = require_choice(payload.status, OBJECT_STATUSES, what="상태")
    if payload.valid_from_year is not None:
        row.valid_from_year = payload.valid_from_year
    if payload.valid_to_year is not None:
        row.valid_to_year = payload.valid_to_year

    if payload.properties is not None:
        defs = properties_of(db, object_type.id)
        merged = merge_properties(row.properties or {}, payload.properties)
        cleaned = validate_properties(defs, merged)
        require_refs_exist(db, defs, cleaned)
        row.properties = cleaned

    after: dict[str, Any] = {
        "key": row.key,
        "label": row.label,
        "status": row.status,
        "properties": dict(row.properties or {}),
    }
    audit.record(
        db,
        action="object.update",
        actor=user,
        target_table="objects",
        target_id=row.id,
        target_label=f"{object_type.slug}:{row.label}",
        workspace_id=row.owner_workspace_id,
        changes=audit.diff(before, after),
    )
    db.commit()
    db.refresh(row)
    return _out(row, object_type.slug, _workspace_slugs(db))


@router.delete("/{type_slug}/{object_id}", status_code=204)
def delete_object(
    type_slug: str,
    object_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    """**지우지 않는다.** `deleted_at` 만 채운다 — 이 객체를 가리키는 관계와
    첨부가 밖에 남아 있고, 몇 년 뒤에도 그것이 무엇이었는지는 물어질 수 있다."""
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 17)
    )

    row.deleted_at = datetime.now(UTC)
    audit.record(
        db,
        action="object.delete",
        actor=user,
        target_table="objects",
        target_id=row.id,
        target_label=f"{object_type.slug}:{row.label}",
        workspace_id=row.owner_workspace_id,
    )
    db.commit()


# --- 관련 객체 --------------------------------------------------------------


def _related(db: Session, user: User, row: ObjectInstance) -> list[RelatedObjectOut]:
    """이 객체에 걸린 관계들 — **양방향 다.**

    「이것이 가리키는 것」 만 주면 「이것을 가리키는 것」 을 물을 자리가 없어진다.
    부품에서 그 부품을 쓰는 어셈블리를 못 보면, 그 부품을 지워도 되는지 알 수 없다.

    **볼 수 있는 것만 준다.** 저쪽 끝이 남의 부서 것이면 그 줄은 안 나온다 —
    없는 것과 안 보이는 것을 같은 말로 답하는 이 틀의 규칙 그대로다.
    """
    edges = list(
        db.scalars(
            select(ObjectRelation)
            .where(
                (ObjectRelation.src_object_id == row.id)
                | (ObjectRelation.dst_object_id == row.id)
            )
            .order_by(ObjectRelation.created_at)
        )
    )
    if not edges:
        return []

    other_ids = {
        (edge.dst_object_id if edge.src_object_id == row.id else edge.src_object_id)
        for edge in edges
    }
    others = {
        one.id: one
        for one in db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.id.in_(other_ids),
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
    }
    kinds = {one.slug: one for one in db.scalars(select(RelationType))}
    types = {one.id: one for one in db.scalars(select(ObjectType))}

    out: list[RelatedObjectOut] = []
    for edge in edges:
        outgoing = edge.src_object_id == row.id
        other = others.get(edge.dst_object_id if outgoing else edge.src_object_id)
        if other is None:
            continue
        kind = kinds.get(edge.relation)
        object_type = types.get(other.type_id)

        # **방향에 맞는 말을 고른다.** 없으면 slug 를 보여 준다 — 빈 칸으로 두면
        # 그 줄이 무슨 관계인지 알 방법이 없다.
        if kind is None:
            label = edge.relation
        elif outgoing or not kind.directed:
            label = kind.label
        else:
            label = kind.inverse_label or f"{kind.label}의 반대"

        out.append(
            RelatedObjectOut(
                relation_id=edge.id,
                relation=edge.relation,
                label=label,
                outgoing=outgoing,
                object_id=other.id,
                object_label=other.label,
                object_key=other.key,
                object_type_slug=object_type.slug if object_type else "",
                object_type_label=object_type.label if object_type else "알 수 없음",
                properties=edge.properties or {},
                evidence_note=edge.evidence_note,
                created_at=edge.created_at,
            )
        )
    return out


@router.post(
    "/{type_slug}/{object_id}/relations", response_model=RelatedObjectOut, status_code=201
)
def add_relation(
    type_slug: str,
    object_id: uuid.UUID,
    payload: RelationCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RelatedObjectOut:
    """관계를 맺는다 — **출발점을 고칠 수 있는 사람만.**

    도착점은 볼 수만 있으면 된다. 양쪽 다 고칠 수 있어야 한다고 하면 부서를
    가로지르는 연결을 아무도 못 만들고, 그러면 **연결하려고 만든 것이 칸막이가
    된다.** 대신 맺은 사람과 근거가 남는다.
    """
    object_type = _type(db, type_slug)
    src = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, src.owner_workspace_id, what="객체", code_value=code("OBJECTS", 27)
    )

    dst = db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == payload.dst_object_id,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    if dst is None:
        raise NotFound(code("OBJECTS", 28), "이을 객체를 찾을 수 없습니다.")

    kind = rel.relation_type(db, payload.relation)
    rel.require_endpoints_allowed(db, kind, src, dst)
    rel.require_cardinality(db, kind, src.id, dst.id)
    rel.require_no_cycle(db, kind, src.id, dst.id)

    existing = db.scalar(
        select(ObjectRelation).where(
            ObjectRelation.src_object_id == src.id,
            ObjectRelation.dst_object_id == dst.id,
            ObjectRelation.relation == kind.slug,
        )
    )
    if existing is not None:
        raise Conflict(code("OBJECTS", 29), "이미 이어져 있습니다.")

    # 관계 종류가 정한 속성 모양대로 검증한다 — **타입의 속성과 같은 규칙**이다.
    defs = list(
        db.scalars(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "relation", PropertyDef.owner_id == kind.id
            )
        )
    )
    properties = validate_properties(defs, payload.properties)

    edge = ObjectRelation(
        src_object_id=src.id,
        dst_object_id=dst.id,
        relation=kind.slug,
        properties=properties,
        evidence_note=payload.evidence_note,
        created_by_id=user.id,
    )
    db.add(edge)
    db.flush()
    audit.record(
        db,
        action="object.relation.add",
        actor=user,
        target_table="object_relations",
        target_id=edge.id,
        target_label=f"{src.label} -{kind.slug}-> {dst.label}",
        workspace_id=src.owner_workspace_id,
    )
    db.commit()

    found = [one for one in _related(db, user, src) if one.relation_id == edge.id]
    return found[0]


@router.patch(
    "/{type_slug}/{object_id}/relations/{relation_id}", response_model=RelatedObjectOut
)
def update_relation(
    type_slug: str,
    object_id: uuid.UUID,
    relation_id: uuid.UUID,
    payload: RelationPatchRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RelatedObjectOut:
    """관계의 속성과 근거를 고친다. **양끝과 종류는 못 바꾼다** — 그건 다른 관계다."""
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 30)
    )
    edge = _edge(db, relation_id, row)

    if payload.evidence_note is not None:
        edge.evidence_note = payload.evidence_note
    if payload.properties is not None:
        kind = rel.relation_type(db, edge.relation)
        defs = list(
            db.scalars(
                select(PropertyDef).where(
                    PropertyDef.owner_kind == "relation", PropertyDef.owner_id == kind.id
                )
            )
        )
        merged = merge_properties(edge.properties or {}, payload.properties)
        edge.properties = validate_properties(defs, merged)

    audit.record(
        db,
        action="object.relation.update",
        actor=user,
        target_table="object_relations",
        target_id=edge.id,
        target_label=f"{row.label} · {edge.relation}",
        workspace_id=row.owner_workspace_id,
    )
    db.commit()
    found = [one for one in _related(db, user, row) if one.relation_id == edge.id]
    return found[0]


@router.delete("/{type_slug}/{object_id}/relations/{relation_id}", status_code=204)
def remove_relation(
    type_slug: str,
    object_id: uuid.UUID,
    relation_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    """관계를 끊는다.

    **엣지는 진짜로 지운다.** 객체와 달리 관계는 그 자체로 기록이 아니라 두 기록
    사이의 말이고, 끊긴 관계를 남겨 두면 「지금 이어져 있나」 를 묻는 모든 질의가
    그 상태를 걸러야 한다. 대신 **감사 로그에 남는다.**
    """
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 31)
    )
    edge = _edge(db, relation_id, row)

    audit.record(
        db,
        action="object.relation.remove",
        actor=user,
        target_table="object_relations",
        target_id=edge.id,
        target_label=f"{row.label} · {edge.relation}",
        workspace_id=row.owner_workspace_id,
    )
    db.delete(edge)
    db.commit()


def _edge(db: Session, relation_id: uuid.UUID, row: ObjectInstance) -> ObjectRelation:
    """그 관계가 **이 객체에 걸린 것인가.** 아니면 남의 관계를 남의 화면에서
    끊을 수 있게 된다."""
    edge = db.get(ObjectRelation, relation_id)
    if edge is None or row.id not in (edge.src_object_id, edge.dst_object_id):
        raise NotFound(code("OBJECTS", 32), "관계를 찾을 수 없습니다.")
    return edge


# --- 연도 배정 --------------------------------------------------------------


@router.get("/{type_slug}/{object_id}/years", response_model=list[int])
def list_years(
    type_slug: str,
    object_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[int]:
    """이 객체가 **명시적으로 배정된** 연도들(`temporal_kind='yearly'`)."""
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    return sorted(db.scalars(select(ObjectYear.year).where(ObjectYear.object_id == row.id)))


@router.put("/{type_slug}/{object_id}/years", response_model=list[int])
def set_years(
    type_slug: str,
    object_id: uuid.UUID,
    years: list[int],
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[int]:
    """연도 배정을 **통째로** 정한다.

    부분 수정이 아닌 이유: 배정은 「이 해들」 이라는 하나의 답이고, 한 해를 빼는
    일이 한 해를 더하는 일만큼 흔하다. 통째로 받으면 **화면이 보여 준 것과
    저장되는 것이 같다.**
    """
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 34)
    )
    if object_type.temporal_kind != "yearly":
        raise Conflict(
            code("OBJECTS", 35),
            f"{object_type.label}은 연도를 배정하는 축이 아닙니다"
            f"(지금 정책: {object_type.temporal_kind}).",
        )

    wanted = sorted({one for one in years if 1900 <= one <= 2999})
    db.query(ObjectYear).filter(ObjectYear.object_id == row.id).delete(
        synchronize_session=False
    )
    for one in wanted:
        db.add(ObjectYear(object_id=row.id, year=one))

    audit.record(
        db,
        action="object.years.set",
        actor=user,
        target_table="objects",
        target_id=row.id,
        target_label=f"{object_type.slug}:{row.label}",
        workspace_id=row.owner_workspace_id,
        changes={"years": wanted},
    )
    db.commit()
    return wanted
