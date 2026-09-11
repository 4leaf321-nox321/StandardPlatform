"""데이터 품질 — **나빠지고 있으면 어딘가에 떠야 한다.**

검증은 넣을 때만 걸린다. 그 뒤에 필수 속성이 생기고, 가리키던 것이 지워지고, 같은
것이 둘로 만들어진다 — 그리고 그 사실은 아무 데도 안 뜬다. 여기서는 넷을 센다:

    missing_required   필수값이 빈 객체 — 필수가 된 뒤에도 안 채운 옛 것
    orphan             관계가 하나도 없는 객체 — 관계가 정의된 타입에서만.
                       관계가 아예 없는 타입에서는 전부가 고아라 세지 않는다
    broken_ref         지워진(또는 없는) 객체를 가리키는 칸
    duplicate          같은 타입에서 이름을 정규화하면 같은 것들 — 「합치기」 로 이어진다

홈 「남은 일」 에 수가 뜨고, 누르면 목록으로 온다. **볼 수 있는 것만 센다** — 남의
부서 것을 세어 주면 수가 새고, 그 수를 고칠 수도 없다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, union
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import system
from app.modules.objects.models import ObjectInstance, ObjectLink, ObjectRelation
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.shared import extensions
from app.shared.permissions import is_any_manager, visible_owner_clause
from app.shared.text import compare_key

KINDS = ("missing_required", "orphan", "broken_ref", "duplicate")
LABELS = {
    "missing_required": "필수값이 빈 객체",
    "orphan": "관계 없는 객체",
    "broken_ref": "지워진 것을 가리키는 칸",
    "duplicate": "이름이 같은 객체",
}
#: 종류·타입마다 목록에 싣는 상한. 수는 전부 세고, 목록만 자른다.
SAMPLE = 50


@dataclass
class Hit:
    id: uuid.UUID
    label: str
    key: str | None
    detail: str
    """왜 걸렸나 — 「무게 비어 있음」 「공급사 → 지워진 ACME」 「같은 이름: 볼트 (3)」."""


@dataclass
class Finding:
    kind: str
    type_slug: str
    type_label: str
    count: int
    hits: list[Hit] = field(default_factory=list)


def _visible_objects(db: Session, user: User, object_type: ObjectType) -> Any:
    return select(ObjectInstance).where(
        ObjectInstance.type_id == object_type.id,
        ObjectInstance.deleted_at.is_(None),
        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
    )


def _empty(raw: Any) -> bool:
    return raw is None or raw == "" or raw == []


def _missing_required(
    db: Session, user: User, object_type: ObjectType, defs: list[PropertyDef]
) -> Finding | None:
    required = [d for d in defs if d.required and d.data_type != "file"]
    if not required:
        return None
    hits: list[Hit] = []
    count = 0
    for row in db.scalars(_visible_objects(db, user, object_type)):
        values = row.properties or {}
        blank = [d.label for d in required if _empty(values.get(d.key))]
        if not blank:
            continue
        count += 1
        if len(hits) < SAMPLE:
            hits.append(
                Hit(
                    id=row.id,
                    label=row.label,
                    key=row.key,
                    detail=f"비어 있음: {', '.join(blank)}",
                )
            )
    return _finding("missing_required", object_type, count, hits)


def _orphans(
    db: Session, user: User, object_type: ObjectType, kinds: list[RelationType]
) -> Finding | None:
    # 이 타입이 끝점이 될 수 있는 관계 종류가 하나라도 있어야 「고아」 가 뜻을 갖는다.
    applies = any(
        one.is_active
        and (
            (one.src_type_slugs is None or object_type.slug in one.src_type_slugs)
            or (one.dst_type_slugs is None or object_type.slug in one.dst_type_slugs)
        )
        for one in kinds
    )
    if not applies:
        return None
    # 원 표(부서·계정)와 이은 선도 관계다 — 안 세면 「담당 부서」 만 걸린 객체가 고아로 뜬다.
    linked = union(
        select(ObjectRelation.src_object_id),
        select(ObjectRelation.dst_object_id),
        select(ObjectLink.src_id),
        select(ObjectLink.dst_id),
    )
    stmt = _visible_objects(db, user, object_type).where(ObjectInstance.id.not_in(linked))
    count = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if not count:
        return None
    rows = db.scalars(stmt.order_by(ObjectInstance.label).limit(SAMPLE))
    hits = [Hit(id=r.id, label=r.label, key=r.key, detail="걸린 관계 없음") for r in rows]
    return _finding("orphan", object_type, int(count), hits)


def _broken_refs(
    db: Session, user: User, object_type: ObjectType, defs: list[PropertyDef]
) -> Finding | None:
    ref_defs = [d for d in defs if d.data_type == "object_ref"]
    if not ref_defs:
        return None
    alive = {
        str(one)
        for one in db.scalars(
            select(ObjectInstance.id).where(ObjectInstance.deleted_at.is_(None))
        )
    }
    names: dict[uuid.UUID, str] = {
        row_id: label
        for row_id, label in db.execute(select(ObjectInstance.id, ObjectInstance.label))
    }
    # 상대가 원 표(system)인 칸은 그 표에서 산 것을 본다 — 객체 표에는 없는 id 라서.
    types = system.types_by_slug(db)
    rows = list(db.scalars(_visible_objects(db, user, object_type)))
    system_alive: dict[str, set[str]] = {}
    for d in ref_defs:
        target = types.get(d.ref_type_slug or "")
        if target is None or not system.is_system(target):
            continue
        wanted: set[uuid.UUID] = set()
        for row in rows:
            raw = (row.properties or {}).get(d.key)
            for item in raw if isinstance(raw, list) else [raw]:
                if isinstance(item, str) and item:
                    try:
                        wanted.add(uuid.UUID(item))
                    except ValueError:
                        continue
        found = system.source_of(target).lookup(db, sorted(wanted)) if wanted else {}
        system_alive[d.key] = {str(one) for one in found}
    hits: list[Hit] = []
    count = 0
    for row in rows:
        values = row.properties or {}
        broken: list[str] = []
        for d in ref_defs:
            raw = values.get(d.key)
            living = system_alive.get(d.key, alive)
            for item in raw if isinstance(raw, list) else [raw]:
                if isinstance(item, str) and item and item not in living:
                    try:
                        name = names.get(uuid.UUID(item))
                    except ValueError:
                        name = None
                    broken.append(f"{d.label} → {name + ' (지워짐)' if name else '없는 객체'}")
        if not broken:
            continue
        count += 1
        if len(hits) < SAMPLE:
            hits.append(Hit(id=row.id, label=row.label, key=row.key, detail="; ".join(broken)))
    return _finding("broken_ref", object_type, count, hits)


def _duplicates(db: Session, user: User, object_type: ObjectType) -> Finding | None:
    groups: dict[str, list[ObjectInstance]] = {}
    for row in db.scalars(_visible_objects(db, user, object_type)):
        groups.setdefault(compare_key(row.label), []).append(row)
    dupes = [members for members in groups.values() if len(members) > 1]
    if not dupes:
        return None
    hits: list[Hit] = []
    count = 0
    for members in sorted(dupes, key=lambda ones: -len(ones)):
        for row in members:
            count += 1
            if len(hits) < SAMPLE:
                hits.append(
                    Hit(
                        id=row.id,
                        label=row.label,
                        key=row.key,
                        detail=f"같은 이름 {len(members)}개",
                    )
                )
    return _finding("duplicate", object_type, count, hits)


def _finding(
    kind: str, object_type: ObjectType, count: int, hits: list[Hit]
) -> Finding | None:
    if not count:
        return None
    return Finding(
        kind=kind,
        type_slug=object_type.slug,
        type_label=object_type.label,
        count=count,
        hits=hits,
    )


def report(db: Session, user: User, *, kinds: tuple[str, ...] = KINDS) -> list[Finding]:
    """전부 — 타입마다, 종류마다. 화면과 홈 「남은 일」 이 같은 것을 읽는다."""
    types = list(
        db.scalars(
            select(ObjectType)
            .where(ObjectType.is_active.is_(True))
            .order_by(ObjectType.sort_order)
        )
    )
    relation_kinds = list(db.scalars(select(RelationType)))
    defs_by_type: dict[uuid.UUID, list[PropertyDef]] = {}
    for d in db.scalars(select(PropertyDef).where(PropertyDef.owner_kind == "type")):
        defs_by_type.setdefault(d.owner_id, []).append(d)

    out: list[Finding] = []
    for object_type in types:
        if object_type.kind_class == "system":
            continue
        defs = defs_by_type.get(object_type.id, [])
        found = [
            _missing_required(db, user, object_type, defs)
            if "missing_required" in kinds
            else None,
            _orphans(db, user, object_type, relation_kinds) if "orphan" in kinds else None,
            _broken_refs(db, user, object_type, defs) if "broken_ref" in kinds else None,
            _duplicates(db, user, object_type) if "duplicate" in kinds else None,
        ]
        out.extend(one for one in found if one is not None)
    return out


# --- 홈과 서버 화면에 등록하는 것 ------------------------------------------------


def maintenance(db: Session, viewer: User) -> list[extensions.MaintenanceItem]:
    """홈 「남은 일」 — 종류마다 한 줄. **고칠 수 있는 사람(부서 관리자)에게만.**
    처리할 수 없는 사람에게 띄우면 그 자리는 못 지우는 숫자가 된다."""
    if not is_any_manager(db, viewer):
        return []
    totals: dict[str, int] = {}
    for one in report(db, viewer):
        totals[one.kind] = totals.get(one.kind, 0) + one.count
    return [
        extensions.MaintenanceItem(
            key=f"quality_{kind}",
            label=LABELS[kind],
            count=totals[kind],
            link=f"/quality#{kind}",
            # 깨진 참조만 경고 — 화면에 뜻 모를 값이 뜨는 것이라. 나머지는 알림.
            severity="warning" if kind == "broken_ref" else "info",
        )
        for kind in KINDS
        if totals.get(kind)
    ]


def stats(db: Session) -> list[extensions.StatItem]:
    def count(stmt: Any) -> int:
        return int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)

    return [
        extensions.StatItem(label="타입", count=count(select(ObjectType))),
        extensions.StatItem(
            label="객체",
            count=count(select(ObjectInstance).where(ObjectInstance.deleted_at.is_(None))),
        ),
        extensions.StatItem(label="관계", count=count(select(ObjectRelation))),
    ]
