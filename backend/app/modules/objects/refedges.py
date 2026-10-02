"""참조 칸을 **선으로** — 참조 칸은 칸에 저장한 많대일 관계다.

「개발모델의 과제」 는 온톨로지로는 관계(모델 —속함→ 과제)인데, 상대가 늘 하나이고 근거가
안 붙어서 **객체의 칸**(`properties.task`)에 저장한다. 저장 자리가 다르다고 그래프 · 관련
객체 · 트리에서 빠지면, 사용자는 구현 사정을 개념처럼 배우게 된다 — 「참조는 관계가 아닌가」.
그래서 여기서 참조 칸을 관계 종류와 같은 모양(slug · 라벨 · 역방향 라벨 · 선)으로 내놓고, 그
화면들이 관계와 **함께** 쓴다.

- slug: `ref:<타입 slug>.<칸 키>` — 관계 종류의 slug 와 겹치지 않는다(`:` 는 slug 에 못
  들어간다).
- 선 id: 양 끝과 칸에서 만든 uuid5 — 줄(row)이 없어도 화면이 같은 선을 같은 것으로 안다.
- 방향: 칸을 가진 객체 → 가리키는 객체. 트리로 쓰면 자식이 부모를 가리키는 모양
  (`parent_end='dst'`).
- 원 표를 비추는 타입(부서 · 계정)을 가리키는 칸은 여기서 안 다룬다 — 그 선은 `object_links`
  다.
- 대상이 **인터페이스**인 칸은 그것을 구현한 타입 전부를 가리킨다(ADR 0006) — 정의에는 적힌
  대로(인터페이스 slug), 그림의 선과 「나를 가리키는 것」 은 구현 타입마다.

조건 · 통계의 `ref.<칸>.<칸>` 은 이미 참조 칸을 「이어진 칸」 으로 다루므로 손대지 않는다.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance, ObjectRef
from app.modules.ontology import interfaces
from app.modules.ontology.models import ObjectType, PropertyDef
from app.shared.batches import chunks
from app.shared.permissions import visible_owner_clause

PREFIX = "ref:"
_NAMESPACE = uuid.UUID("5f1f4b7e-2f7c-4b1e-9d3a-7c2e6f0a9b11")


def is_ref(relation: str) -> bool:
    return relation.startswith(PREFIX)


def slug_of(type_slug: str, key: str) -> str:
    return f"{PREFIX}{type_slug}.{key}"


def edge_id(src: uuid.UUID, key: str, dst: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{src}:{key}:{dst}")


@dataclass(frozen=True)
class RefKind:
    """관계 종류와 같은 모양으로 읽히는 참조 칸 — 그래프 · 관련 객체가 라벨을 여기서 읽는다."""

    slug: str
    label: str
    inverse_label: str
    src_type: ObjectType
    target_slug: str
    """칸이 적은 대상 — 타입 또는 인터페이스."""
    dst_types: tuple[ObjectType, ...]
    """가리킬 수 있는 타입 — 대상이 인터페이스면 그 구현 타입 전부(없으면 비어 있다)."""
    key: str
    multi: bool
    directed: bool = True

    @property
    def src_type_slugs(self) -> list[str]:
        return [self.src_type.slug]

    @property
    def dst_type_slugs(self) -> list[str]:
        """적힌 대로 — 관계 종류의 끝처럼 읽는 쪽이 인터페이스를 편다(`interfaces.Ends`)."""
        return [self.target_slug]


def kinds(db: Session) -> dict[str, RefKind]:
    """이 설치의 참조 칸 전부 — 양 끝이 **객체 타입**(원 표 아님)인 것만. 대상이 인터페이스면
    그 구현 타입들이 끝이다."""
    types = {row.slug: row for row in db.scalars(select(ObjectType))}
    by_id = {row.id: row for row in types.values()}
    ends = interfaces.load_ends(db)
    out: dict[str, RefKind] = {}
    for definition in db.scalars(
        select(PropertyDef).where(
            PropertyDef.owner_kind == "type", PropertyDef.data_type == "object_ref"
        )
    ):
        owner = by_id.get(definition.owner_id)
        target = definition.ref_type_slug or ""
        if owner is None or owner.kind_class == "system":
            continue
        if target in ends.interfaces:
            dst_types = tuple(types[one] for one in sorted(ends.expand([target]) or ()))
        elif target in types:
            dst_types = (types[target],)
        else:
            continue
        if any(one.kind_class == "system" for one in dst_types):
            continue
        slug = slug_of(owner.slug, definition.key)
        out[slug] = RefKind(
            slug=slug,
            label=definition.label,
            # 역방향 이름이 없으면 「가리키는 타입 이름」 — 빈 말보다 낫고, 대개 맞다.
            inverse_label=definition.inverse_label or owner.label,
            src_type=owner,
            target_slug=target,
            dst_types=dst_types,
            key=definition.key,
            multi=definition.multi,
        )
    return out


def _targets(raw: Any) -> list[uuid.UUID]:
    items = raw if isinstance(raw, list) else [raw]
    out: list[uuid.UUID] = []
    for item in items:
        if isinstance(item, str):
            try:
                out.append(uuid.UUID(item))
            except ValueError:
                continue
    return out


@dataclass(frozen=True)
class RefEdge:
    id: uuid.UUID
    relation: str
    src: uuid.UUID
    dst: uuid.UUID


def _visible_ids(db: Session, user: User, ids: set[uuid.UUID]) -> set[uuid.UUID]:
    out: set[uuid.UUID] = set()
    for batch in chunks(ids):
        out.update(
            db.scalars(
                select(ObjectInstance.id).where(
                    ObjectInstance.id.in_(batch),
                    ObjectInstance.deleted_at.is_(None),
                    visible_owner_clause(user, ObjectInstance.owner_workspace_id),
                )
            )
        )
    return out


def _outgoing(
    rows: list[ObjectInstance], by_type: dict[uuid.UUID, list[RefKind]]
) -> list[RefEdge]:
    out: list[RefEdge] = []
    for row in rows:
        for kind in by_type.get(row.type_id, []):
            for target in _targets((row.properties or {}).get(kind.key)):
                out.append(
                    RefEdge(edge_id(row.id, kind.key, target), kind.slug, row.id, target)
                )
    return out


def _by_type(
    all_kinds: dict[str, RefKind], wanted: list[str] | None
) -> dict[uuid.UUID, list[RefKind]]:
    grouped: dict[uuid.UUID, list[RefKind]] = {}
    for kind in all_kinds.values():
        if wanted is not None and kind.slug not in wanted:
            continue
        grouped.setdefault(kind.src_type.id, []).append(kind)
    return grouped


def _kind_clause(kinds_: list[RefKind]) -> Any:
    """색인에서 이 칸들만 — (가리키는 타입 · 칸) 짝마다."""
    return or_(
        *(
            and_(ObjectRef.src_type_id == kind.src_type.id, ObjectRef.key == kind.key)
            for kind in kinds_
        )
    )


def _incoming(
    db: Session,
    user: User,
    ids: list[uuid.UUID],
    kinds_by_dst: dict[uuid.UUID, list[RefKind]],
    *,
    per_target: int | None = None,
) -> list[RefEdge]:
    """ids 를 가리키는 **보이는** 객체들 — 참조 색인(`object_refs`)으로 묻는다(ADR 0010).

    예전에는 가리키는 타입을 통째로 읽었다 — 기록 200만 건에서 인기 모델의 상세가 오류로
    멈췄다. `per_target` 이면 대상마다 그만큼만(인기 모델은 10만 건을 끌어안는다 — 다 실을
    화면이 없다).
    """
    # 대상이 인터페이스인 칸은 구현 타입마다 걸려 있다 — 한 번씩만 묻는다(두 번 물으면 선이
    # 두 겹이 된다).
    unique = {kind.slug: kind for kind_list in kinds_by_dst.values() for kind in kind_list}
    if not unique or not ids:
        return []
    slug_of_pair = {(kind.src_type.id, kind.key): kind.slug for kind in unique.values()}
    out: list[RefEdge] = []
    for batch in chunks(ids):
        stmt = (
            select(ObjectRef.src_id, ObjectRef.key, ObjectRef.dst_id, ObjectRef.src_type_id)
            .join(ObjectInstance, ObjectInstance.id == ObjectRef.src_id)
            .where(
                ObjectRef.dst_id.in_(batch),
                _kind_clause(list(unique.values())),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
        if per_target is not None:
            ranked = stmt.add_columns(
                func.row_number()
                .over(partition_by=ObjectRef.dst_id, order_by=ObjectRef.src_id)
                .label("rank")
            ).subquery()
            stmt = select(
                ranked.c.src_id, ranked.c.key, ranked.c.dst_id, ranked.c.src_type_id
            ).where(ranked.c.rank <= per_target)
        for src, key, dst, src_type in db.execute(stmt):
            slug = slug_of_pair.get((src_type, key))
            if slug is not None:
                out.append(RefEdge(edge_id(src, key, dst), slug, src, dst))
    return out


def neighbor_edges(
    db: Session,
    *,
    frontier: list[uuid.UUID],
    user: User,
    fanout: int,
    relations: list[str] | None = None,
    type_ids: list[uuid.UUID] | None = None,
    skip_logs: bool = False,
) -> list[RefEdge]:
    """frontier 의 각 노드에서 참조 칸으로 이어진 이웃 — 가리키는 것과 나를 가리키는 것.
    노드마다 fanout 개까지(관계의 이웃과 같은 상한).

    `skip_logs` 면 **나를 가리키는 기록**(ADR 0011)은 뺀다 — 인기 모델은 기록 10만 건이
    가리킨다. 그것은 줄 대신 `log_counts` 의 수로 선다."""
    if not frontier:
        return []
    all_kinds = kinds(db)
    if relations is not None and not any(is_ref(one) for one in relations):
        return []
    wanted = [one for one in relations if is_ref(one)] if relations is not None else None
    by_src = _by_type(all_kinds, wanted)
    rows = list(
        db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.id.in_(frontier), ObjectInstance.deleted_at.is_(None)
            )
        )
    )
    outgoing = _outgoing(rows, by_src)
    visible_targets = _visible_ids(db, user, {edge.dst for edge in outgoing})
    edges = [edge for edge in outgoing if edge.dst in visible_targets]
    by_dst: dict[uuid.UUID, list[RefKind]] = {}
    for kind in all_kinds.values():
        if skip_logs and kind.src_type.usage == "log":
            continue
        if wanted is None or kind.slug in wanted:
            for one in kind.dst_types:
                by_dst.setdefault(one.id, []).append(kind)
    row_types = {row.id: row.type_id for row in rows}
    relevant = {
        type_id: kind_list
        for type_id, kind_list in by_dst.items()
        if type_id in set(row_types.values())
    }
    edges += _incoming(db, user, frontier, relevant, per_target=fanout)
    if type_ids is not None:
        allowed = set(type_ids)
        types_of = _types_of(db, {e.dst for e in edges} | {e.src for e in edges})
        edges = [
            e for e in edges if types_of.get(e.dst if e.src in row_types else e.src) in allowed
        ]
    # 노드마다 fanout 개까지 — 관계의 이웃과 같은 상한.
    taken: Counter[uuid.UUID] = Counter()
    kept: list[RefEdge] = []
    for edge in edges:
        anchor = edge.src if edge.src in row_types else edge.dst
        if taken[anchor] >= fanout:
            continue
        taken[anchor] += 1
        kept.append(edge)
    return kept


def _types_of(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    if not ids:
        return {}
    return dict(
        db.execute(
            select(ObjectInstance.id, ObjectInstance.type_id).where(ObjectInstance.id.in_(ids))
        ).tuples()
    )


def induced_edges(
    db: Session, *, ids: list[uuid.UUID], limit: int, relations: list[str] | None = None
) -> list[RefEdge]:
    """양 끝이 모두 ids 안인 참조 — 이미 화면에 있는 노드끼리의 선."""
    if not ids:
        return []
    if relations is not None and not any(is_ref(one) for one in relations):
        return []
    wanted = [one for one in relations if is_ref(one)] if relations is not None else None
    by_src = _by_type(kinds(db), wanted)
    rows = list(db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(ids))))
    inside = set(ids)
    return [edge for edge in _outgoing(rows, by_src) if edge.dst in inside][:limit]


def degree_counts(
    db: Session, *, ids: list[uuid.UUID], user: User, skip_logs: bool = False
) -> dict[uuid.UUID, int]:
    """각 노드에 걸린 보이는 참조의 수(가리키는 것 + 나를 가리키는 것). `skip_logs` 면
    나를 가리키는 기록은 안 센다 — 이웃에서 뺀 것을 「+N 더」 로 세면 펼쳐도 안 나온다."""
    if not ids:
        return {}
    all_kinds = kinds(db)
    rows = list(db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(ids))))
    outgoing = _outgoing(rows, _by_type(all_kinds, None))
    visible = _visible_ids(db, user, {edge.dst for edge in outgoing})
    counts: Counter[uuid.UUID] = Counter(e.src for e in outgoing if e.dst in visible)
    by_dst: dict[uuid.UUID, list[RefKind]] = {}
    row_types = {row.type_id for row in rows}
    for kind in all_kinds.values():
        if skip_logs and kind.src_type.usage == "log":
            continue
        for one in kind.dst_types:
            if one.id in row_types:
                by_dst.setdefault(one.id, []).append(kind)
    # 나를 가리키는 것은 **세기만** 한다 — 인기 모델 하나가 10만 줄을 끌고 오지 않게.
    unique = {kind.slug: kind for kind_list in by_dst.values() for kind in kind_list}
    if unique:
        for batch in chunks(ids):
            pointing = db.execute(
                select(ObjectRef.dst_id, func.count())
                .join(ObjectInstance, ObjectInstance.id == ObjectRef.src_id)
                .where(
                    ObjectRef.dst_id.in_(batch),
                    _kind_clause(list(unique.values())),
                    visible_owner_clause(user, ObjectInstance.owner_workspace_id),
                )
                .group_by(ObjectRef.dst_id)
            )
            for dst, count in pointing:
                counts[dst] += int(count)
    return dict(counts)


@dataclass(frozen=True)
class LogCount:
    """이 객체를 가리키는 기록 — 타입 · 칸 하나의 수."""

    kind: RefKind
    count: int


def log_counts(db: Session, *, target: uuid.UUID, user: User) -> list[LogCount]:
    """이 객체를 가리키는 **기록**(ADR 0011)의 수 — 타입 · 칸마다, 보이는 것만.

    축의 상세 · 그래프는 기록을 줄로 싣지 않고 이 수로 보인다(「시장 서비스 100,000건」 →
    걸러진 목록). 색인의 `(dst_id, key)` 로 센다 — 시스템 관리자면 객체 표를 거치지 않는다
    (색인에는 살아 있는 것만 있다)."""
    by_pair = {
        (kind.src_type.id, kind.key): kind
        for kind in kinds(db).values()
        if kind.src_type.usage == "log"
    }
    if not by_pair:
        return []
    stmt = select(ObjectRef.src_type_id, ObjectRef.key, func.count()).where(
        ObjectRef.dst_id == target,
        ObjectRef.src_type_id.in_({type_id for type_id, _ in by_pair}),
    )
    if not user.is_system_admin:
        stmt = stmt.join(ObjectInstance, ObjectInstance.id == ObjectRef.src_id).where(
            visible_owner_clause(user, ObjectInstance.owner_workspace_id)
        )
    out = [
        LogCount(by_pair[(type_id, key)], int(count))
        for type_id, key, count in db.execute(
            stmt.group_by(ObjectRef.src_type_id, ObjectRef.key)
        )
        if (type_id, key) in by_pair
    ]
    out.sort(key=lambda one: (-one.count, one.kind.slug))
    return out


@dataclass(frozen=True)
class TypeRefEdge:
    relation: str
    src_type_id: uuid.UUID
    dst_type_id: uuid.UUID
    count: int


def type_edge_counts(db: Session, *, user: User) -> list[TypeRefEdge]:
    """타입 사이에 참조 칸으로 실제 몇 개가 이어졌나 — 정의 그래프의 선 굵기.

    보이는 규칙을 **양 끝에 따로** 건다 — 관계의 굵기와 같은 원칙(`graph.type_edge_counts`).
    참조 색인을 **한 번** 묶는다(예전에는 칸마다 가리키는 타입을 두 번씩 통째로 읽었다)."""
    all_kinds = kinds(db)
    if not all_kinds:
        return []
    source = aliased(ObjectInstance)
    target = aliased(ObjectInstance)
    rows = db.execute(
        select(ObjectRef.src_type_id, ObjectRef.key, target.type_id, func.count())
        .join(source, source.id == ObjectRef.src_id)
        .join(target, target.id == ObjectRef.dst_id)
        .where(
            target.deleted_at.is_(None),
            visible_owner_clause(user, source.owner_workspace_id),
            visible_owner_clause(user, target.owner_workspace_id),
        )
        .group_by(ObjectRef.src_type_id, ObjectRef.key, target.type_id)
    )
    counted = {(src, key, dst): int(count) for src, key, dst, count in rows}
    # 대상이 인터페이스면 **구현 타입마다** 한 선 — 그림에는 타입만 선다. 0 인 선도 낸다.
    return [
        TypeRefEdge(
            kind.slug,
            kind.src_type.id,
            dst.id,
            counted.get((kind.src_type.id, kind.key, dst.id), 0),
        )
        for kind in all_kinds.values()
        for dst in kind.dst_types
    ]


# --- 트리 — 자식이 부모를 칸으로 가리킨다 ------------------------------------


def _kind(db: Session, relation: str) -> RefKind | None:
    return kinds(db).get(relation)


def child_ids(db: Session, *, relation: str, parent_id: uuid.UUID) -> list[uuid.UUID]:
    kind = _kind(db, relation)
    if kind is None:
        return []
    return list(
        db.scalars(
            select(ObjectRef.src_id).where(
                ObjectRef.dst_id == parent_id,
                ObjectRef.src_type_id == kind.src_type.id,
                ObjectRef.key == kind.key,
            )
        )
    )


def child_counts(
    db: Session, *, relation: str, parent_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    kind = _kind(db, relation)
    if kind is None or not parent_ids:
        return {}
    counts: Counter[uuid.UUID] = Counter()
    for batch in chunks(parent_ids):
        rows = db.execute(
            select(ObjectRef.dst_id, func.count())
            .where(
                ObjectRef.dst_id.in_(batch),
                ObjectRef.src_type_id == kind.src_type.id,
                ObjectRef.key == kind.key,
            )
            .group_by(ObjectRef.dst_id)
        )
        for dst, count in rows:
            counts[dst] += int(count)
    return dict(counts)


def descendant_ids(
    db: Session, *, relation: str, root_id: uuid.UUID, max_depth: int
) -> list[uuid.UUID]:
    """같은 타입끼리 가리키는 칸(상위 기술 분야 등)을 따라 내려간다 — 한 단계씩, 깊이 상한."""
    out: list[uuid.UUID] = []
    seen = {root_id}
    frontier = [root_id]
    for _ in range(max_depth):
        found = [
            child
            for parent in frontier
            for child in child_ids(db, relation=relation, parent_id=parent)
            if child not in seen
        ]
        if not found:
            break
        seen.update(found)
        out.extend(found)
        frontier = found
    return out


def ancestor_ids(
    db: Session, *, relation: str, node_id: uuid.UUID, max_depth: int
) -> list[uuid.UUID]:
    kind = _kind(db, relation)
    if kind is None:
        return []
    out: list[uuid.UUID] = []
    seen = {node_id}
    current = node_id
    for _ in range(max_depth):
        row = db.get(ObjectInstance, current)
        if row is None:
            break
        parents = [p for p in _targets((row.properties or {}).get(kind.key)) if p not in seen]
        if not parents:
            break
        current = parents[0]
        seen.add(current)
        out.append(current)
    return out


def parentless_ids(db: Session, *, relation: str, type_id: uuid.UUID) -> list[uuid.UUID]:
    """그 칸으로 아무것도 가리키지 않는 것 — 트리의 뿌리."""
    kind = _kind(db, relation)
    if kind is None:
        return []
    pointing = select(ObjectRef.src_id).where(
        ObjectRef.src_id == ObjectInstance.id, ObjectRef.key == kind.key
    )
    return list(
        db.scalars(
            select(ObjectInstance.id).where(
                ObjectInstance.type_id == type_id,
                ObjectInstance.deleted_at.is_(None),
                ~pointing.exists(),
            )
        )
    )
