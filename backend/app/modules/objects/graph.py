"""그래프 트래버설 — **전환 경계.**

내부는 재귀 CTE 로 시작한다. 그래프 엔진(AGE 같은)으로 갈 일이 생기면 **이 모듈
안만** 바뀌고 라우터·화면은 그대로다. 그래서 밖에서는 아래 공개 함수만 쓴다.

전환 트리거는 **데이터 규모가 아니다**(Postgres 는 수억 행도 처리한다). 관계
종류가 다양해지고, 임의 경로 질의가 잦아지고, AI 가 질의를 만들기 시작하는
시점이다. 미리 깔면 운영 표면만 늘고 얻는 것이 없다.

## 부모가 어느 끝인가

관계는 사람이 정의하므로 방향이 둘 다 가능하다 — `part_of`(자식 -> 부모)로 적을
수도 있고 `contains`(부모 -> 자식)로 적을 수도 있다. 그래서 트리를 그리는 쪽이
**부모가 어느 끝인지**를 함께 준다(`parent_end`). 안 주고 하나로 정하면, 반대로
정의한 사람의 트리가 **뒤집힌 채로 그려지고 그것을 말해 주는 자리가 없다.**
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import any_, case, false, func, literal, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import ColumnElement

from app.modules.accounts.models import User
from app.modules.objects import refedges, typecounts
from app.modules.objects.models import ObjectInstance, ObjectLink, ObjectRef, ObjectRelation
from app.shared.batches import chunks
from app.shared.permissions import sees_every_department, visible_owner_clause

ParentEnd = Literal["src", "dst"]

#: 재귀를 여기서 끊는다. **없으면 관계가 촘촘해지는 날 질의 하나가 DB 를 통째로
#: 훑는다** — 그리고 느려진 이유는 화면 어디에도 안 적힌다.
MAX_DEPTH = 20


@dataclass(frozen=True)
class Tree:
    """한 타입의 트리 — 「아래 전부」 를 펼칠 관계와 부모 쪽 끝(`list_view.tree`)."""

    relation: str
    parent_end: ParentEnd
    type_id: uuid.UUID
    type_label: str


def tree_spec(list_view: dict[str, Any] | None) -> tuple[str | None, ParentEnd]:
    """`list_view.tree` 가 정한 트리 관계와 부모 쪽 끝 — 트리 화면 · 「하위 포함」 · 조건의
    「하위 전체」 가 같은 것을 읽는다.

    안 정했으면 트리가 없다 — **`transitive` 인 관계가 둘 이상일 수 있어서** 아무거나 골라
    펼치면 그 「아래」 가 무엇의 아래인지 말할 수 없다.
    """
    spec = (list_view or {}).get("tree") or {}
    relation = spec.get("relation")
    parent_end: ParentEnd = "src" if spec.get("parent") == "src" else "dst"
    return (relation if isinstance(relation, str) and relation else None, parent_end)


def subtree_ids(db: Session, tree: Tree, roots: list[uuid.UUID]) -> list[uuid.UUID]:
    """roots 와 **그 아래 전부**(깊이 MAX_DEPTH) — 조건의 「하위 전체」 가 먼저 뽑아
    `any_of` 로 건다.

    재귀를 조건식 안에 넣지 않는다 — PostgreSQL 은 재귀 CTE 의 줄 수를 어림하지 못해(늘
    백여 줄) 아래가 큰데도 한 줄씩 견주는 길을 골랐다(기록 200만, 모델 1,555개 아래 5초 ·
    전부 19초). 뽑은 것을 배열 하나로 넘기면 개수를 알고 고른다(같은 것이 0.6초 · 0.9초, 실측).
    """
    if not roots:
        return []
    seed = select(
        func.unnest(literal(list(roots), ARRAY(PgUUID(as_uuid=True)))).label("id"),
        literal(0).label("depth"),
    )
    walk = seed.cte("subtree", recursive=True)
    if refedges.is_ref(tree.relation):
        key = tree.relation[len(refedges.PREFIX) :].split(".", 1)[1]
        step = aliased(ObjectRef)
        parent_col, child_col = {"dst": ("dst_id", "src_id"), "src": ("src_id", "dst_id")}[
            tree.parent_end
        ]
        walk = walk.union_all(
            select(getattr(step, child_col), walk.c.depth + 1).where(
                step.key == key,
                step.src_type_id == tree.type_id,
                getattr(step, parent_col) == walk.c.id,
                walk.c.depth < MAX_DEPTH,
            )
        )
    else:
        step_rel = aliased(ObjectRelation)
        parent_col, child_col = _ends(tree.parent_end)
        walk = walk.union_all(
            select(getattr(step_rel, child_col), walk.c.depth + 1).where(
                step_rel.relation == tree.relation,
                getattr(step_rel, parent_col) == walk.c.id,
                walk.c.depth < MAX_DEPTH,
            )
        )
    return list(dict.fromkeys(db.scalars(select(walk.c.id))))


@dataclass(frozen=True)
class Levels:
    """한 트리를 단계 하나로 접은 것 — 계층 축(ADR 0035 3단계)이 쓴다."""

    up: dict[uuid.UUID, uuid.UUID]
    """그 단계보다 깊은 노드 → 그 단계의 조상. 여기 없는 노드는 자기 자신이 값이다."""
    folded: frozenset[uuid.UUID]
    """**그 단계의** 노드 — 자기와 그 아래가 한 값이다. 그래서 이 값의 칸을 누르면 「하위
    전체」 로 돌아간다. 나머지(그 단계보다 얕은 노드, 뿌리에서 닿지 않는 노드 — 부모가
    지워졌거나 깊이 상한 너머)는 자기 자신만 센 값이라 「같음」 이다. 「얕은 것만 같음」 으로
    두던 때는 닿지 않는 노드의 칸을 누르면 그 아래 기록까지 섞였다(2026-10-24 점검)."""


def levels(db: Session, tree: Tree, level: int) -> Levels:
    """뿌리에서 아래로 한 번 훑어 노드마다 **그 단계의 조상**을 찾는다(뿌리가 0단, 깊이
    MAX_DEPTH).

    뿌리는 그 타입의 지우지 않은 객체 중 부모가 없는 것이다. 부모가 여럿이면(트리가 아니라
    DAG) 뿌리에 가장 가까운 길 하나를 고른다 — 같은 노드가 두 값에 들면 칸의 합이 기록 수와
    어긋난다.
    """
    if refedges.is_ref(tree.relation):
        key = tree.relation[len(refedges.PREFIX) :].split(".", 1)[1]
        table = "object_refs"
        parent_col, child_col = {"dst": ("dst_id", "src_id"), "src": ("src_id", "dst_id")}[
            tree.parent_end
        ]
        edge = "e.key = :key AND e.src_type_id = :type"
    else:
        key = ""
        table = "object_relations"
        parent_col, child_col = _ends(tree.parent_end)
        edge = "e.relation = :rel"
    rows = db.execute(
        text(f"""
            WITH RECURSIVE down(id, depth, anc) AS (
                SELECT o.id, 0, o.id
                  FROM objects o
                 WHERE o.type_id = :type AND o.deleted_at IS NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM {table} e WHERE {edge} AND e.{child_col} = o.id
                   )
                -- UNION(ALL 아님) — 부모가 여럿인 DAG 에서 같은 (노드, 깊이, 조상)으로 모이는
                -- 길을 한 줄로 접는다. ALL 이면 길마다 줄이 갈라져 깊이마다 곱으로 늘었다.
                UNION
                SELECT e.{child_col}, d.depth + 1,
                       CASE WHEN d.depth + 1 <= :level THEN e.{child_col} ELSE d.anc END
                  FROM {table} e
                  JOIN down d ON e.{parent_col} = d.id
                 WHERE {edge} AND d.depth < :max_depth
            )
            SELECT DISTINCT ON (id) id, depth, anc FROM down ORDER BY id, depth, anc
        """),
        {
            "type": str(tree.type_id),
            "rel": tree.relation,
            "key": key,
            "level": level,
            "max_depth": MAX_DEPTH,
        },
    )
    up: dict[uuid.UUID, uuid.UUID] = {}
    folded: set[uuid.UUID] = set()
    for row in rows:
        if row.depth > level:
            up[row.id] = row.anc
        elif row.depth == level:
            folded.add(row.id)
    return Levels(up=up, folded=frozenset(folded))


def any_of(column: Any, ids: list[uuid.UUID]) -> ColumnElement[bool]:
    """`column = ANY(배열)` — 자리표 하나. `IN (…)` 은 값마다 자리표라 수만 개면 넘친다."""
    if not ids:
        return false()
    found: ColumnElement[bool] = column == any_(
        literal(list(ids), ARRAY(PgUUID(as_uuid=True)))
    )
    return found


def _ends(parent_end: ParentEnd) -> tuple[str, str]:
    """(부모 쪽 칸, 자식 쪽 칸).

    `parent_end='dst'` 는 `part_of` 처럼 **자식이 부모를 가리키는** 모양이다.
    """
    return (
        ("dst_object_id", "src_object_id")
        if parent_end == "dst"
        else (
            "src_object_id",
            "dst_object_id",
        )
    )


def child_ids(
    db: Session, *, relation: str, parent_id: uuid.UUID, parent_end: ParentEnd
) -> list[uuid.UUID]:
    """바로 아래 한 단계. **한 단계씩 읽는 것이 트리의 기본**이다 — 통째로
    불러오면 부품 5천 개짜리에서 첫 화면이 안 뜬다."""
    if refedges.is_ref(relation):
        # 참조 칸 트리 — 자식이 칸으로 부모를 가리킨다(`refedges`).
        return refedges.child_ids(db, relation=relation, parent_id=parent_id)
    parent_col, child_col = _ends(parent_end)
    rows = db.execute(
        text(f"""
            SELECT r.{child_col} AS id
              FROM object_relations r
              JOIN objects o ON o.id = r.{child_col}
             WHERE r.{parent_col} = :parent
               AND r.relation = :rel
               AND o.deleted_at IS NULL
        """),
        {"parent": str(parent_id), "rel": relation},
    )
    return [row.id for row in rows]


def descendant_ids(
    db: Session,
    *,
    relation: str,
    root_id: uuid.UUID,
    parent_end: ParentEnd,
    max_depth: int = MAX_DEPTH,
) -> list[uuid.UUID]:
    """그 아래 **전부**(자기 자신은 뺀다).

    「아래 것까지 포함」 이 이것을 쓴다. 안 쓰면 상위 노드를 눌렀을 때 목록이
    비고, **그 빈 목록은 「없다」 로 읽힌다.**
    """
    if refedges.is_ref(relation):
        return refedges.descendant_ids(
            db, relation=relation, root_id=root_id, max_depth=max_depth
        )
    parent_col, child_col = _ends(parent_end)
    rows = db.execute(
        text(f"""
            WITH RECURSIVE walk(id, depth) AS (
                SELECT r.{child_col}, 1
                  FROM object_relations r
                 WHERE r.{parent_col} = :root AND r.relation = :rel
                UNION
                SELECT r.{child_col}, w.depth + 1
                  FROM object_relations r
                  JOIN walk w ON r.{parent_col} = w.id
                 WHERE r.relation = :rel AND w.depth < :max_depth
            )
            SELECT DISTINCT w.id
              FROM walk w
              JOIN objects o ON o.id = w.id
             WHERE o.deleted_at IS NULL
        """),
        {"root": str(root_id), "rel": relation, "max_depth": max_depth},
    )
    return [row.id for row in rows]


def ancestor_ids(
    db: Session,
    *,
    relation: str,
    object_id: uuid.UUID,
    parent_end: ParentEnd,
    max_depth: int = MAX_DEPTH,
) -> list[uuid.UUID]:
    """위로 올라가며 만나는 것 전부. 상세 화면의 경로(빵부스러기)가 쓴다."""
    if refedges.is_ref(relation):
        return refedges.ancestor_ids(
            db, relation=relation, node_id=object_id, max_depth=max_depth
        )
    parent_col, child_col = _ends(parent_end)
    rows = db.execute(
        text(f"""
            WITH RECURSIVE walk(id, depth) AS (
                SELECT r.{parent_col}, 1
                  FROM object_relations r
                 WHERE r.{child_col} = :start AND r.relation = :rel
                UNION
                SELECT r.{parent_col}, w.depth + 1
                  FROM object_relations r
                  JOIN walk w ON r.{child_col} = w.id
                 WHERE r.relation = :rel AND w.depth < :max_depth
            )
            SELECT DISTINCT w.id
              FROM walk w
              JOIN objects o ON o.id = w.id
             WHERE o.deleted_at IS NULL
        """),
        {"start": str(object_id), "rel": relation, "max_depth": max_depth},
    )
    return [row.id for row in rows]


def child_counts(
    db: Session,
    *,
    relation: str,
    parent_ids: list[uuid.UUID],
    parent_end: ParentEnd,
    user: User | None = None,
) -> dict[uuid.UUID, int]:
    """여러 노드의 자식 수를 **한 번에** 센다.

    노드마다 세면 한 단계를 펼칠 때 질의가 노드 수만큼 붙는다. 자식 수를 미리
    아는 이유는 **펼침 표시를 그릴지 정하기 위해서**다 — 없는데 펼침 화살표가
    보이면 눌러 보고서야 빈 것을 안다.

    `user` 를 주면 **그 사람이 볼 수 있는 자식만** 센다 — 펼치면 안 보이는 것을 세면
    「자식 3」 을 눌러 빈 가지가 열리고, 그 수가 남의 부서에 무엇이 있는지 말한다.
    """
    if not parent_ids:
        return {}
    if refedges.is_ref(relation):
        return refedges.child_counts(db, relation=relation, parent_ids=parent_ids, user=user)
    parent_col, child_col = _columns(parent_end)
    counts: dict[uuid.UUID, int] = {}
    for batch in chunks(parent_ids):
        stmt = (
            select(parent_col, func.count())
            .join(ObjectInstance, ObjectInstance.id == child_col)
            .where(
                ObjectRelation.relation == relation,
                parent_col.in_(batch),
                ObjectInstance.deleted_at.is_(None),
            )
            .group_by(parent_col)
        )
        if user is not None:
            stmt = stmt.where(visible_owner_clause(user, ObjectInstance.owner_workspace_id))
        for parent, n in db.execute(stmt):
            counts[parent] = counts.get(parent, 0) + int(n)
    return counts


def parentless_ids(
    db: Session,
    *,
    relation: str,
    type_id: uuid.UUID,
    parent_end: ParentEnd,
    user: User | None = None,
) -> list[uuid.UUID]:
    """부모가 없는 것들 — 트리의 꼭대기 후보.

    자식이 있는 것은 **뿌리**이고, 자식도 없는 것은 **어디에도 안 걸린 것**이다.
    둘을 가르는 이유: 트리를 아직 안 만든 타입에서는 거의 모두가 부모가 없어,
    안 가르면 뿌리 목록이 곧 전체 목록이 된다.

    `user` 를 주면 그 사람이 볼 수 있는 것만 — 「어디에도 안 걸린 것 N」 이 남의 부서 것까지
    세면 그 수가 곧 샌 것이다.
    """
    if refedges.is_ref(relation):
        return refedges.parentless_ids(db, relation=relation, type_id=type_id, user=user)
    _, child_col = _columns(parent_end)
    has_parent = (
        select(ObjectRelation.id)
        .where(child_col == ObjectInstance.id, ObjectRelation.relation == relation)
        .exists()
    )
    stmt = select(ObjectInstance.id).where(
        ObjectInstance.type_id == type_id,
        ObjectInstance.deleted_at.is_(None),
        ~has_parent,
    )
    if user is not None:
        stmt = stmt.where(visible_owner_clause(user, ObjectInstance.owner_workspace_id))
    return list(db.scalars(stmt))


def _columns(parent_end: ParentEnd) -> tuple[Any, Any]:
    """(부모 쪽 칸, 자식 쪽 칸) — `_ends` 와 같은 뜻의 열 객체."""
    rel = ObjectRelation
    if parent_end == "dst":
        return rel.dst_object_id, rel.src_object_id
    return rel.src_object_id, rel.dst_object_id


# --- 이웃 — 그래프 화면이 쓴다 ---------------------------------------------
#
# 위의 트리 함수들은 **관계 하나**를 따라가고, 아래는 **모든 관계**를 양방향으로
# 본다. 그래서 위와 달리 한 단계에 **두 종류의 상한**이 든다:
#
#     fanout   노드 하나가 데려오는 이웃 수. 없으면 허브 하나(「본사」 같은
#              것)가 이웃 5천 개를 끌고 와서 화면이 검은 덩어리가 된다
#     node cap 한 번에 돌려주는 노드 수 — 호출하는 쪽이 건다
#
# 잘린 것은 **잘렸다고 말한다**(`degree_counts` 로 원래 몇이었는지 함께 준다).
# 말 안 하면 그 그림은 「이게 전부」 로 읽히고, 그것은 틀린 그림이다.


@dataclass(frozen=True)
class Edge:
    id: uuid.UUID
    relation: str
    src: uuid.UUID
    dst: uuid.UUID
    src_type: str | None = None
    dst_type: str | None = None
    """링크(`object_links`)만 채운다 — 원 표의 행은 id 만으로는 어느 타입인지 모른다
    (부서를 비추는 타입이 둘일 수 있다). 관계는 객체 행이 타입을 안다."""


def _ends_of(
    ids: list[uuid.UUID],
) -> tuple[ColumnElement[uuid.UUID], ColumnElement[uuid.UUID]]:
    """(내 쪽 끝, 저쪽 끝). 양쪽 다 ids 에 있으면 src 를 내 쪽으로 친다."""
    rel = ObjectRelation
    anchor = case((rel.src_object_id.in_(ids), rel.src_object_id), else_=rel.dst_object_id)
    other = case((rel.src_object_id.in_(ids), rel.dst_object_id), else_=rel.src_object_id)
    return anchor, other


def neighbor_edges(
    db: Session,
    *,
    frontier: list[uuid.UUID],
    user: User,
    fanout: int,
    relations: list[str] | None = None,
    type_ids: list[uuid.UUID] | None = None,
) -> list[Edge]:
    """frontier 의 각 노드에서 **fanout 개까지**의 이웃 관계.

    저쪽 끝이 지워졌거나 안 보이는 관계는 아예 안 센다 — 보이지 않는 이웃 때문에
    보이는 이웃이 fanout 에서 밀려나면 그 이유를 아무도 설명할 수 없다.
    """
    if not frontier:
        return []
    rel, obj = ObjectRelation, ObjectInstance
    anchor, other = _ends_of(frontier)
    conditions = [
        or_(rel.src_object_id.in_(frontier), rel.dst_object_id.in_(frontier)),
        obj.deleted_at.is_(None),
        visible_owner_clause(user, obj.owner_workspace_id),
    ]
    if relations:
        conditions.append(rel.relation.in_(relations))
    if type_ids:
        conditions.append(obj.type_id.in_(type_ids))
    ranked = (
        select(
            rel.id,
            rel.relation,
            rel.src_object_id,
            rel.dst_object_id,
            func.row_number().over(partition_by=anchor, order_by=rel.created_at).label("rn"),
        )
        .select_from(rel)
        .join(obj, obj.id == other)
        .where(*conditions)
        .subquery()
    )
    rows = db.execute(select(ranked).where(ranked.c.rn <= fanout))
    return [
        Edge(id=r.id, relation=r.relation, src=r.src_object_id, dst=r.dst_object_id)
        for r in rows
    ]


def induced_edges(
    db: Session, *, ids: list[uuid.UUID], limit: int, relations: list[str] | None = None
) -> list[Edge]:
    """양 끝이 모두 ids 안인 관계 — **이미 화면에 있는 노드끼리의 선.**

    fanout 에 밀려 안 따라간 관계도 양 끝이 다 보이면 그려야 한다. 안 그리면
    화면에 나란히 선 둘이 「관계없음」 으로 읽힌다.
    """
    if not ids:
        return []
    rel = ObjectRelation
    conditions = [rel.src_object_id.in_(ids), rel.dst_object_id.in_(ids)]
    if relations:
        conditions.append(rel.relation.in_(relations))
    rows = db.execute(
        select(rel.id, rel.relation, rel.src_object_id, rel.dst_object_id)
        .where(*conditions)
        .order_by(rel.created_at)
        .limit(limit)
    )
    return [
        Edge(id=r.id, relation=r.relation, src=r.src_object_id, dst=r.dst_object_id)
        for r in rows
    ]


def degree_counts(db: Session, *, ids: list[uuid.UUID], user: User) -> dict[uuid.UUID, int]:
    """각 노드에 걸린 **보이는** 관계의 수. 화면이 「+N 더」 를 적는 근거다."""
    if not ids:
        return {}
    rel, obj = ObjectRelation, ObjectInstance
    counts: dict[uuid.UUID, int] = {}
    # 내가 출발점인 것과 도착점인 것을 따로 센다 — 한 질의로 합치면 양 끝이 다
    # ids 인 관계가 한쪽에만 잡힌다.
    for mine, other in (
        (rel.src_object_id, rel.dst_object_id),
        (rel.dst_object_id, rel.src_object_id),
    ):
        rows = db.execute(
            select(mine, func.count())
            .select_from(rel)
            .join(obj, obj.id == other)
            .where(
                mine.in_(ids),
                obj.deleted_at.is_(None),
                visible_owner_clause(user, obj.owner_workspace_id),
            )
            .group_by(mine)
        )
        for node_id, n in rows:
            counts[node_id] = counts.get(node_id, 0) + int(n)
    return counts


@dataclass(frozen=True)
class TypeEdge:
    relation: str
    src_type_id: uuid.UUID
    dst_type_id: uuid.UUID
    count: int


def type_edge_counts(db: Session, *, user: User) -> list[TypeEdge]:
    """타입 사이에 실제로 몇 개의 관계가 걸려 있나 — **정의 그래프의 선 굵기.**

    객체가 백만 개여도 답은 (관계 종류 x 타입 쌍) 줄이다. 그래서 이것이 그래프
    화면의 첫 그림이다 — 객체를 하나도 안 그리고 전체 모양을 보여 준다.
    """
    rel = ObjectRelation
    src = aliased(ObjectInstance)
    dst = aliased(ObjectInstance)
    # 보이는 규칙을 **양 끝에 따로** 건다. 한쪽만 걸면 남의 부서 객체가 「선의
    # 저쪽 끝」 으로 세어져, 보이지 않는 것의 수가 굵기로 새어 나온다.
    rows = db.execute(
        select(rel.relation, src.type_id, dst.type_id, func.count())
        .select_from(rel)
        .join(src, src.id == rel.src_object_id)
        .join(dst, dst.id == rel.dst_object_id)
        .where(
            src.deleted_at.is_(None),
            dst.deleted_at.is_(None),
            visible_owner_clause(user, src.owner_workspace_id),
            visible_owner_clause(user, dst.owner_workspace_id),
        )
        .group_by(rel.relation, src.type_id, dst.type_id)
    )
    return [
        TypeEdge(relation=r[0], src_type_id=r[1], dst_type_id=r[2], count=int(r[3]))
        for r in rows
    ]


def object_counts_by_type(db: Session, *, user: User) -> dict[uuid.UUID, int]:
    """타입마다 보이는 객체가 몇 개인가 — 정의 그래프의 노드 크기. 모든 부서를 보는 사람이면
    계수표(ADR 0038), 부서로 좁힌 설치의 멤버만 그 자리에서 센다."""
    if sees_every_department(user):
        return typecounts.counts(db)
    obj = ObjectInstance
    rows = db.execute(
        select(obj.type_id, func.count())
        .where(obj.deleted_at.is_(None), visible_owner_clause(user, obj.owner_workspace_id))
        .group_by(obj.type_id)
    )
    return {type_id: int(n) for type_id, n in rows}


# --- 원 표와 이은 선(`object_links`) ------------------------------------------
#
# 한쪽 끝이 system 객체(부서·계정·승격한 표)인 선은 FK 없이 `object_links` 에 있다.
# 그래프가 이것을 모르면 「담당 부서」 로만 이어진 객체가 그림에서 외톨이로 보이고,
# 그 외톨이는 「관계없음」 으로 읽힌다. 규칙은 관계와 같다 — 저쪽 끝이 객체면 보이는
# 것만, system 이면 항상(원 표의 행은 부서 소유가 아니다).


def _link_other_visible(
    user: User, other_type: Any, other_id: Any, systems: list[str]
) -> ColumnElement[bool]:
    obj = aliased(ObjectInstance)
    seen = (
        select(obj.id)
        .where(
            obj.id == other_id,
            obj.deleted_at.is_(None),
            visible_owner_clause(user, obj.owner_workspace_id),
        )
        .exists()
    )
    return or_(other_type.in_(systems), seen) if systems else seen


def neighbor_link_edges(
    db: Session,
    *,
    frontier: list[uuid.UUID],
    user: User,
    fanout: int,
    systems: list[str],
    relations: list[str] | None = None,
    type_slugs: list[str] | None = None,
) -> list[Edge]:
    """frontier 의 각 노드에서 fanout 개까지의 링크 — `neighbor_edges` 와 같은 규칙."""
    if not frontier:
        return []
    link = ObjectLink
    anchor = case((link.src_id.in_(frontier), link.src_id), else_=link.dst_id)
    other_type = case((link.src_id.in_(frontier), link.dst_type), else_=link.src_type)
    other_id = case((link.src_id.in_(frontier), link.dst_id), else_=link.src_id)
    conditions = [
        or_(link.src_id.in_(frontier), link.dst_id.in_(frontier)),
        _link_other_visible(user, other_type, other_id, systems),
    ]
    if relations:
        conditions.append(link.relation.in_(relations))
    if type_slugs:
        conditions.append(other_type.in_(type_slugs))
    ranked = (
        select(
            link.id,
            link.relation,
            link.src_id,
            link.dst_id,
            link.src_type,
            link.dst_type,
            func.row_number().over(partition_by=anchor, order_by=link.created_at).label("rn"),
        )
        .where(*conditions)
        .subquery()
    )
    rows = db.execute(select(ranked).where(ranked.c.rn <= fanout))
    return [
        Edge(
            id=r.id,
            relation=r.relation,
            src=r.src_id,
            dst=r.dst_id,
            src_type=r.src_type,
            dst_type=r.dst_type,
        )
        for r in rows
    ]


def induced_link_edges(
    db: Session, *, ids: list[uuid.UUID], limit: int, relations: list[str] | None = None
) -> list[Edge]:
    """양 끝이 모두 ids 안인 링크."""
    if not ids:
        return []
    link = ObjectLink
    conditions = [link.src_id.in_(ids), link.dst_id.in_(ids)]
    if relations:
        conditions.append(link.relation.in_(relations))
    rows = db.execute(
        select(link.id, link.relation, link.src_id, link.dst_id, link.src_type, link.dst_type)
        .where(*conditions)
        .order_by(link.created_at)
        .limit(limit)
    )
    return [
        Edge(
            id=r.id,
            relation=r.relation,
            src=r.src_id,
            dst=r.dst_id,
            src_type=r.src_type,
            dst_type=r.dst_type,
        )
        for r in rows
    ]


def link_degree_counts(
    db: Session, *, ids: list[uuid.UUID], user: User, systems: list[str]
) -> dict[uuid.UUID, int]:
    """각 노드에 걸린 보이는 링크의 수 — `degree_counts` 에 더한다."""
    if not ids:
        return {}
    link = ObjectLink
    counts: dict[uuid.UUID, int] = {}
    for mine, other_type, other_id in (
        (link.src_id, link.dst_type, link.dst_id),
        (link.dst_id, link.src_type, link.src_id),
    ):
        rows = db.execute(
            select(mine, func.count())
            .where(mine.in_(ids), _link_other_visible(user, other_type, other_id, systems))
            .group_by(mine)
        )
        for node_id, n in rows:
            counts[node_id] = counts.get(node_id, 0) + int(n)
    return counts


@dataclass(frozen=True)
class TypeLinkEdge:
    relation: str
    src_type_slug: str
    dst_type_slug: str
    count: int


def type_link_counts(db: Session, *, user: User, systems: list[str]) -> list[TypeLinkEdge]:
    """타입 사이에 걸린 링크 수 — 정의 그래프의 선 굵기에 더한다. 양 끝에 보이는
    규칙을 따로 건다(관계와 같다)."""
    link = ObjectLink
    rows = db.execute(
        select(link.relation, link.src_type, link.dst_type, func.count())
        .where(
            _link_other_visible(user, link.src_type, link.src_id, systems),
            _link_other_visible(user, link.dst_type, link.dst_id, systems),
        )
        .group_by(link.relation, link.src_type, link.dst_type)
    )
    return [
        TypeLinkEdge(relation=r[0], src_type_slug=r[1], dst_type_slug=r[2], count=int(r[3]))
        for r in rows
    ]


def link_partner_ids(db: Session, *, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """ids 와 링크로 이어진 저쪽 끝의 id 들 — 「함께 고른 원 표」 에서 무엇을 먼저 실을지."""
    if not ids:
        return set()
    link = ObjectLink
    out: set[uuid.UUID] = set()
    for mine, other in ((link.src_id, link.dst_id), (link.dst_id, link.src_id)):
        out.update(db.scalars(select(other).where(mine.in_(ids))))
    return out


# --- 두 객체 사이의 길 ---------------------------------------------------------
#
# 「A 와 B 는 어떻게 이어져 있나」. 이웃 그림은 노드마다 fanout 개까지만 데려오므로, 그것을
# 이어 붙여 길을 찾으면 잘린 곳에 있던 길을 「없다」 로 답한다. 그래서 길 찾기는 **양 끝에서
# 한 층씩**(작은 쪽을 넓힌다) 넓히고, 상한에 닿으면 「없다」 가 아니라 「찾다가 멈췄다」 고
# 말한다. 선은 이웃 그림과 같은 둘(관계 · 참조 칸) — 보이는 것 · 지운 것 · 기록을 다루는
# 규칙이 같다.

#: 길 하나의 최대 길이(선 수). 그 너머의 「이어짐」 은 사람에게 뜻이 없고, 탐색만 커진다.
PATH_MAX_DEPTH = 8
#: 길 찾기가 들고 다니는 노드 수 상한 — 넘으면 멈추고 `truncated`.
PATH_VISIT_LIMIT = 50_000
#: 노드 하나에서 따라가는 선 수 상한. 허브(「본사」)가 이웃 수만 개를 끌고 오지 않게 —
#: 닿으면 그 너머를 덜 본 것이라 `truncated`.
PATH_FANOUT = 2_000
#: 한 층에서 읽는 선의 상한 — 넘으면 그 층에서 멈추고 `truncated`.
PATH_EDGE_BUDGET = 200_000
#: 한 번에 넓히는 frontier 조각(`IN (...)` 의 크기).
_PATH_CHUNK = 1_000

PathEdge = Edge | refedges.RefEdge


@dataclass(frozen=True)
class PathSearch:
    """길 찾기의 답 — 같은 길이의 가장 짧은 길들."""

    nodes: list[list[uuid.UUID]]
    """길마다 노드 차례(출발 → 도착)."""
    edges: list[list[PathEdge]]
    """길마다 선 차례 — `nodes[i][k]` 와 `nodes[i][k+1]` 을 잇는 것이 `edges[i][k]`."""
    length: int | None
    """선 수. 못 찾았으면 None."""
    visited: int
    """양쪽에서 들른 노드 수."""
    truncated: bool
    """상한(노드 수 · 노드당 선 수)에 닿아 덜 찾았다 — **길이 없다는 뜻이 아니다.**"""


def _path_step(
    db: Session,
    frontier: list[uuid.UUID],
    *,
    user: User,
    relations: list[str] | None,
    type_ids: list[uuid.UUID] | None,
    records: bool,
) -> tuple[list[PathEdge], bool]:
    """frontier 에서 한 걸음 — (선들, 상한에 닿았나).

    노드당 상한은 관계 · 참조 칸이 **따로** 건다(`neighbor_edges` 마다 `fanout`). 둘을
    합쳐 세던 때는 관계 1,500 · 참조 600 처럼 어느 쪽도 안 잘렸는데 「상한에 닿음」 이라 했다.
    한 층에서 읽는 선도 `PATH_EDGE_BUDGET` 에서 멈춘다 — 들른 노드 상한은 층 사이에서만 보므로,
    넓은 층 하나가 그 사이에 선을 수백만 개 읽을 수 있었다(2026-10-24 점검)."""
    found: list[PathEdge] = []
    here = set(frontier)
    hit = False
    for part in chunks(frontier, _PATH_CHUNK):
        for edges in (
            neighbor_edges(
                db,
                frontier=part,
                user=user,
                fanout=PATH_FANOUT,
                relations=relations,
                type_ids=type_ids,
            ),
            refedges.neighbor_edges(
                db,
                frontier=part,
                user=user,
                fanout=PATH_FANOUT,
                relations=relations,
                type_ids=type_ids,
                skip_logs=not records,
            ),
        ):
            found += edges
            hit = hit or _capped(edges, here)
        if len(found) >= PATH_EDGE_BUDGET:
            return found, True
    return found, hit


def _capped(edges: Sequence[PathEdge], here: set[uuid.UUID]) -> bool:
    """한 종류(관계 또는 참조 칸)의 선이 어느 노드에서 노드당 상한에 닿았나."""
    per_anchor: dict[uuid.UUID, int] = {}
    for edge in edges:
        for end in {edge.src, edge.dst} & here:
            per_anchor[end] = per_anchor.get(end, 0) + 1
    return any(n >= PATH_FANOUT for n in per_anchor.values())


def _chains(
    parents: dict[uuid.UUID, list[tuple[uuid.UUID, PathEdge]]],
    node: uuid.UUID,
    limit: int,
) -> list[tuple[list[uuid.UUID], list[PathEdge]]]:
    """node 에서 그 쪽의 출발점까지 거꾸로 — (노드들, 선들), **출발점이 먼저**.
    limit 개까지."""
    if not parents[node]:
        return [([node], [])]
    out: list[tuple[list[uuid.UUID], list[PathEdge]]] = []
    for previous, edge in parents[node]:
        for nodes, edges in _chains(parents, previous, limit):
            out.append(([*nodes, node], [*edges, edge]))
            if len(out) >= limit:
                return out
    return out


def shortest_paths(
    db: Session,
    *,
    source: uuid.UUID,
    target: uuid.UUID,
    user: User,
    max_depth: int = 6,
    relations: list[str] | None = None,
    type_ids: list[uuid.UUID] | None = None,
    records: bool = False,
    limit: int = 3,
) -> PathSearch:
    """source 에서 target 까지 **가장 짧은 길들**(같은 길이로 limit 개까지).

    방향은 보지 않는다 — 「이 부품이 들어간 제품」 처럼 관계를 거슬러 가는 길도 길이다(선의
    방향은 답에 그대로 실린다). 기록(ADR 0011)을 거쳐 가는 길은 `records` 일 때만 — 인기 모델은
    기록 10만 건이 가리켜서, 기록으로 넓히면 거의 모든 것이 두 걸음 안에 이어진다.
    """
    max_depth = max(1, min(max_depth, PATH_MAX_DEPTH))
    if source == target:
        return PathSearch(nodes=[[source]], edges=[[]], length=0, visited=1, truncated=False)
    dist: tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]] = ({source: 0}, {target: 0})
    parents: tuple[
        dict[uuid.UUID, list[tuple[uuid.UUID, PathEdge]]],
        dict[uuid.UUID, list[tuple[uuid.UUID, PathEdge]]],
    ] = ({source: []}, {target: []})
    frontier: list[list[uuid.UUID]] = [[source], [target]]
    depth = [0, 0]
    truncated = False
    meet: list[uuid.UUID] = []
    while frontier[0] and frontier[1] and depth[0] + depth[1] < max_depth:
        if len(dist[0]) + len(dist[1]) >= PATH_VISIT_LIMIT:
            truncated = True
            break
        side = 0 if len(frontier[0]) <= len(frontier[1]) else 1
        here = set(frontier[side])
        found, capped = _path_step(
            db,
            frontier[side],
            user=user,
            relations=relations,
            type_ids=type_ids,
            records=records,
        )
        truncated = truncated or capped
        mine, theirs = dist[side], dist[1 - side]
        step = depth[side] + 1
        nxt: list[uuid.UUID] = []
        for edge in found:
            for near, far in ((edge.src, edge.dst), (edge.dst, edge.src)):
                if near not in here:
                    continue
                if far not in mine:
                    mine[far] = step
                    parents[side][far] = [(near, edge)]
                    nxt.append(far)
                elif mine[far] == step and all(e.id != edge.id for _, e in parents[side][far]):
                    parents[side][far].append((near, edge))
        depth[side] = step
        frontier[side] = nxt
        touching = [node for node in nxt if node in theirs]
        if touching:
            best = min(dist[0][node] + dist[1][node] for node in touching)
            meet = [node for node in touching if dist[0][node] + dist[1][node] == best]
            break
    if not meet:
        return PathSearch(
            nodes=[],
            edges=[],
            length=None,
            visited=len(dist[0]) + len(dist[1]),
            truncated=truncated,
        )
    out_nodes: list[list[uuid.UUID]] = []
    out_edges: list[list[PathEdge]] = []
    seen: set[tuple[uuid.UUID, ...]] = set()
    for node in meet:
        for head_nodes, head_edges in _chains(parents[0], node, limit):
            for tail_nodes, tail_edges in _chains(parents[1], node, limit):
                nodes = head_nodes + list(reversed(tail_nodes))[1:]
                edges = head_edges + list(reversed(tail_edges))
                signature = tuple(edge.id for edge in edges)
                if signature in seen:
                    continue
                seen.add(signature)
                out_nodes.append(nodes)
                out_edges.append(edges)
                if len(out_nodes) >= limit:
                    break
            if len(out_nodes) >= limit:
                break
        if len(out_nodes) >= limit:
            break
    return PathSearch(
        nodes=out_nodes,
        edges=out_edges,
        length=len(out_edges[0]),
        visited=len(dist[0]) + len(dist[1]),
        truncated=truncated,
    )
