"""관계를 맺을 수 있는가 — **DB 가 못 잡는 것들.**

엣지 표에는 유니크 하나뿐이다. 나머지 셋은 관계 **종류**가 정하는 규칙이라
데이터에 있고, 그래서 여기서 본다:

    허용 타입    「공급사를 시험함」 같은 말이 안 되는 관계를 막는다
    개수 제약    「한 부품의 공급사는 하나」 를 지킨다
    순환 가드    자기 조상을 자식으로 넣으면 트리가 무한히 돈다

**한 곳에 모으는 이유**는 이 틀의 다른 판정과 같다 — 흩뿌리면 「화면에서는
되는데 저장이 안 되는」 경우가 생기고, 그때 어느 쪽이 맞는지 알 방법이 없다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectInstance, ObjectRelation
from app.modules.ontology.models import ObjectType, RelationType
from app.shared.errors import Conflict, NotFound, code


def relation_type(db: Session, slug: str) -> RelationType:
    row = db.scalar(select(RelationType).where(RelationType.slug == slug))
    if row is None:
        raise NotFound(code("OBJECTS", 20), f"관계 종류를 찾을 수 없습니다: {slug}")
    if not row.is_active:
        raise Conflict(code("OBJECTS", 21), f"{row.label}은 지금 쓰지 않는 관계 종류입니다.")
    return row


def require_endpoints_allowed(
    db: Session, kind: RelationType, src: ObjectInstance, dst: ObjectInstance
) -> None:
    """양끝이 이 관계가 허용한 타입인가.

    **안 막으면 말이 안 되는 관계가 데이터에 남고, 그 뒤로 그 데이터로는
    아무것도 못 믿는다.**
    """
    rows = list(db.scalars(select(ObjectType)))
    types = {row.id: row for row in rows}
    #: slug -> 사람이 읽는 이름. **오류에 slug 를 그대로 쓰면 아무도 못 읽는다** —
    #: 화면에는 「부품」 이라고 적혀 있는데 메시지는 `part_87b8` 라고 말한다.
    labels = {row.slug: row.label for row in rows}

    def check(end: ObjectInstance, allowed: list[str] | None, what: str) -> None:
        if not allowed:
            return
        found = types.get(end.type_id)
        if found is None or found.slug not in allowed:
            names = ", ".join(labels.get(slug, slug) for slug in allowed)
            raise Conflict(
                code("OBJECTS", 22),
                f"{kind.label}의 {what}은 {names} 만 됩니다. "
                f"{found.label if found else '알 수 없는 타입'}은 안 됩니다.",
            )

    check(src, kind.src_type_slugs, "출발")
    check(dst, kind.dst_type_slugs, "도착")


def require_cardinality(
    db: Session, kind: RelationType, src_id: uuid.UUID, dst_id: uuid.UUID
) -> None:
    """개수 제약을 지키는가.

    **없으면 「한 부품의 공급사는 하나」 가 조용히 여럿이 된다.** 그 상태는
    화면에서 「왜 둘이 뜨지」 로만 드러나고, 그때는 어느 쪽이 맞는지 모른다.
    """
    src_limited = kind.cardinality in ("one_to_one", "many_to_one")
    dst_limited = kind.cardinality in ("one_to_one", "one_to_many")

    if src_limited:
        existing = db.scalar(
            select(ObjectRelation.id).where(
                ObjectRelation.relation == kind.slug,
                ObjectRelation.src_object_id == src_id,
            )
        )
        if existing is not None:
            raise Conflict(
                code("OBJECTS", 23),
                f"{kind.label}은 하나만 맺을 수 있습니다. 있는 것을 먼저 끊으세요.",
            )

    if dst_limited:
        existing = db.scalar(
            select(ObjectRelation.id).where(
                ObjectRelation.relation == kind.slug,
                ObjectRelation.dst_object_id == dst_id,
            )
        )
        if existing is not None:
            raise Conflict(
                code("OBJECTS", 24),
                f"{kind.label}의 도착 쪽은 하나만 받을 수 있습니다. 있는 것을 먼저 끊으세요.",
            )


def require_no_cycle(
    db: Session, kind: RelationType, src_id: uuid.UUID, dst_id: uuid.UUID
) -> None:
    """이으면 순환이 생기는가.

    **자기 조상을 자식으로 넣는 순간 트리 렌더가 무한히 돈다** — 그 상태는
    화면이 멈추는 것으로만 드러나고, 어디를 고쳐야 하는지는 아무 데도 안 적힌다.

    재귀 CTE 로 도착점의 조상을 훑는다. `graph.py` 의 트래버설과 같은 모양이지만
    **여기서는 「닿는가」 만** 보므로 깊이를 안 센다.
    """
    if not kind.acyclic:
        return
    if src_id == dst_id:
        raise Conflict(code("OBJECTS", 25), "자기 자신과는 맺을 수 없습니다.")

    # dst 에서 출발해 같은 관계를 따라 올라가다 src 를 만나면 순환이다.
    reachable = db.execute(
        text("""
            WITH RECURSIVE walk(id) AS (
                SELECT dst_object_id FROM object_relations
                 WHERE src_object_id = :dst AND relation = :rel
                UNION
                SELECT r.dst_object_id FROM object_relations r
                  JOIN walk w ON r.src_object_id = w.id
                 WHERE r.relation = :rel
            )
            SELECT 1 FROM walk WHERE id = :src LIMIT 1
        """),
        {"dst": str(dst_id), "src": str(src_id), "rel": kind.slug},
    ).scalar()

    if reachable:
        raise Conflict(
            code("OBJECTS", 26),
            f"이으면 {kind.label}에 순환이 생깁니다. "
            "돌아오는 길이 있으면 트리를 그릴 수 없습니다.",
        )
