"""관계를 맺을 수 있는가 — **DB 가 못 잡는 것들.**

엣지 표에는 유니크 하나뿐이다. 나머지 셋은 관계 **종류**가 정하는 규칙이라
데이터에 있고, 그래서 여기서 본다:

    허용 타입    「공급사를 시험함」 같은 말이 안 되는 관계를 막는다. 끝에는 인터페이스도
                 적힌다 — 그 인터페이스를 구현한 타입이면 된다(ADR 0006)
    개수 제약    「한 부품의 공급사는 하나」 를 지킨다 — 유효 기간이 있으면 **같은 때에** 하나
    순환 가드    자기 조상을 자식으로 넣으면 트리가 무한히 돈다 — 기간을 안 보고 막는다

**한 곳에 모으는 이유**는 이 틀의 다른 판정과 같다 — 흩뿌리면 「화면에서는
되는데 저장이 안 되는」 경우가 생기고, 그때 어느 쪽이 맞는지 알 방법이 없다.

## 맺기 — 한 길

객체 끼리 선 하나를 맺는 검사 · 넣기 · 감사는 `add_edge` 하나다. 화면 · API 의 「관계 추가」
(`routes.add_relation`)와 제안의 확정(`proposals.accept`, ADR 0042)이 함께 부른다 — 두 길이면
확정이 개수 제약이나 순환 검사 하나를 빠뜨리고, 그 선은 「직접 이으면 거절될 선」 이 된다.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select, text, true
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import edges
from app.modules.objects.models import ObjectInstance, ObjectLink, ObjectRelation
from app.modules.ontology import interfaces
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.modules.ontology.services import validate_properties
from app.shared import audit
from app.shared.errors import Conflict, NotFound, code


def relation_type(db: Session, slug: str) -> RelationType:
    row = db.scalar(select(RelationType).where(RelationType.slug == slug))
    if row is None:
        raise NotFound(code("OBJECTS", 20), f"관계 종류를 찾을 수 없습니다: {slug}")
    if not row.is_active:
        raise Conflict(code("OBJECTS", 21), f"{row.label}은 비활성 관계 종류입니다.")
    return row


def require_endpoints_allowed(
    db: Session, kind: RelationType, src: ObjectInstance, dst: ObjectInstance
) -> None:
    """양끝이 이 관계가 허용한 타입인가.

    **안 막으면 말이 안 되는 관계가 데이터에 남고, 그 뒤로 그 데이터로는
    아무것도 못 믿는다.**
    """
    types = {row.id: row for row in db.scalars(select(ObjectType))}
    src_type = types.get(src.type_id)
    dst_type = types.get(dst.type_id)
    require_end_types_allowed(
        db, kind, src_type.slug if src_type else "", dst_type.slug if dst_type else ""
    )


def require_end_types_allowed(
    db: Session,
    kind: RelationType,
    src_slug: str,
    dst_slug: str,
    ends: interfaces.Ends | None = None,
) -> None:
    """타입 slug 로 보는 같은 검사 — 한쪽 끝이 system 객체(행이 없는 것)여도 된다.

    `ends` 는 **이미 읽어 둔** 정의(`interfaces.load_ends`). 일괄 가져오기가 줄마다 부르므로,
    안 주면 파일 한 장에 타입 표를 오천 번 읽는다.
    """
    if not kind.src_type_slugs and not kind.dst_type_slugs:
        return
    #: 이름 — **오류에 slug 를 그대로 쓰면 아무도 못 읽는다.** 화면에는 「부품」 이라고 적혀
    #: 있는데 메시지는 `part_87b8` 라고 말한다.
    known = ends if ends is not None else interfaces.load_ends(db)

    def check(slug: str, allowed: list[str] | None, what: str) -> None:
        if known.allows(allowed, slug):
            return
        raise Conflict(
            code("OBJECTS", 22),
            f"{kind.label}의 {what}은 {known.describe(allowed or [])} 만 됩니다. "
            f"{known.labels.get(slug, '알 수 없는 타입')}은 안 됩니다.",
        )

    check(src_slug, kind.src_type_slugs, "출발")
    check(dst_slug, kind.dst_type_slugs, "도착")


def require_cardinality(
    db: Session,
    kind: RelationType,
    src_id: uuid.UUID,
    dst_id: uuid.UUID,
    *,
    valid_from: date | None = None,
    valid_to: date | None = None,
    skip: uuid.UUID | None = None,
) -> None:
    """개수 제약을 지키는가.

    **없으면 「한 부품의 공급사는 하나」 가 조용히 여럿이 된다.** 그 상태는
    화면에서 「왜 둘이 뜨지」 로만 드러나고, 그때는 어느 쪽이 맞는지 모른다.

    유효 기간이 있으면 **기간이 겹치는 선만** 센다(ADR 0039) — 「3월부터 공급사가 B 로 바뀜」
    은 A 의 선(~3월)과 B 의 선(3월~)이 함께 있어도 같은 때에는 하나다. 기간이 빈 선은 늘
    살아 있어 무엇과도 겹친다(꺼진 종류는 예전 그대로). `skip` 은 고치는 그 선 자신.
    """
    period = edges.overlaps(ObjectRelation, valid_from, valid_to)
    mine = ObjectRelation.id != skip if skip is not None else true()
    src_limited = kind.cardinality in ("one_to_one", "many_to_one")
    dst_limited = kind.cardinality in ("one_to_one", "one_to_many")

    # 선은 두 표에 있다 — 양끝이 객체면 `object_relations`, 한쪽이 system 이면
    # `object_links`. 한쪽만 세면 「공급사는 하나」 가 부서 끝에서 조용히 둘이 된다.
    if src_limited:
        existing = db.scalar(
            select(ObjectRelation.id).where(
                ObjectRelation.relation == kind.slug,
                ObjectRelation.src_object_id == src_id,
                period,
                mine,
            )
        ) or db.scalar(
            select(ObjectLink.id).where(
                ObjectLink.relation == kind.slug, ObjectLink.src_id == src_id
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
                period,
                mine,
            )
        ) or db.scalar(
            select(ObjectLink.id).where(
                ObjectLink.relation == kind.slug, ObjectLink.dst_id == dst_id
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

    **유효 기간을 보지 않는다**(ADR 0039) — 기간이 갈린 선끼리 돌아오는 길을 만들어도 막는다.
    한 기준일만 보면 그날은 트리지만, 기준일을 비운 화면(지난 · 앞으로의 선 포함)과 순환을
    모르는 재귀가 그 길을 돈다. 부품이 제 조상의 부품이 되는 BOM 은 어느 때든 데이터 오류다.
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


def relation_defs(db: Session, kind: RelationType) -> list[PropertyDef]:
    """그 관계 종류의 속성 정의 — 선의 값을 검증하는 모양."""
    return list(
        db.scalars(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "relation", PropertyDef.owner_id == kind.id
            )
        )
    )


def require_can_add(
    db: Session,
    kind: RelationType,
    src: ObjectInstance,
    dst: ObjectInstance,
    *,
    properties: dict[str, Any],
    valid_from: date | None,
    valid_to: date | None,
) -> dict[str, Any]:
    """`add_edge` 의 검사만 — **넣지 않는다**(제안 확정의 미리 보기가 같은 것을 본다). 검증한
    속성을 돌려준다. 차례는 화면의 「관계 추가」 그대로: 끝 타입 → 같은 선(기간 겹침) → 개수
    제약 → 순환 → 속성."""
    require_endpoints_allowed(db, kind, src, dst)
    # 같은 선 — 꺼진 종류는 하나, 켜진 종류는 기간이 겹치지 않게(ADR 0039). 개수 제약보다
    # 먼저 본다: 같은 선이 이미 있으면 「하나만 맺을 수 있다」 보다 「이미 이어져 있다」 가
    # 맞는 말이다.
    edges.require_no_overlap(
        db,
        src_id=src.id,
        dst_id=dst.id,
        relation=kind.slug,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    require_cardinality(db, kind, src.id, dst.id, valid_from=valid_from, valid_to=valid_to)
    require_no_cycle(db, kind, src.id, dst.id)
    # 관계 종류가 정한 속성 모양대로 검증한다 — **타입의 속성과 같은 규칙**이다.
    return validate_properties(relation_defs(db, kind), properties, apply_defaults=True)


def add_edge(
    db: Session,
    user: User,
    kind: RelationType,
    src: ObjectInstance,
    dst: ObjectInstance,
    *,
    properties: dict[str, Any],
    evidence_note: str,
    valid_from: date | None,
    valid_to: date | None,
    reason: str | None = None,
) -> ObjectRelation:
    """객체 끼리 선 하나를 맺는다 — **검사 · 넣기 · 감사가 한 길**이다. 커밋하지 않는다.

    부르는 쪽이 먼저 본 것: 출발점을 고칠 수 있나 · 쓰는 종류인가(`relation_type`) · 허브가
    관리하는 종류가 아닌가 · 기간이 그 종류에 맞나(`edges.checked_period` — 받은 값을 그대로
    넘긴다) · 도착점이 보이고 살아 있나. 여기서는 `require_can_add` 의 검사 → 넣기(DB 제약 ·
    계보 고정 트리거) → 감사(`object.relation.add`, 양 끝 id — 객체 이력의 근거).
    """
    checked = require_can_add(
        db, kind, src, dst, properties=properties, valid_from=valid_from, valid_to=valid_to
    )
    edge = ObjectRelation(
        src_object_id=src.id,
        dst_object_id=dst.id,
        relation=kind.slug,
        properties=checked,
        evidence_note=evidence_note,
        created_by_id=user.id,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    db.add(edge)
    # 같은 순간에 들어온 같은 선은 DB 가 막는다 — 그 거절도 사람의 말로.
    edges.flush(db)
    audit.record(
        db,
        action="object.relation.add",
        actor=user,
        target_table="object_relations",
        target_id=edge.id,
        target_label=f"{src.label} -{kind.slug}-> {dst.label}",
        workspace_id=src.owner_workspace_id,
        # 양 끝을 id 로 남긴다 — 객체의 이력이 「이 관계가 나에게 걸린 것」 을 찾는 근거.
        changes=audit.relation_endpoints(edge, src.label, dst.label),
        reason=reason,
    )
    return edge
