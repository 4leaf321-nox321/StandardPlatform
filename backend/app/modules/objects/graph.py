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
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

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
