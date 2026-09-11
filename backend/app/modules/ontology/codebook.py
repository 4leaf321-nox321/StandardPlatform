"""코드표 — **고를 값 목록을 속성 밖으로 꺼내는 길.**

`enum_options` 는 속성 정의 안 문자열 배열이다. 작은 목록(「경미/보통/심각」)에는
그것으로 충분하지만, 두 가지가 안 된다:

    1. 이름을 바꾸면 이미 저장된 값이 옛 이름으로 남는다 — 거르기에서 빠지고, 그
       사실은 아무 데도 안 뜬다
    2. 여러 타입이 같은 목록을 나눠 쓸 수 없다 — 두 벌이 되고, 두 벌은 반드시 갈린다

그래서 둘을 둔다:

    rename    옵션 이름을 바꾸면서 **저장된 값도 함께** 바꾼다
    promote   enum 속성을 **참조 타입(코드표)** 으로 승격한다 — 옵션마다 객체가 생기고,
              저장된 문자열이 그 객체를 가리키게 바뀐다. 값에 설명·사용 중지·정렬이
              공짜로 생기고, 다른 타입도 같은 코드표를 가리킬 수 있다

둘 다 정의 가져오기와 같은 무늬 — **계획 먼저, 전부 아니면 무.**
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import NavGroup, ObjectType, PropertyDef
from app.modules.ontology.services import require_slug
from app.shared import audit
from app.shared.errors import Conflict, code


@dataclass
class RenamePlan:
    from_value: str
    to_value: str
    objects_with_value: int
    errors: list[str] = field(default_factory=list)


def _holders(db: Session, owner: ObjectType, key: str, value: str) -> list[ObjectInstance]:
    """이 값을 담고 있는 객체들 — 단일값도 다중값도."""
    column = ObjectInstance.properties[key]
    return list(
        db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.type_id == owner.id,
                ObjectInstance.deleted_at.is_(None),
                or_(column.astext == value, column.contains([value])),
            )
        )
    )


def _swap(raw: Any, old: str, new: str) -> Any:
    if isinstance(raw, list):
        return [new if item == old else item for item in raw]
    return new if raw == old else raw


def plan_rename(
    db: Session, owner: ObjectType, definition: PropertyDef, from_value: str, to_value: str
) -> RenamePlan:
    plan = RenamePlan(from_value=from_value, to_value=to_value, objects_with_value=0)
    options = definition.enum_options or []
    if definition.data_type != "enum":
        plan.errors.append("고를 값이 있는 속성(enum)에만 씁니다.")
        return plan
    if from_value not in options:
        plan.errors.append(f"「{from_value}」 은 고를 값에 없습니다.")
    if not to_value.strip():
        plan.errors.append("새 이름이 비어 있습니다.")
    elif to_value in options and to_value != from_value:
        plan.errors.append(
            f"「{to_value}」 은 이미 있는 값입니다. 둘을 합치려면 먼저 저장된 값을 옮기세요."
        )
    plan.objects_with_value = len(_holders(db, owner, definition.key, from_value))
    return plan


def apply_rename(
    db: Session,
    user: User,
    owner: ObjectType,
    definition: PropertyDef,
    from_value: str,
    to_value: str,
) -> RenamePlan:
    """옵션 이름과 **저장된 값을 한 트랜잭션에** 바꾼다. 객체마다 기록이 남는다."""
    plan = plan_rename(db, owner, definition, from_value, to_value)
    if plan.errors:
        return plan
    reason = f"고를 값 이름 바꿈: {from_value} → {to_value}"
    for row in _holders(db, owner, definition.key, from_value):
        before = dict(row.properties or {})
        after = dict(before)
        after[definition.key] = _swap(before.get(definition.key), from_value, to_value)
        row.properties = after
        audit.record(
            db,
            action="object.update",
            actor=user,
            target_table="objects",
            target_id=row.id,
            target_label=f"{owner.slug}:{row.label}",
            workspace_id=row.owner_workspace_id,
            changes=audit.diff({"properties": before}, {"properties": after}),
            reason=reason,
        )
    definition.enum_options = [
        to_value if one == from_value else one for one in definition.enum_options or []
    ]
    if definition.default_value == from_value:
        definition.default_value = to_value
    audit.record(
        db,
        action="ontology.property.update",
        actor=user,
        target_table="property_defs",
        target_id=definition.id,
        target_label=f"{owner.slug}.{definition.key}",
        changes={"enum_option": {"before": from_value, "after": to_value}},
        reason=f"저장된 값 {plan.objects_with_value}개도 함께",
    )
    db.commit()
    return plan


# --- 승격 -----------------------------------------------------------------------


@dataclass
class PromoteOption:
    value: str
    action: str
    """`create`(코드표에 새로 만듦) · `reuse`(이미 있는 객체에 붙임)."""
    object_id: uuid.UUID | None
    objects_with_value: int


@dataclass
class PromotePlan:
    target_slug: str
    target_label: str
    target_new: bool
    options: list[PromoteOption] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _target(
    db: Session,
    *,
    existing_slug: str | None,
    new_slug: str | None,
    new_label: str | None,
) -> tuple[ObjectType | None, str, str, bool]:
    """(있는 타입 | None, slug, label, 새로 만드나)."""
    if existing_slug:
        found = db.scalar(select(ObjectType).where(ObjectType.slug == existing_slug))
        if found is None:
            raise Conflict(code("ONTOLOGY", 60), f"타입을 찾을 수 없습니다: {existing_slug}")
        if found.kind_class != "reference":
            raise Conflict(
                code("ONTOLOGY", 61),
                f"{found.label}은 코드표(reference)가 아닙니다. "
                "코드표 타입만 가리킬 수 있습니다.",
            )
        return found, found.slug, found.label, False
    if not new_slug or not new_label:
        raise Conflict(
            code("ONTOLOGY", 62), "붙일 코드표를 고르거나, 새 코드표의 slug 와 이름을 주세요."
        )
    slug = require_slug(new_slug, what="타입 slug")
    if db.scalar(select(ObjectType).where(ObjectType.slug == slug)) is not None:
        raise Conflict(
            code("ONTOLOGY", 63),
            f"이미 있는 slug 입니다: {slug}. 「있는 코드표에 붙이기」 를 쓰세요.",
        )
    return None, slug, new_label.strip(), True


def plan_promote(
    db: Session,
    owner: ObjectType,
    definition: PropertyDef,
    *,
    existing_slug: str | None,
    new_slug: str | None,
    new_label: str | None,
) -> PromotePlan:
    target, slug, label, is_new = _target(
        db, existing_slug=existing_slug, new_slug=new_slug, new_label=new_label
    )
    plan = PromotePlan(target_slug=slug, target_label=label, target_new=is_new)
    if definition.data_type != "enum":
        plan.errors.append("고를 값이 있는 속성(enum)만 승격합니다.")
        return plan
    options = list(definition.enum_options or [])
    if not options:
        plan.errors.append("고를 값이 하나도 없습니다.")
        return plan

    existing_by_label: dict[str, ObjectInstance] = {}
    if target is not None:
        for row in db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.type_id == target.id, ObjectInstance.deleted_at.is_(None)
            )
        ):
            existing_by_label.setdefault(row.label.strip(), row)
            if row.key:
                existing_by_label.setdefault(row.key, row)

    for value in options:
        found = existing_by_label.get(value)
        plan.options.append(
            PromoteOption(
                value=value,
                action="reuse" if found else "create",
                object_id=found.id if found else None,
                objects_with_value=len(_holders(db, owner, definition.key, value)),
            )
        )

    # 옵션에 없는데 저장돼 있는 값 — 옮길 곳이 없다. 조용히 두면 uuid 도 문자열도 아닌
    # 값이 참조 칸에 남는다.
    stray = _stray_values(db, owner, definition.key, set(options))
    if stray:
        plan.errors.append(
            f"고를 값에 없는 저장값이 있습니다: {', '.join(sorted(stray))}. "
            "먼저 그 객체들을 고치거나 고를 값에 더하세요."
        )
    if target is not None and target.key_policy == "required":
        plan.errors.append(
            f"{target.label}은 식별자가 필수라 값 이름만으로는 객체를 못 만듭니다."
        )
    return plan


def _stray_values(db: Session, owner: ObjectType, key: str, allowed: set[str]) -> set[str]:
    rows = db.scalars(
        select(ObjectInstance).where(
            ObjectInstance.type_id == owner.id,
            ObjectInstance.deleted_at.is_(None),
            ObjectInstance.properties.has_key(key),
        )
    )
    stray: set[str] = set()
    for row in rows:
        raw = (row.properties or {}).get(key)
        for item in raw if isinstance(raw, list) else [raw]:
            if isinstance(item, str) and item and item not in allowed:
                stray.add(item)
    return stray


def apply_promote(
    db: Session,
    user: User,
    owner: ObjectType,
    definition: PropertyDef,
    *,
    existing_slug: str | None,
    new_slug: str | None,
    new_label: str | None,
    nav_group_slug: str | None,
) -> PromotePlan:
    """코드표 객체를 만들고, 정의를 참조로 바꾸고, 저장된 값을 옮긴다 — **한 트랜잭션.**"""
    plan = plan_promote(
        db,
        owner,
        definition,
        existing_slug=existing_slug,
        new_slug=new_slug,
        new_label=new_label,
    )
    if plan.errors:
        return plan

    target = db.scalar(select(ObjectType).where(ObjectType.slug == plan.target_slug))
    if target is None:
        group_id = None
        if nav_group_slug:
            group = db.scalar(select(NavGroup).where(NavGroup.slug == nav_group_slug))
            group_id = group.id if group else None
        target = ObjectType(
            slug=plan.target_slug,
            label=plan.target_label,
            kind_class="reference",
            entry_policy="closed",
            key_policy="optional",
            nav_group_id=group_id,
            description=f"{owner.label}의 「{definition.label}」 에서 승격한 코드표.",
        )
        db.add(target)
        db.flush()
        audit.record(
            db,
            action="ontology.type.create",
            actor=user,
            target_table="object_types",
            target_id=target.id,
            target_label=target.slug,
            reason=f"{owner.slug}.{definition.key} 승격",
        )

    id_of: dict[str, str] = {}
    for index, option in enumerate(plan.options):
        if option.action == "reuse" and option.object_id is not None:
            id_of[option.value] = str(option.object_id)
            continue
        row = ObjectInstance(
            type_id=target.id,
            label=option.value,
            description="",
            properties={},
            owner_workspace_id=None,
            created_by_id=user.id,
        )
        db.add(row)
        db.flush()
        option.object_id = row.id
        id_of[option.value] = str(row.id)
        audit.record(
            db,
            action="object.create",
            actor=user,
            target_table="objects",
            target_id=row.id,
            target_label=f"{target.slug}:{row.label}",
            reason=f"{owner.slug}.{definition.key} 승격 (순서 {index + 1})",
        )

    reason = f"「{definition.label}」 을 코드표 {target.label}(으)로 승격"
    moved = 0
    for value, object_id in id_of.items():
        for row in _holders(db, owner, definition.key, value):
            before = dict(row.properties or {})
            after = dict(before)
            after[definition.key] = _swap(before.get(definition.key), value, object_id)
            row.properties = after
            moved += 1
            audit.record(
                db,
                action="object.update",
                actor=user,
                target_table="objects",
                target_id=row.id,
                target_label=f"{owner.slug}:{row.label}",
                workspace_id=row.owner_workspace_id,
                changes=audit.diff({"properties": before}, {"properties": after}),
                reason=reason,
            )

    definition.data_type = "object_ref"
    definition.ref_type_slug = target.slug
    definition.enum_options = None
    if isinstance(definition.default_value, str):
        definition.default_value = id_of.get(definition.default_value)
    audit.record(
        db,
        action="ontology.property.promote",
        actor=user,
        target_table="property_defs",
        target_id=definition.id,
        target_label=f"{owner.slug}.{definition.key}",
        changes={
            "data_type": {"before": "enum", "after": "object_ref"},
            "ref_type_slug": target.slug,
        },
        reason=f"저장된 값 {moved}개를 옮김",
    )
    db.commit()
    return plan
