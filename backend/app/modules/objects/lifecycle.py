"""지우기와 합치기 — **누르기 전에 무엇이 걸렸는지 안다.**

객체를 지우면 그것을 가리키던 참조 속성은 uuid 만 남고, 화면에는 뜻 모를 긴 문자열이
뜬다. 그 손실은 지운 사람 눈에 안 보인다 — 다른 객체의 화면에서 드러난다. 그래서
지우기 전에 「이것을 가리키는 것」 을 세어 보여 주고, 걸린 것이 있으면 사람이 고른다:

    block    그대로 둔다 — 먼저 끊거나 고치러 간다 (기본)
    detach   참조를 비우고 관계를 끊고 지운다 — 가리키던 객체마다 감사 기록이 남는다
    merge    다른 객체에 합치고 지운다 — 참조·관계가 이긴 쪽으로 옮겨 가고,
             지는 쪽은 `merged_into` 로 남아 옛 링크가 새 것으로 간다

## 보이지 않는 것도 센다

남의 부서 객체가 이것을 가리키고 있으면 그 사람은 그것을 볼 수 없다. 그래도 **수는
말한다** — 안 말하면 「아무것도 안 걸렸다」 로 읽고 지우고, 남의 부서 화면이 깨진다.
무엇인지는 안 말한다(없는 것과 안 보이는 것을 같은 말로 답하는 규칙).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance, ObjectRelation
from app.modules.ontology.models import ObjectType, PropertyDef
from app.shared import audit
from app.shared.errors import Conflict, code
from app.shared.permissions import require_owner_edit, visible_owner_clause


@dataclass
class RefHit:
    """이 객체를 **속성으로** 가리키는 객체 하나."""

    object_id: uuid.UUID
    label: str
    key: str | None
    type_slug: str
    type_label: str
    property_key: str
    property_label: str


@dataclass
class RelationHit:
    relation_id: uuid.UUID
    relation: str
    outgoing: bool
    other_id: uuid.UUID
    other_label: str
    other_type_slug: str


@dataclass
class References:
    property_refs: list[RefHit] = field(default_factory=list)
    relations: list[RelationHit] = field(default_factory=list)
    hidden_property_refs: int = 0
    """볼 수 없는 부서의 참조 수. 수만 말한다."""
    hidden_relations: int = 0

    @property
    def total(self) -> int:
        return (
            len(self.property_refs)
            + len(self.relations)
            + self.hidden_property_refs
            + self.hidden_relations
        )


def _ref_defs(db: Session, type_slug: str) -> list[tuple[ObjectType, PropertyDef]]:
    """이 타입을 가리키는 `object_ref` 속성 정의 전부.

    어느 타입의 어느 칸이 나를 가리킬 수 있나.
    """
    defs = list(
        db.scalars(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "type",
                PropertyDef.data_type == "object_ref",
                PropertyDef.ref_type_slug == type_slug,
            )
        )
    )
    if not defs:
        return []
    types = {row.id: row for row in db.scalars(select(ObjectType))}
    return [(types[d.owner_id], d) for d in defs if d.owner_id in types]


def _pointing_rows(
    db: Session, definition: PropertyDef, target_id: uuid.UUID
) -> list[ObjectInstance]:
    """이 칸에 target 을 담은 객체들. 단일값·다중값 모두 — JSONB 안이라 텍스트로 본다."""
    column = ObjectInstance.properties[definition.key]
    wanted = str(target_id)
    return list(
        db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.type_id == definition.owner_id,
                ObjectInstance.deleted_at.is_(None),
                or_(column.astext == wanted, column.contains([wanted])),
            )
        )
    )


def references_of(
    db: Session, user: User, row: ObjectInstance, object_type: ObjectType
) -> References:
    """이 객체를 가리키는 것 전부 — 속성 참조와 관계."""
    out = References()
    for owner_type, definition in _ref_defs(db, object_type.slug):
        for other in _pointing_rows(db, definition, row.id):
            if _visible(db, user, other):
                out.property_refs.append(
                    RefHit(
                        object_id=other.id,
                        label=other.label,
                        key=other.key,
                        type_slug=owner_type.slug,
                        type_label=owner_type.label,
                        property_key=definition.key,
                        property_label=definition.label,
                    )
                )
            else:
                out.hidden_property_refs += 1

    types = {one.id: one for one in db.scalars(select(ObjectType))}
    edges = list(
        db.scalars(
            select(ObjectRelation).where(
                (ObjectRelation.src_object_id == row.id)
                | (ObjectRelation.dst_object_id == row.id)
            )
        )
    )
    for edge in edges:
        outgoing = edge.src_object_id == row.id
        other_id = edge.dst_object_id if outgoing else edge.src_object_id
        end = db.get(ObjectInstance, other_id)
        if end is None or end.deleted_at is not None:
            continue
        if _visible(db, user, end):
            out.relations.append(
                RelationHit(
                    relation_id=edge.id,
                    relation=edge.relation,
                    outgoing=outgoing,
                    other_id=end.id,
                    other_label=end.label,
                    other_type_slug=types[end.type_id].slug if end.type_id in types else "",
                )
            )
        else:
            out.hidden_relations += 1
    return out


def _visible(db: Session, user: User, row: ObjectInstance) -> bool:
    return (
        db.scalar(
            select(ObjectInstance.id).where(
                ObjectInstance.id == row.id,
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
        is not None
    )


def _soft_delete(
    db: Session, user: User, row: ObjectInstance, object_type: ObjectType, *, reason: str
) -> None:
    row.deleted_at = datetime.now(UTC)
    audit.record(
        db,
        action="object.delete",
        actor=user,
        target_table="objects",
        target_id=row.id,
        target_label=f"{object_type.slug}:{row.label}",
        workspace_id=row.owner_workspace_id,
        reason=reason,
    )


def delete_blocking(
    db: Session, user: User, row: ObjectInstance, object_type: ObjectType
) -> None:
    """기본 — 가리키는 것이 있으면 **막고 무엇이 걸렸는지 말한다.**"""
    refs = references_of(db, user, row, object_type)
    if refs.total:
        raise Conflict(
            code("OBJECTS", 50),
            f"{row.label}을 가리키는 것이 {refs.total}개 있어 그대로는 지울 수 없습니다. "
            "참조를 비우고 지우거나, 다른 객체에 합치거나, 먼저 끊으세요.",
            details={
                "property_refs": len(refs.property_refs) + refs.hidden_property_refs,
                "relations": len(refs.relations) + refs.hidden_relations,
            },
        )
    _soft_delete(db, user, row, object_type, reason="")
    db.commit()


def _rewrite_ref(
    values: dict[str, Any], key: str, old: str, new: str | None
) -> dict[str, Any]:
    """속성 하나에서 old 를 new 로(또는 비움). 다중값이면 자리만 바꾸고 겹치면 하나로."""
    merged = dict(values)
    raw = merged.get(key)
    if isinstance(raw, list):
        items = [item for item in raw if item != old]
        if new is not None and new not in items:
            items.append(new)
        if items:
            merged[key] = items
        else:
            merged.pop(key, None)
    elif raw == old:
        if new is None:
            merged.pop(key, None)
        else:
            merged[key] = new
    return merged


def _rewrite_property_refs(
    db: Session,
    user: User,
    row: ObjectInstance,
    object_type: ObjectType,
    *,
    new_id: uuid.UUID | None,
    reason: str,
) -> int:
    """이 객체를 가리키는 모든 칸을 고친다 — **보이지 않는 것까지.** 반쯤 고치면 남의
    부서 화면에만 uuid 가 남고, 그것은 이 사람이 알 수 없다."""
    touched = 0
    for owner_type, definition in _ref_defs(db, object_type.slug):
        for other in _pointing_rows(db, definition, row.id):
            before = dict(other.properties or {})
            other.properties = _rewrite_ref(
                before, definition.key, str(row.id), None if new_id is None else str(new_id)
            )
            touched += 1
            audit.record(
                db,
                action="object.update",
                actor=user,
                target_table="objects",
                target_id=other.id,
                target_label=f"{owner_type.slug}:{other.label}",
                workspace_id=other.owner_workspace_id,
                changes=audit.diff({"properties": before}, {"properties": other.properties}),
                reason=reason,
            )
    return touched


def delete_detaching(
    db: Session, user: User, row: ObjectInstance, object_type: ObjectType
) -> dict[str, int]:
    """참조를 비우고 관계를 끊고 지운다. **가리키던 객체마다 감사 기록이 남는다** —
    그 객체의 화면에서 「왜 이 칸이 비었지」 를 물으면 답이 있어야 한다."""
    reason = f"{row.label} 을 지우면서 참조를 비움"
    cleared = _rewrite_property_refs(db, user, row, object_type, new_id=None, reason=reason)
    edges = list(
        db.scalars(
            select(ObjectRelation).where(
                (ObjectRelation.src_object_id == row.id)
                | (ObjectRelation.dst_object_id == row.id)
            )
        )
    )
    for edge in edges:
        audit.record(
            db,
            action="object.relation.remove",
            actor=user,
            target_table="object_relations",
            target_id=edge.id,
            target_label=f"{edge.src_object_id} -{edge.relation}-> {edge.dst_object_id}",
            changes=audit.relation_endpoints(edge, "", ""),
            reason=reason,
        )
        db.delete(edge)
    _soft_delete(db, user, row, object_type, reason="참조를 비우고 지움")
    db.commit()
    return {"property_refs": cleared, "relations": len(edges)}


def merge_into(
    db: Session,
    user: User,
    row: ObjectInstance,
    object_type: ObjectType,
    target: ObjectInstance,
) -> dict[str, int]:
    """row 를 target 에 합친다 — 참조·관계를 옮기고 row 는 `merged_into` 로 남긴다.

    **같은 타입끼리만.** 공급사를 부품에 합치면 그것을 가리키던 「공급사」 칸이 부품을
    가리키게 되고, 그 칸의 정의(ref_type_slug)와 어긋난다.

    옮기다 겹치는 관계(target 에 이미 같은 선이 있거나, 자기 자신으로 가는 선)는
    버린다 — 두 겹으로 남기면 그것은 병합이 아니라 복제다.
    """
    if target.id == row.id:
        raise Conflict(code("OBJECTS", 51), "자기 자신에게는 합칠 수 없습니다.")
    if target.type_id != row.type_id:
        raise Conflict(code("OBJECTS", 52), "같은 타입끼리만 합칩니다.")
    require_owner_edit(
        db, user, target.owner_workspace_id, what="객체", code_value=code("OBJECTS", 53)
    )
    reason = f"{row.label} 을 {target.label} 에 합침"
    moved_refs = _rewrite_property_refs(
        db, user, row, object_type, new_id=target.id, reason=reason
    )

    edges = list(
        db.scalars(
            select(ObjectRelation).where(
                (ObjectRelation.src_object_id == row.id)
                | (ObjectRelation.dst_object_id == row.id)
            )
        )
    )
    moved_edges = 0
    dropped_edges = 0
    for edge in edges:
        src = target.id if edge.src_object_id == row.id else edge.src_object_id
        dst = target.id if edge.dst_object_id == row.id else edge.dst_object_id
        duplicate = src == dst or (
            db.scalar(
                select(ObjectRelation.id).where(
                    ObjectRelation.src_object_id == src,
                    ObjectRelation.dst_object_id == dst,
                    ObjectRelation.relation == edge.relation,
                )
            )
            is not None
        )
        if duplicate:
            db.delete(edge)
            dropped_edges += 1
            continue
        edge.src_object_id = src
        edge.dst_object_id = dst
        moved_edges += 1
    db.flush()

    # 지는 쪽의 비어 있는 칸을 이긴 쪽이 채운다 — 값이 있는 칸은 이긴 쪽이 맞다.
    before = dict(target.properties or {})
    filled = dict(before)
    for key, value in (row.properties or {}).items():
        if key not in filled or filled[key] in (None, "", []):
            filled[key] = value
    if filled != before:
        target.properties = filled
    if not target.description and row.description:
        target.description = row.description

    row.merged_into_id = target.id
    _soft_delete(db, user, row, object_type, reason=reason)
    audit.record(
        db,
        action="object.merge",
        actor=user,
        target_table="objects",
        target_id=target.id,
        target_label=f"{object_type.slug}:{target.label}",
        workspace_id=target.owner_workspace_id,
        changes={
            "merged_from": str(row.id),
            "merged_from_label": row.label,
            "property_refs": moved_refs,
            "relations_moved": moved_edges,
            "relations_dropped": dropped_edges,
            **audit.diff({"properties": before}, {"properties": target.properties or {}}),
        },
        reason=reason,
    )
    db.commit()
    return {
        "property_refs": moved_refs,
        "relations_moved": moved_edges,
        "relations_dropped": dropped_edges,
    }
