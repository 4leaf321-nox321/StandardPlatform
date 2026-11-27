"""묶음의 **무덤** — 허브에서 사라진 것을 받는 쪽에 알린다.

## 왜 있나

내보내기는 살아 있는 것만 보냈다. 그래서 허브에서 **지운 객체와 끊긴 선이 쌍둥이에 살아
남았고**, 두 설치는 그때부터 조용히 갈렸다. 코어 API 는 같은 사실을 이미 `merged_into` ·
`deleted` 로 말하고 있었으니, **두 길이 다르게 움직인 것**이 더 나쁜 쪽이다.

## 받는 쪽은 지우지 않는다

- 객체는 **사용 중지**(`deprecated`)로 둔다. 이쪽에서 그것을 가리키는 관계 · 첨부 · 문서가
  있고, 지우면 그것들이 끊어진다. 「그만 쓴다」 를 상태로 말하는 것이 이 플랫폼의 규칙이다.
- 합쳐져 사라진 것은 **합친다**(`merge_into`) — 그래야 이쪽의 참조 · 관계가 이긴 쪽으로
  옮겨진다. 이긴 쪽이 이 설치에 없으면 그 줄은 오류다(옮길 자리가 없다).
- 선은 **끊는다.** 선은 상태가 아니라 있음/없음이다.

계획은 다른 단계와 같은 모양(`bulk.Plan`)으로 나간다 — 한 표로 읽는다.

## 원천이 잠근 타입이면 원천이 지울 수 있다(ADR 0090)

시험으로 보낸 것 · 잘못 보낸 것은 사용 중지로는 목록에 남고, 잠긴 타입이라 화면에서도 못
지웠다(OBJECTS-87). 그래서 무덤 한 줄에 `action: "delete"` 를 적으면 **화면의 삭제와 같이**
지운다 — 그 타입을 잠근 원천(`managed_by == source`)의 묶음일 때만. 남이 잠근 타입 · 사람이
만든 타입은 지금처럼 화면의 일이다.

- 가리키는 것이 있으면 막는다(화면의 `mode=block`). 같은 묶음이 끊는 선 · 함께 지우는 객체는
  빼고 센다(`undo.blockers` 와 같은 잣대) — 그래서 지우는 줄은 **선 무덤 뒤에** 계획한다.
- 함께 지우는 것끼리 서로 가리키면, 밖에서 막힌 것이 빠질 때까지 다시 센다 — 하나만 막혔는데
  나머지가 지워져 그것이 지운 것을 가리키는 일이 없게.
- 계획의 동작은 `delete`(「지움」)다 — 사용 중지(`deprecate`)와 이름 · 건수가 따로 선다.

## 누가 할 수 있나

**허브에서 받은 묶음**(`source`)만 여기 온다 — 그 묶음은 시스템 관리자만 넣는다
(`bundles/services.py` 가 앞에서 막는다). 그리고 줄마다 **그 객체를 고칠 수 있는지**
다시 본다: 앞의 문이 언젠가 헐거워져도 남의 부서 것이 조용히 사용 중지되지 않게.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.bundles import journal, undo
from app.modules.bundles.schemas import (
    ObjectTombstoneIn,
    RelationTombstoneIn,
    TombstonesIn,
)
from app.modules.objects import bulk, edges, lifecycle
from app.modules.objects.models import ObjectInstance, ObjectRelation
from app.modules.ontology import managed
from app.modules.ontology.models import ObjectType
from app.shared import audit
from app.shared.errors import AppError, code
from app.shared.permissions import require_owner_edit


def _refusal(
    db: Session, user: User, object_type: ObjectType, row: ObjectInstance, source: str
) -> str:
    """이 객체를 여기서 고칠 수 있나 — 못 하면 그 까닭(빈 글이면 된다).

    **줄마다 본다.** 허브 묶음은 시스템 관리자만 넣지만, 그 문 하나에 기대면 문이 헐거워진
    날 남의 부서 객체가 조용히 사용 중지된다 — 그 사실은 아무 데도 안 적힌다.
    """
    refused = managed.objects_refusal(object_type, source=source, what="고치지")
    if refused:
        return refused
    try:
        require_owner_edit(
            db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 12)
        )
    except AppError as denied:
        return denied.message
    return ""


def run(
    db: Session,
    user: User,
    data: TombstonesIn,
    *,
    source: str = "",
    apply: bool = False,
) -> bulk.Plan:
    """무덤을 계획으로 — `apply` 면 그대로 적용한다.

    **여기서 커밋하지 않는다.** 묶음은 한 트랜잭션이라, 커밋하면 뒤 단계가 실패해도 이것만
    남는다.
    """
    plan = bulk.Plan()
    types = {one.slug: one for one in db.scalars(select(ObjectType))}
    index = 0
    deleting: list[tuple[int, ObjectType, ObjectTombstoneIn]] = []
    for grave in data.objects:
        index += 1
        object_type = types.get(grave.type_slug)
        if object_type is None:
            plan.rows.append(_no_type(index, grave.key, grave.type_slug))
            continue
        if grave.action == "delete":
            # **선 무덤 뒤에** — 같은 묶음이 끊는 선은 가리키는 것으로 세지 않는다.
            deleting.append((index, object_type, grave))
            continue
        plan.rows.append(
            _object_row(db, user, object_type, grave, index, source=source, apply=apply)
        )
    for edge in data.relations:
        index += 1
        object_type = types.get(edge.type_slug)
        label = f"{edge.src} -{edge.relation}-> {edge.dst}"
        if object_type is None:
            plan.rows.append(_no_type(index, label, edge.type_slug))
            continue
        plan.rows.append(
            _relation_row(db, user, object_type, edge, index, source=source, apply=apply)
        )
    if deleting:
        cut = {one.object_id for one in plan.rows if one.action == "unlink" and one.object_id}
        plan.rows.extend(_delete_rows(db, user, deleting, cut, source=source, apply=apply))
        plan.rows.sort(key=lambda one: one.row)
    return plan


def _no_type(index: int, label: str, slug: str) -> bulk.RowPlan:
    return bulk.RowPlan(
        row=index, action="error", label=label, message=f"타입을 찾을 수 없습니다: {slug}"
    )


def _lookup(
    db: Session, object_type: ObjectType, key: str
) -> tuple[ObjectInstance | None, str]:
    """식별자(없으면 이름)로 — 허브가 보낸 값은 그 둘 중 하나다. (찾은 것, 못 고른 까닭).
    이름이 여럿에 맞으면 못 찾은 것으로 본다: 엉뚱한 것을 사용 중지로 만드는 것보다 낫다.
    **식별자가 여럿에 맞으면**(범위가 부서인 타입은 부서마다 같은 식별자를 쓴다) 줄 오류다 —
    전에는 그중 아무것이나 집어 다른 부서의 객체를 지울 수 있었다."""
    stmt = select(ObjectInstance).where(
        ObjectInstance.type_id == object_type.id, ObjectInstance.deleted_at.is_(None)
    )
    by_key = list(db.scalars(stmt.where(ObjectInstance.key == key).limit(2)))
    if len(by_key) > 1:
        return None, (
            f"식별자 {key} 인 {object_type.slug} 가 부서 여럿에 있어 어느 것인지 모릅니다 — "
            "받는 쪽에서 화면으로 처리합니다"
        )
    if by_key:
        return by_key[0], ""
    rows = list(db.scalars(stmt.where(ObjectInstance.label == key).limit(2)))
    return (rows[0] if len(rows) == 1 else None), ""


def _find(db: Session, object_type: ObjectType, key: str) -> ObjectInstance | None:
    return _lookup(db, object_type, key)[0]


def _object_row(
    db: Session,
    user: User,
    object_type: ObjectType,
    grave: ObjectTombstoneIn,
    index: int,
    *,
    source: str,
    apply: bool,
) -> bulk.RowPlan:
    row, unsure = _lookup(db, object_type, grave.key)
    if unsure:
        return bulk.RowPlan(row=index, action="error", label=grave.key, message=unsure)
    if row is None:
        # 이미 없다 — 애초에 안 받았거나 지난번에 처리했다.
        return bulk.RowPlan(
            row=index, action="unchanged", label=grave.key, message="이미 없습니다"
        )
    refused = _refusal(db, user, object_type, row, source)
    if refused:
        return bulk.RowPlan(row=index, action="error", label=grave.key, message=refused)
    if grave.merged_into:
        winner = _find(db, object_type, grave.merged_into)
        if winner is None:
            return bulk.RowPlan(
                row=index,
                action="error",
                label=grave.key,
                message=f"합칠 자리를 찾을 수 없습니다: {grave.merged_into}. "
                "이긴 쪽을 먼저 받으세요 — 같은 묶음의 객체 단계가 그것을 넣습니다.",
            )
        if apply:
            try:
                lifecycle.merge_into(db, user, row, object_type, winner)
            except AppError as caught:
                return bulk.RowPlan(
                    row=index, action="error", label=grave.key, message=caught.message
                )
        return bulk.RowPlan(
            row=index,
            action="merge",
            label=grave.key,
            object_id=row.id,
            message=f"{grave.merged_into} 에 합칩니다",
        )
    if row.status == "deprecated":
        return bulk.RowPlan(
            row=index, action="unchanged", label=grave.key, message="이미 사용 중지입니다"
        )
    if apply:
        was = row.status
        row.status = "deprecated"
        journal.changed(
            db,
            "objects",
            row.id,
            before={"status": was},
            after={"status": "deprecated"},
            label=f"{object_type.slug}:{row.label}",
        )
        db.flush()
        audit.record(
            db,
            action="object.update",
            actor=user,
            target_table="objects",
            target_id=row.id,
            target_label=f"{object_type.slug}:{row.label}",
            workspace_id=row.owner_workspace_id,
            changes={"status": {"before": was, "after": "deprecated"}},
            reason="허브에서 사라짐 — 지우지 않고 사용 중지",
        )
    return bulk.RowPlan(
        row=index,
        action="deprecate",
        label=grave.key,
        object_id=row.id,
        message="허브에서 사라져 사용 중지로 둡니다",
    )


@dataclass
class _Doomed:
    index: int
    object_type: ObjectType
    grave: ObjectTombstoneIn
    row: ObjectInstance


def _delete_rows(
    db: Session,
    user: User,
    deleting: list[tuple[int, ObjectType, ObjectTombstoneIn]],
    cut: set[uuid.UUID],
    *,
    source: str,
    apply: bool,
) -> list[bulk.RowPlan]:
    """원천이 **지우는** 줄들(`action: "delete"`) — 계획 · 적용이 같은 셈을 한다."""
    out: list[bulk.RowPlan] = []
    candidates: dict[uuid.UUID, _Doomed] = {}
    for index, object_type, grave in deleting:
        row, unsure = _lookup(db, object_type, grave.key)
        if unsure:
            out.append(
                bulk.RowPlan(row=index, action="error", label=grave.key, message=unsure)
            )
            continue
        if row is None:
            out.append(
                bulk.RowPlan(
                    row=index, action="unchanged", label=grave.key, message="이미 없습니다"
                )
            )
            continue
        refused = _refusal(db, user, object_type, row, source) or _delete_refusal(
            object_type, grave, source
        )
        if refused:
            out.append(
                bulk.RowPlan(row=index, action="error", label=grave.key, message=refused)
            )
            continue
        if row.id in candidates:
            # 같은 객체를 지우는 줄이 앞에 있다 — 전에는 이 줄이 계획에서 말없이 빠졌다.
            out.append(
                bulk.RowPlan(
                    row=index,
                    action="unchanged",
                    label=grave.key,
                    object_id=row.id,
                    message=f"같은 객체를 {candidates[row.id].index}번 줄이 지웁니다",
                )
            )
            continue
        candidates[row.id] = _Doomed(index, object_type, grave, row)
    # 함께 지우는 것끼리는 서로 막지 않는다 — 그러나 **밖에서 막힌 것은 빠지고**, 그것이
    # 가리키는 것은 다시 센다(빠진 것이 지운 것을 가리키게 두지 않는다).
    doomed = set(candidates)
    while True:
        blocked = {
            one for one in doomed if undo.blockers(db, candidates[one].row, doomed, cut)
        }
        if not blocked:
            break
        doomed -= blocked
    for object_id, one in candidates.items():
        if object_id not in doomed:
            why = undo.blockers(db, one.row, doomed, cut)
            out.append(
                bulk.RowPlan(
                    row=one.index,
                    action="error",
                    label=one.grave.key,
                    object_id=object_id,
                    message=f"참조하는 항목({why})이 있어 삭제하지 않습니다 — 같은 묶음에 그 "
                    "관계의 해제 · 그 객체의 삭제를 함께 포함하거나, 화면에서 정리한 뒤 다시 "
                    "보내십시오.",
                )
            )
            continue
        was = one.row.status
        if apply:
            lifecycle._soft_delete(
                db,
                user,
                one.row,
                one.object_type,
                reason=f"원천({source})의 요청으로 삭제 — 묶음의 삭제 표식(action=delete)",
            )
            db.flush()
        out.append(
            bulk.RowPlan(
                row=one.index,
                action="delete",
                label=one.grave.key,
                object_id=object_id,
                message=(
                    "사용 중지 상태인 객체를 원천의 요청으로 삭제합니다"
                    if was == "deprecated"
                    else "원천의 요청으로 삭제합니다 — 사용 중지가 아니라 삭제입니다"
                )
                + "(이력 · 감사 기록은 남고, 복원할 수 없습니다)",
            )
        )
    return out


def _delete_refusal(object_type: ObjectType, grave: ObjectTombstoneIn, source: str) -> str:
    """원천이 **지울** 수 있나 — 그 타입을 잠근 원천의 묶음일 때만(ADR 0090)."""
    if grave.merged_into:
        return (
            "병합(merged_into)과 삭제(action=delete)를 함께 지정했습니다 — 병합된 항목이면 "
            "merged_into 만, 삭제할 항목이면 action 만 지정하십시오."
        )
    if managed.owner_of(object_type) != source or not source:
        return (
            f"{object_type.label}은(는) 이 원천({source})이 관리하는 타입이 아니라 원천의 "
            "요청으로 삭제하지 않습니다 — 사용 중지(action 생략)는 가능합니다. 삭제는 "
            "화면에서 하십시오."
        )
    return ""


def _relation_row(
    db: Session,
    user: User,
    object_type: ObjectType,
    grave: RelationTombstoneIn,
    index: int,
    *,
    source: str,
    apply: bool,
) -> bulk.RowPlan:
    label = f"{grave.src} -{grave.relation}-> {grave.dst}"
    src, unsure = _lookup(db, object_type, grave.src)
    if unsure:
        return bulk.RowPlan(row=index, action="error", label=label, message=unsure)
    if src is None:
        return bulk.RowPlan(
            row=index, action="unchanged", label=label, message="출발점이 이미 없습니다"
        )
    refused = _refusal(db, user, object_type, src, source)
    if refused:
        return bulk.RowPlan(row=index, action="error", label=label, message=refused)
    found: ObjectRelation | None = None
    # 도착 타입을 적었으면 그 타입의 것만 — 끝이 인터페이스면 같은 식별자가 두 타입에 있을 수
    # 있다(이 설치에 없는 타입 이름이면 버린다).
    dst_type = (
        db.scalar(select(ObjectType.id).where(ObjectType.slug == grave.dst_type))
        if grave.dst_type
        else None
    )
    # 어느 선인가 — 기간을 적었으면 그 기간의 선, 안 적었으면(코어 창구 · 예전 묶음) 오늘
    # 유효한 선(ADR 0039). 지난 · 앞으로의 선은 「지금 끊겼다」 는 말에 들지 않는다.
    which = (
        and_(
            ObjectRelation.valid_from.is_(None)
            if grave.valid_from is None
            else ObjectRelation.valid_from == grave.valid_from,
            ObjectRelation.valid_to.is_(None)
            if grave.valid_to is None
            else ObjectRelation.valid_to == grave.valid_to,
        )
        if grave.period
        else edges.alive(ObjectRelation, edges.today())
    )
    for one in db.scalars(
        select(ObjectRelation).where(
            ObjectRelation.src_object_id == src.id,
            ObjectRelation.relation == grave.relation,
            which,
        )
    ):
        end = db.get(ObjectInstance, one.dst_object_id)
        if end is None or (dst_type is not None and end.type_id != dst_type):
            continue
        if grave.dst in (end.key, end.label):
            found = one
            break
    if found is None:
        return bulk.RowPlan(
            row=index, action="unchanged", label=label, message="이미 끊겨 있습니다"
        )
    if apply:
        audit.record(
            db,
            action="object.relation.remove",
            actor=user,
            target_table="object_relations",
            target_id=found.id,
            target_label=label,
            changes={"reason": "허브에서 끊김"},
            reason="묶음 가져오기 — 허브에서 끊김",
        )
        journal.removed(
            db, "object_relations", found.id, before=bulk._edge_state(found), label=label
        )
        db.delete(found)
        db.flush()
    return bulk.RowPlan(
        row=index,
        action="unlink",
        label=label,
        object_id=found.id,
        message="허브에서 끊겼습니다",
    )
