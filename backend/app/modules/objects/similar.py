"""비슷한 기록 — 축 태그가 많이 겹치는 기록(ADR 0022 B).

기록 하나(또는 태그 묶음)의 참조 칸 태그 — (칸, 가리키는 객체) — 마다 무게를 둔다:

    w(t) = ln(N / df(t))      N = 그 타입의 보이는 기록 수, df = 그 태그를 가진 기록 수

드문 태그(힌지 + 크리프)가 겹칠수록 비슷하고, 거의 모든 기록에 붙은 태그(제품군 하나)는 무게가
0 에 가깝다. 점수는 무게를 단 자카드 — 겹친 무게 / 합친 무게(0 ~ 1). 후보는 태그를 하나라도
함께 가진 기록 중 겹친 무게가 큰 것부터(`CANDIDATES`), 보이는 것만. 왜 비슷한지(겹친 태그)를
함께 낸다.

참조 색인(`object_refs`, ADR 0010)을 읽는다 — 단일값 · 여러 값이 같은 모양이다. 본문의 뜻으로
찾는 것은 하지 않는다(에이전트가 본문을 읽는다). 축 이름은 모른다 — 그 타입의 참조 칸이 곧
축이다.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance, ObjectRef
from app.modules.objects.services import properties_of
from app.modules.ontology.models import ObjectType, PropertyDef
from app.shared.errors import AppError, code
from app.shared.permissions import visible_owner_clause

#: 점수를 매길 후보 수 — 겹친 무게가 큰 것부터.
CANDIDATES = 300
LIMIT_MAX = 50


@dataclass(frozen=True, order=True)
class Tag:
    key: str
    value: str


# --- 순수 함수 -----------------------------------------------------------------------


def weights(df: Mapping[Tag, int], total: int) -> dict[Tag, float]:
    """태그마다 ln(N / df) — 모든 기록에 붙은 태그는 0, 없는 태그(df 0)도 0."""
    return {
        tag: math.log(total / count) if count > 0 and total > 0 else 0.0
        for tag, count in df.items()
    }


def score(query: set[Tag], other: set[Tag], weight: Mapping[Tag, float]) -> float:
    """무게를 단 자카드 — 겹친 무게 / 합친 무게."""
    union = sum(weight.get(one, 0.0) for one in query | other)
    if union <= 0:
        return 0.0
    return sum(weight.get(one, 0.0) for one in query & other) / union


# --- 읽기 --------------------------------------------------------------------------


@dataclass
class Item:
    row: ObjectInstance
    score: float
    shared: list[Tag]
    extra: int
    """후보에만 있는 태그 수."""


@dataclass
class Found:
    total: int
    fields: list[PropertyDef]
    query: list[Tag]
    weight: dict[Tag, float]
    df: dict[Tag, int]
    items: list[Item]
    names: dict[str, str] = field(default_factory=dict)
    """태그 값(객체 id) → 이름. 안 보이는 것은 없다."""


def axis_fields(
    db: Session, object_type: ObjectType, wanted: Iterable[str] | None
) -> list[PropertyDef]:
    """태그로 볼 칸 — 그 타입의 참조 칸(주면 그중에서). 참조 칸이 아니면 거절."""
    refs = [one for one in properties_of(db, object_type.id) if one.data_type == "object_ref"]
    if wanted is None:
        return refs
    by_key = {one.key: one for one in refs}
    out: list[PropertyDef] = []
    for key in wanted:
        found = by_key.get(key)
        if found is None:
            raise AppError(
                code("OBJECTS", 110),
                f"「{object_type.label}」 의 참조 칸이 아닙니다: {key} — 있는 것: "
                f"{', '.join(by_key) or '(없음)'}",
                status=422,
            )
        out.append(found)
    return out


def tags_of(db: Session, object_id: uuid.UUID, fields: list[PropertyDef]) -> set[Tag]:
    keys = [one.key for one in fields]
    rows = db.execute(
        select(ObjectRef.key, ObjectRef.dst_id).where(
            ObjectRef.src_id == object_id, ObjectRef.key.in_(keys)
        )
    )
    return {Tag(row.key, str(row.dst_id)) for row in rows}


def similar(
    db: Session,
    user: User,
    object_type: ObjectType,
    *,
    query: set[Tag],
    fields: list[PropertyDef],
    exclude: uuid.UUID | None = None,
    limit: int = 10,
) -> Found:
    """`query` 태그와 많이 겹치는 기록 — 보이는 것만, 점수 높은 순 `limit` 개."""
    limit = max(1, min(limit, LIMIT_MAX))
    keys = [one.key for one in fields]
    seen = visible_owner_clause(user, ObjectInstance.owner_workspace_id)
    alive = and_(
        ObjectInstance.type_id == object_type.id, ObjectInstance.deleted_at.is_(None), seen
    )
    total = int(db.scalar(select(func.count()).select_from(ObjectInstance).where(alive)) or 0)
    df = _df(db, object_type, alive, query)
    weight = weights(df, total)
    found = Found(total, fields, sorted(query), weight, df, [])
    useful = [one for one in query if weight.get(one, 0.0) > 0]
    if not useful:
        return _named(db, user, found)
    hit = or_(
        *(
            and_(ObjectRef.key == one.key, ObjectRef.dst_id == uuid.UUID(one.value))
            for one in useful
        )
    )
    gain = func.sum(
        case(
            *(
                (
                    and_(ObjectRef.key == one.key, ObjectRef.dst_id == uuid.UUID(one.value)),
                    weight[one],
                )
                for one in useful
            ),
            else_=0.0,
        )
    )
    stmt = (
        select(ObjectRef.src_id, gain.label("gain"))
        .join(ObjectInstance, ObjectInstance.id == ObjectRef.src_id)
        .where(ObjectRef.src_type_id == object_type.id, hit, alive)
        .group_by(ObjectRef.src_id)
        .order_by(gain.desc())
        .limit(CANDIDATES + 1)
    )
    if exclude is not None:
        stmt = stmt.where(ObjectRef.src_id != exclude)
    candidates = [row.src_id for row in db.execute(stmt)][:CANDIDATES]
    if not candidates:
        return _named(db, user, found)
    theirs: dict[uuid.UUID, set[Tag]] = {one: set() for one in candidates}
    for row in db.execute(
        select(ObjectRef.src_id, ObjectRef.key, ObjectRef.dst_id).where(
            ObjectRef.src_id.in_(candidates), ObjectRef.key.in_(keys)
        )
    ):
        theirs[row.src_id].add(Tag(row.key, str(row.dst_id)))
    more = {one for tags in theirs.values() for one in tags} - set(df)
    if more:
        df.update(_df(db, object_type, alive, more))
        weight = weights(df, total)
        found.weight, found.df = weight, df
    ranked = sorted(
        ((score(query, tags, weight), src) for src, tags in theirs.items()),
        key=lambda one: (-one[0], str(one[1])),
    )[:limit]
    rows = {
        row.id: row
        for row in db.scalars(
            select(ObjectInstance).where(ObjectInstance.id.in_([src for _, src in ranked]))
        )
    }
    found.items = [
        Item(
            rows[src],
            value,
            sorted(query & theirs[src], key=lambda one: -weight.get(one, 0.0)),
            len(theirs[src] - query),
        )
        for value, src in ranked
        if value > 0 and src in rows
    ]
    return _named(db, user, found)


def _df(
    db: Session, object_type: ObjectType, alive: Any, tags: Iterable[Tag]
) -> dict[Tag, int]:
    """태그마다 그것을 가진 보이는 기록 수."""
    wanted = list(tags)
    out = {one: 0 for one in wanted}
    for start in range(0, len(wanted), 500):
        part = wanted[start : start + 500]
        hit = or_(
            *(
                and_(ObjectRef.key == one.key, ObjectRef.dst_id == uuid.UUID(one.value))
                for one in part
            )
        )
        rows = db.execute(
            select(
                ObjectRef.key,
                ObjectRef.dst_id,
                func.count(func.distinct(ObjectRef.src_id)).label("n"),
            )
            .join(ObjectInstance, ObjectInstance.id == ObjectRef.src_id)
            .where(ObjectRef.src_type_id == object_type.id, hit, alive)
            .group_by(ObjectRef.key, ObjectRef.dst_id)
        )
        for row in rows:
            out[Tag(row.key, str(row.dst_id))] = int(row.n)
    return out


def _named(db: Session, user: User, found: Found) -> Found:
    """태그 값의 이름 — 보이는 객체만(안 보이면 이름 없이 둔다)."""
    values = {one.value for one in found.query}
    for item in found.items:
        values.update(one.value for one in item.shared)
    ids = [uuid.UUID(one) for one in values]
    if ids:
        for row in db.execute(
            select(ObjectInstance.id, ObjectInstance.label).where(
                ObjectInstance.id.in_(ids),
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        ):
            found.names[str(row.id)] = row.label
    return found
