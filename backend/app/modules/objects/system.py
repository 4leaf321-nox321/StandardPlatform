"""`system` 타입의 객체 — **행 없이 원 표를 읽는다.**

부서·계정 같은 1급 표는 이미 있다. 그것을 온톨로지에서 가리키려고 `objects` 에 행을
복제하면 두 벌이 되고 두 벌은 반드시 갈린다. 그래서 `kind_class='system'` 인 타입은
행이 없고, 목록·상세·참조 풀이가 전부 `shared/system_sources.py` 에 등록된 원 표에서
나온다. 그 끝단과 잇는 선은 `object_links` 다(FK 로 묶을 수 없으므로).

여기 있는 것은 셋이다:

    투영     원 표의 행을 `ObjectOut` 모양으로 — 목록·상세·picker 가 같은 것을 본다
    참조     `object_ref` 가 system 타입을 가리킬 때의 존재 확인과 이름 풀이
    끝점     관계의 한쪽 끝이 객체든 system 이든 같은 모양(`End`)으로 다루기
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance, ObjectLink
from app.modules.objects.schemas import ObjectOut
from app.modules.ontology.models import ObjectType, PropertyDef
from app.shared import system_sources
from app.shared.errors import Conflict, code
from app.shared.permissions import visible_owner_clause
from app.shared.system_sources import SystemRef, SystemSource


def is_system(object_type: ObjectType) -> bool:
    return object_type.kind_class == "system"


def source_of(object_type: ObjectType) -> SystemSource:
    """이 타입이 비추는 원 표. **등록이 빠졌으면 여기서 멈춘다** — 빈 목록을 돌려주면
    「없다」 로 읽히고, 사람은 없는 것을 새로 만든다."""
    found = system_sources.system_source(object_type.system_source)
    if found is None:
        raise Conflict(
            code("OBJECTS", 70),
            f"{object_type.label}이 비추는 원 표({object_type.system_source or '없음'})가 "
            "이 설치에 등록돼 있지 않습니다. 타입 정의를 확인하세요.",
        )
    return found


def source_or_none(object_type: ObjectType) -> SystemSource | None:
    """등록이 빠졌으면 None. **한 타입 때문에 화면 전체가 멈추면 안 되는 자리**에서 쓴다.

    원 표는 `main.py` 에서 조립된다 — 도메인을 떼거나 되돌리면 그 표를 가리키던 타입이
    정의에 남는다. 그때 홈의 「남은 일」 처럼 **모든 타입을 훑는 화면**이 409 를 내면,
    설치 전체가 그 타입 하나 때문에 멎는다. 목록·상세처럼 그 타입을 **직접** 여는
    자리에서는 여전히 `source_of` 가 멈춰 이유를 말한다.
    """
    return system_sources.system_source(object_type.system_source)


def types_by_slug(db: Session) -> dict[str, ObjectType]:
    return {row.slug: row for row in db.scalars(select(ObjectType))}


# --- 투영 --------------------------------------------------------------------


def projected(ref: SystemRef, type_slug: str, at: datetime) -> ObjectOut:
    """원 표의 행 하나를 객체 모양으로. 속성은 없다 — 있다면 그 표의 화면에서 본다."""
    return ObjectOut(
        id=ref.id,
        type_slug=type_slug,
        key=ref.key,
        label=ref.label,
        description=ref.hint,
        properties={},
        ref_labels={},
        status="active" if ref.active else "deprecated",
        owner_workspace_slug=None,
        valid_from_year=None,
        valid_to_year=None,
        created_at=at,
        updated_at=at,
    )


def find(db: Session, object_type: ObjectType, object_id: uuid.UUID) -> SystemRef | None:
    return source_of(object_type).lookup(db, [object_id]).get(object_id)


# --- 참조 --------------------------------------------------------------------


def _ids_by_target(
    defs: list[PropertyDef], rows: list[dict[str, Any]]
) -> dict[str, set[uuid.UUID]]:
    """참조 속성이 가리키는 id 들을 **상대 타입별로** 모은다. 타입을 알아야 어느
    표에 물을지 정해진다."""
    out: dict[str, set[uuid.UUID]] = {}
    for definition in defs:
        if definition.data_type != "object_ref":
            continue
        target = definition.ref_type_slug or ""
        for values in rows:
            raw = values.get(definition.key)
            for item in raw if isinstance(raw, list) else [raw]:
                if not isinstance(item, str) or not item:
                    continue
                try:
                    out.setdefault(target, set()).add(uuid.UUID(item))
                except ValueError:
                    continue
    return out


def ref_labels(
    db: Session, defs: list[PropertyDef], rows: list[dict[str, Any]]
) -> dict[uuid.UUID, str]:
    """이 값들이 가리키는 것들의 이름을 **한 번에** — 객체는 `objects` 에서, system 은
    원 표에서. 지워진 것을 가리키면 **지워졌다고 적는다.** 이름만 보이면 살아 있는 줄 안다.
    """
    by_target = _ids_by_target(defs, rows)
    if not by_target:
        return {}
    types = types_by_slug(db)
    out: dict[uuid.UUID, str] = {}
    plain: set[uuid.UUID] = set()
    for slug, ids in by_target.items():
        target = types.get(slug)
        if target is not None and is_system(target):
            found = source_of(target).lookup(db, sorted(ids))
            for one in ids:
                ref = found.get(one)
                out[one] = ref.label if ref else "(지워짐)"
        else:
            # 상대 타입을 모르는(정의가 비었거나 지워진) 참조도 객체 표에서 찾아 본다.
            plain.update(ids)
    if plain:
        for row in db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(plain))):
            out[row.id] = row.label if row.deleted_at is None else f"{row.label} (지워짐)"
    return out


def missing_refs(db: Session, defs: list[PropertyDef], values: dict[str, Any]) -> list[str]:
    """가리키는 것이 **실제로 없는** id 들. 저장 전에 부른다."""
    by_target = _ids_by_target(defs, [values])
    if not by_target:
        return []
    types = types_by_slug(db)
    missing: list[str] = []
    for slug, ids in by_target.items():
        target = types.get(slug)
        if target is not None and is_system(target):
            found = source_of(target).lookup(db, sorted(ids))
            missing.extend(str(one) for one in sorted(ids) if one not in found)
        else:
            alive = set(
                db.scalars(
                    select(ObjectInstance.id).where(
                        ObjectInstance.id.in_(ids), ObjectInstance.deleted_at.is_(None)
                    )
                )
            )
            missing.extend(str(one) for one in sorted(ids) if one not in alive)
    return missing


# --- 끝점 --------------------------------------------------------------------


@dataclass(frozen=True)
class End:
    """관계의 한쪽 끝 — 객체든 system 이든 같은 모양."""

    id: uuid.UUID
    type_slug: str
    label: str
    is_system: bool
    owner_workspace_id: uuid.UUID | None = None
    """system 끝은 부서 소유가 아니다(None). 고칠 수 있는지는 객체 쪽 끝이 정한다."""


def end_of(row: ObjectInstance, object_type: ObjectType) -> End:
    return End(
        id=row.id,
        type_slug=object_type.slug,
        label=row.label,
        is_system=False,
        owner_workspace_id=row.owner_workspace_id,
    )


def find_end(
    db: Session,
    user: User,
    object_id: uuid.UUID,
    *,
    allowed: list[str] | None,
) -> End | None:
    """id 하나로 끝점을 찾는다 — **객체 표 먼저, 그다음 허용된 system 타입의 원 표.**

    `allowed` 가 비었으면(관계 종류가 도착 타입을 안 정했으면) system 은 안 본다 —
    원 표를 전부 뒤지면 「부서 id 를 넣었더니 계정이 걸렸다」 같은 일이 생긴다.
    """
    types = types_by_slug(db)
    row = db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == object_id,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    if row is not None:
        found_type = next((t for t in types.values() if t.id == row.type_id), None)
        return end_of(row, found_type) if found_type else None
    for slug in allowed or []:
        target = types.get(slug)
        if target is None or not is_system(target):
            continue
        ref = find(db, target, object_id)
        if ref is not None:
            return End(id=ref.id, type_slug=slug, label=ref.label, is_system=True)
    return None


def links_of(db: Session, object_id: uuid.UUID) -> list[ObjectLink]:
    """이 끝에 걸린 선 전부 — 객체든 system 이든."""
    return list(
        db.scalars(
            select(ObjectLink)
            .where((ObjectLink.src_id == object_id) | (ObjectLink.dst_id == object_id))
            .order_by(ObjectLink.created_at)
        )
    )


def other_end(
    db: Session, user: User, link: ObjectLink, mine: uuid.UUID, types: dict[str, ObjectType]
) -> End | None:
    """선의 반대쪽 끝. **못 보는 것이면 None** — 없는 것과 안 보이는 것을 같은 말로."""
    outgoing = link.src_id == mine
    slug = link.dst_type if outgoing else link.src_type
    other_id = link.dst_id if outgoing else link.src_id
    target = types.get(slug)
    if target is None:
        return None
    if is_system(target):
        ref = find(db, target, other_id)
        return End(id=ref.id, type_slug=slug, label=ref.label, is_system=True) if ref else None
    row = db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == other_id,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    return end_of(row, target) if row is not None else None
