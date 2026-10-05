"""축 사이의 길 — 커버리지 · 재발이 온톨로지를 따라 축에서 축으로 걷는다(ADR 0022).

지표의 기준이 축(다른 타입을 가리키는 참조 · 관계)이면 그 축 타입을 안다. 한 축 타입에서 다음
축 타입으로 가는 길은 기준 주소와 같은 문법(`ref.<참조 칸>` · `out.<관계>` · `in.<관계>` ·
`in.<타입>:<칸>`, 셋까지)이고, 비우면 **두 타입 사이에 한 걸음 길이 하나뿐일 때** 그것을 쓴다 —
여럿이면 후보를 말하고 거절한다(어느 쪽인지 사람이 고른다).

축 이름(제품 · 부품 · 메커니즘 · 해석법)은 여기 없다 — 정의에서 읽는다. 그래서 어느
쌍둥이에서도 돈다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import spec as spec_module
from app.modules.metrics.recipes import common
from app.modules.objects import axes, paths
from app.modules.objects import scope as scope_module
from app.modules.objects.models import ObjectInstance
from app.modules.objects.scope import Scope
from app.shared.errors import AppError
from app.shared.permissions import visible_owner_clause

#: 한 번에 따라갈 객체 수 — IN 목록을 이만큼씩 끊는다.
CHUNK = 1000


@dataclass
class Way:
    address: str
    label: str


def axis_type(built: spec_module.Built, name: str) -> str:
    """기준이 가리키는 축 타입의 slug — 참조 · 관계(한 타입)인 기준만 축이다."""
    dim = common.dim_of(built, name)
    kind, target = dim.signature
    if kind != "ref" or not target:
        raise common.refuse(
            39,
            f"「{dim.axis.label}」 은 축(다른 타입을 가리키는 참조 · 관계)이 아닙니다 — 축인 "
            "기준만 길을 따라 걸을 수 있습니다.",
        )
    return target


def scope_of(db: Session, slug: str) -> Scope:
    found = scope_module.find(db, slug)
    if found is None:
        raise common.refuse(39, f"없는 타입입니다: {slug}")
    return found


def ways(db: Session, source: str, target: str) -> list[Way]:
    """`source` 타입에서 한 걸음에 `target` 타입에 닿는 길 — 참조 칸 · 관계 양방향 · 들어오는
    참조."""
    there = scope_of(db, target)
    reach = set(there.match_slugs) | {there.slug}
    resolver = paths.Resolver(db, scope_of(db, source))
    return [
        Way(f"{hop.kind}.{hop.name}", hop.heading)
        for hop in resolver.hops()
        if reach & set(hop.target_slugs)
    ]


def pick(db: Session, source: str, target: str, given: str | None, *, what: str) -> Way:
    """길 하나 — 주었으면 그것(닿는 타입을 확인), 아니면 하나뿐인 후보. 여럿 · 없음이면
    거절."""
    there = scope_of(db, target)
    if given:
        try:
            found = axes.JoinPlan(db, scope_of(db, source), prefix="wp").reach(given)
        except AppError as caught:
            raise common.refuse(39, f"{what}: {caught.message}") from caught
        last = found.hops[-1]
        if not (set(there.match_slugs) | {there.slug}) & set(last.target_slugs):
            raise common.refuse(
                39,
                f"{what}: 「{given}」 는 「{there.label}」 에 닿지 않습니다 — "
                f"{', '.join(last.target_slugs) or '(정해지지 않음)'} 에 닿습니다.",
            )
        return Way(given, " → ".join(hop.heading for hop in found.hops))
    found_ways = ways(db, source, target)
    if len(found_ways) == 1:
        return found_ways[0]
    if not found_ways:
        raise common.refuse(
            39,
            f"{what}: 「{scope_of(db, source).label}」 에서 「{there.label}」 로 가는 한 걸음 "
            "길(참조 칸 · 관계)이 온톨로지에 없습니다 — 관계를 정의하거나 여러 걸음 길을 "
            "적습니다(예: ref.a.out.b).",
        )
    raise common.refuse(
        39,
        f"{what}: 「{scope_of(db, source).label}」 에서 「{there.label}」 로 가는 길이 "
        f"여럿입니다 — 하나를 고릅니다: "
        + ", ".join(f"{one.address}({one.label})" for one in found_ways),
    )


def follow(
    db: Session, user: User, source: str, address: str, ids: Iterable[str]
) -> dict[str, set[str]]:
    """객체마다 길 끝의 객체들 — 보이는 것만(양 끝). id 가 아닌 값(빈 값 · 글자)은 건너뛴다."""
    plan = axes.JoinPlan(db, scope_of(db, source), prefix="fw", viewer=user)
    found = plan.reach(address)
    keys = [one for one in ids if _is_uuid(one)]
    out: dict[str, set[str]] = {}
    for start in range(0, len(keys), CHUNK):
        part = [uuid.UUID(one) for one in keys[start : start + CHUNK]]
        stmt = select(
            cast(ObjectInstance.id, String).label("src"), found.expr.label("dst")
        ).where(
            ObjectInstance.id.in_(part),
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            or_(
                found.entity.id.is_(None),
                visible_owner_clause(user, found.entity.owner_workspace_id),
            ),
        )
        seen: set[int] = set()
        for target, onclause in found.joins:
            if id(target) in seen:
                continue
            seen.add(id(target))
            stmt = stmt.outerjoin_from(ObjectInstance, target, onclause)
        for row in db.execute(stmt):
            if row.dst is not None:
                out.setdefault(row.src, set()).add(str(row.dst))
    return out


def _is_uuid(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        uuid.UUID(raw)
    except ValueError:
        return False
    return True
