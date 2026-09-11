"""메타모델 라우터 — **읽기는 누구나, 고치기는 시스템 관리자만.**

타입을 부서마다 열지 않는 이유는 [ADR 0005](../../../../docs/adr/0005-온톨로지-메타모델.md)
에 있다: 같은 개념이 부서마다 갈리면 **연결하려고 만든 것이 칸막이가 된다.**
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance, ObjectRelation
from app.modules.ontology import codebook, importer, views
from app.modules.ontology.models import (
    CARDINALITIES,
    DATA_TYPES,
    ENTRY_POLICIES,
    KEY_POLICIES,
    KEY_SCOPES,
    KIND_CLASSES,
    NAV_AUDIENCES,
    TEMPORAL_KINDS,
    NavGroup,
    ObjectType,
    OntologySnapshot,
    PropertyDef,
    RelationType,
)
from app.modules.ontology.schemas import (
    ChangeOut,
    ImportPlanOut,
    NavGroupNode,
    NavGroupOut,
    NavGroupPatchRequest,
    NavGroupWriteRequest,
    ObjectTypeOut,
    ObjectTypePatchRequest,
    ObjectTypeSchema,
    ObjectTypeWriteRequest,
    OntologySchemaOut,
    PromoteOptionOut,
    PromoteOut,
    PromoteRequest,
    PropertyDefOut,
    PropertyDefWriteRequest,
    PropertyUsage,
    RelationTypeOut,
    RelationTypePatchRequest,
    RelationTypeWriteRequest,
    RenameOptionOut,
    RenameOptionRequest,
    SnapshotOut,
)
from app.modules.ontology.services import (
    require_choice,
    require_key,
    require_slug,
)
from app.shared.audit import record as record_audit
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import Conflict, NotFound, code

router = APIRouter(prefix="/ontology", tags=["ontology"])


def _audit(
    db: Session, user: User, *, action: str, table: str, row_id: uuid.UUID, label: str
) -> None:
    """**메타모델 변경도 감사 대상이다.** 속성 하나가 화면의 모양을 바꾸고, 값이
    안 보이게 만든다 — 그것이 언제 누구 손에 바뀌었는지 물을 자리가 있어야 한다."""
    record_audit(
        db,
        action=action,
        actor=user,
        target_table=table,
        target_id=row_id,
        target_label=label,
    )


# --- 조회 헬퍼 --------------------------------------------------------------


def _group(db: Session, slug: str) -> NavGroup:
    row = db.scalar(select(NavGroup).where(NavGroup.slug == slug))
    if row is None:
        raise NotFound(code("ONTOLOGY", 30), f"그룹을 찾을 수 없습니다: {slug}")
    return row


def _type(db: Session, slug: str) -> ObjectType:
    row = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
    if row is None:
        raise NotFound(code("ONTOLOGY", 31), f"타입을 찾을 수 없습니다: {slug}")
    return row


def _counts(db: Session) -> dict[uuid.UUID, int]:
    """타입별 객체 수. **한 번에 센다** — 타입마다 세면 목록 하나에 질의가
    타입 수만큼 붙고, 타입은 늘어나기만 한다."""
    rows = db.execute(
        select(ObjectInstance.type_id, func.count())
        .where(ObjectInstance.deleted_at.is_(None))
        .group_by(ObjectInstance.type_id)
    )
    return {type_id: count for type_id, count in rows}


def _type_out(row: ObjectType, group_slug: str | None, count: int) -> ObjectTypeOut:
    return ObjectTypeOut(
        id=row.id,
        slug=row.slug,
        label=row.label,
        icon=row.icon,
        description=row.description,
        sort_order=row.sort_order,
        nav_group_id=row.nav_group_id,
        nav_group_slug=group_slug,
        kind_class=row.kind_class,
        entry_policy=row.entry_policy,
        key_policy=row.key_policy,
        key_scope=row.key_scope,
        temporal_kind=row.temporal_kind,
        list_view=row.list_view or {},
        form_view=row.form_view or {},
        detail_view=row.detail_view or {},
        title_template=row.title_template,
        is_active=row.is_active,
        object_count=count,
    )


def _group_slugs(db: Session) -> dict[uuid.UUID, str]:
    return {row.id: row.slug for row in db.scalars(select(NavGroup))}


# --- 그룹 -------------------------------------------------------------------


@router.get("/groups", response_model=list[NavGroupOut])
def list_groups(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[NavGroup]:
    return list(db.scalars(select(NavGroup).order_by(NavGroup.sort_order, NavGroup.label)))


@router.post("/groups", response_model=NavGroupOut, status_code=201)
def create_group(
    payload: NavGroupWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> NavGroup:
    slug = require_slug(payload.slug, what="그룹 slug")
    require_choice(payload.audience, NAV_AUDIENCES, what="보이는 대상")
    if db.scalar(select(NavGroup).where(NavGroup.slug == slug)) is not None:
        raise Conflict(code("ONTOLOGY", 32), f"이미 있는 그룹입니다: {slug}")

    row = NavGroup(
        slug=slug,
        label=payload.label,
        icon=payload.icon,
        audience=payload.audience,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    db.add(row)
    db.flush()
    _audit(
        db, user, action="ontology.group.create", table="nav_groups", row_id=row.id, label=slug
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/groups/{slug}", response_model=NavGroupOut)
def update_group(
    slug: str,
    payload: NavGroupPatchRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> NavGroup:
    """**보낸 것만 바꾼다.** slug 는 안 바꾼다 — 타입은 id 로 가리키지만 사람과
    문서는 slug 로 가리키고, 바꾸면 그 말들이 조용히 틀린 것이 된다."""
    row = _group(db, slug)
    sent = payload.model_fields_set

    if "audience" in sent and payload.audience is not None:
        row.audience = require_choice(payload.audience, NAV_AUDIENCES, what="보이는 대상")
    if "label" in sent and payload.label is not None:
        row.label = payload.label
    if "icon" in sent and payload.icon is not None:
        row.icon = payload.icon
    if "sort_order" in sent and payload.sort_order is not None:
        row.sort_order = payload.sort_order
    if "is_active" in sent and payload.is_active is not None:
        row.is_active = payload.is_active

    _audit(
        db, user, action="ontology.group.update", table="nav_groups", row_id=row.id, label=slug
    )
    db.commit()
    db.refresh(row)
    return row


# --- 타입 -------------------------------------------------------------------


@router.get("/types", response_model=list[ObjectTypeOut])
def list_types(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[ObjectTypeOut]:
    counts = _counts(db)
    groups = _group_slugs(db)
    rows = db.scalars(select(ObjectType).order_by(ObjectType.sort_order, ObjectType.label))
    return [
        _type_out(
            row,
            groups.get(row.nav_group_id) if row.nav_group_id else None,
            counts.get(row.id, 0),
        )
        for row in rows
    ]


@router.post("/types", response_model=ObjectTypeOut, status_code=201)
def create_type(
    payload: ObjectTypeWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ObjectTypeOut:
    slug = require_slug(payload.slug, what="타입 slug")
    _check_type_choices(payload)
    if db.scalar(select(ObjectType).where(ObjectType.slug == slug)) is not None:
        raise Conflict(code("ONTOLOGY", 33), f"이미 있는 타입입니다: {slug}")

    group = _group(db, payload.nav_group_slug) if payload.nav_group_slug else None
    # 새 타입에는 속성이 없다 — 뷰가 속성을 가리키면 그 자리에서 걸린다.
    views.validate_list_view(payload.list_view, [])
    views.validate_form_view(payload.form_view, [], what="폼 화면")
    views.validate_form_view(payload.detail_view, [], what="상세 화면")
    row = ObjectType(
        slug=slug,
        label=payload.label,
        icon=payload.icon,
        description=payload.description,
        sort_order=payload.sort_order,
        nav_group_id=group.id if group else None,
        kind_class=payload.kind_class,
        entry_policy=payload.entry_policy,
        key_policy=payload.key_policy,
        key_scope=payload.key_scope,
        temporal_kind=payload.temporal_kind,
        list_view=payload.list_view,
        form_view=payload.form_view,
        detail_view=payload.detail_view,
        title_template=payload.title_template,
        is_active=payload.is_active,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        user,
        action="ontology.type.create",
        table="object_types",
        row_id=row.id,
        label=slug,
    )
    db.commit()
    db.refresh(row)
    return _type_out(row, group.slug if group else None, 0)


@router.patch("/types/{slug}", response_model=ObjectTypeOut)
def update_type(
    slug: str,
    payload: ObjectTypePatchRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ObjectTypeOut:
    """**보낸 것만 바꾼다.**

    전체 교체로 두면 화면이 `list_view` 를 안 실어 보낸 날 그 설정이 통째로
    날아가고, **그 손실은 저장한 사람 눈에 안 보인다.**

    **slug 는 안 바꾼다** — URL(`/o/<slug>`)·관계의 허용 타입·MCP 도구 이름이
    여기 물려 있어서, 바꾸면 그 셋이 조용히 어긋난다.
    """
    row = _type(db, slug)
    sent = payload.model_fields_set

    choices = (
        ("kind_class", payload.kind_class, KIND_CLASSES, "객체 분류"),
        ("entry_policy", payload.entry_policy, ENTRY_POLICIES, "입력 정책"),
        ("key_policy", payload.key_policy, KEY_POLICIES, "식별자 정책"),
        ("key_scope", payload.key_scope, KEY_SCOPES, "식별자 범위"),
        ("temporal_kind", payload.temporal_kind, TEMPORAL_KINDS, "시간 정책"),
    )
    for field, value, allowed, what in choices:
        if field in sent and value is not None:
            setattr(row, field, require_choice(value, allowed, what=what))

    if "label" in sent and payload.label is not None:
        row.label = payload.label
    if "icon" in sent and payload.icon is not None:
        row.icon = payload.icon
    if "description" in sent and payload.description is not None:
        row.description = payload.description
    if "sort_order" in sent and payload.sort_order is not None:
        row.sort_order = payload.sort_order
    # **모르는 키가 오면 거절한다 — 조용히 무시하지 않는다.** 무시하면 「스펙에는
    # 있는데 안 그려지는 필드」 가 쌓이고, 적은 쪽은 적용된 줄 안다(ADR 0005).
    defs = _properties_of(db, row.id)
    if "list_view" in sent and payload.list_view is not None:
        row.list_view = views.validate_list_view(payload.list_view, defs)
    if "form_view" in sent and payload.form_view is not None:
        row.form_view = views.validate_form_view(payload.form_view, defs, what="폼 화면")
    if "detail_view" in sent and payload.detail_view is not None:
        row.detail_view = views.validate_form_view(payload.detail_view, defs, what="상세 화면")
    if "title_template" in sent and payload.title_template is not None:
        row.title_template = payload.title_template
    if "is_active" in sent and payload.is_active is not None:
        row.is_active = payload.is_active

    # **`null` 을 명시하면 사이드바에서 뺀다.** 안 보내면 그대로 둔다 — 그 둘을
    # 안 가르면 다른 칸 하나 고칠 때마다 메뉴에서 사라진다.
    if "nav_group_slug" in sent:
        group = _group(db, payload.nav_group_slug) if payload.nav_group_slug else None
        row.nav_group_id = group.id if group else None

    _audit(
        db,
        user,
        action="ontology.type.update",
        table="object_types",
        row_id=row.id,
        label=slug,
    )
    db.commit()
    db.refresh(row)
    return _type_out(
        row,
        _group_slugs(db).get(row.nav_group_id) if row.nav_group_id else None,
        _counts(db).get(row.id, 0),
    )


def _check_type_choices(payload: ObjectTypeWriteRequest) -> None:
    """만들 때의 고른 값 검사. **고치기는 보낸 것만 보므로 따로 본다**(update_type)."""
    require_choice(payload.kind_class, KIND_CLASSES, what="객체 분류")
    require_choice(payload.entry_policy, ENTRY_POLICIES, what="입력 정책")
    require_choice(payload.key_policy, KEY_POLICIES, what="식별자 정책")
    require_choice(payload.key_scope, KEY_SCOPES, what="식별자 범위")
    require_choice(payload.temporal_kind, TEMPORAL_KINDS, what="시간 정책")


@router.delete("/types/{slug}", status_code=204)
def delete_type(
    slug: str,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> None:
    """타입을 지운다 — **객체가 하나도 없을 때만.**

    행이 있는데 지우면 그 데이터가 통째로 고아가 된다. 그런데 그것은 화면의 실수
    한 번으로 일어날 일이 아니다. 그래서 몇 개가 걸려 있는지 말하며 막고,
    **그만 쓰려는 것이면 비활성으로 두라고** 알려 준다 — 이 저장소는 지우지 않는다.

    비어 있으면 진짜로 지운다. 잘못 만든 타입이 목록에 영원히 남으면, 그 목록은
    곧 아무도 안 읽는다.
    """
    row = _type(db, slug)
    count = db.scalar(
        select(func.count())
        .select_from(ObjectInstance)
        .where(ObjectInstance.type_id == row.id)
    )
    if count:
        raise Conflict(
            code("ONTOLOGY", 38),
            f"{row.label}에 {count}개가 들어 있어 지울 수 없습니다. "
            "그만 쓰려는 것이면 「사용 안 함」 으로 두세요 — 자료는 남고 화면에서만 빠집니다.",
            details={"object_count": int(count)},
        )

    _audit(
        db,
        user,
        action="ontology.type.delete",
        table="object_types",
        row_id=row.id,
        label=slug,
    )
    # 속성 정의는 FK 가 없다(가리키는 표가 둘이라 걸 수 없다) — 여기서 함께 지운다.
    # 안 지우면 같은 slug 로 타입을 다시 만들 때 **옛 속성이 되살아난다.**
    db.query(PropertyDef).filter(
        PropertyDef.owner_kind == "type", PropertyDef.owner_id == row.id
    ).delete(synchronize_session=False)
    db.delete(row)
    db.commit()


@router.delete("/groups/{slug}", status_code=204)
def delete_group(
    slug: str,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> None:
    """묶음을 지운다 — **걸린 타입이 없을 때만.**

    FK 가 SET NULL 이라 DB 는 지우게 두지만, 그러면 그 타입들이 **조용히
    사이드바에서 사라진다.** 없어진 이유를 물을 자리가 없으므로 여기서 막는다.
    """
    row = _group(db, slug)
    attached = list(
        db.scalars(select(ObjectType.label).where(ObjectType.nav_group_id == row.id))
    )
    if attached:
        raise Conflict(
            code("ONTOLOGY", 39),
            f"{row.label}에 {', '.join(attached)} 이(가) 걸려 있습니다. "
            "그 타입들의 묶음을 먼저 바꾸세요 — 안 그러면 사이드바에서 조용히 사라집니다.",
            details={"types": attached},
        )

    _audit(
        db, user, action="ontology.group.delete", table="nav_groups", row_id=row.id, label=slug
    )
    db.delete(row)
    db.commit()


# --- 관계 종류 --------------------------------------------------------------


def _relation_types(db: Session) -> list[RelationType]:
    return list(
        db.scalars(select(RelationType).order_by(RelationType.sort_order, RelationType.label))
    )


def _relation_type(db: Session, slug: str) -> RelationType:
    row = db.scalar(select(RelationType).where(RelationType.slug == slug))
    if row is None:
        raise NotFound(code("ONTOLOGY", 40), f"관계 종류를 찾을 수 없습니다: {slug}")
    return row


def _check_type_slugs(db: Session, slugs: list[str] | None, *, what: str) -> None:
    """허용 타입이 **실재하는 타입인가.**

    없는 slug 를 넣어 두면 그 관계는 아무것도 못 맺는데, 화면은 「고를 것이
    없습니다」 라고만 말한다 — 오타인지 데이터가 없는 것인지 구별되지 않는다.
    """
    if not slugs:
        return
    known = {row.slug for row in db.scalars(select(ObjectType))}
    missing = sorted(set(slugs) - known)
    if missing:
        raise NotFound(
            code("ONTOLOGY", 41), f"{what}에 없는 타입이 있습니다: {', '.join(missing)}"
        )


def _check_relation_shape(payload: RelationTypeWriteRequest) -> None:
    require_choice(payload.cardinality, CARDINALITIES, what="개수 제약")
    # **이행적인데 순환을 허용하면 트리 렌더가 무한히 돈다.** 재귀 펼침이 자기
    # 자신으로 돌아오기 때문이다 — 그 상태는 화면이 멈추는 것으로만 드러난다.
    if payload.transitive and not payload.acyclic:
        raise Conflict(
            code("ONTOLOGY", 42),
            "재귀로 펼치는 관계는 순환을 막아야 합니다. "
            "안 그러면 자기 조상을 자식으로 넣는 순간 트리가 무한히 돕니다.",
        )


@router.get("/relation-types", response_model=list[RelationTypeOut])
def list_relation_types(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[RelationType]:
    return _relation_types(db)


@router.post("/relation-types", response_model=RelationTypeOut, status_code=201)
def create_relation_type(
    payload: RelationTypeWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RelationType:
    slug = require_slug(payload.slug, what="관계 slug")
    _check_relation_shape(payload)
    _check_type_slugs(db, payload.src_type_slugs, what="출발 타입")
    _check_type_slugs(db, payload.dst_type_slugs, what="도착 타입")
    if db.scalar(select(RelationType).where(RelationType.slug == slug)) is not None:
        raise Conflict(code("ONTOLOGY", 43), f"이미 있는 관계 종류입니다: {slug}")

    row = RelationType(
        slug=slug,
        label=payload.label,
        inverse_label=payload.inverse_label,
        description=payload.description,
        directed=payload.directed,
        transitive=payload.transitive,
        acyclic=payload.acyclic,
        cardinality=payload.cardinality,
        src_type_slugs=payload.src_type_slugs,
        dst_type_slugs=payload.dst_type_slugs,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        user,
        action="ontology.relation_type.create",
        table="relation_types",
        row_id=row.id,
        label=slug,
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/relation-types/{slug}", response_model=RelationTypeOut)
def update_relation_type(
    slug: str,
    payload: RelationTypePatchRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RelationType:
    """**보낸 것만 바꾼다.** slug 는 안 바꾼다 — 이미 맺힌 관계가 그 값을
    문자열로 들고 있어서, 바꾸면 그 관계들이 통째로 고아가 된다."""
    row = _relation_type(db, slug)
    sent = payload.model_fields_set

    if "cardinality" in sent and payload.cardinality is not None:
        row.cardinality = require_choice(payload.cardinality, CARDINALITIES, what="개수 제약")
    for field in ("label", "inverse_label", "description", "sort_order"):
        value = getattr(payload, field)
        if field in sent and value is not None:
            setattr(row, field, value)
    for flag in ("directed", "transitive", "acyclic", "is_active"):
        value = getattr(payload, flag)
        if flag in sent and value is not None:
            setattr(row, flag, value)

    # **`null` 을 명시하면 제약을 푼다.** 안 보내면 그대로 둔다.
    for field, what in (("src_type_slugs", "출발 타입"), ("dst_type_slugs", "도착 타입")):
        if field in sent:
            value = getattr(payload, field)
            _check_type_slugs(db, value, what=what)
            setattr(row, field, value)

    if row.transitive and not row.acyclic:
        raise Conflict(
            code("ONTOLOGY", 42),
            "재귀로 펼치는 관계는 순환을 막아야 합니다. "
            "안 그러면 자기 조상을 자식으로 넣는 순간 트리가 무한히 돕니다.",
        )

    _audit(
        db,
        user,
        action="ontology.relation_type.update",
        table="relation_types",
        row_id=row.id,
        label=slug,
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/relation-types/{slug}", status_code=204)
def delete_relation_type(
    slug: str,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> None:
    """관계 종류를 지운다 — **맺힌 관계가 하나도 없을 때만.**

    엣지는 이 slug 를 문자열로 들고 있다(FK 가 없다 — 종류의 추가·삭제가
    설정 변경이어야 하기 때문이다). 종류를 지우면 그 관계들은 **이름 없는 엣지**
    로 남고, 화면은 그 줄에 slug 를 그대로 보여 줄 수밖에 없다.

    비어 있으면 지운다. **속성 정의도 함께** — 안 지우면 같은 slug 로 다시 만들 때
    옛 속성이 되살아난다.
    """
    row = _relation_type(db, slug)
    edges = db.scalar(
        select(func.count()).select_from(ObjectRelation).where(ObjectRelation.relation == slug)
    )
    if edges:
        raise Conflict(
            code("ONTOLOGY", 44),
            f"{row.label}으로 맺힌 관계가 {edges}개 있어 지울 수 없습니다. "
            "그만 쓰려는 것이면 「사용함」 을 끄세요 — 맺힌 것은 남고 새로 맺지만 못합니다.",
            details={"relation_count": int(edges)},
        )
    _audit(
        db,
        user,
        action="ontology.relation_type.delete",
        table="relation_types",
        row_id=row.id,
        label=slug,
    )
    db.query(PropertyDef).filter(
        PropertyDef.owner_kind == "relation", PropertyDef.owner_id == row.id
    ).delete(synchronize_session=False)
    db.delete(row)
    db.commit()


# --- 속성 정의 --------------------------------------------------------------


@router.get("/types/{slug}/properties", response_model=list[PropertyDefOut])
def list_properties(
    slug: str, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[PropertyDef]:
    row = _type(db, slug)
    return _properties_of(db, row.id)


def _properties_of(db: Session, owner_id: uuid.UUID) -> list[PropertyDef]:
    return list(
        db.scalars(
            select(PropertyDef)
            .where(PropertyDef.owner_kind == "type", PropertyDef.owner_id == owner_id)
            .order_by(PropertyDef.sort_order, PropertyDef.label)
        )
    )


@router.post("/types/{slug}/properties", response_model=PropertyDefOut, status_code=201)
def create_property(
    slug: str,
    payload: PropertyDefWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PropertyDef:
    owner = _type(db, slug)
    key = require_key(payload.key)
    require_choice(payload.data_type, DATA_TYPES, what="속성 종류")
    _check_property_shape(payload)

    exists = db.scalar(
        select(PropertyDef).where(
            PropertyDef.owner_kind == "type",
            PropertyDef.owner_id == owner.id,
            PropertyDef.key == key,
        )
    )
    if exists is not None:
        raise Conflict(code("ONTOLOGY", 34), f"이미 있는 속성입니다: {key}")

    row = PropertyDef(
        owner_kind="type",
        owner_id=owner.id,
        key=key,
        label=payload.label,
        data_type=payload.data_type,
        unit=payload.unit,
        help=payload.help,
        required=payload.required,
        multi=payload.multi,
        enum_options=payload.enum_options,
        ref_type_slug=payload.ref_type_slug,
        min_value=payload.min_value,
        max_value=payload.max_value,
        decimals=payload.decimals,
        pattern=payload.pattern,
        default_value=payload.default_value,
        unique=payload.unique,
        section=payload.section,
        sort_order=payload.sort_order,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        user,
        action="ontology.property.create",
        table="property_defs",
        row_id=row.id,
        label=f"{slug}.{key}",
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/types/{slug}/properties/{key}", response_model=PropertyDefOut)
def update_property(
    slug: str,
    key: str,
    payload: PropertyDefWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PropertyDef:
    row = _property(db, slug, key)
    require_choice(payload.data_type, DATA_TYPES, what="속성 종류")
    _check_property_shape(payload)

    # **키와 종류는 안 바꾼다.** 키를 바꾸면 이미 저장된 값이 전부 고아가 되고,
    # 종류를 바꾸면 그 값들이 새 종류에 안 맞는데 **화면은 아무 말도 안 한다.**
    # 바꾸려면 새 속성을 만들고 옮긴다.
    if payload.data_type != row.data_type:
        raise Conflict(
            code("ONTOLOGY", 35),
            f"속성 종류는 바꿀 수 없습니다({row.data_type} → {payload.data_type}). "
            "이미 저장된 값이 새 종류에 안 맞아도 화면이 그것을 말해 주지 못합니다. "
            "새 속성을 만들어 옮기세요.",
        )
    row.label = payload.label
    row.unit = payload.unit
    row.help = payload.help
    row.required = payload.required
    row.multi = payload.multi
    row.enum_options = payload.enum_options
    row.ref_type_slug = payload.ref_type_slug
    row.min_value = payload.min_value
    row.max_value = payload.max_value
    row.decimals = payload.decimals
    row.pattern = payload.pattern
    row.default_value = payload.default_value
    row.unique = payload.unique
    row.section = payload.section
    row.sort_order = payload.sort_order
    _audit(
        db,
        user,
        action="ontology.property.update",
        table="property_defs",
        row_id=row.id,
        label=f"{slug}.{key}",
    )
    db.commit()
    db.refresh(row)
    return row


@router.post("/types/{slug}/properties/{key}/rename-option", response_model=RenameOptionOut)
def rename_option(
    slug: str,
    key: str,
    payload: RenameOptionRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RenameOptionOut:
    """고를 값의 이름을 바꾸면서 **저장된 값도 함께** 바꾼다.

    이름만 바꾸면 이미 저장된 값이 옛 이름으로 남아 거르기에서 빠지고, 그 사실은
    아무 데도 안 뜬다. `apply=false` 면 몇 개가 함께 바뀔지만 말한다.
    """
    owner = _type(db, slug)
    definition = _property(db, slug, key)
    if payload.apply:
        plan = codebook.apply_rename(
            db, user, owner, definition, payload.from_value, payload.to_value
        )
        return RenameOptionOut(applied=not plan.errors, **vars(plan))
    plan = codebook.plan_rename(db, owner, definition, payload.from_value, payload.to_value)
    return RenameOptionOut(applied=False, **vars(plan))


@router.post("/types/{slug}/properties/{key}/promote", response_model=PromoteOut)
def promote_property(
    slug: str,
    key: str,
    payload: PromoteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PromoteOut:
    """enum 속성을 **코드표(참조 타입)** 로 승격한다.

    옵션마다 객체가 생기고(또는 있는 것에 붙고), 저장된 문자열이 그 객체를 가리키게
    바뀐다. 정의가 바뀌므로 **되돌릴 자리(스냅샷)** 를 먼저 남긴다 — 값 이전은
    스냅샷이 못 되돌리지만, 정의만이라도 되돌릴 수 있어야 다음 수를 둘 수 있다.
    """
    owner = _type(db, slug)
    definition = _property(db, slug, key)
    args = {
        "existing_slug": payload.target_type_slug,
        "new_slug": payload.new_slug,
        "new_label": payload.new_label,
    }
    if not payload.apply:
        plan = codebook.plan_promote(db, owner, definition, **args)
        return _promote_out(plan, applied=False, snapshot_id=None)
    snapshot = _snapshot(db, user, reason=f"승격 직전: {slug}.{key}")
    snapshot_id = snapshot.id
    plan = codebook.apply_promote(
        db, user, owner, definition, nav_group_slug=payload.nav_group_slug, **args
    )
    if plan.errors:
        db.rollback()
        return _promote_out(plan, applied=False, snapshot_id=None)
    return _promote_out(plan, applied=True, snapshot_id=snapshot_id)


def _promote_out(
    plan: codebook.PromotePlan, *, applied: bool, snapshot_id: uuid.UUID | None
) -> PromoteOut:
    return PromoteOut(
        applied=applied,
        target_slug=plan.target_slug,
        target_label=plan.target_label,
        target_new=plan.target_new,
        options=[PromoteOptionOut(**vars(one)) for one in plan.options],
        errors=plan.errors,
        warnings=plan.warnings,
        snapshot_id=snapshot_id,
    )


@router.get("/types/{slug}/properties/{key}/usage", response_model=PropertyUsage)
def property_usage(
    slug: str, key: str, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> PropertyUsage:
    """**지우기 전에 무엇이 사라지는지.** 화면의 확인 창이 이것을 읽어 말한다."""
    row = _property(db, slug, key)
    owner = _type(db, slug)
    count = db.scalar(
        select(func.count())
        .select_from(ObjectInstance)
        .where(
            ObjectInstance.type_id == owner.id,
            ObjectInstance.deleted_at.is_(None),
            ObjectInstance.properties.has_key(key),
        )
    )
    return PropertyUsage(key=row.key, label=row.label, objects_with_value=int(count or 0))


@router.delete("/types/{slug}/properties/{key}", status_code=204)
def delete_property(
    slug: str,
    key: str,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> None:
    """정의를 지운다. **값은 JSONB 에 그대로 남는다.**

    값까지 훑어 지우면 되돌릴 방법이 없다. 남겨 두면 정의를 되살렸을 때 값이
    돌아온다 — 실수로 지우는 일이 실제로 있기 때문이다. 대신 화면이 몇 개가
    안 보이게 되는지(`/usage`) 먼저 말한다.
    """
    row = _property(db, slug, key)
    owner = _type(db, slug)
    # **안 걷어내면 그 뒤로 타입을 고칠 때마다 「없는 속성」 이라고 거절당한다** —
    # 그리고 사람은 자기가 방금 고친 것과 상관없는 그 오류를 이해할 수 없다.
    owner.list_view = views.prune_field(owner.list_view or {}, key)
    _audit(
        db,
        user,
        action="ontology.property.delete",
        table="property_defs",
        row_id=row.id,
        label=f"{slug}.{key}",
    )
    db.delete(row)
    db.commit()


def _property(db: Session, slug: str, key: str) -> PropertyDef:
    owner = _type(db, slug)
    row = db.scalar(
        select(PropertyDef).where(
            PropertyDef.owner_kind == "type",
            PropertyDef.owner_id == owner.id,
            PropertyDef.key == key,
        )
    )
    if row is None:
        raise NotFound(code("ONTOLOGY", 36), f"속성을 찾을 수 없습니다: {slug}.{key}")
    return row


def _check_property_shape(payload: PropertyDefWriteRequest) -> None:
    if (
        payload.min_value is not None
        and payload.max_value is not None
        and payload.min_value > payload.max_value
    ):
        # **뒤집힌 범위는 아무 값도 안 받는다.** 그런데 화면에는 「값이 틀렸다」
        # 로만 뜨므로, 정의가 잘못된 것을 아무도 못 찾는다.
        raise Conflict(
            code("ONTOLOGY", 38),
            f"아래 끝({payload.min_value})이 위 끝({payload.max_value})보다 큽니다. "
            "이러면 어떤 값도 못 넣습니다.",
        )
    if payload.data_type == "enum" and not payload.enum_options:
        raise Conflict(
            code("ONTOLOGY", 37),
            "고를 값 목록이 비어 있습니다. 선택 속성은 고를 것이 있어야 합니다.",
        )


# --- 스키마와 사이드바 ------------------------------------------------------


@router.get("/schema", response_model=OntologySchemaOut)
def ontology_schema(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> OntologySchemaOut:
    """**자기 설명적 스키마 — MCP 의 입력.**

    이 하나를 읽으면 도구를 동적으로 만들 수 있다. 도메인마다 MCP 서버를 새로
    짜지 않아도 되는 이유가 이것이다.
    """
    counts = _counts(db)
    groups = list(db.scalars(select(NavGroup).order_by(NavGroup.sort_order, NavGroup.label)))
    group_slugs = {g.id: g.slug for g in groups}

    types: list[ObjectTypeSchema] = []
    for row in db.scalars(
        select(ObjectType).order_by(ObjectType.sort_order, ObjectType.label)
    ):
        base = _type_out(
            row,
            group_slugs.get(row.nav_group_id) if row.nav_group_id else None,
            counts.get(row.id, 0),
        )
        types.append(
            ObjectTypeSchema(
                **base.model_dump(),
                properties=[
                    PropertyDefOut.model_validate(p) for p in _properties_of(db, row.id)
                ],
            )
        )

    return OntologySchemaOut(
        groups=[NavGroupOut.model_validate(g) for g in groups],
        types=types,
        relation_types=[RelationTypeOut.model_validate(r) for r in _relation_types(db)],
        data_types=list(DATA_TYPES),
        generated_at=datetime.now(UTC),
    )


@router.get("/nav", response_model=list[NavGroupNode])
def dynamic_nav(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[NavGroupNode]:
    """동적 사이드바. **정적 메뉴를 대체하지 않고 그 아래 합류한다.**

    빈 묶음은 내보내지 않는다 — 제목은 「여기 여럿이 있다」 는 신호라서, 하나도
    없는데 달면 거짓말이 된다.
    """
    groups = list(
        db.scalars(
            select(NavGroup)
            .where(NavGroup.is_active.is_(True))
            .order_by(NavGroup.sort_order, NavGroup.label)
        )
    )
    types = list(
        db.scalars(
            select(ObjectType)
            .where(ObjectType.is_active.is_(True), ObjectType.nav_group_id.is_not(None))
            .order_by(ObjectType.sort_order, ObjectType.label)
        )
    )

    out: list[NavGroupNode] = []
    for group in groups:
        items = [
            {"label": t.label, "icon": t.icon, "to": f"/o/{t.slug}", "slug": t.slug}
            for t in types
            if t.nav_group_id == group.id
        ]
        if not items:
            continue
        out.append(
            NavGroupNode(
                slug=group.slug,
                label=group.label,
                icon=group.icon,
                audience=group.audience,
                items=items,
            )
        )
    return out


# --- 가져오기와 되돌리기 -----------------------------------------------------


def _snapshot(db: Session, user: User, *, reason: str) -> OntologySnapshot:
    """지금 정의를 통째로 남긴다. **부르는 쪽이 커밋한다.**"""
    row = OntologySnapshot(
        actor_id=user.id,
        # **그때의 이름을 박는다.** 계정이 지워지면 누가 했는지 모르게 되는데,
        # 그건 되돌릴 자리가 존재하는 이유와 정면으로 어긋난다.
        actor_label=user.display_name or user.email,
        reason=reason,
        schema=importer.capture(db),
    )
    db.add(row)
    db.flush()
    return row


def _plan_out(
    prepared: importer.Plan, *, applied: bool, snapshot_id: uuid.UUID | None
) -> ImportPlanOut:
    return ImportPlanOut(
        applied=applied,
        changes=[
            ChangeOut(kind=c.kind, slug=c.slug, action=c.action, fields=c.fields)
            for c in prepared.changes
        ],
        warnings=prepared.warnings,
        errors=prepared.errors,
        snapshot_id=snapshot_id,
    )


@router.post("/import", response_model=ImportPlanOut)
def import_schema(
    payload: dict[str, Any],
    dry_run: bool = Query(default=True, description="적용하지 않고 계획만 본다"),
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ImportPlanOut:
    """정의를 통째로 받아 **한 트랜잭션으로** 적용한다.

    **기본이 `dry_run=true` 인 이유**: 기계가 부르는 자리이고, 실수가 기계 속도로
    반영되면 그 아래 쌓인 것이 전부 흔들린다. 적용은 **의도를 적어야** 일어난다.

    **더하고 고치기만 한다.** 「스키마에 없으니 지운다」 로 만들면 부분 스키마를
    한 번 보낸 날 그 타입의 객체가 통째로 갈 곳을 잃는다.
    """
    try:
        prepared = importer.plan(db, payload) if dry_run else importer.apply(db, payload)
    except ValueError as caught:
        # **모르는 항목은 거절한다.** 조용히 무시하면 보낸 쪽은 적용된 줄 안다.
        raise Conflict(code("ONTOLOGY", 70), str(caught)) from None

    if dry_run or prepared.errors:
        # 오류가 하나라도 있으면 **아무것도 안 바꾼다.**
        db.rollback()
        return _plan_out(prepared, applied=False, snapshot_id=None)

    # **적용 직전의 모습을 남긴다.** 감사 로그는 누가 뭘 했는지는 알려 주지만
    # 되돌려 주지는 않는다.
    db.rollback()
    snapshot = _snapshot(db, user, reason="가져오기")
    prepared = importer.apply(db, payload)
    _audit(
        db,
        user,
        action="ontology.import",
        table="ontology_snapshots",
        row_id=snapshot.id,
        label=f"{len([c for c in prepared.changes if c.action != 'unchanged'])}건",
    )
    db.commit()
    return _plan_out(prepared, applied=True, snapshot_id=snapshot.id)


@router.get("/snapshots", response_model=list[SnapshotOut])
def list_snapshots(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> list[SnapshotOut]:
    rows = db.scalars(
        select(OntologySnapshot).order_by(OntologySnapshot.taken_at.desc()).limit(50)
    )
    return [
        SnapshotOut(
            id=row.id,
            taken_at=row.taken_at,
            actor_label=row.actor_label,
            reason=row.reason,
            type_count=len((row.schema or {}).get("types") or []),
            relation_count=len((row.schema or {}).get("relation_types") or []),
        )
        for row in rows
    ]


@router.post("/snapshots/{snapshot_id}/restore", response_model=ImportPlanOut)
def restore_snapshot(
    snapshot_id: uuid.UUID,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ImportPlanOut:
    """그때의 정의를 다시 덮어씌운다.

    **그 뒤에 새로 만든 것은 안 지운다.** 지우면 그 사이에 쌓인 객체가 통째로 갈
    곳을 잃는다 — 되돌리기가 그것까지 하면 되돌리기 자체가 위험해진다.
    """
    row = db.get(OntologySnapshot, snapshot_id)
    if row is None:
        raise NotFound(code("ONTOLOGY", 71), "스냅샷을 찾을 수 없습니다.")

    # 되돌리기 **직전**도 남긴다 — 되돌린 것을 되돌릴 수 있어야 한다.
    before = _snapshot(db, user, reason=f"되돌리기 직전 ({row.taken_at:%Y-%m-%d %H:%M})")
    prepared = importer.apply(db, row.schema or {})
    if prepared.errors:
        db.rollback()
        return _plan_out(prepared, applied=False, snapshot_id=None)

    _audit(
        db,
        user,
        action="ontology.restore",
        table="ontology_snapshots",
        row_id=row.id,
        label=f"{row.taken_at:%Y-%m-%d %H:%M} 로 되돌림",
    )
    db.commit()
    return _plan_out(prepared, applied=True, snapshot_id=before.id)
