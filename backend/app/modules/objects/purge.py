"""지운 객체의 영구 삭제 — **타입을 지울 때만, 지운 객체만 남았을 때만**(ADR 0008).

객체를 지우면 행이 남는다(`deleted_at`) — 이 저장소는 지우지 않는다. 그런데 그 행이 타입을
RESTRICT 로 붙들어서, 객체를 한 번이라도 넣어 본 타입은 영영 못 지운다. 그래서 **타입
삭제의 한 단계로만** 그 타입의 지운 객체를 정말로 지운다. 객체 하나를 영구 삭제하는 길은
따로 내지 않는다 — 그 길이 있으면 「지우지 않는다」 가 객체마다 무너진다.

- 살아 있는 객체가 있으면 여기까지 오지 않는다(타입 삭제가 막는다).
- 살아 있는 객체가 지운 객체를 가리키면 하지 않는다(`pointing`) — 지금은 「(지워짐)」 으로
  보이는 칸이, 지운 뒤에는 무엇을 가리켰는지조차 모르는 값이 된다.
- 감사 기록은 남는다(누가 언제 무엇을). 객체 상세 · 이력 되짚기 · 병합 전 옛 주소는 사라진다.
- 첨부는 **행만** 지운다(`files` 의 규칙 — 같은 내용을 다른 행이 가리킬 수 있다).
  초기화와 같다.

관계 · 원 표와 이은 선 · 별칭 · 연도 배정은 FK 가 함께 지울 것도 여기서 **명시적으로** 지운다 —
무엇이 얼마나 사라졌는지가 미리 보기와 맞아야 사람이 믿는다. 끊은 선의 무덤은 남기지 않는다:
끝의 객체까지 사라져 받는 쪽이 식별자로 바꿀 수 없고, 그 객체의 삭제 표식이 이미 그것을 말했다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.files.models import Attachment
from app.modules.objects import lifecycle
from app.modules.objects.models import (
    ObjectAlias,
    ObjectInstance,
    ObjectLink,
    ObjectRef,
    ObjectRelation,
    ObjectWatch,
    ObjectYear,
)
from app.modules.ontology.models import ObjectType
from app.shared.errors import AppError, code


@dataclass
class Purge:
    """영구 삭제할 것 — 미리 보기와 적용이 같은 것을 센다.

    지운 객체의 id 를 **목록으로 들고 다니지 않는다** — 수만 건이면 `IN (...)` 이 바인드 한도
    (65,535)에 걸려 질의가 아예 안 나간다. 「이 타입의 지운 객체」 를 부분 질의로 묻는다."""

    type_id: uuid.UUID | None = None
    objects: int = 0
    relations: int = 0
    links: int = 0
    aliases: int = 0
    years: int = 0
    attachments: int = 0
    pointing: list[str] = field(default_factory=list)
    """살아 있는 객체가 지운 객체를 가리키는 자리 — 「타입.칸 N개」. 있으면 하지 않는다."""

    def removes(self) -> list[str]:
        """함께 사라지는 것 — 0 인 것은 안 적는다(읽을 것만 남긴다)."""
        if not self.objects:
            return []
        lines = [f"지운 객체 {self.objects}개 — 영구 삭제(되돌릴 수 없습니다)"]
        for count, what in (
            (self.relations, "그 객체들의 관계"),
            (self.links, "원 표와 이은 선"),
            (self.aliases, "별칭"),
            (self.years, "연도 배정"),
            (self.attachments, "첨부(파일 목록의 행)"),
        ):
            if count:
                lines.append(f"{what} {count}개")
        return lines


def _count(db: Session, model: type, *where: object) -> int:
    stmt = select(func.count()).select_from(model)
    for clause in where:
        stmt = stmt.where(clause)  # type: ignore[arg-type]
    return int(db.scalar(stmt) or 0)


def _doomed(type_id: uuid.UUID) -> Any:
    """이 타입의 지운 객체 — 부분 질의(바인드 변수 없음)."""
    return select(ObjectInstance.id).where(
        ObjectInstance.type_id == type_id, ObjectInstance.deleted_at.is_not(None)
    )


def plan(db: Session, object_type: ObjectType) -> Purge:
    """이 타입의 지운 객체와 거기 매달린 것. **아무것도 안 지운다.**"""
    ids = _doomed(object_type.id)
    objects = _count(
        db,
        ObjectInstance,
        ObjectInstance.type_id == object_type.id,
        ObjectInstance.deleted_at.is_not(None),
    )
    if not objects:
        return Purge()
    return Purge(
        type_id=object_type.id,
        objects=objects,
        relations=_count(
            db,
            ObjectRelation,
            or_(ObjectRelation.src_object_id.in_(ids), ObjectRelation.dst_object_id.in_(ids)),
        ),
        links=_count(
            db, ObjectLink, or_(ObjectLink.src_id.in_(ids), ObjectLink.dst_id.in_(ids))
        ),
        aliases=_count(db, ObjectAlias, ObjectAlias.object_id.in_(ids)),
        years=_count(db, ObjectYear, ObjectYear.object_id.in_(ids)),
        attachments=_count(
            db, Attachment, Attachment.owner_table == "objects", Attachment.owner_id.in_(ids)
        ),
        pointing=_pointing(db, object_type, ids),
    )


def _pointing(db: Session, object_type: ObjectType, ids: Any) -> list[str]:
    """살아 있는 객체가 이 지운 객체들을 가리키는 칸 — **보이지 않는 부서 것까지** 센다.

    반쯤만 세면 「걸린 것 없음」 이라 하고 지운 뒤 남의 칸이 뜻 없는 값이 된다. 참조 색인으로
    칸마다 한 번 묻는다(ADR 0010)."""
    out: list[str] = []
    for owner, definition in lifecycle.ref_defs(db, object_type.slug):
        count = int(
            db.scalar(
                select(func.count(func.distinct(ObjectRef.src_id))).where(
                    ObjectRef.src_type_id == definition.owner_id,
                    ObjectRef.key == definition.key,
                    ObjectRef.dst_id.in_(ids),
                )
            )
            or 0
        )
        if count:
            out.append(f"{owner.slug}.{definition.key} {count}개")
    return out


def apply(db: Session, found: Purge) -> None:
    """지운다. **커밋하지 않는다** — 타입 삭제와 한 트랜잭션이어야 반쯤 지워진 타입이
    안 남는다."""
    if found.type_id is None or not found.objects:
        return
    ids = _doomed(found.type_id)
    try:
        db.execute(
            delete(Attachment).where(
                Attachment.owner_table == "objects", Attachment.owner_id.in_(ids)
            )
        )
        db.execute(delete(ObjectYear).where(ObjectYear.object_id.in_(ids)))
        db.execute(delete(ObjectAlias).where(ObjectAlias.object_id.in_(ids)))
        db.execute(delete(ObjectWatch).where(ObjectWatch.object_id.in_(ids)))
        db.execute(
            delete(ObjectLink).where(
                or_(ObjectLink.src_id.in_(ids), ObjectLink.dst_id.in_(ids))
            )
        )
        db.execute(
            delete(ObjectRelation).where(
                or_(
                    ObjectRelation.src_object_id.in_(ids),
                    ObjectRelation.dst_object_id.in_(ids),
                )
            )
        )
        # merged_into_id 는 SET NULL 이다 — 합쳐진 쪽도 같은 타입이라 함께 지워진다.
        db.execute(
            delete(ObjectInstance).where(
                ObjectInstance.type_id == found.type_id, ObjectInstance.deleted_at.is_not(None)
            )
        )
        db.flush()
    except IntegrityError as caught:
        db.rollback()
        # **어느 표가 막았는지 말한다.** 도메인(확장)이 `objects` 를 RESTRICT 로 가리키는 표를
        # 더했으면 여기서 걸리고, 그때 이 파일에 한 줄을 더하면 된다(초기화와 같은 자리).
        raise AppError(
            code("ONTOLOGY", 87),
            "지운 객체를 가리키는 것이 남아 있어 영구 삭제하지 못했습니다. 새로 더한 표가 "
            f"객체를 가리키고 있는지 확인하세요: {str(caught.orig)[:300]}",
            status=409,
        ) from None
