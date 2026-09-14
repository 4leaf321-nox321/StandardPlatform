"""여럿을 골라 한 칸 바꾸기 — **계획 먼저, 전부 아니면 무.**

「이 열 건의 담당 부서를 바꿔라」 는 자주 나오는 일이다. 지금까지 그 길은 CSV 로
내려받아 고쳐 다시 올리는 것뿐이었는데, 파일을 왕복하는 동안 **그 사이에 누가 고친
것을 덮어쓴다.** 그리고 열 건을 위해 파일을 여는 일은 아무도 즐겁게 하지 않는다.

## 왜 계획을 먼저 보이나

한 번에 수백 건을 바꾸는 일은 되돌리기가 어렵다(감사 기록으로 한 건씩 되돌릴 수는
있지만, 그것을 수백 번 하는 사람은 없다). 그래서 누르기 전에 **몇 건이 실제로
바뀌는지, 몇 건이 이미 그 값인지, 몇 건은 왜 안 되는지**를 보여 준다.

## 못 고치는 것이 섞여 있으면

남의 부서 것이 골라져 있을 수 있다(목록은 볼 수 있으니까). 그때 **조용히 건너뛰지
않는다** — 건너뛴 것은 「바꿨다」 고 믿은 사람에게 나중에 다른 값으로 나타난다.
행마다 이유를 적고, 적용은 여전히 나머지만 한다.

## 한 번에 한 칸

여러 칸을 동시에 바꾸게 하면 이 화면은 곧 「폼」 이 되고, 그때 실수 한 번의 크기가
수백 배가 된다. 칸 하나씩 고른다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.audit.models import AuditEntry
from app.modules.objects.models import OBJECT_STATUSES, ObjectInstance
from app.modules.objects.services import (
    audit_state,
    properties_of,
    require_refs_exist,
    require_unique_properties,
)
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.ontology.services import validate_properties
from app.shared import audit
from app.shared.errors import AppError, Conflict, NotFound, code
from app.shared.permissions import require_owner_edit, resolve_owner_workspace
from app.shared.text import clean

#: 한 번에 바꿀 수 있는 수. 그 위로는 파일로 하는 편이 낫고, 화면이 계획을 보여 줄
#: 수도 없다(수백 줄짜리 계획은 아무도 안 읽는다).
MAX_ROWS = 500

#: 바꿀 수 있는 고정 칸. **이름·식별자는 없다** — 여럿에게 같은 이름을 붙이는 일은
#: 없고, 식별자는 유일해야 해서 한 번에 같은 값을 넣을 수 없다.
FIXED_FIELDS = {
    "status": "상태",
    "description": "설명",
    "workspace": "소유 부서",
}


#: 감사 기록의 `changes` 안에 **묶음 표식**을 두는 키. `_` 로 시작하는 키는 사람이 읽을
#: 칸이 아니라서 이력·알림이 건너뛴다.
#:
#: 묶음 표식이 따로 필요한 이유: 기록의 diff 는 key·label·status·properties 만 보므로
#: 설명·소유 부서를 바꾼 기록은 **diff 가 비어 있다.** 그것으로는 되돌릴 값을 모른다.
#: 그래서 바꾼 칸 하나의 전·후를 원래 모양 그대로 함께 적는다.
BATCH_KEY = "_batch"


@dataclass
class RowPlan:
    id: uuid.UUID
    label: str
    action: str
    """change · unchanged · error."""
    before: str = ""
    after: str = ""
    message: str = ""


@dataclass
class EditPlan:
    field_label: str = ""
    rows: list[RowPlan] = field(default_factory=list)
    applied: bool = False

    @property
    def counts(self) -> dict[str, int]:
        out = {"change": 0, "unchanged": 0, "error": 0}
        for one in self.rows:
            out[one.action] = out.get(one.action, 0) + 1
        return out

    @property
    def ok(self) -> bool:
        return any(one.action == "change" for one in self.rows)


def _shown(value: Any) -> str:
    if value is None or value == "":
        return "(비어 있음)"
    if isinstance(value, list):
        return ", ".join(str(one) for one in value)
    return str(value)


def plan(
    db: Session,
    user: User,
    object_type: ObjectType,
    *,
    ids: list[uuid.UUID],
    field_name: str,
    value: Any,
    rows: list[ObjectInstance],
) -> EditPlan:
    """무엇이 어떻게 바뀌나. **아무것도 안 바꾼다.**"""
    if len(ids) > MAX_ROWS:
        raise AppError(
            code("OBJECTS", 54),
            f"한 번에 {MAX_ROWS}건까지입니다. 그보다 많으면 파일로 넣는 편이 낫습니다.",
            status=422,
        )
    defs = properties_of(db, object_type.id)
    found = EditPlan(field_label=_field_label(object_type, defs, field_name))
    by_id = {row.id: row for row in rows}
    for object_id in ids:
        row = by_id.get(object_id)
        if row is None:
            # 볼 수 없거나 이미 지워진 것. **조용히 빼지 않는다.**
            found.rows.append(
                RowPlan(
                    id=object_id,
                    label="(없음)",
                    action="error",
                    message="찾을 수 없습니다 — 지워졌거나 볼 수 없는 부서의 것입니다.",
                )
            )
            continue
        found.rows.append(_one(db, user, object_type, defs, row, field_name, value))
    return found


def _field_label(object_type: ObjectType, defs: list[PropertyDef], field_name: str) -> str:
    if field_name in FIXED_FIELDS:
        return FIXED_FIELDS[field_name]
    key = field_name.split(".", 1)[-1]
    found = next((one for one in defs if one.key == key), None)
    if found is None:
        raise AppError(
            code("OBJECTS", 55),
            f"{object_type.label}에 없는 칸입니다: {key}",
            status=422,
        )
    return str(found.label)


def _one(
    db: Session,
    user: User,
    object_type: ObjectType,
    defs: list[PropertyDef],
    row: ObjectInstance,
    field_name: str,
    value: Any,
) -> RowPlan:
    try:
        require_owner_edit(
            db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 16)
        )
    except Exception:
        return RowPlan(
            id=row.id,
            label=row.label,
            action="error",
            message="이 부서의 것은 고칠 수 없습니다.",
        )

    if field_name == "status":
        text = str(value or "")
        if text not in OBJECT_STATUSES:
            return RowPlan(
                id=row.id,
                label=row.label,
                action="error",
                message=f"상태는 {', '.join(OBJECT_STATUSES)} 중 하나입니다.",
            )
        return _diff(row, row.status, text)
    if field_name == "description":
        return _diff(row, row.description, clean(str(value or "")))
    if field_name == "workspace":
        return _workspace(db, user, row, value)

    key = field_name.split(".", 1)[-1]
    merged = {**(row.properties or {}), key: value}
    try:
        cleaned = validate_properties(defs, merged)
        require_refs_exist(db, defs, cleaned)
        require_unique_properties(
            db,
            object_type,
            defs,
            cleaned,
            owner_workspace_id=row.owner_workspace_id,
            exclude_id=row.id,
        )
    except AppError as caught:
        return RowPlan(id=row.id, label=row.label, action="error", message=caught.message)
    return _diff(row, (row.properties or {}).get(key), cleaned.get(key))


def _workspace(db: Session, user: User, row: ObjectInstance, value: Any) -> RowPlan:
    slug = str(value or "").strip()
    try:
        target = (
            resolve_owner_workspace(
                db, user, slug, what="객체", code_value=code("OBJECTS", 17)
            )
            if slug
            else None
        )
    except AppError as caught:
        return RowPlan(id=row.id, label=row.label, action="error", message=caught.message)
    before = row.owner_workspace_id
    if before == target:
        return RowPlan(id=row.id, label=row.label, action="unchanged", before=slug, after=slug)
    return RowPlan(
        id=row.id,
        label=row.label,
        action="change",
        before=_slug_of(db, before),
        after=slug or "(전역)",
    )


def _slug_of(db: Session, workspace_id: uuid.UUID | None) -> str:
    if workspace_id is None:
        return "(전역)"
    from app.modules.workspaces.models import Workspace

    found = db.get(Workspace, workspace_id)
    return found.slug if found else "(지워진 부서)"


def _diff(row: ObjectInstance, before: Any, after: Any) -> RowPlan:
    if before == after:
        return RowPlan(
            id=row.id,
            label=row.label,
            action="unchanged",
            before=_shown(before),
            after=_shown(after),
        )
    return RowPlan(
        id=row.id,
        label=row.label,
        action="change",
        before=_shown(before),
        after=_shown(after),
    )


def apply_to(
    db: Session,
    user: User,
    object_type: ObjectType,
    *,
    field_name: str,
    value: Any,
    rows: list[ObjectInstance],
    planned: EditPlan,
) -> None:
    """계획에서 `change` 인 것만 실제로 바꾼다. **커밋은 부르는 쪽이** — 감사 기록과
    같은 트랜잭션에 있어야 「값은 바뀌었는데 기록은 없는」 상태가 안 생긴다."""
    wanted = {one.id for one in planned.rows if one.action == "change"}
    by_id = {row.id: row for row in rows}
    defs = properties_of(db, object_type.id)
    for object_id in wanted:
        row = by_id.get(object_id)
        if row is None:
            continue
        if field_name == "status":
            row.status = str(value)
        elif field_name == "description":
            row.description = clean(str(value or ""))
        elif field_name == "workspace":
            slug = str(value or "").strip()
            row.owner_workspace_id = (
                resolve_owner_workspace(
                    db, user, slug, what="객체", code_value=code("OBJECTS", 17)
                )
                if slug
                else None
            )
        else:
            key = field_name.split(".", 1)[-1]
            row.properties = validate_properties(defs, {**(row.properties or {}), key: value})


def ids_of(raw: list[uuid.UUID]) -> list[uuid.UUID]:
    """같은 것을 두 번 골라도 한 번만. 순서는 고른 차례 그대로."""
    seen: set[uuid.UUID] = set()
    out: list[uuid.UUID] = []
    for one in raw:
        if one not in seen:
            seen.add(one)
            out.append(one)
    return out


def selectable(object_type: ObjectType, defs: list[PropertyDef]) -> list[dict[str, str]]:
    """고를 수 있는 칸 — 고정 칸과 속성. **파일 칸과 긴 글은 뺀다**: 여럿에게 같은
    긴 글을 넣는 일은 없고, 파일은 한 건씩 올린다."""
    out = [{"field": key, "label": label} for key, label in FIXED_FIELDS.items()]
    out.extend(
        {"field": f"properties.{one.key}", "label": one.label}
        for one in defs
        if one.data_type not in ("file", "text_long")
    )
    return out


def rows_for(db: Session, ids: list[uuid.UUID]) -> list[ObjectInstance]:
    return list(
        db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.id.in_(ids), ObjectInstance.deleted_at.is_(None)
            )
        )
    )


# --- 묶음으로 기록하고 묶음으로 되돌리기 ----------------------------------------------
#
# 한 번에 수백 건을 바꾸는 길이 있는데 되돌리는 길이 한 건씩뿐이면, 실수 한 번은
# 사실상 되돌릴 수 없다(그것을 수백 번 하는 사람은 없다). 그래서 같이 바뀐 것을 한
# 묶음으로 기록하고, 그 묶음을 **계획 먼저** 되돌린다.
#
# **그 뒤에 누가 또 고친 행은 덮어쓰지 않는다.** 묶음이 넣은 값이 아직 그대로인 행만
# 되돌린다 — 아니면 되돌리기가 남의 새 작업을 지운다. 건너뛴 행은 이유를 적는다.


def raw_value(row: ObjectInstance, field_name: str) -> Any:
    """칸 하나의 **저장된 모양 그대로의** 값. 되돌릴 때 비교하고 그대로 넣는다."""
    if field_name == "status":
        return row.status
    if field_name == "description":
        return row.description
    if field_name == "workspace":
        return str(row.owner_workspace_id) if row.owner_workspace_id else None
    return (row.properties or {}).get(field_name.split(".", 1)[-1])


def state_of(row: ObjectInstance) -> dict[str, Any]:
    """감사 diff 가 보는 모양 — 고치는 길 모두와 같은 한 벌(`audit_state`)."""
    return audit_state(row)


def record(
    db: Session,
    user: User,
    object_type: ObjectType,
    *,
    rows: list[ObjectInstance],
    before_state: dict[uuid.UUID, dict[str, Any]],
    before_raw: dict[uuid.UUID, Any],
    changed: set[uuid.UUID],
    field_name: str,
    field_label: str,
    batch_id: uuid.UUID,
    reason: str,
) -> None:
    """바뀐 행마다 기록 하나 — **같은 묶음 번호를 달고.**

    한 줄로 뭉뚱그리면 그 객체의 이력에서 이 변경이 사라지고, 지켜보는 사람에게도
    안 간다. 커밋은 부르는 쪽이 한다.
    """
    for row in rows:
        if row.id not in changed:
            continue
        audit.record(
            db,
            action="object.update",
            actor=user,
            target_table="objects",
            target_id=row.id,
            target_label=f"{object_type.slug}:{row.label}",
            workspace_id=row.owner_workspace_id,
            changes={
                **audit.diff(before_state[row.id], state_of(row)),
                BATCH_KEY: {
                    "id": str(batch_id),
                    "field": field_name,
                    "field_label": field_label,
                    "before": before_raw[row.id],
                    "after": raw_value(row, field_name),
                    "size": len(changed),
                },
            },
            reason=reason,
        )


def batch_entries(db: Session, batch_id: uuid.UUID) -> list[AuditEntry]:
    """이 묶음의 기록 — 행마다 하나, 넣은 차례대로."""
    found = db.scalars(
        select(AuditEntry)
        .where(
            AuditEntry.target_table == "objects",
            AuditEntry.changes[BATCH_KEY]["id"].astext == str(batch_id),
        )
        .order_by(AuditEntry.seq.asc())
    )
    seen: set[uuid.UUID] = set()
    out: list[AuditEntry] = []
    for entry in found:
        if entry.target_id is None or entry.target_id in seen:
            continue
        seen.add(entry.target_id)
        out.append(entry)
    return out


@dataclass
class UndoPlan:
    field_name: str
    plan: EditPlan
    inputs: dict[uuid.UUID, Any] = field(default_factory=dict)
    """행마다 넣을 값 — `_one`·`apply_to` 가 받는 모양(부서는 슬러그)."""


def _raw_shown(db: Session, field_name: str, value: Any) -> str:
    if field_name == "workspace":
        return _slug_of(db, uuid.UUID(value) if value else None)
    return _shown(value)


def undo_plan(
    db: Session,
    user: User,
    object_type: ObjectType,
    *,
    entries: list[AuditEntry],
    rows: list[ObjectInstance],
) -> UndoPlan:
    """무엇이 그때 값으로 돌아가나. **아무것도 안 바꾼다.**"""
    if not entries:
        raise NotFound(
            code("OBJECTS", 84),
            "그 묶음을 찾을 수 없습니다 — 일괄 수정으로 바꾼 기록이 아닙니다.",
        )
    meta = entries[0].changes.get(BATCH_KEY) or {}
    field_name = str(meta.get("field") or "")
    defs = properties_of(db, object_type.id)
    try:
        label = _field_label(object_type, defs, field_name)
    except AppError as caught:
        # 그 사이 속성 정의를 지웠다. 정의가 없는 칸에 값을 되살리면 어느 화면에도 안
        # 나오는 값이 된다.
        raise Conflict(
            code("OBJECTS", 85),
            f"「{meta.get('field_label') or field_name}」 칸이 지금은 정의에 없어 "
            "되돌릴 수 없습니다.",
        ) from caught

    undo = UndoPlan(field_name=field_name, plan=EditPlan(field_label=label))
    by_id = {row.id: row for row in rows}
    for entry in entries:
        target = entry.target_id
        if target is None:  # pragma: no cover - batch_entries 가 거른다
            continue
        row = by_id.get(target)
        if row is None:
            # 볼 수 없는 부서의 것일 수 있다 — **이름을 안 싣는다**(기록의 이름표를
            # 그대로 내보내면 못 보는 사람에게 남의 객체 이름이 샌다).
            undo.plan.rows.append(
                RowPlan(
                    id=target,
                    label="(없음)",
                    action="error",
                    message="찾을 수 없습니다 — 지워졌거나 볼 수 없는 부서의 것입니다.",
                )
            )
            continue
        one = entry.changes.get(BATCH_KEY) or {}
        was, put, now = one.get("before"), one.get("after"), raw_value(row, field_name)
        if now == was:
            shown = _raw_shown(db, field_name, now)
            undo.plan.rows.append(
                RowPlan(
                    id=row.id, label=row.label, action="unchanged", before=shown, after=shown
                )
            )
            continue
        if now != put:
            undo.plan.rows.append(
                RowPlan(
                    id=row.id,
                    label=row.label,
                    action="error",
                    before=_raw_shown(db, field_name, now),
                    after=_raw_shown(db, field_name, was),
                    message="그 뒤에 다시 바뀌었습니다 — 지금 값을 덮어쓰지 않습니다.",
                )
            )
            continue
        value: Any = was
        if field_name == "workspace":
            value = _slug_of(db, uuid.UUID(was)) if was else ""
            if was and value == "(지워진 부서)":
                undo.plan.rows.append(
                    RowPlan(
                        id=row.id,
                        label=row.label,
                        action="error",
                        message="그때의 부서가 지워져 되돌릴 수 없습니다.",
                    )
                )
                continue
        # **저장과 같은 검증을 거친다** — 그 사이 규칙이 생겼거나 가리키던 것이
        # 지워졌으면 그 행만 막고 이유를 말한다.
        undo.inputs[row.id] = value
        undo.plan.rows.append(_one(db, user, object_type, defs, row, field_name, value))
    return undo


def apply_undo(
    db: Session,
    user: User,
    object_type: ObjectType,
    *,
    rows: list[ObjectInstance],
    undo: UndoPlan,
) -> None:
    """계획에서 `change` 인 행만 그때 값으로. 커밋은 부르는 쪽이."""
    by_id = {row.id: row for row in rows}
    for one in undo.plan.rows:
        row = by_id.get(one.id)
        if one.action != "change" or row is None:
            continue
        apply_to(
            db,
            user,
            object_type,
            field_name=undo.field_name,
            value=undo.inputs[one.id],
            rows=[row],
            planned=EditPlan(rows=[one]),
        )
