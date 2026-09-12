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
from app.modules.objects.models import OBJECT_STATUSES, ObjectInstance
from app.modules.objects.services import (
    properties_of,
    require_refs_exist,
    require_unique_properties,
)
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.ontology.services import validate_properties
from app.shared.errors import AppError, code
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
