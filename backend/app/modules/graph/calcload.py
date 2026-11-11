"""밤 그래프 계산이 읽는 그래프 — 기준정보 객체와 그 사이의 선을 **번호 배열**로
(ADR 0067).

- 노드: 기준정보(기록이 아닌) 타입의 살아 있는 객체(지운 것 · 병합돼 사라진 것은
  뺀다). 번호는 id 차례다.
- **방향 없는 짝**(군집 · 다리 역할): 기준정보끼리의 참조 칸 + 오늘 유효한 관계 —
  지도와 같은 그래프(ADR 0046). 겹침 · 제자리 선은 뺀다. `whole` 을 주면 그 관계 · 참조 칸
  (`<타입>.<칸>`)만(`GRAPH_CALC_WHOLE_RELATIONS`).
- **이어지는 관계**(기대는 수 · 비슷한 것): 이어짐(`transitive`)을 켠 관계 종류마다
  (부모, 자식) 짝.

id → 번호는 **DB 안에서** 바꾼다(임시 표) — 선 수백만 줄을 uuid 로 받아 파이썬
사전으로 바꾸면 그것만 수십 초다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.graph import maphubs
from app.modules.graph.calcengine import Ints
from app.modules.objects import edges as edge_rule
from app.modules.objects.scope import as_scope
from app.modules.ontology.models import ObjectType, RelationType
from app.shared.errors import AppError, code

_EMPTY = np.zeros(0, np.int64)


@dataclass
class Loaded:
    ids: list[uuid.UUID]
    """번호 → 객체 id."""
    type_index: Ints
    """번호 → `types` 안의 자리."""
    types: list[ObjectType]
    a: Ints = field(default_factory=lambda: _EMPTY)
    b: Ints = field(default_factory=lambda: _EMPTY)
    """방향 없는 짝(a < b, 겹침 없음)."""
    transitive: dict[str, tuple[Ints, Ints]] = field(default_factory=dict)
    """이어지는 관계 slug → (부모 번호, 자식 번호)."""
    whole: list[str] = field(default_factory=list)
    """방향 없는 짝을 지은 선의 이름 — 비면 전부."""

    @property
    def n(self) -> int:
        return len(self.ids)


def _ints(values: list[int] | None) -> Ints:
    return np.asarray(values or [], dtype=np.int64)


class UnknownWhole(AppError):
    """무리 · 다리 역할을 지을 선의 이름을 모른다(설정이 틀렸다)."""


def _whole_names(
    db: Session, types: list[ObjectType], wanted: list[str]
) -> tuple[list[str], list[str]]:
    """설정의 이름들 → (관계 slug, `<타입>.<칸>` 참조 칸). 모르는 이름이면 아는 것을 말하고
    실패한다 — 조용히 빼면 「무리가 왜 이렇지」 를 아무도 못 푼다."""
    relations = set(db.scalars(select(RelationType.slug)))
    # 타입의 칸 — 인터페이스에서 받은 공통 속성까지(`Scope.defs`).
    refs = {
        f"{row.slug}.{one.key}"
        for row in types
        for one in as_scope(db, row).defs
        if one.data_type == "object_ref"
    }
    unknown = [one for one in wanted if one not in relations and one not in refs]
    if unknown:
        known = ", ".join(sorted(relations | refs)) or "(없음)"
        raise UnknownWhole(
            code("GRAPH", 44),
            f"GRAPH_CALC_WHOLE_RELATIONS 에 모르는 이름이 있습니다: {', '.join(unknown)} — "
            f"관계 slug 또는 참조 칸 `<타입>.<칸>`: {known}",
            status=422,
        )
    return [one for one in wanted if one in relations], [one for one in wanted if one in refs]


def load(db: Session, *, whole: list[str] | None = None) -> Loaded:
    """지금 그래프 — 트랜잭션 안에서 부른다(임시 표가 커밋 때 사라진다). `whole` 은 방향 없는
    짝(무리 · 다리 역할)을 지을 관계 slug · 참조 칸 `<타입>.<칸>` — 비우면 전부."""
    master_ids = maphubs.master_type_ids(db)
    types = list(db.scalars(select(ObjectType).where(ObjectType.id.in_(master_ids))))
    types.sort(key=lambda row: row.slug)
    wanted = [one.strip() for one in whole or [] if one.strip()]
    pick_relations, pick_refs = _whole_names(db, types, wanted) if wanted else ([], [])
    seat = {row.id: at for at, row in enumerate(types)}
    db.execute(text("DROP TABLE IF EXISTS graph_calc_nodes"))
    db.execute(
        text("""
            CREATE TEMPORARY TABLE graph_calc_nodes ON COMMIT DROP AS
            SELECT id, type_id, (row_number() OVER (ORDER BY id) - 1)::int AS i
            FROM objects
            WHERE type_id = ANY(CAST(:types AS uuid[]))
              AND deleted_at IS NULL AND merged_into_id IS NULL
        """),
        {"types": master_ids},
    )
    db.execute(text("ALTER TABLE graph_calc_nodes ADD PRIMARY KEY (id)"))
    db.execute(text("ANALYZE graph_calc_nodes"))
    rows = db.execute(text("SELECT id, type_id FROM graph_calc_nodes ORDER BY i")).all()
    ids = [row[0] for row in rows]
    type_index = np.asarray([seat[row[1]] for row in rows], dtype=np.int64)
    out = Loaded(ids, type_index, types, whole=wanted)
    if not ids:
        return out

    alive = edge_rule.alive_sql("r", edge_rule.at(None))
    refs = db.execute(
        text("""
            SELECT array_agg(s.i), array_agg(d.i)
            FROM object_refs f
            JOIN graph_calc_nodes s ON s.id = f.src_id
            JOIN graph_calc_nodes d ON d.id = f.dst_id
            JOIN object_types t ON t.id = f.src_type_id
            WHERE s.i <> d.i
              AND (CAST(:every AS boolean)
                   OR (t.slug || '.' || f.key) = ANY(CAST(:refs AS text[])))
        """),
        {"every": not wanted, "refs": pick_refs},
    ).one()
    rels = db.execute(
        text(f"""
            SELECT r.relation, array_agg(s.i), array_agg(d.i)
            FROM object_relations r
            JOIN graph_calc_nodes s ON s.id = r.src_object_id
            JOIN graph_calc_nodes d ON d.id = r.dst_object_id
            WHERE s.i <> d.i AND {alive}
            GROUP BY r.relation
        """)
    ).all()
    n = len(ids)
    a_parts = [_ints(refs[0])]
    b_parts = [_ints(refs[1])]
    transitive = set(
        db.scalars(select(RelationType.slug).where(RelationType.transitive.is_(True)))
    )
    for slug, src, dst in rels:
        s, d = _ints(src), _ints(dst)
        if not wanted or slug in pick_relations:
            a_parts.append(s)
            b_parts.append(d)
        if slug in transitive:
            # 같은 부모 → 같은 자식 선이 여럿이어도(옵션 · 기간) 한 번.
            key = np.unique(s * n + d)
            out.transitive[slug] = (key // n, key % n)
    a_all = np.concatenate(a_parts)
    b_all = np.concatenate(b_parts)
    lo, hi = np.minimum(a_all, b_all), np.maximum(a_all, b_all)
    key = np.unique(lo * n + hi)
    out.a, out.b = key // n, key % n
    return out
