"""승격 — **JSONB 로 커진 타입을 전용 표로 내리고, 온톨로지에서는 투영으로 남긴다.**

`properties` JSONB 에는 유니크·FK·CHECK 를 못 건다(ADR 0005 결정 8). 그 대가를 감당
못 할 타입은 전용 표를 만들고 그 표를 `system_sources` 에 등록한 뒤, 타입을
`kind_class='system'` 으로 돌린다. 그러면 화면과 MCP 는 표가 바뀐 것을 모른다 —
같은 slug, 같은 id 로 똑같이 보이고 똑같이 참조된다.

여기가 그 절차의 **기계적인 마지막 단계**다(전체 절차는 `docs/승격-경로.md`):

    1. 전용 표가 같은 id 로 채워졌는지 확인한다 — 하나라도 빠지면 아무것도 안 한다
    2. 이 타입에 걸린 관계(`object_relations`)를 `object_links` 로 옮긴다 — id 그대로
    3. `objects` 행을 지운다(`deleted_at`) — 참조는 원 표에서 풀리므로 끊기지 않는다
    4. 타입을 `system` + `system_source` 로 돌린다

**id 를 보존하는 것이 전부다.** 다른 타입의 `object_ref` 칸에는 이 타입 객체의 uuid
가 그대로 들어 있다. 전용 표가 새 id 를 쓰면 그 칸이 전부 「(지워짐)」 이 되고, 그
손실은 다른 타입의 화면에서만 드러난다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance, ObjectLink, ObjectRelation
from app.modules.ontology.models import ObjectType
from app.shared import audit, system_sources
from app.shared.errors import Conflict, code


@dataclass
class PromotionPlan:
    type_slug: str
    source_key: str
    objects: int = 0
    relations: int = 0
    missing_ids: list[str] = field(default_factory=list)
    """전용 표에 없는 객체 id — **하나라도 있으면 적용하지 않는다.**"""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.missing_ids


def plan(db: Session, object_type: ObjectType, source_key: str) -> PromotionPlan:
    out = PromotionPlan(type_slug=object_type.slug, source_key=source_key)
    if object_type.kind_class == "system":
        out.errors.append(f"{object_type.label}은 이미 투영(system) 타입입니다.")
        return out
    source = system_sources.system_source(source_key)
    if source is None:
        known = ", ".join(system_sources.known_source_keys()) or "(없음)"
        out.errors.append(f"등록되지 않은 원 표입니다: {source_key}. 등록된 것: {known}")
        return out

    rows = list(
        db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.type_id == object_type.id, ObjectInstance.deleted_at.is_(None)
            )
        )
    )
    out.objects = len(rows)
    ids = [row.id for row in rows]
    found = source.lookup(db, ids) if ids else {}
    out.missing_ids = [str(one) for one in ids if one not in found]

    edges = _edges_of(db, ids)
    out.relations = len(edges)
    return out


def _edges_of(db: Session, ids: list[uuid.UUID]) -> list[ObjectRelation]:
    if not ids:
        return []
    return list(
        db.scalars(
            select(ObjectRelation).where(
                or_(
                    ObjectRelation.src_object_id.in_(ids),
                    ObjectRelation.dst_object_id.in_(ids),
                )
            )
        )
    )


def apply(db: Session, user: User, object_type: ObjectType, source_key: str) -> PromotionPlan:
    """**한 트랜잭션.** 부르는 쪽이 커밋한다. 계획이 안 서면 아무것도 안 바꾼다."""
    prepared = plan(db, object_type, source_key)
    if not prepared.ok:
        return prepared

    rows = list(
        db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.type_id == object_type.id, ObjectInstance.deleted_at.is_(None)
            )
        )
    )
    ids = [row.id for row in rows]
    types = {row.id: row.slug for row in db.scalars(select(ObjectType))}
    type_of = {row.id: types.get(row.type_id, "") for row in rows}

    # 2) 관계 → 링크. id 를 그대로 물려주어 감사 기록의 relation id 가 계속 맞는다.
    for edge in _edges_of(db, ids):
        src_type = type_of.get(edge.src_object_id) or _type_slug_of(
            db, edge.src_object_id, types
        )
        dst_type = type_of.get(edge.dst_object_id) or _type_slug_of(
            db, edge.dst_object_id, types
        )
        db.add(
            ObjectLink(
                id=edge.id,
                src_type=src_type,
                src_id=edge.src_object_id,
                dst_type=dst_type,
                dst_id=edge.dst_object_id,
                relation=edge.relation,
                properties=edge.properties or {},
                evidence_note=edge.evidence_note,
                created_by_id=edge.created_by_id,
                created_at=edge.created_at,
            )
        )
        db.delete(edge)
    db.flush()

    # 3) 행을 지운다 — 원 표가 같은 id 로 답하므로 참조는 끊기지 않는다.
    now = datetime.now(UTC)
    reason = f"{object_type.slug} 를 {source_key} 표로 승격"
    for row in rows:
        row.deleted_at = now
        audit.record(
            db,
            action="object.promote",
            actor=user,
            target_table="objects",
            target_id=row.id,
            target_label=f"{object_type.slug}:{row.label}",
            workspace_id=row.owner_workspace_id,
            reason=reason,
        )

    # 4) 타입을 투영으로.
    object_type.kind_class = "system"
    object_type.system_source = source_key
    audit.record(
        db,
        action="ontology.type.promote",
        actor=user,
        target_table="object_types",
        target_id=object_type.id,
        target_label=object_type.slug,
        changes={
            "source": source_key,
            "objects": prepared.objects,
            "relations": prepared.relations,
        },
        reason=reason,
    )
    return prepared


def _type_slug_of(db: Session, object_id: uuid.UUID, types: dict[uuid.UUID, str]) -> str:
    row = db.get(ObjectInstance, object_id)
    if row is None:
        raise Conflict(code("ONTOLOGY", 35), f"관계의 끝을 찾을 수 없습니다: {object_id}")
    return types.get(row.type_id, "")
