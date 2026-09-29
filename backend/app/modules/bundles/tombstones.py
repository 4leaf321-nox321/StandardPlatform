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

## 누가 할 수 있나

**허브에서 받은 묶음**(`source`)만 여기 온다 — 그 묶음은 시스템 관리자만 넣는다
(`bundles/services.py` 가 앞에서 막는다). 그리고 줄마다 **그 객체를 고칠 수 있는지**
다시 본다: 앞의 문이 언젠가 헐거워져도 남의 부서 것이 조용히 사용 중지되지 않게.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.bundles.schemas import (
    ObjectTombstoneIn,
    RelationTombstoneIn,
    TombstonesIn,
)
from app.modules.objects import bulk, lifecycle
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
    for grave in data.objects:
        index += 1
        object_type = types.get(grave.type_slug)
        if object_type is None:
            plan.rows.append(_no_type(index, grave.key, grave.type_slug))
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
    return plan


def _no_type(index: int, label: str, slug: str) -> bulk.RowPlan:
    return bulk.RowPlan(
        row=index, action="error", label=label, message=f"타입을 찾을 수 없습니다: {slug}"
    )


def _find(db: Session, object_type: ObjectType, key: str) -> ObjectInstance | None:
    """식별자(없으면 이름)로 — 허브가 보낸 값은 그 둘 중 하나다. 이름이 여럿에 맞으면
    못 찾은 것으로 본다: 엉뚱한 것을 사용 중지로 만드는 것보다 낫다."""
    stmt = select(ObjectInstance).where(
        ObjectInstance.type_id == object_type.id, ObjectInstance.deleted_at.is_(None)
    )
    found = db.scalar(stmt.where(ObjectInstance.key == key))
    if found is not None:
        return found
    rows = list(db.scalars(stmt.where(ObjectInstance.label == key).limit(2)))
    return rows[0] if len(rows) == 1 else None


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
    row = _find(db, object_type, grave.key)
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
    src = _find(db, object_type, grave.src)
    if src is None:
        return bulk.RowPlan(
            row=index, action="unchanged", label=label, message="출발점이 이미 없습니다"
        )
    refused = _refusal(db, user, object_type, src, source)
    if refused:
        return bulk.RowPlan(row=index, action="error", label=label, message=refused)
    found: ObjectRelation | None = None
    for one in db.scalars(
        select(ObjectRelation).where(
            ObjectRelation.src_object_id == src.id,
            ObjectRelation.relation == grave.relation,
        )
    ):
        end = db.get(ObjectInstance, one.dst_object_id)
        if end is not None and grave.dst in (end.key, end.label):
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
        db.delete(found)
        db.flush()
    return bulk.RowPlan(
        row=index,
        action="unlink",
        label=label,
        object_id=found.id,
        message="허브에서 끊겼습니다",
    )
