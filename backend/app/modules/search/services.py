"""전역 찾기 — **타입을 먼저 고르게 하지 않는다.**

목록은 타입마다 따로다(`/objects/{type_slug}`). 그런데 사람이 무엇을 찾을 때 그것이
어느 타입인지 아는 경우는 드물다. 「BT-2041」 을 치는 사람은 그것이 부품인지 시험인지
모르고, 알았다면 이미 그 목록에 가 있었을 것이다.

## 세 가지를 본다

    이름      label
    식별자    key
    별칭      object_aliases — 사람이 붙인 다른 이름과 외부 시스템의 식별자

별칭을 빼면 **찾는 사람이 아는 유일한 이름**으로는 못 찾는 일이 생긴다. 외부에서
들어온 것은 저쪽 코드로 기억되고 있다.

## 왜 걸렸는지 적는다

「Ansys」 를 쳤는데 「ANSYS Inc.」 가 아니라 엉뚱해 보이는 줄이 나오면 사람은 그것을
오류로 읽는다. 별칭으로 걸렸다면 **그 별칭을 함께 보여 준다** — 그러면 같은 결과가
답이 된다.

## 타입마다 몇 건인지 먼저 센다

한 줄로 늘어놓으면 상한에 걸린 순간 나머지가 어디 있는지 알 수 없다. 타입별 수를 함께
주면 「부품에 30건」 을 보고 그 타입으로 좁힐 수 있다.

## 투영 타입도 찾는다

부서와 계정은 `objects` 에 행이 없지만 **사람이 찾는 것은 그 둘이 가장 많다.** 원 표가
자기 `search` 를 갖고 있으므로 여기서는 그것을 부르기만 한다. 원 표가 등록 안 된
타입은 건너뛴다 — 하나 때문에 찾기 전체가 멎으면 안 된다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import system
from app.modules.objects.models import ObjectAlias, ObjectInstance
from app.modules.ontology.models import ObjectType
from app.shared import system_sources
from app.shared.permissions import visible_owner_clause

#: 이 글자 수 미만으로는 안 찾는다. 한 글자로 찾으면 거의 모든 행이 걸려 「찾기」 가
#: 아니라 「전부 보기」 가 되고, 그 질의는 느리기까지 하다.
MIN_QUERY = 2

#: 섞어 볼 때 투영 타입에서 얹는 수. 부서·계정을 아예 안 보여 주면 「부서는 못 찾나」
#: 를 사람이 알 길이 없고, 많이 얹으면 그것들이 첫 쪽을 다 차지한다.
SYSTEM_PEEK = 5


@dataclass
class Hit:
    id: uuid.UUID
    type_slug: str
    type_label: str
    icon: str
    label: str
    key: str | None
    matched: str
    """label · key · alias. **왜 이게 나왔는지**를 화면이 말할 근거."""
    matched_text: str
    """식별자·별칭으로 걸렸으면 그 값. 이름으로 걸렸으면 빈 글자."""


@dataclass
class TypeCount:
    type_slug: str
    type_label: str
    icon: str
    count: int


@dataclass
class Result:
    total: int = 0
    types: list[TypeCount] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)


def _needle(q: str) -> str:
    # `%` 와 `_` 는 LIKE 의 와일드카드다. 사람이 친 그대로 넣으면 「50%」 가 「50 뒤에
    # 아무거나」 로 읽혀 엉뚱한 것이 걸린다.
    escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _where(user: User, q: str) -> tuple[Any, ...]:
    needle = _needle(q)
    return (
        ObjectInstance.deleted_at.is_(None),
        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        or_(
            ObjectInstance.label.ilike(needle, escape="\\"),
            ObjectInstance.key.ilike(needle, escape="\\"),
            ObjectInstance.id.in_(
                select(ObjectAlias.object_id).where(
                    ObjectAlias.value.ilike(needle, escape="\\")
                )
            ),
        ),
    )


def _projection(object_type: ObjectType) -> system_sources.SystemSource | None:
    """등록된 원 표를 비추는 **활성** 타입만. 등록이 빠졌으면 None — 그 타입만 빠지고
    찾기는 선다."""
    if not system.is_system(object_type) or not object_type.is_active:
        return None
    return system.source_or_none(object_type)


def _why(row: ObjectInstance, aliases: list[ObjectAlias], q: str) -> tuple[str, str]:
    """무엇으로 걸렸나. **이름이 우선이다** — 이름에도 들어 있으면 그것이 답이다."""
    lowered = q.strip().lower()
    if lowered in (row.label or "").lower():
        return "label", ""
    if row.key and lowered in row.key.lower():
        return "key", row.key
    for one in aliases:
        if lowered in one.value.lower():
            # 외부 식별자(`source:<slug>`)면 어느 소스의 것인지까지 적는다 — 「저쪽
            # 코드로 찾았다」 가 결과를 설명한다.
            source = one.kind.split(":", 1)[1] if one.kind.startswith("source:") else ""
            return "alias", f"{one.value} ({source})" if source else one.value
    return "label", ""


def _aliases_of(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[ObjectAlias]]:
    """한 쪽에 실린 것들의 별칭을 **한 질의로.** 행마다 물으면 쪽마다 질의가 수십 개 붙는다."""
    out: dict[uuid.UUID, list[ObjectAlias]] = {}
    if not ids:
        return out
    for row in db.scalars(select(ObjectAlias).where(ObjectAlias.object_id.in_(ids))):
        out.setdefault(row.object_id, []).append(row)
    return out


def counts(db: Session, user: User, q: str) -> list[TypeCount]:
    """타입마다 몇 건. **0 건인 타입은 안 넣는다** — 빈 줄이 늘어서면 있는 쪽이 묻힌다."""
    types = {row.id: row for row in db.scalars(select(ObjectType))}
    rows = db.execute(
        select(ObjectInstance.type_id, func.count())
        .where(*_where(user, q))
        .group_by(ObjectInstance.type_id)
    )
    out: list[TypeCount] = []
    for type_id, count in rows:
        object_type = types.get(type_id)
        if object_type is not None:
            out.append(_count_of(object_type, int(count)))
    for object_type in types.values():
        source = _projection(object_type)
        if source is None:
            continue
        _refs, total = source.search(db, user, q.strip(), 1, 0)
        if total:
            out.append(_count_of(object_type, int(total)))
    # 많은 것부터 — 찾는 사람이 먼저 볼 곳이 거기다.
    out.sort(key=lambda one: (-one.count, one.type_label))
    return out


def _count_of(object_type: ObjectType, count: int) -> TypeCount:
    return TypeCount(
        type_slug=object_type.slug,
        type_label=object_type.label,
        icon=object_type.icon,
        count=count,
    )


def _hit_of(object_type: ObjectType, ref: system_sources.SystemRef, q: str) -> Hit:
    return Hit(
        id=ref.id,
        type_slug=object_type.slug,
        type_label=object_type.label,
        icon=object_type.icon,
        label=ref.label,
        key=ref.key,
        matched="label" if q.lower() in ref.label.lower() else "key",
        matched_text=ref.hint,
    )


def search(
    db: Session,
    user: User,
    q: str,
    *,
    type_slug: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Result:
    """한 타입으로 좁혔으면 그 타입만, 아니면 전부 섞어서.

    **너무 짧은 말에는 빈 결과를 준다.** 한 글자로 거의 모든 행을 돌려주면 그것은 찾기가
    아니라 목록이고, 사람은 그 안에서 또 찾아야 한다.
    """
    q = q.strip()
    found = Result()
    if len(q) < MIN_QUERY:
        return found

    found.types = counts(db, user, q)
    if type_slug:
        found.types = [one for one in found.types if one.type_slug == type_slug]
    found.total = sum(one.count for one in found.types)

    types = {row.slug: row for row in db.scalars(select(ObjectType))}
    target = types.get(type_slug) if type_slug else None
    if type_slug and target is None:
        return found

    # 투영 타입으로 좁혔으면 원 표가 자기 쪽 넘김을 한다.
    if target is not None:
        source = _projection(target)
        if source is not None:
            refs, _total = source.search(db, user, q, limit, offset)
            found.hits = [_hit_of(target, ref, q) for ref in refs]
            return found

    where = _where(user, q)
    if target is not None:
        where = (*where, ObjectInstance.type_id == target.id)
    rows = list(
        db.scalars(
            select(ObjectInstance)
            .where(*where)
            .order_by(ObjectInstance.label, ObjectInstance.id)
            .limit(limit)
            .offset(offset)
        )
    )
    by_id = {row.id: row for row in types.values()}
    alias_rows = _aliases_of(db, [row.id for row in rows])
    for row in rows:
        object_type = by_id.get(row.type_id)
        matched, text = _why(row, alias_rows.get(row.id, []), q)
        found.hits.append(
            Hit(
                id=row.id,
                type_slug=object_type.slug if object_type else "",
                type_label=object_type.label if object_type else "알 수 없음",
                icon=object_type.icon if object_type else "",
                label=row.label,
                key=row.key,
                matched=matched,
                matched_text=text,
            )
        )
    # 섞어 볼 때는 투영 타입의 것도 첫 쪽에 몇 개 얹는다.
    if target is None and offset == 0:
        for object_type in types.values():
            source = _projection(object_type)
            if source is None:
                continue
            refs, _total = source.search(db, user, q, SYSTEM_PEEK, 0)
            found.hits.extend(_hit_of(object_type, ref, q) for ref in refs)
    return found
