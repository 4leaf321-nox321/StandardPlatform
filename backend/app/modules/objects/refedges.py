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

조건 · 통계의 `ref.<칸>.<칸>` 은 이미 참조 칸을 「이어진 칸」 으로 다루므로 손대지 않는다.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import String, bindparam, cast, func, select, text, true
from sqlalchemy.orm import Session, aliased

from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import ObjectType, PropertyDef
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
    dst_type: ObjectType
    key: str
    multi: bool
    directed: bool = True

    @property
    def src_type_slugs(self) -> list[str]:
        return [self.src_type.slug]

    @property
    def dst_type_slugs(self) -> list[str]:
        return [self.dst_type.slug]


def kinds(db: Session) -> dict[str, RefKind]:
    """이 설치의 참조 칸 전부 — 양 끝이 **객체 타입**(원 표 아님)인 것만."""
    types = {row.slug: row for row in db.scalars(select(ObjectType))}
    by_id = {row.id: row for row in types.values()}
    out: dict[str, RefKind] = {}
    for definition in db.scalars(
        select(PropertyDef).where(
            PropertyDef.owner_kind == "type", PropertyDef.data_type == "object_ref"
        )
    ):
        owner = by_id.get(definition.owner_id)
        target = types.get(definition.ref_type_slug or "")
        if owner is None or target is None:
            continue
        if owner.kind_class == "system" or target.kind_class == "system":
            continue
        slug = slug_of(owner.slug, definition.key)
        out[slug] = RefKind(
            slug=slug,
            label=definition.label,
            # 역방향 이름이 없으면 「가리키는 타입 이름」 — 빈 말보다 낫고, 대개 맞다.
            inverse_label=definition.inverse_label or owner.label,
            src_type=owner,
            dst_type=target,
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
    if not ids:
        return set()
    return set(
        db.scalars(
            select(ObjectInstance.id).where(
                ObjectInstance.id.in_(ids),
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
    )


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


_INCOMING = text("""
    SELECT o.id AS src, o.type_id, v.value AS dst
      FROM objects o
      CROSS JOIN LATERAL (
          SELECT o.properties ->> :key AS value
           WHERE jsonb_typeof(o.properties -> :key) = 'string'
          UNION ALL
          SELECT jsonb_array_elements_text(o.properties -> :key)
           WHERE jsonb_typeof(o.properties -> :key) = 'array'
      ) v
     WHERE o.type_id = :owner
       AND o.deleted_at IS NULL
       AND v.value IN :ids
""").bindparams(bindparam("ids", expanding=True))


def _incoming(
    db: Session, user: User, ids: list[uuid.UUID], kinds_by_dst: dict[uuid.UUID, list[RefKind]]
) -> list[RefEdge]:
    """ids 를 가리키는 객체들 — 칸의 값이 ids 중 하나인 행."""
    out: list[RefEdge] = []
    wanted = [str(one) for one in ids]
    sources: set[uuid.UUID] = set()
    found: list[tuple[RefKind, uuid.UUID, uuid.UUID]] = []
    for kind_list in kinds_by_dst.values():
        for kind in kind_list:
            rows = db.execute(
                _INCOMING, {"key": kind.key, "owner": str(kind.src_type.id), "ids": wanted}
            )
            for row in rows:
                found.append((kind, row.src, uuid.UUID(row.dst)))
                sources.add(row.src)
    visible = _visible_ids(db, user, sources)
    for kind, src, dst in found:
        if src in visible:
            out.append(RefEdge(edge_id(src, kind.key, dst), kind.slug, src, dst))
    return out


def neighbor_edges(
    db: Session,
    *,
    frontier: list[uuid.UUID],
    user: User,
    fanout: int,
    relations: list[str] | None = None,
    type_ids: list[uuid.UUID] | None = None,
) -> list[RefEdge]:
    """frontier 의 각 노드에서 참조 칸으로 이어진 이웃 — 가리키는 것과 나를 가리키는 것.
    노드마다 fanout 개까지(관계의 이웃과 같은 상한)."""
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
        if wanted is None or kind.slug in wanted:
            by_dst.setdefault(kind.dst_type.id, []).append(kind)
    row_types = {row.id: row.type_id for row in rows}
    relevant = {
        type_id: kind_list
        for type_id, kind_list in by_dst.items()
        if type_id in set(row_types.values())
    }
    edges += _incoming(db, user, frontier, relevant)
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


def degree_counts(db: Session, *, ids: list[uuid.UUID], user: User) -> dict[uuid.UUID, int]:
    """각 노드에 걸린 보이는 참조의 수(가리키는 것 + 나를 가리키는 것)."""
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
        if kind.dst_type.id in row_types:
            by_dst.setdefault(kind.dst_type.id, []).append(kind)
    for edge in _incoming(db, user, ids, by_dst):
        counts[edge.dst] += 1
    return dict(counts)


@dataclass(frozen=True)
class TypeRefEdge:
    relation: str
    src_type_id: uuid.UUID
    dst_type_id: uuid.UUID
    count: int


def type_edge_counts(db: Session, *, user: User) -> list[TypeRefEdge]:
    """타입 사이에 참조 칸으로 실제 몇 개가 이어졌나 — 정의 그래프의 선 굵기.

    보이는 규칙을 **양 끝에 따로** 건다 — 관계의 굵기와 같은 원칙(`graph.type_edge_counts`)."""
    out: list[TypeRefEdge] = []
    source = aliased(ObjectInstance)
    target = aliased(ObjectInstance)
    for kind in kinds(db).values():
        column = source.properties[kind.key]
        values = (
            func.jsonb_array_elements_text(column).table_valued("value").lateral("ref_values")
        )
        where = [
            source.type_id == kind.src_type.id,
            source.deleted_at.is_(None),
            target.deleted_at.is_(None),
            visible_owner_clause(user, source.owner_workspace_id),
            visible_owner_clause(user, target.owner_workspace_id),
        ]
        single = (
            select(func.count())
            .select_from(source)
            .join(target, cast(target.id, String) == column.astext)
            .where(*where, func.jsonb_typeof(column) == "string")
        )
        many = (
            select(func.count())
            .select_from(source)
            .join(values, true())
            .join(target, cast(target.id, String) == values.c.value)
            .where(*where, func.jsonb_typeof(column) == "array")
        )
        count = int(db.scalar(single) or 0) + int(db.scalar(many) or 0)
        out.append(TypeRefEdge(kind.slug, kind.src_type.id, kind.dst_type.id, count))
    return out


# --- 트리 — 자식이 부모를 칸으로 가리킨다 ------------------------------------


_CHILDREN = text("""
    SELECT o.id
      FROM objects o
     WHERE o.type_id = :owner
       AND o.deleted_at IS NULL
       AND (
           o.properties ->> :key = :parent
           OR (jsonb_typeof(o.properties -> :key) = 'array'
               AND jsonb_exists(o.properties -> :key, :parent))
       )
""")


def _kind(db: Session, relation: str) -> RefKind | None:
    return kinds(db).get(relation)


def child_ids(db: Session, *, relation: str, parent_id: uuid.UUID) -> list[uuid.UUID]:
    kind = _kind(db, relation)
    if kind is None:
        return []
    rows = db.execute(
        _CHILDREN, {"owner": str(kind.src_type.id), "key": kind.key, "parent": str(parent_id)}
    )
    return [row.id for row in rows]


def child_counts(
    db: Session, *, relation: str, parent_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    kind = _kind(db, relation)
    if kind is None or not parent_ids:
        return {}
    counts: Counter[uuid.UUID] = Counter()
    rows = db.execute(
        _INCOMING,
        {
            "key": kind.key,
            "owner": str(kind.src_type.id),
            "ids": [str(one) for one in parent_ids],
        },
    )
    for row in rows:
        counts[uuid.UUID(row.dst)] += 1
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
    kind = _kind(db, relation)
    if kind is None:
        return []
    rows = db.execute(
        text("""
            SELECT o.id
              FROM objects o
             WHERE o.type_id = :type_id
               AND o.deleted_at IS NULL
               AND (
                   o.properties -> :key IS NULL
                   OR jsonb_typeof(o.properties -> :key) = 'null'
                   OR (jsonb_typeof(o.properties -> :key) = 'array'
                       AND jsonb_array_length(o.properties -> :key) = 0)
               )
        """),
        {"type_id": str(type_id), "key": kind.key},
    )
    return [row.id for row in rows]
