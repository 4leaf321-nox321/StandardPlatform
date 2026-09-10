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
from app.modules.objects.models import OBJECT_STATUSES, ObjectInstance
from app.modules.objects.schemas import (
    AttachmentBrief,
    ObjectCreateRequest,
    ObjectOut,
    ObjectPatchRequest,
    ObjectProfileOut,
)
from app.modules.objects.services import (
    apply_property_filters,
    apply_search,
    apply_sort,
    count_of,
    normalize_key,
    properties_of,
    require_key_free,
    require_refs_exist,
)
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.ontology.schemas import PropertyDefOut
from app.modules.ontology.services import (
    merge_properties,
    require_choice,
    validate_properties,
)
from app.modules.workspaces.models import Workspace
from app.shared import audit
from app.shared.auth import current_user
from app.shared.errors import Forbidden, NotFound, code
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


# --- 목록 -------------------------------------------------------------------


@router.get("/{type_slug}", response_model=Page[ObjectOut])
def list_objects(
    type_slug: str,
    request: Request,
    q: str | None = Query(default=None, description="이름·식별자·검색 속성"),
    status: str | None = Query(default=None),
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
