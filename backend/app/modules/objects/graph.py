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
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import ColumnElement

from app.modules.accounts.models import User
from app.modules.objects import refedges
from app.modules.objects.models import ObjectInstance, ObjectLink, ObjectRelation
from app.shared.permissions import visible_owner_clause

ParentEnd = Literal["src", "dst"]

#: 재귀를 여기서 끊는다. **없으면 관계가 촘촘해지는 날 질의 하나가 DB 를 통째로
#: 훑는다** — 그리고 느려진 이유는 화면 어디에도 안 적힌다.
MAX_DEPTH = 20


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
                UNION ALL
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
                UNION ALL
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
    db: Session, *, relation: str, parent_ids: list[uuid.UUID], parent_end: ParentEnd
) -> dict[uuid.UUID, int]:
    """여러 노드의 자식 수를 **한 번에** 센다.

    노드마다 세면 한 단계를 펼칠 때 질의가 노드 수만큼 붙는다. 자식 수를 미리
    아는 이유는 **펼침 표시를 그릴지 정하기 위해서**다 — 없는데 펼침 화살표가
    보이면 눌러 보고서야 빈 것을 안다.
    """
    if not parent_ids:
        return {}
    if refedges.is_ref(relation):
        return refedges.child_counts(db, relation=relation, parent_ids=parent_ids)
    parent_col, child_col = _ends(parent_end)
    rows = db.execute(
        text(f"""
            SELECT r.{parent_col} AS parent, COUNT(*) AS n
              FROM object_relations r
              JOIN objects o ON o.id = r.{child_col}
             WHERE r.relation = :rel
               AND r.{parent_col} = ANY(:parents)
               AND o.deleted_at IS NULL
             GROUP BY r.{parent_col}
        """),
        {"rel": relation, "parents": [str(one) for one in parent_ids]},
    )
    return {row.parent: int(row.n) for row in rows}


def parentless_ids(
    db: Session, *, relation: str, type_id: uuid.UUID, parent_end: ParentEnd
) -> list[uuid.UUID]:
    """부모가 없는 것들 — 트리의 꼭대기 후보.

    자식이 있는 것은 **뿌리**이고, 자식도 없는 것은 **어디에도 안 걸린 것**이다.
    둘을 가르는 이유: 트리를 아직 안 만든 타입에서는 거의 모두가 부모가 없어,
    안 가르면 뿌리 목록이 곧 전체 목록이 된다.
    """
    if refedges.is_ref(relation):
        return refedges.parentless_ids(db, relation=relation, type_id=type_id)
    _, child_col = _ends(parent_end)
    rows = db.execute(
        text(f"""
            SELECT o.id
              FROM objects o
             WHERE o.type_id = :type_id
               AND o.deleted_at IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM object_relations r
                    WHERE r.{child_col} = o.id AND r.relation = :rel
               )
        """),
        {"type_id": str(type_id), "rel": relation},
    )
    return [row.id for row in rows]


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
    """타입마다 보이는 객체가 몇 개인가 — 정의 그래프의 노드 크기."""
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
