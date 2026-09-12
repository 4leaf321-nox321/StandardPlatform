"""별칭 — **같은 것을 다르게 불러도 같은 것으로 풀린다.**

사람이 붙인 다른 이름(`alias`)과 바깥 시스템의 식별자(`source:<slug>`)를 한 표에 둔다.
찾기·참조 풀이·파일·동기화가 전부 여기를 본다 — 한 곳만 보면 「파일로는 풀리는데 화면
찾기에는 안 걸리는」 상태가 되고, 그때 어느 쪽이 맞는지 알 방법이 없다.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectAlias, ObjectInstance
from app.modules.ontology.models import ObjectType
from app.modules.ontology.services import InvalidValue
from app.shared.errors import code
from app.shared.text import compare_key

HUMAN = "alias"


def source_kind(slug: str) -> str:
    return f"source:{slug}"


def clean(values: list[str]) -> list[str]:
    """빈 것·겹치는 것(비교키 기준)을 뺀, 사람이 적은 차례 그대로."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = (raw or "").strip()
        if not text:
            continue
        norm = compare_key(text)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(text[:200])
    return out


def of(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[ObjectAlias]]:
    """객체별 별칭 전부 — 목록 한 쪽을 한 질의로."""
    out: dict[uuid.UUID, list[ObjectAlias]] = defaultdict(list)
    if not ids:
        return out
    for row in db.scalars(
        select(ObjectAlias)
        .where(ObjectAlias.object_id.in_(ids))
        .order_by(ObjectAlias.created_at)
    ):
        out[row.object_id].append(row)
    return out


