"""`object_links` 를 맺고 끊는다 — **한쪽 끝이 system 객체인 선.**

양끝이 객체인 선(`object_relations`)과 규칙은 같다: 같은 선은 한 번, 관계 종류의
허용 타입·개수 제약을 지키고, 맺고 끊는 것이 감사 기록에 남는다. 다른 것은 저장
자리뿐이다 — system 객체는 `objects` 에 행이 없어 FK 로 묶을 수 없기 때문이다.

감사 기록의 `changes` 모양을 `audit.relation_endpoints` 와 같게 둔다. 객체의 이력이
「이 선이 나에게 걸린 것」 을 `changes.src / dst` 로 찾기 때문이다 — 모양이 다르면
링크는 이력에서 빠지고, 그 빠짐은 아무 데도 안 뜬다.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import relations as rel
from app.modules.objects.models import ObjectLink
from app.modules.objects.system import End
from app.modules.ontology.models import RelationType
from app.shared import audit
from app.shared.errors import Conflict, code

TABLE = "object_links"


def endpoints(link: ObjectLink, src_label: str, dst_label: str) -> dict[str, Any]:
    return {
        "relation": link.relation,
        "src": str(link.src_id),
        "dst": str(link.dst_id),
        "src_label": src_label,
        "dst_label": dst_label,
    }


def existing(
    db: Session, src_id: uuid.UUID, dst_id: uuid.UUID, relation: str
) -> ObjectLink | None:
    return db.scalar(
        select(ObjectLink).where(
            ObjectLink.src_id == src_id,
            ObjectLink.dst_id == dst_id,
            ObjectLink.relation == relation,
        )
    )


def add(
    db: Session,
    user: User,
    kind: RelationType,
    src: End,
    dst: End,
    *,
    properties: dict[str, Any] | None = None,
    evidence_note: str = "",
    reason: str | None = None,
) -> ObjectLink:
    """선 하나. **부르는 쪽이 커밋한다.** 허용 타입·중복·개수 제약을 여기서 본다 —
    객체끼리의 선과 같은 규칙이 두 자리에서 다르게 서면 어느 쪽이 맞는지 알 수 없다."""
    if not (src.is_system or dst.is_system):
        raise Conflict(code("OBJECTS", 71), "양끝이 모두 객체인 선은 관계로 맺습니다.")
    rel.require_end_types_allowed(db, kind, src.type_slug, dst.type_slug)
    if existing(db, src.id, dst.id, kind.slug) is not None:
        raise Conflict(code("OBJECTS", 29), "이미 이어져 있습니다.")
    rel.require_cardinality(db, kind, src.id, dst.id)

    link = ObjectLink(
        src_type=src.type_slug,
        src_id=src.id,
        dst_type=dst.type_slug,
        dst_id=dst.id,
        relation=kind.slug,
        properties=properties or {},
        evidence_note=evidence_note,
        created_by_id=user.id,
    )
    db.add(link)
    db.flush()
    audit.record(
        db,
        action="object.relation.add",
        actor=user,
        target_table=TABLE,
        target_id=link.id,
        target_label=f"{src.label} -{kind.slug}-> {dst.label}",
        workspace_id=src.owner_workspace_id or dst.owner_workspace_id,
        changes=endpoints(link, src.label, dst.label),
        reason=reason,
    )
    return link


def remove(
    db: Session,
    user: User,
    link: ObjectLink,
    *,
    src_label: str,
    dst_label: str,
    workspace_id: uuid.UUID | None,
    reason: str | None = None,
) -> None:
    """**진짜로 지운다** — 관계와 같다. 대신 감사 로그에 남는다."""
    audit.record(
        db,
        action="object.relation.remove",
        actor=user,
        target_table=TABLE,
        target_id=link.id,
        target_label=f"{src_label} -{link.relation}-> {dst_label}",
        workspace_id=workspace_id,
        changes=endpoints(link, src_label, dst_label),
        reason=reason,
    )
    db.delete(link)
