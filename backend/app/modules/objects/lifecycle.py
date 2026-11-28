"""지우기와 합치기 — **누르기 전에 무엇이 걸렸는지 안다.**

객체를 지우면 그것을 가리키던 참조 속성은 uuid 만 남고, 화면에는 뜻 모를 긴 문자열이
뜬다. 그 손실은 지운 사람 눈에 안 보인다 — 다른 객체의 화면에서 드러난다. 그래서
지우기 전에 「이것을 가리키는 것」 을 세어 보여 주고, 걸린 것이 있으면 사람이 고른다:

    block    그대로 둔다 — 먼저 끊거나 고치러 간다 (기본)
    detach   참조를 비우고 관계를 끊고 지운다 — 가리키던 객체마다 감사 기록이 남는다
    merge    다른 객체에 합치고 지운다 — 참조·관계(제안된 관계도)가 이긴 쪽으로 옮겨
             가고, 지는 쪽은 `merged_into` 로 남아 옛 링크가 새 것으로 간다

## 보이지 않는 것도 센다

남의 부서 객체가 이것을 가리키고 있으면 그 사람은 그것을 볼 수 없다. 그래도 **수는
말한다** — 안 말하면 「아무것도 안 걸렸다」 로 읽고 지우고, 남의 부서 화면이 깨진다.
무엇인지는 안 말한다(없는 것과 안 보이는 것을 같은 말로 답하는 규칙).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.files import services as files_services
from app.modules.objects import aliases, links, proposals, rewrite, system
from app.modules.objects import edges as edge_rule
from app.modules.objects import relations as rel
from app.modules.objects.models import ObjectInstance, ObjectRef, ObjectRelation
from app.modules.ontology import interfaces
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.shared import audit
from app.shared.errors import AppError, Conflict, NotFound, code
from app.shared.permissions import require_owner_edit, visible_owner_clause


@dataclass
class RefHit:
    """이 객체를 **속성으로** 가리키는 객체 하나."""

    object_id: uuid.UUID
    label: str
    key: str | None
    type_slug: str
    type_label: str
    property_key: str
    property_label: str


@dataclass
class RelationHit:
    relation_id: uuid.UUID
    relation: str
    outgoing: bool
    other_id: uuid.UUID
    other_label: str
    other_type_slug: str
    valid_from: date | None = None
    valid_to: date | None = None
    """유효 기간(ADR 0039) — 지우면 기간이 지난 선 · 앞으로의 선도 함께 끊긴다."""


#: 「가리키는 것」 목록에 싣는 수 — 칸마다. 인기 모델은 10만 건이 가리킨다 — 다 실을 화면이
#: 없고, 실으면 응답이 멈춘다(실측). 넘는 것은 수로 말한다(`more_property_refs`).
REF_LIST_CAP = 200


@dataclass
class References:
    property_refs: list[RefHit] = field(default_factory=list)
    relations: list[RelationHit] = field(default_factory=list)
    hidden_property_refs: int = 0
    """볼 수 없는 부서의 참조 수. 수만 말한다."""
    hidden_relations: int = 0
    more_property_refs: int = 0
    """보이지만 목록에 다 싣지 않은 참조 수(칸마다 `REF_LIST_CAP` 넘는 것)."""

    @property
    def total(self) -> int:
        return (
            len(self.property_refs)
            + self.more_property_refs
            + len(self.relations)
            + self.hidden_property_refs
            + self.hidden_relations
        )


def ref_defs(db: Session, type_slug: str) -> list[tuple[ObjectType, PropertyDef]]:
    """이 타입을 가리키는 `object_ref` 속성 정의 전부.

    어느 타입의 어느 칸이 나를 가리킬 수 있나. 대상이 이 타입이 구현한 인터페이스(상위까지)인
    칸도 든다 — 「설비」 를 가리키는 칸은 시험장비도 가리킨다. 빼면 지우기 전 확인이 「걸린 것
    없음」 이라 하고, 지운 뒤 그 칸은 사라진 것을 가리킨다.
    """
    defs = list(
        db.scalars(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "type",
                PropertyDef.data_type == "object_ref",
                PropertyDef.ref_type_slug.in_(interfaces.load_ends(db).reach_of(type_slug)),
            )
        )
    )
    if not defs:
        return []
    types = {row.id: row for row in db.scalars(select(ObjectType))}
    return [(types[d.owner_id], d) for d in defs if d.owner_id in types]


def _pointing(definition: PropertyDef, target_id: uuid.UUID) -> Any:
    """이 칸에 target 을 담은 살아 있는 객체들 — 참조 색인으로(ADR 0010). 단일값 · 다중값
    모두."""
    return (
        select(ObjectInstance)
        .join(ObjectRef, ObjectRef.src_id == ObjectInstance.id)
        .where(
            ObjectRef.dst_id == target_id,
            ObjectRef.src_type_id == definition.owner_id,
            ObjectRef.key == definition.key,
        )
    )


def references_of(
    db: Session, user: User, row: ObjectInstance, object_type: ObjectType
) -> References:
    """이 객체를 가리키는 것 전부 — 속성 참조와 관계."""
    out = References()
    for owner_type, definition in ref_defs(db, object_type.slug):
        pointing = _pointing(definition, row.id)
        seen = visible_owner_clause(user, ObjectInstance.owner_workspace_id)
        everyone = int(db.scalar(select(func.count()).select_from(pointing.subquery())) or 0)
        if not everyone:
            continue
        visible = int(
            db.scalar(select(func.count()).select_from(pointing.where(seen).subquery())) or 0
        )
        listed = list(
            db.scalars(pointing.where(seen).order_by(ObjectInstance.label).limit(REF_LIST_CAP))
        )
        for other in listed:
            out.property_refs.append(
                RefHit(
                    object_id=other.id,
                    label=other.label,
                    key=other.key,
                    type_slug=owner_type.slug,
                    type_label=owner_type.label,
                    property_key=definition.key,
                    property_label=definition.label,
                )
            )
        out.more_property_refs += visible - len(listed)
        out.hidden_property_refs += everyone - visible

    types = {one.id: one for one in db.scalars(select(ObjectType))}
    types_by_slug = {one.slug: one for one in types.values()}
    edges = list(
        db.scalars(
            select(ObjectRelation).where(
                (ObjectRelation.src_object_id == row.id)
                | (ObjectRelation.dst_object_id == row.id)
            )
        )
    )
    for edge in edges:
        outgoing = edge.src_object_id == row.id
        other_id = edge.dst_object_id if outgoing else edge.src_object_id
        end = db.get(ObjectInstance, other_id)
        if end is None or end.deleted_at is not None:
            continue
        if _visible(db, user, end):
            out.relations.append(
                RelationHit(
                    relation_id=edge.id,
                    relation=edge.relation,
                    outgoing=outgoing,
                    other_id=end.id,
                    other_label=end.label,
                    other_type_slug=types[end.type_id].slug if end.type_id in types else "",
                    valid_from=edge.valid_from,
                    valid_to=edge.valid_to,
                )
            )
        else:
            out.hidden_relations += 1

    # 원 표(system)와 이은 선도 「가리키는 것」 이다 — 안 세면 「담당 부서」 선이
    # 걸린 객체가 「아무것도 안 걸렸다」 로 지워진다.
    for link in system.links_of(db, row.id):
        far = system.other_end(db, user, link, row.id, types_by_slug)
        if far is None:
            out.hidden_relations += 1
            continue
        out.relations.append(
            RelationHit(
                relation_id=link.id,
                relation=link.relation,
                outgoing=link.src_id == row.id,
                other_id=far.id,
                other_label=far.label,
                other_type_slug=far.type_slug,
            )
        )
    return out


def _visible(db: Session, user: User, row: ObjectInstance) -> bool:
    return (
        db.scalar(
            select(ObjectInstance.id).where(
                ObjectInstance.id == row.id,
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
        is not None
    )


def _soft_delete(
    db: Session, user: User, row: ObjectInstance, object_type: ObjectType, *, reason: str
) -> None:
    row.deleted_at = datetime.now(UTC)
    # 지운 객체의 별칭은 비운다 — 남기면 그 이름을 다른 객체가 못 쓴다. (합치기는 이 전에
    # 이긴 쪽으로 옮겨 두었다.)
    aliases.drop_all(db, row.id)
    # 제안된 관계(ADR 0042)는 남긴다 — 끝이 지워진 제안은 목록 · 검토함에서 빠지고 확정은
    # 거절된다(OBJECTS-0141). 되살리면 다시 뜬다.
    audit.record(
        db,
        action="object.delete",
        actor=user,
        target_table="objects",
        target_id=row.id,
        target_label=f"{object_type.slug}:{row.label}",
        workspace_id=row.owner_workspace_id,
        reason=reason,
    )


def delete_blocking(
    db: Session, user: User, row: ObjectInstance, object_type: ObjectType
) -> None:
    """기본 — 가리키는 것이 있으면 **막고 무엇이 걸렸는지 말한다.**"""
    refs = references_of(db, user, row, object_type)
    if refs.total:
        raise Conflict(
            code("OBJECTS", 50),
            f"{row.label}을 가리키는 것이 {refs.total}개 있어 그대로는 지울 수 없습니다. "
            "참조를 비우고 삭제하거나, 다른 객체에 병합하거나, 먼저 해제하세요.",
            details={
                "property_refs": len(refs.property_refs) + refs.hidden_property_refs,
                "relations": len(refs.relations) + refs.hidden_relations,
            },
        )
    _soft_delete(db, user, row, object_type, reason="")
    db.commit()


#: 가리키는 것이 이보다 많으면 요청 안에서 고치지 않는다 — 작업으로 돈다(ADR 0010). 인기 모델
#: (기록 10만 건)의 병합이 31초였다 — 클라이언트 · 프록시가 먼저 끊는다.
REWRITE_INLINE = 20_000


def _require_inline(
    db: Session, row: ObjectInstance, object_type: ObjectType, *, what: str, job: str
) -> None:
    """가리키는 것이 많으면 작업으로 가라고 말한다(OBJECTS-96, `details.job_path`)."""
    bounded = (
        select(ObjectRef.src_id)
        .where(ObjectRef.dst_id == row.id)
        .limit(REWRITE_INLINE + 1)
        .subquery()
    )
    if int(db.scalar(select(func.count()).select_from(bounded)) or 0) <= REWRITE_INLINE:
        return
    path = f"/api/objects/{object_type.slug}/{row.id}/{job}/job"
    raise Conflict(
        code("OBJECTS", 96),
        f"{row.label}을(를) 가리키는 것이 {REWRITE_INLINE:,}건을 넘어 {what}을(를) 작업으로 "
        f"돌립니다 — POST {path}.",
        details={"limit": REWRITE_INLINE, "job_path": path},
    )


def _rewrite_ref(
    values: dict[str, Any], key: str, old: str, new: str | None
) -> dict[str, Any]:
    """속성 하나에서 old 를 new 로(또는 비움). 다중값이면 자리만 바꾸고 겹치면 하나로."""
    merged = dict(values)
    raw = merged.get(key)
    if isinstance(raw, list):
        items = [item for item in raw if item != old]
        if new is not None and new not in items:
            items.append(new)
        if items:
            merged[key] = items
        else:
            merged.pop(key, None)
    elif raw == old:
        if new is None:
            merged.pop(key, None)
        else:
            merged[key] = new
    return merged


#: 칸 하나에서 가리키는 것을 바꾸는 규칙 — 검토함의 「지워진 것을 가리키는 칸」 이 같은
#: 규칙으로 줄마다 새 값을 만든다(`objects/review.py`). 병합과 다른 규칙이면 여러 값 칸에서
#: 둘이 갈린다.
replace_ref = _rewrite_ref


# --- 기록의 까닭 — 이름을 싣지 않는다 ------------------------------------------------
#
# 지우기 · 합치기는 **가리키던 객체마다** 기록을 남긴다. 그 객체는 남의 부서 것일 수 있고, 그
# 기록은 그 객체의 이력과 그 부서의 변경 이력에 선다. 예전에는 까닭에 지운 · 합친 객체의 이름을
# 박았다(「ACME 을 Altair 에 합침」) — 조회를 부서로 좁힌 설치에서 그 객체를 못 보는 사람에게
# 이름이 샜다(이력은 관계의 끝 이름만 가렸다, 2026-11-28 점검). 이제 까닭은 이름 없는 말로
# 두고, 누구를 지웠고 어디에 합쳤는지는 `changes` 의 표식(`BECAUSE`)에 **id 로** 남긴다 —
# 이력이 보는 사람마다 이름을 붙이고, 못 보는 객체는 「(볼 수 없는 객체)」 로
# 적는다(`history`).

#: 지우면서 가리키던 칸을 비운 기록의 까닭.
DETACHED = "가리키던 객체를 삭제하면서 참조를 비움"
#: 다른 객체에 합치면서 가리키던 칸을 옮긴 기록의 까닭.
MERGED = "가리키던 객체를 다른 객체에 병합"
#: 이긴 쪽의 합침 기록(`object.merge`)의 까닭.
MERGED_INTO = "다른 객체를 이 객체에 병합"
#: 진 쪽의 지움 기록의 까닭 — 「참조를 비우고 지움」 과 짝.
MERGED_AWAY = "다른 객체에 병합하고 삭제"
#: `changes` 에 싣는 표식의 키 — `{"kind": "detach" | "merge", "object": 지운 · 진 쪽 id,
#: "into": 이긴 쪽 id(합침만)}`. `_` 로 시작해 이력의 칸 목록에는 안 선다.
BECAUSE = "_because"


def because(
    kind: str, row: ObjectInstance, into: ObjectInstance | None = None
) -> dict[str, str]:
    """기록에 싣는 표식 — 이름이 아니라 id 로(`BECAUSE`)."""
    marker = {"kind": kind, "object": str(row.id)}
    if into is not None:
        marker["into"] = str(into.id)
    return marker


def _lock_for_rewrite(db: Session, row: ObjectInstance, *also: ObjectInstance) -> None:
    """지우는 · 지는 쪽(`row`), 이긴 쪽(`also`), 그리고 `row` 를 가리키는 객체 전부를 **id
    차례로 한 문장에** 잠그고, `row` · `also` 를 잠근 뒤의 값으로 다시 읽는다.

    합치기는 이긴 쪽의 속성을 읽어 빈 칸을 채운 뒤 통째로 쓴다 — 안 잠그면 그 사이 끝난 이긴
    쪽의 고치기가 소리 없이 옛 값으로 돌아갔다(2026-11-28 점검). 둘만 잠그면 이번에는 차례가
    갈린다 — 가리키는 객체는 뒤에 칸 정의마다 잠그므로(`_rewrite_property_refs`), 자기 타입을
    가리키는 타입에서 일괄 수정(id 차례로 잠금)이 가리키는 객체를 쥔 채 지는 쪽을 기다리면 서로
    기다린다. 그래서 처음에 **전부를 한 차례로** 잡는다 — 뒤의 덩어리 잠금은 이미 쥔 줄이라 안
    기다린다. 가리키는 수는 요청이면 2만 건 아래(`_require_inline`)이고, 작업이어도 어차피 전부
    고쳐 쓸 줄이다. 세지만 하고 id 는 받아 오지 않는다.

    그 사이 다른 합치기 · 지우기가 먼저 끝났으면 없는 것과 같은 말로 거절한다(OBJECTS-11)."""
    # 세션에 실린 변경을 먼저 쓴다 — 아래 다시 읽기(`populate_existing`)가 그것을 덮지 않게.
    db.flush()
    pinned = [row.id, *(one.id for one in also)]
    pointing = select(ObjectRef.src_id).where(ObjectRef.dst_id == row.id)
    locked = (
        select(ObjectInstance.id)
        .where(or_(ObjectInstance.id.in_(pinned), ObjectInstance.id.in_(pointing)))
        .order_by(ObjectInstance.id)
        .with_for_update(of=ObjectInstance)
        .subquery()
    )
    db.execute(select(func.count()).select_from(locked))
    db.scalars(
        select(ObjectInstance)
        .where(ObjectInstance.id.in_(pinned))
        .execution_options(populate_existing=True)
    ).all()
    if any(one.deleted_at is not None for one in (row, *also)):
        raise NotFound(code("OBJECTS", 11), "객체를 찾을 수 없습니다.")


def _rewrite_property_refs(
    db: Session,
    user: User,
    row: ObjectInstance,
    object_type: ObjectType,
    *,
    new_id: uuid.UUID | None,
    reason: str,
    marker: dict[str, str],
) -> int:
    """이 객체를 가리키는 모든 칸을 고친다 — **보이지 않는 것까지.** 반쯤 고치면 남의
    부서 화면에만 uuid 가 남고, 그것은 이 사람이 알 수 없다.

    기록마다 `marker`(`because`)를 싣는다 — 까닭(`reason`)에는 이름이 없다(위 「기록의 까닭」).

    인기 모델은 기록 10만 건이 가리킨다 — 덩어리마다 그 칸만 읽어 잠그고, 문장 하나로 그 칸만
    고치고, 이력은 바뀐 칸만 덩어리째 남긴다(`objects.rewrite`). 바깥 알림은 합치기 · 지우기
    한 줄로 간다. 예전에는 가리키는 객체를 하나씩 실어 고쳤다."""
    # 이 세션에 실린 변경을 먼저 쓴다 — 아래는 ORM 을 거치지 않는다.
    db.flush()
    old = str(row.id)
    new = None if new_id is None else str(new_id)
    touched = 0
    for owner_type, definition in ref_defs(db, object_type.slug):
        last: uuid.UUID | None = None
        while True:
            stmt = (
                select(
                    ObjectInstance.id,
                    ObjectInstance.label,
                    ObjectInstance.owner_workspace_id,
                    ObjectInstance.properties[definition.key],
                )
                .join(ObjectRef, ObjectRef.src_id == ObjectInstance.id)
                .where(
                    ObjectRef.dst_id == row.id,
                    ObjectRef.src_type_id == definition.owner_id,
                    ObjectRef.key == definition.key,
                )
                .order_by(ObjectInstance.id)
                .limit(rewrite.CHUNK)
                .with_for_update(of=ObjectInstance)
            )
            if last is not None:
                stmt = stmt.where(ObjectInstance.id > last)
            found = [(one[0], one[1], one[2], one[3]) for one in db.execute(stmt)]
            if not found:
                break
            writes: list[tuple[uuid.UUID, bool, Any]] = []
            history: list[tuple[uuid.UUID, str, uuid.UUID | None, dict[str, Any]]] = []
            for other_id, label, workspace_id, raw in found:
                after = _rewrite_ref({definition.key: raw}, definition.key, old, new)
                writes.append(
                    (other_id, definition.key not in after, after.get(definition.key))
                )
                history.append(
                    (
                        other_id,
                        f"{owner_type.slug}:{label}",
                        workspace_id,
                        {
                            **rewrite.changed_property(definition.key, raw, after),
                            BECAUSE: marker,
                        },
                    )
                )
            rewrite.write_key(db, definition.key, writes)
            audit.record_rows(
                db,
                action="object.update",
                actor=user,
                target_table="objects",
                rows=history,
                reason=reason,
            )
            touched += len(found)
            last = found[-1][0]
    # 세션에 실린 객체(이긴 쪽이 진 쪽을 가리키던 칸 등)는 옛 값을 든다 — 다시 읽게 한다.
    db.expire_all()
    return touched


def delete_detaching(
    db: Session,
    user: User,
    row: ObjectInstance,
    object_type: ObjectType,
    *,
    inline: bool = True,
) -> dict[str, int]:
    """참조를 비우고 관계를 끊고 지운다. **가리키던 객체마다 감사 기록이 남는다** —
    그 객체의 화면에서 「왜 이 칸이 비었지」 를 물으면 답이 있어야 한다."""
    if inline:
        _require_inline(db, row, object_type, what="참조를 비우고 지우기", job="detach")
    _lock_for_rewrite(db, row)
    # 까닭에 이름을 싣지 않는다 — 가리키던 객체(남의 부서 것일 수 있다)의 이력에 선다.
    reason = DETACHED
    marker = because("detach", row)
    cleared = _rewrite_property_refs(
        db, user, row, object_type, new_id=None, reason=reason, marker=marker
    )
    edges = list(
        db.scalars(
            select(ObjectRelation).where(
                (ObjectRelation.src_object_id == row.id)
                | (ObjectRelation.dst_object_id == row.id)
            )
        )
    )
    for edge in edges:
        audit.record(
            db,
            action="object.relation.remove",
            actor=user,
            target_table="object_relations",
            target_id=edge.id,
            target_label=f"{edge.src_object_id} -{edge.relation}-> {edge.dst_object_id}",
            changes={**audit.relation_endpoints(edge, "", ""), BECAUSE: marker},
            reason=reason,
        )
        db.delete(edge)
    found_links = system.links_of(db, row.id)
    for link in found_links:
        links.remove(
            db,
            user,
            link,
            src_label="",
            dst_label="",
            workspace_id=row.owner_workspace_id,
            reason=reason,
        )
    _soft_delete(db, user, row, object_type, reason="참조를 비우고 지움")
    db.commit()
    return {"property_refs": cleared, "relations": len(edges) + len(found_links)}


def merge_into(
    db: Session,
    user: User,
    row: ObjectInstance,
    object_type: ObjectType,
    target: ObjectInstance,
    *,
    inline: bool = True,
) -> dict[str, int]:
    """row 를 target 에 합친다 — 참조·관계를 옮기고 row 는 `merged_into` 로 남긴다.

    **같은 타입끼리만.** 공급사를 부품에 합치면 그것을 가리키던 「공급사」 칸이 부품을
    가리키게 되고, 그 칸의 정의(ref_type_slug)와 어긋난다.

    옮기다 겹치는 관계(target 에 이미 같은 선이 있거나, 자기 자신으로 가는 선)는
    버린다 — 두 겹으로 남기면 그것은 병합이 아니라 복제다.
    """
    if target.id == row.id:
        raise Conflict(code("OBJECTS", 51), "자기 자신에게는 합칠 수 없습니다.")
    if target.type_id != row.type_id:
        raise Conflict(code("OBJECTS", 52), "같은 타입끼리만 합칩니다.")
    require_owner_edit(
        db, user, target.owner_workspace_id, what="객체", code_value=code("OBJECTS", 53)
    )
    if inline:
        _require_inline(db, row, object_type, what="합치기", job="merge")
    # 둘과 가리키는 것 전부를 id 차례로 잠근다 — 아래가 이긴 쪽의 칸을 읽어 통째로 쓴다.
    _lock_for_rewrite(db, row, target)
    # 까닭에 이름을 싣지 않는다 — 가리키던 객체 · 이긴 쪽의 이력에 서고, 그것을 읽는 사람이
    # 진 쪽(남의 부서 것일 수 있다)을 못 볼 수 있다. 이름은 이력이 표식에서 붙인다.
    reason = MERGED
    marker = because("merge", row, target)
    moved_refs = _rewrite_property_refs(
        db, user, row, object_type, new_id=target.id, reason=reason, marker=marker
    )

    mine = list(
        db.scalars(
            select(ObjectRelation).where(
                (ObjectRelation.src_object_id == row.id)
                | (ObjectRelation.dst_object_id == row.id)
            )
        )
    )
    kinds = {one.slug: one for one in db.scalars(select(RelationType))}
    moved_edges = 0
    dropped_edges = 0
    for edge in mine:
        src = target.id if edge.src_object_id == row.id else edge.src_object_id
        dst = target.id if edge.dst_object_id == row.id else edge.dst_object_id
        # 이긴 쪽에 같은 선이 이미 있나 — 유효 기간이 있으면 **기간이 겹치는** 같은 선만
        # 같은 것이다(ADR 0039). 기간이 갈린 선은 함께 옮겨 이긴 쪽의 이력이 된다.
        duplicate = src == dst or (
            edge_rule.find_overlap(
                db,
                src_id=src,
                dst_id=dst,
                relation=edge.relation,
                valid_from=edge.valid_from,
                valid_to=edge.valid_to,
                skip=edge.id,
            )
            is not None
        )
        if duplicate:
            db.delete(edge)
            dropped_edges += 1
            continue
        # 옮긴 뒤에도 관계 종류의 규칙(카디널리티·순환)이 서야 한다 — 직접 이을 때
        # 거절당할 선을 합치기가 몰래 만들면 그 선은 아무도 설명 못 한다. 안 서면 버린다.
        kind = kinds.get(edge.relation)
        if kind is not None:
            # 이 선은 아직 지는 쪽에 걸려 있어 이긴 쪽 기준의 개수·순환 검사에 안 세어진다.
            try:
                rel.require_cardinality(
                    db,
                    kind,
                    src,
                    dst,
                    valid_from=edge.valid_from,
                    valid_to=edge.valid_to,
                    skip=edge.id,
                )
                rel.require_no_cycle(db, kind, src, dst)
            except AppError:
                db.delete(edge)
                dropped_edges += 1
                continue
        edge.src_object_id = src
        edge.dst_object_id = dst
        moved_edges += 1
    db.flush()

    # 원 표와 이은 선도 이긴 쪽으로 — 겹치거나 개수 제약에 걸리면 버린다(위와 같다).
    for link in system.links_of(db, row.id):
        src_id = target.id if link.src_id == row.id else link.src_id
        dst_id = target.id if link.dst_id == row.id else link.dst_id
        kind = kinds.get(link.relation)
        if links.existing(db, src_id, dst_id, link.relation) is not None:
            db.delete(link)
            dropped_edges += 1
            continue
        if kind is not None:
            try:
                rel.require_cardinality(db, kind, src_id, dst_id)
            except AppError:
                db.delete(link)
                dropped_edges += 1
                continue
        link.src_id = src_id
        link.dst_id = dst_id
        if link.src_type == object_type.slug and src_id == target.id:
            link.src_type = object_type.slug
        moved_edges += 1
    db.flush()

    # 제안된 관계(ADR 0042)도 이긴 쪽으로 — 같은 열린 제안이 되거나 자기 자신으로 가면 버린다.
    proposals_moved, proposals_dropped = proposals.move_on_merge(db, row, target)

    # 지는 쪽의 비어 있는 칸을 이긴 쪽이 채운다 — 값이 있는 칸은 이긴 쪽이 맞다.
    before = dict(target.properties or {})
    filled = dict(before)
    for key, value in (row.properties or {}).items():
        if key not in filled or filled[key] in (None, "", []):
            filled[key] = value
    if filled != before:
        target.properties = filled
    if not target.description and row.description:
        target.description = row.description

    # 지는 쪽의 별칭과 이름을 이긴 쪽에 — 같은 표기로 다시 와도 같은 것으로 풀린다.
    aliases_moved, aliases_dropped = aliases.move(db, row, target)
    # 첨부(사진)도 이긴 쪽으로 — 같은 타입이라 같은 파일 칸이 있다. 안 옮기면 지운 쪽에 남아
    # 아무 화면에서도 안 보인다(ADR 0012).
    attachments_moved = files_services.move_owner(
        db, "objects", row.id, target.id, target.owner_workspace_id
    )

    row.merged_into_id = target.id
    _soft_delete(db, user, row, object_type, reason=MERGED_AWAY)
    audit.record(
        db,
        action="object.merge",
        actor=user,
        target_table="objects",
        target_id=target.id,
        target_label=f"{object_type.slug}:{target.label}",
        workspace_id=target.owner_workspace_id,
        changes={
            "merged_from": str(row.id),
            "merged_from_label": row.label,
            "property_refs": moved_refs,
            "relations_moved": moved_edges,
            "relations_dropped": dropped_edges,
            "aliases_moved": aliases_moved,
            "aliases_dropped": aliases_dropped,
            "attachments_moved": attachments_moved,
            "proposals_moved": proposals_moved,
            "proposals_dropped": proposals_dropped,
            **audit.diff({"properties": before}, {"properties": target.properties or {}}),
            BECAUSE: marker,
        },
        reason=MERGED_INTO,
    )
    db.commit()
    return {
        "property_refs": moved_refs,
        "relations_moved": moved_edges,
        "relations_dropped": dropped_edges,
    }