def human_of(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    return {
        object_id: [one.value for one in rows if one.kind == HUMAN]
        for object_id, rows in of(db, ids).items()
    }


def taken_by(
    db: Session, object_type: ObjectType, kind: str, values: list[str]
) -> dict[str, uuid.UUID]:
    """이 값들을 **이미 쓰는 다른 객체** — {비교키: 객체 id}."""
    norms = [compare_key(one) for one in values]
    if not norms:
        return {}
    rows = db.execute(
        select(ObjectAlias.norm, ObjectAlias.object_id).where(
            ObjectAlias.type_id == object_type.id,
            ObjectAlias.kind == kind,
            ObjectAlias.norm.in_(norms),
        )
    )
    return {norm: object_id for norm, object_id in rows}


def require_free(
    db: Session,
    object_type: ObjectType,
    values: list[str],
    *,
    exclude_id: uuid.UUID | None,
    kind: str = HUMAN,
) -> None:
    """다른 객체가 쓰는 별칭이면 거절한다 — **어느 객체인지 말하며.**"""
    taken = taken_by(db, object_type, kind, values)
    for value in values:
        holder = taken.get(compare_key(value))
        if holder is not None and holder != exclude_id:
            other = db.get(ObjectInstance, holder)
            raise InvalidValue(
                code("OBJECTS", 80),
                f"「{value}」 은 이미 {other.label if other else '다른 객체'}의 별칭입니다. "
                "같은 별칭이 둘이면 어느 쪽인지 아무도 모릅니다.",
            )


def set_human(
    db: Session, row: ObjectInstance, object_type: ObjectType, values: list[str]
) -> tuple[list[str], list[str]]:
    """사람이 붙인 별칭을 통째로 바꾼다. **부르는 쪽이 커밋하고 감사 기록을 남긴다.**
    (전, 후) 를 돌려준다."""
    wanted = clean(values)
    require_free(db, object_type, wanted, exclude_id=row.id)
    current = [
        one
        for one in db.scalars(
            select(ObjectAlias).where(
                ObjectAlias.object_id == row.id, ObjectAlias.kind == HUMAN
            )
        )
    ]
    before = [one.value for one in current]
    keep = {compare_key(one) for one in wanted}
    for one in current:
        if one.norm not in keep:
            db.delete(one)
    have = {one.norm for one in current}
    for value in wanted:
        norm = compare_key(value)
        if norm not in have:
            db.add(
                ObjectAlias(
                    object_id=row.id,
                    type_id=object_type.id,
                    kind=HUMAN,
                    value=value,
                    norm=norm,
                )
            )
    db.flush()
    return before, wanted


def set_external(
    db: Session, row: ObjectInstance, object_type: ObjectType, slug: str, value: str
) -> None:
    """데이터 소스의 외부 식별자 — 소스마다 하나. 있으면 값을 갈아 끼운다."""
    kind = source_kind(slug)
    found = db.scalar(
        select(ObjectAlias).where(ObjectAlias.object_id == row.id, ObjectAlias.kind == kind)
    )
    norm = compare_key(value)
    if found is not None:
        found.value = value[:200]
        found.norm = norm
        return
    db.add(
        ObjectAlias(
            object_id=row.id, type_id=object_type.id, kind=kind, value=value[:200], norm=norm
        )
    )


def lookup(
    db: Session, object_type: ObjectType, text: str, *, kind: str | None = None
) -> list[uuid.UUID]:
    """이 글자를 별칭(또는 외부 식별자)으로 가진 객체들 — 지워진 것은 뺀다."""
    stmt = (
        select(ObjectAlias.object_id)
        .join(ObjectInstance, ObjectInstance.id == ObjectAlias.object_id)
        .where(
            ObjectAlias.type_id == object_type.id,
            ObjectAlias.norm == compare_key(text),
            ObjectInstance.deleted_at.is_(None),
        )
    )
    if kind is not None:
        stmt = stmt.where(ObjectAlias.kind == kind)
    return list(dict.fromkeys(db.scalars(stmt)))


def index_of(db: Session, object_type: ObjectType) -> dict[str, list[uuid.UUID]]:
    """타입 하나의 별칭 전부 — {비교키: [객체 id]}. 파일을 풀 때 한 번 읽는다."""
    out: dict[str, list[uuid.UUID]] = defaultdict(list)
    rows = db.execute(
        select(ObjectAlias.norm, ObjectAlias.object_id)
        .join(ObjectInstance, ObjectInstance.id == ObjectAlias.object_id)
        .where(ObjectAlias.type_id == object_type.id, ObjectInstance.deleted_at.is_(None))
    )
    for norm, object_id in rows:
        if object_id not in out[norm]:
            out[norm].append(object_id)
    return out


def move(db: Session, loser: ObjectInstance, winner: ObjectInstance) -> tuple[int, int]:
    """합치기 — 지는 쪽의 별칭·외부 식별자와 **이름**을 이긴 쪽으로. 겹치면 버린다.
    (옮긴 수, 버린 수)."""
    winner_rows = list(
        db.scalars(select(ObjectAlias).where(ObjectAlias.object_id == winner.id))
    )
    have = {(one.kind, one.norm) for one in winner_rows}
    have.add((HUMAN, compare_key(winner.label)))
    moved = dropped = 0
    for one in db.scalars(select(ObjectAlias).where(ObjectAlias.object_id == loser.id)):
        if (one.kind, one.norm) in have:
            db.delete(one)
            dropped += 1
            continue
        one.object_id = winner.id
        have.add((one.kind, one.norm))
        moved += 1
    # 지는 쪽 이름도 별칭으로 — 같은 표기로 다시 와도 같은 것으로 풀리게.
    loser_norm = compare_key(loser.label)
    if (HUMAN, loser_norm) not in have and loser_norm:
        db.add(
            ObjectAlias(
                object_id=winner.id,
                type_id=winner.type_id,
                kind=HUMAN,
                value=loser.label[:200],
                norm=loser_norm,
            )
        )
        moved += 1
    db.flush()
    return moved, dropped


def drop_all(db: Session, object_id: uuid.UUID) -> None:
    """지운 객체의 별칭을 비운다 — 남기면 그 이름을 다른 객체가 못 쓴다."""
    db.execute(delete(ObjectAlias).where(ObjectAlias.object_id == object_id))
