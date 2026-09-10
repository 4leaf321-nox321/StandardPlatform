"""메타모델 라우터 — **읽기는 누구나, 고치기는 시스템 관리자만.**

타입을 부서마다 열지 않는 이유는 [ADR 0005](../../../../docs/adr/0005-온톨로지-메타모델.md)
에 있다: 같은 개념이 부서마다 갈리면 **연결하려고 만든 것이 칸막이가 된다.**
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import (
    DATA_TYPES,
    ENTRY_POLICIES,
    KEY_POLICIES,
    KEY_SCOPES,
    KIND_CLASSES,
    NAV_AUDIENCES,
    TEMPORAL_KINDS,
    NavGroup,
    ObjectType,
    PropertyDef,
)
from app.modules.ontology.schemas import (
    NavGroupNode,
    NavGroupOut,
    NavGroupPatchRequest,
    NavGroupWriteRequest,
    ObjectTypeOut,
    ObjectTypePatchRequest,
    ObjectTypeSchema,
    ObjectTypeWriteRequest,
    OntologySchemaOut,
    PropertyDefOut,
    PropertyDefWriteRequest,
    PropertyUsage,
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
    """타입별 인스턴스 수. **한 번에 센다** — 타입마다 세면 목록 하나에 질의가
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
    if "list_view" in sent and payload.list_view is not None:
        row.list_view = payload.list_view
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
    """타입을 지운다 — **인스턴스가 하나도 없을 때만.**

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
