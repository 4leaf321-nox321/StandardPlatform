"""일괄 — 파일로 넣고, 파일로 뺀다.

사람은 폼으로 한 건을 만들고, 현실의 데이터는 엑셀에 300건이 있다. 그 사이의 길이
이것이다. 정의 가져오기(`ontology/importer.py`)와 같은 무늬를 쓴다:

    1. 계획 먼저   `apply=False` 면 아무것도 안 바꾸고 행마다 무엇이 될지 돌려준다
    2. 전부 아니면 무   한 행이라도 틀리면 **아무것도 안 넣는다** — 반쯤 들어간 파일은
                        어디까지 들어갔는지를 사람이 챙겨야 하고, 아무도 안 챙긴다
    3. 부분 수정 규칙   파일에 없는 열은 안 건드리고, 빈 칸은 「안 보냄」 이다.
                        비우려면 `\\null` 을 적는다. 안 그러면 열 하나 빠진 파일로
                        300개 속성이 날아가고, 그 손실은 올린 사람 눈에 안 보인다

## 행의 모양

고정 열은 `id` · `key` · `label` · `description` · `status` · `valid_from_year` ·
`valid_to_year`. 나머지 열은 속성 **키**다(정의의 라벨로 적어도 받는다 — 겹치지
않을 때만). 여러 값은 `;` 로 가르고, 참조는 상대의 **식별자**(없으면 이름)로 적는다.
이름이 둘 이상에 맞으면 짐작하지 않고 거절한다.

## 어느 행이 어느 객체인가

`id` 가 있으면 그것. 없으면 타입이 식별자를 쓰고 `key` 가 있으면 그것. 둘 다 없으면
새로 만든다. 그래서 같은 파일을 두 번 올려도 두 벌이 안 된다(upsert).
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import relations as rel
from app.modules.objects.models import OBJECT_STATUSES, ObjectInstance, ObjectRelation
from app.modules.objects.services import (
    normalize_key,
    properties_of,
    require_key_free,
    require_refs_exist,
    require_unique_properties,
)
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.modules.ontology.services import InvalidValue, merge_properties, validate_properties
from app.shared import audit
from app.shared.errors import AppError, code
from app.shared.permissions import require_owner_edit, visible_owner_clause

#: 비움 표시. 빈 칸은 「안 보냄」 이라, 지우려면 이것을 적는다.
NULL_MARK = "\\null"
#: 여러 값의 구분자.
MULTI_SEP = ";"
FIXED_COLUMNS = (
    "id",
    "key",
    "label",
    "description",
    "status",
    "valid_from_year",
    "valid_to_year",
)
#: 한 파일의 상한. 넘으면 나눠 올린다 — 한 트랜잭션이 너무 커지면 실패했을 때
#: 되돌리는 시간도 그만큼 길어진다.
MAX_ROWS = 5000

TRUE_WORDS = {"true", "1", "y", "yes", "예", "참", "o"}
FALSE_WORDS = {"false", "0", "n", "no", "아니오", "거짓", "x"}


@dataclass
class RowPlan:
    row: int
    """파일의 몇 번째 행인가(헤더 다음이 1)."""
    action: str
    """`create` · `update` · `unchanged` · `error`."""
    label: str = ""
    key: str | None = None
    object_id: uuid.UUID | None = None
    changes: list[str] = field(default_factory=list)
    message: str = ""


@dataclass
class Plan:
    rows: list[RowPlan] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    """행과 무관한 오류(모르는 열, 상한 초과). 하나라도 있으면 안 적용한다."""

    @property
    def counts(self) -> dict[str, int]:
        out = {"create": 0, "update": 0, "unchanged": 0, "error": 0}
        for one in self.rows:
            out[one.action] += 1
        return out

    @property
    def ok(self) -> bool:
        return not self.errors and self.counts["error"] == 0


# --- 파일 읽기 ----------------------------------------------------------------


def parse_file(name: str, raw: bytes) -> list[dict[str, Any]]:
    """CSV 나 JSON 을 행 목록으로. **BOM 을 벗긴다** — 엑셀이 붙이는 것이라 안 벗기면
    첫 열 이름이 `\\ufeffkey` 가 되어 「모르는 열」 로 거절된다."""
    text = raw.decode("utf-8-sig")
    if name.lower().endswith(".json"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as caught:
            raise InvalidValue(
                code("OBJECTS", 40), f"JSON 을 읽을 수 없습니다: {caught}"
            ) from None
        rows = data.get("rows") if isinstance(data, dict) else data
        if not isinstance(rows, list) or not all(isinstance(one, dict) for one in rows):
            raise InvalidValue(
                code("OBJECTS", 40), 'JSON 은 객체의 배열이거나 {"rows": [...]} 여야 합니다.'
            )
        return rows
    reader = csv.DictReader(io.StringIO(text))
    out: list[dict[str, Any]] = []
    for row in reader:
        # 열 이름의 앞뒤 공백은 사람 눈에 안 보이는 오타다.
        out.append({(k or "").strip(): v for k, v in row.items() if k is not None})
    return out


# --- 값 읽기 -------------------------------------------------------------------


class _Refs:
    """참조 풀이 — 상대 타입의 식별자·이름을 id 로. 타입마다 한 번만 읽는다."""

    def __init__(self, db: Session, user: User) -> None:
        self.db = db
        self.user = user
        self.cache: dict[
            str, tuple[dict[str, uuid.UUID], dict[str, list[uuid.UUID]], set[str]]
        ] = {}

    def _load(
        self, type_slug: str
    ) -> tuple[dict[str, uuid.UUID], dict[str, list[uuid.UUID]], set[str]]:
        if type_slug not in self.cache:
            object_type = self.db.scalar(
                select(ObjectType).where(ObjectType.slug == type_slug)
            )
            by_key: dict[str, uuid.UUID] = {}
            by_label: dict[str, list[uuid.UUID]] = {}
            ids: set[str] = set()
            if object_type is not None:
                rows = self.db.scalars(
                    select(ObjectInstance).where(
                        ObjectInstance.type_id == object_type.id,
                        ObjectInstance.deleted_at.is_(None),
                        visible_owner_clause(self.user, ObjectInstance.owner_workspace_id),
                    )
                )
                for row in rows:
                    ids.add(str(row.id))
                    if row.key:
                        by_key[row.key] = row.id
                    by_label.setdefault(row.label.strip(), []).append(row.id)
            self.cache[type_slug] = (by_key, by_label, ids)
        return self.cache[type_slug]

    def resolve(self, definition: PropertyDef, raw: str) -> str:
        """식별자 → 이름 → uuid 순으로 맞춘다. 이름이 여럿에 맞으면 거절."""
        target = definition.ref_type_slug or ""
        by_key, by_label, ids = self._load(target)
        text = raw.strip()
        if text in by_key:
            return str(by_key[text])
        hits = by_label.get(text, [])
        if len(hits) == 1:
            return str(hits[0])
        if len(hits) > 1:
            raise InvalidValue(
                code("OBJECTS", 41),
                f"{definition.label}: 「{text}」 이름이 {len(hits)}개에 맞습니다. "
                "식별자로 적으세요.",
            )
        # uuid 로 적었어도 **있는 것이어야** 한다 — 모양만 보고 받으면 없는 것을 가리키는
        # 참조가 저장되고, 화면에는 빈 칸으로 뜬다.
        if text in ids:
            return text
        raise InvalidValue(
            code("OBJECTS", 41),
            f"{definition.label}: 「{text}」 을 {target} 에서 찾을 수 없습니다.",
        )


def _one_from_text(definition: PropertyDef, raw: Any, refs: _Refs) -> Any:
    """칸 하나를 그 속성의 모양으로. JSON 으로 온 값(이미 숫자·불)은 그대로 둔다."""
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    kind = definition.data_type
    if kind == "number":
        try:
            number = float(text)
        except ValueError:
            raise InvalidValue(
                code("ONTOLOGY", 11), f"{definition.label}: 숫자여야 합니다: {text!r}"
            ) from None
        return int(number) if number.is_integer() else number
    if kind == "bool":
        low = text.lower()
        if low in TRUE_WORDS:
            return True
        if low in FALSE_WORDS:
            return False
        raise InvalidValue(
            code("ONTOLOGY", 12), f"{definition.label}: 예/아니오 값이어야 합니다: {text!r}"
        )
    if kind == "object_ref":
        return refs.resolve(definition, text)
    return text


def cell_to_value(definition: PropertyDef, raw: Any, refs: _Refs) -> Any:
    """칸 → 저장할 값. 빈 칸은 「안 보냄」(생략), `\\null` 은 「비움」(None)."""
    if isinstance(raw, str):
        text = raw.strip()
        if text == NULL_MARK:
            return None
        if definition.multi:
            parts = [one.strip() for one in text.split(MULTI_SEP) if one.strip()]
            return [_one_from_text(definition, one, refs) for one in parts]
        return _one_from_text(definition, text, refs)
    if definition.multi and isinstance(raw, list):
        return [_one_from_text(definition, one, refs) for one in raw]
    return raw


def _is_blank(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


def _patch_of(
    row: dict[str, Any],
    mapping: dict[str, str],
    by_key: dict[str, PropertyDef],
    refs: _Refs,
) -> dict[str, Any]:
    """행 → 속성 패치. **없는 열과 빈 칸은 「안 보냄」, JSON 의 null 과 `\\null` 은 「비움」.**

    CSV 는 빈 칸으로 「비움」 을 말할 수 없어 표시(`\\null`)를 쓰고, JSON 은 null 로
    말한다 — MCP 가 그렇게 약속했다. 둘 다 None 으로 모여 merge 에서 그 키를 지운다.
    """
    patch: dict[str, Any] = {}
    for header, prop_key in mapping.items():
        if header not in row:
            continue
        raw = row[header]
        if raw is None:
            patch[prop_key] = None
            continue
        if isinstance(raw, str) and raw.strip() == "":
            continue
        patch[prop_key] = cell_to_value(by_key[prop_key], raw, refs)
    return patch


# --- 객체 -----------------------------------------------------------------------


def _column_map(
    defs: list[PropertyDef], headers: set[str]
) -> tuple[dict[str, str], list[str]]:
    """열 이름 → 속성 키. 라벨로 적은 열도 받되, 겹치면 거절한다."""
    by_key = {d.key: d.key for d in defs}
    by_label: dict[str, list[str]] = {}
    for d in defs:
        by_label.setdefault(d.label, []).append(d.key)
    mapping: dict[str, str] = {}
    unknown: list[str] = []
    for header in headers:
        if header in FIXED_COLUMNS or header == "":
            continue
        if header in by_key:
            mapping[header] = header
        elif header in by_label and len(by_label[header]) == 1:
            mapping[header] = by_label[header][0]
        else:
            unknown.append(header)
    return mapping, sorted(unknown)


def _fixed(row: dict[str, Any], name: str) -> Any:
    raw = row.get(name)
    if isinstance(raw, str):
        text = raw.strip()
        if text == NULL_MARK:
            return None
        if text == "":
            return _MISSING
        return text
    return _MISSING if raw is None else raw


_MISSING = object()


def plan_objects(
    db: Session,
    user: User,
    object_type: ObjectType,
    rows: list[dict[str, Any]],
    *,
    owner_workspace_id: uuid.UUID | None,
) -> Plan:
    """행마다 무엇이 될지 — **아무것도 안 바꾼다.**"""
    plan = Plan()
    if len(rows) > MAX_ROWS:
        plan.errors.append(
            f"한 번에 {MAX_ROWS}행까지 넣습니다 (넣은 행 {len(rows)}). 나눠 올리세요."
        )
        return plan
    if not object_type.is_active or object_type.kind_class == "system":
        plan.errors.append(f"{object_type.label}에는 파일로 넣지 않습니다.")
        return plan

    defs = [d for d in properties_of(db, object_type.id) if d.data_type != "file"]
    headers = {key for row in rows for key in row}
    mapping, unknown = _column_map(defs, headers)
    if unknown:
        plan.errors.append(
            f"모르는 열: {', '.join(unknown)}. 속성 키나 라벨로 적으세요 — "
            "템플릿을 받으면 열 이름이 있습니다."
        )
        return plan
    by_key = {d.key: d for d in defs}
    refs = _Refs(db, user)

    # 이 파일 안에서 같은 식별자가 둘이면 어느 쪽이 맞는지 알 수 없다.
    seen_keys: dict[str, int] = {}
    seen_ids: dict[str, int] = {}

    for index, row in enumerate(rows, start=1):
        try:
            plan.rows.append(
                _plan_row(
                    db,
                    user,
                    object_type,
                    defs,
                    by_key,
                    mapping,
                    refs,
                    row,
                    index,
                    owner_workspace_id=owner_workspace_id,
                    seen_keys=seen_keys,
                    seen_ids=seen_ids,
                )
            )
        except AppError as caught:
            plan.rows.append(
                RowPlan(
                    row=index,
                    action="error",
                    label=str(row.get("label") or ""),
                    message=caught.message,
                )
            )
    return plan


def _plan_row(
    db: Session,
    user: User,
    object_type: ObjectType,
    defs: list[PropertyDef],
    by_key: dict[str, PropertyDef],
    mapping: dict[str, str],
    refs: _Refs,
    row: dict[str, Any],
    index: int,
    *,
    owner_workspace_id: uuid.UUID | None,
    seen_keys: dict[str, int],
    seen_ids: dict[str, int],
) -> RowPlan:
    patch = _patch_of(row, mapping, by_key, refs)

    raw_id = _fixed(row, "id")
    raw_key = _fixed(row, "key")
    raw_label = _fixed(row, "label")
    raw_status = _fixed(row, "status")
    raw_description = _fixed(row, "description")
    raw_from = _fixed(row, "valid_from_year")
    raw_to = _fixed(row, "valid_to_year")

    if (
        raw_status is not _MISSING
        and raw_status is not None
        and raw_status not in OBJECT_STATUSES
    ):
        raise InvalidValue(
            code("OBJECTS", 42),
            f"상태는 {', '.join(OBJECT_STATUSES)} 중 하나입니다: {raw_status}",
        )
    for name, raw in (("valid_from_year", raw_from), ("valid_to_year", raw_to)):
        if raw is not _MISSING and raw is not None:
            try:
                int(raw)
            except (TypeError, ValueError):
                raise InvalidValue(
                    code("OBJECTS", 42), f"{name}: 연도(숫자)여야 합니다: {raw}"
                ) from None

    # 어느 객체인가.
    existing: ObjectInstance | None = None
    if raw_id is not _MISSING and raw_id is not None:
        try:
            wanted = uuid.UUID(str(raw_id))
        except ValueError:
            raise InvalidValue(
                code("OBJECTS", 43), f"id 가 uuid 가 아닙니다: {raw_id}"
            ) from None
        if str(wanted) in seen_ids:
            raise InvalidValue(
                code("OBJECTS", 44), f"같은 id 가 {seen_ids[str(wanted)]}행에도 있습니다."
            )
        seen_ids[str(wanted)] = index
        existing = db.scalar(
            select(ObjectInstance).where(
                ObjectInstance.id == wanted,
                ObjectInstance.type_id == object_type.id,
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
        if existing is None:
            raise InvalidValue(code("OBJECTS", 43), f"id 에 맞는 객체가 없습니다: {raw_id}")
    key = normalize_key(object_type, None if raw_key in (_MISSING, None) else str(raw_key))
    if existing is None and key is not None:
        if key in seen_keys:
            raise InvalidValue(
                code("OBJECTS", 44), f"같은 식별자가 {seen_keys[key]}행에도 있습니다: {key}"
            )
        seen_keys[key] = index
        stmt = select(ObjectInstance).where(
            ObjectInstance.type_id == object_type.id,
            ObjectInstance.key == key,
            ObjectInstance.deleted_at.is_(None),
        )
        if object_type.key_scope == "workspace":
            stmt = (
                stmt.where(ObjectInstance.owner_workspace_id.is_(None))
                if owner_workspace_id is None
                else stmt.where(ObjectInstance.owner_workspace_id == owner_workspace_id)
            )
        existing = db.scalar(stmt)
        if existing is not None and not _can_see(db, user, existing):
            # 같은 식별자가 남의 부서에 있다 — 없는 것과 같은 말로 답하되 만들지도 못한다.
            raise InvalidValue(code("OBJECTS", 3), f"같은 식별자가 이미 있습니다: {key}")

    if existing is None:
        if raw_label is _MISSING or raw_label is None or not str(raw_label).strip():
            raise InvalidValue(
                code("OBJECTS", 45), "새로 만들 행에는 label(이름)이 있어야 합니다."
            )
        properties = validate_properties(
            defs, {k: v for k, v in patch.items() if v is not None}, apply_defaults=True
        )
        require_refs_exist(db, defs, properties)
        require_unique_properties(
            db, object_type, defs, properties, owner_workspace_id=owner_workspace_id
        )
        return RowPlan(
            row=index,
            action="create",
            label=str(raw_label).strip(),
            key=key,
            changes=sorted(properties),
        )

    # 고침 — 보낸 것만.
    require_owner_edit(
        db, user, existing.owner_workspace_id, what="객체", code_value=code("OBJECTS", 16)
    )
    changes: list[str] = []
    if (
        raw_label is not _MISSING
        and raw_label is not None
        and str(raw_label).strip() != existing.label
    ):
        changes.append("label")
    if raw_description is not _MISSING and (raw_description or "") != (
        existing.description or ""
    ):
        changes.append("description")
    if raw_status is not _MISSING and raw_status is not None and raw_status != existing.status:
        changes.append("status")
    if (
        raw_from is not _MISSING
        and (None if raw_from is None else int(raw_from)) != existing.valid_from_year
    ):
        changes.append("valid_from_year")
    if (
        raw_to is not _MISSING
        and (None if raw_to is None else int(raw_to)) != existing.valid_to_year
    ):
        changes.append("valid_to_year")
    if key is not None and key != existing.key:
        require_key_free(
            db,
            object_type,
            key,
            owner_workspace_id=existing.owner_workspace_id,
            exclude_id=existing.id,
        )
        changes.append("key")
    if patch:
        merged = merge_properties(existing.properties or {}, patch)
        cleaned = validate_properties(defs, merged)
        require_refs_exist(db, defs, cleaned)
        require_unique_properties(
            db,
            object_type,
            defs,
            cleaned,
            owner_workspace_id=existing.owner_workspace_id,
            exclude_id=existing.id,
        )
        current = existing.properties or {}
        for prop_key in patch:
            if current.get(prop_key) != cleaned.get(prop_key):
                changes.append(prop_key)
    return RowPlan(
        row=index,
        action="update" if changes else "unchanged",
        label=existing.label,
        # 바꿀 식별자가 있으면 그것 — 적용이 이 값을 쓴다.
        key=key if key is not None else existing.key,
        object_id=existing.id,
        changes=changes,
    )


def _can_see(db: Session, user: User, row: ObjectInstance) -> bool:
    return (
        db.scalar(
            select(ObjectInstance.id).where(
                ObjectInstance.id == row.id,
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
        is not None
    )


def apply_objects(
    db: Session,
    user: User,
    object_type: ObjectType,
    rows: list[dict[str, Any]],
    *,
    owner_workspace_id: uuid.UUID | None,
) -> Plan:
    """계획을 다시 세우고, 오류가 없을 때만 **한 트랜잭션으로** 넣는다.

    계획을 다시 세우는 이유: 미리 보기와 적용 사이에 다른 사람이 무엇을 바꿨을 수
    있다. 그때 옛 계획대로 넣으면 그 사람의 변경이 조용히 덮인다.
    """
    plan = plan_objects(db, user, object_type, rows, owner_workspace_id=owner_workspace_id)
    if not plan.ok:
        return plan

    defs = [d for d in properties_of(db, object_type.id) if d.data_type != "file"]
    by_key = {d.key: d for d in defs}
    mapping, _ = _column_map(defs, {key for row in rows for key in row})
    refs = _Refs(db, user)

    for row_plan, row in zip(plan.rows, rows, strict=True):
        if row_plan.action == "unchanged":
            continue
        patch = _patch_of(row, mapping, by_key, refs)
        raw_label = _fixed(row, "label")
        raw_description = _fixed(row, "description")
        raw_status = _fixed(row, "status")
        raw_from = _fixed(row, "valid_from_year")
        raw_to = _fixed(row, "valid_to_year")

        if row_plan.action == "create":
            target = ObjectInstance(
                type_id=object_type.id,
                key=row_plan.key,
                label=row_plan.label,
                description=""
                if raw_description in (_MISSING, None)
                else str(raw_description),
                properties=validate_properties(
                    defs,
                    {k: v for k, v in patch.items() if v is not None},
                    apply_defaults=True,
                ),
                status="active" if raw_status in (_MISSING, None) else str(raw_status),
                owner_workspace_id=owner_workspace_id,
                valid_from_year=None if raw_from in (_MISSING, None) else int(raw_from),
                valid_to_year=None if raw_to in (_MISSING, None) else int(raw_to),
                created_by_id=user.id,
            )
            db.add(target)
            db.flush()
            row_plan.object_id = target.id
            audit.record(
                db,
                action="object.create",
                actor=user,
                target_table="objects",
                target_id=target.id,
                target_label=f"{object_type.slug}:{target.label}",
                workspace_id=owner_workspace_id,
                reason="일괄 가져오기",
            )
            continue

        found = db.get(ObjectInstance, row_plan.object_id)
        if found is None:  # pragma: no cover - 방금 계획에서 찾았다
            continue
        target = found
        before = {
            "key": target.key,
            "label": target.label,
            "status": target.status,
            "properties": dict(target.properties or {}),
        }
        if raw_label not in (_MISSING, None):
            target.label = str(raw_label).strip()
        if raw_description is not _MISSING:
            target.description = raw_description or ""
        if raw_status not in (_MISSING, None):
            target.status = str(raw_status)
        if raw_from is not _MISSING:
            target.valid_from_year = None if raw_from is None else int(raw_from)
        if raw_to is not _MISSING:
            target.valid_to_year = None if raw_to is None else int(raw_to)
        if row_plan.key is not None and "key" in row_plan.changes:
            target.key = row_plan.key
        if patch:
            target.properties = validate_properties(
                defs, merge_properties(target.properties or {}, patch)
            )
        after = {
            "key": target.key,
            "label": target.label,
            "status": target.status,
            "properties": dict(target.properties or {}),
        }
        audit.record(
            db,
            action="object.update",
            actor=user,
            target_table="objects",
            target_id=target.id,
            target_label=f"{object_type.slug}:{target.label}",
            workspace_id=target.owner_workspace_id,
            changes=audit.diff(before, after),
            reason="일괄 가져오기",
        )

    counts = plan.counts
    audit.record(
        db,
        action="object.import",
        actor=user,
        target_table="objects",
        target_id=None,
        target_label=object_type.slug,
        workspace_id=owner_workspace_id,
        changes={"rows": len(rows), **counts},
    )
    db.commit()
    return plan


# --- 내보내기 --------------------------------------------------------------------


def export_columns(defs: list[PropertyDef]) -> list[str]:
    return [*FIXED_COLUMNS, *(d.key for d in defs if d.data_type != "file")]


def export_rows(
    db: Session, defs: list[PropertyDef], rows: list[ObjectInstance]
) -> list[dict[str, Any]]:
    """저장된 모양 → 파일의 모양.

    참조는 상대의 **식별자**(없으면 이름)로 — 다시 넣을 수 있게.
    """
    ref_keys = [d.key for d in defs if d.data_type == "object_ref"]
    wanted: set[uuid.UUID] = set()
    for row in rows:
        for key in ref_keys:
            raw = (row.properties or {}).get(key)
            for item in raw if isinstance(raw, list) else [raw]:
                if isinstance(item, str):
                    try:
                        wanted.add(uuid.UUID(item))
                    except ValueError:
                        continue
    names: dict[str, str] = {}
    if wanted:
        for found in db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(wanted))):
            names[str(found.id)] = found.key or found.label

    out: list[dict[str, Any]] = []
    for row in rows:
        record: dict[str, Any] = {
            "id": str(row.id),
            "key": row.key or "",
            "label": row.label,
            "description": row.description or "",
            "status": row.status,
            "valid_from_year": row.valid_from_year if row.valid_from_year is not None else "",
            "valid_to_year": row.valid_to_year if row.valid_to_year is not None else "",
        }
        values = row.properties or {}
        for d in defs:
            if d.data_type == "file":
                continue
            raw = values.get(d.key)
            if raw is None:
                record[d.key] = ""
                continue
            items = raw if isinstance(raw, list) else [raw]
            if d.data_type == "object_ref":
                items = [names.get(str(item), str(item)) for item in items]
            elif d.data_type == "bool":
                items = ["예" if item else "아니오" for item in items]
            record[d.key] = (
                MULTI_SEP.join(str(item) for item in items)
                if d.multi
                else (items[0] if items else "")
            )
        out.append(record)
    return out


def to_csv(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    """엑셀이 한글을 안 깨뜨리게 BOM 을 붙인다."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return ("﻿" + buffer.getvalue()).encode("utf-8")


# --- 관계 -----------------------------------------------------------------------

RELATION_COLUMNS = ("src", "relation", "dst", "evidence_note")


def _find_endpoint(
    db: Session, user: User, text: str, allowed_types: list[uuid.UUID] | None
) -> ObjectInstance:
    """식별자 → 이름 → uuid. 이름이 여럿에 맞으면 거절."""
    stmt = select(ObjectInstance).where(
        ObjectInstance.deleted_at.is_(None),
        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
    )
    if allowed_types:
        stmt = stmt.where(ObjectInstance.type_id.in_(allowed_types))
    by_key = list(db.scalars(stmt.where(ObjectInstance.key == text)))
    if len(by_key) == 1:
        return by_key[0]
    if len(by_key) > 1:
        raise InvalidValue(
            code("OBJECTS", 46),
            f"「{text}」 식별자가 {len(by_key)}개 타입에 있습니다. "
            "관계 종류의 타입을 좁히세요.",
        )
    by_label = list(db.scalars(stmt.where(ObjectInstance.label == text)))
    if len(by_label) == 1:
        return by_label[0]
    if len(by_label) > 1:
        raise InvalidValue(
            code("OBJECTS", 46),
            f"「{text}」 이름이 {len(by_label)}개에 맞습니다. 식별자로 적으세요.",
        )
    try:
        found = db.scalar(stmt.where(ObjectInstance.id == uuid.UUID(text)))
    except ValueError:
        found = None
    if found is None:
        raise InvalidValue(code("OBJECTS", 46), f"「{text}」 을 찾을 수 없습니다.")
    return found


def _type_ids(db: Session, slugs: list[str] | None) -> list[uuid.UUID] | None:
    if not slugs:
        return None
    return list(db.scalars(select(ObjectType.id).where(ObjectType.slug.in_(slugs))))


def plan_relations(
    db: Session, user: User, object_type: ObjectType, rows: list[dict[str, Any]]
) -> Plan:
    """관계 파일 — `src, relation, dst, evidence_note`. 출발점은 이 타입이어야 한다.

    이미 이어진 것은 `unchanged`. 그래서 같은 파일을 두 번 올려도 선이 두 겹이 안 된다.
    """
    plan = Plan()
    if len(rows) > MAX_ROWS:
        plan.errors.append(
            f"한 번에 {MAX_ROWS}행까지 넣습니다 (넣은 행 {len(rows)}). 나눠 올리세요."
        )
        return plan
    headers = {key for row in rows for key in row} - {""}
    unknown = sorted(headers - set(RELATION_COLUMNS))
    if unknown:
        plan.errors.append(
            f"모르는 열: {', '.join(unknown)}. "
            f"관계 파일의 열은 {', '.join(RELATION_COLUMNS)} 입니다."
        )
        return plan

    kinds = {row.slug: row for row in db.scalars(select(RelationType))}
    seen: set[tuple[uuid.UUID, str, uuid.UUID]] = set()
    for index, row in enumerate(rows, start=1):
        try:
            plan.rows.append(_plan_relation(db, user, object_type, kinds, row, index, seen))
        except AppError as caught:
            plan.rows.append(RowPlan(row=index, action="error", message=caught.message))
    return plan


def _plan_relation(
    db: Session,
    user: User,
    object_type: ObjectType,
    kinds: dict[str, RelationType],
    row: dict[str, Any],
    index: int,
    seen: set[tuple[uuid.UUID, str, uuid.UUID]],
) -> RowPlan:
    src_text = str(row.get("src") or "").strip()
    dst_text = str(row.get("dst") or "").strip()
    slug = str(row.get("relation") or "").strip()
    if not src_text or not dst_text or not slug:
        raise InvalidValue(code("OBJECTS", 47), "src · relation · dst 가 모두 있어야 합니다.")
    kind = kinds.get(slug)
    if kind is None:
        # 라벨로 적었을 수도 있다 — 겹치지 않을 때만.
        matches = [one for one in kinds.values() if one.label == slug]
        if len(matches) != 1:
            raise InvalidValue(code("OBJECTS", 47), f"모르는 관계 종류: {slug}")
        kind = matches[0]
    if not kind.is_active:
        raise InvalidValue(
            code("OBJECTS", 47), f"{kind.label}은 지금 쓰지 않는 관계 종류입니다."
        )

    src = _find_endpoint(db, user, src_text, [object_type.id])
    dst = _find_endpoint(db, user, dst_text, _type_ids(db, kind.dst_type_slugs))
    require_owner_edit(
        db, user, src.owner_workspace_id, what="객체", code_value=code("OBJECTS", 27)
    )
    rel.require_endpoints_allowed(db, kind, src, dst)

    triple = (src.id, kind.slug, dst.id)
    if triple in seen:
        raise InvalidValue(code("OBJECTS", 44), "같은 관계가 이 파일에 두 번 있습니다.")
    seen.add(triple)
    label = f"{src.label} -{kind.label}-> {dst.label}"
    existing = db.scalar(
        select(ObjectRelation.id).where(
            ObjectRelation.src_object_id == src.id,
            ObjectRelation.dst_object_id == dst.id,
            ObjectRelation.relation == kind.slug,
        )
    )
    if existing is not None:
        return RowPlan(row=index, action="unchanged", label=label, object_id=existing)
    rel.require_cardinality(db, kind, src.id, dst.id)
    rel.require_no_cycle(db, kind, src.id, dst.id)
    return RowPlan(row=index, action="create", label=label)


def apply_relations(
    db: Session, user: User, object_type: ObjectType, rows: list[dict[str, Any]]
) -> Plan:
    plan = plan_relations(db, user, object_type, rows)
    if not plan.ok:
        return plan
    kinds = {row.slug: row for row in db.scalars(select(RelationType))}
    by_label = {row.label: row for row in kinds.values()}
    for row_plan, row in zip(plan.rows, rows, strict=True):
        if row_plan.action != "create":
            continue
        slug = str(row.get("relation") or "").strip()
        kind = kinds.get(slug) or by_label[slug]
        src = _find_endpoint(db, user, str(row.get("src") or "").strip(), [object_type.id])
        dst = _find_endpoint(
            db, user, str(row.get("dst") or "").strip(), _type_ids(db, kind.dst_type_slugs)
        )
        # 앞 행이 만든 관계가 카디널리티를 채웠을 수 있다 — 넣기 직전에 한 번 더.
        rel.require_cardinality(db, kind, src.id, dst.id)
        rel.require_no_cycle(db, kind, src.id, dst.id)
        edge = ObjectRelation(
            src_object_id=src.id,
            dst_object_id=dst.id,
            relation=kind.slug,
            properties={},
            evidence_note=str(row.get("evidence_note") or "").strip(),
            created_by_id=user.id,
        )
        db.add(edge)
        db.flush()
        row_plan.object_id = edge.id
        audit.record(
            db,
            action="object.relation.add",
            actor=user,
            target_table="object_relations",
            target_id=edge.id,
            target_label=row_plan.label,
            workspace_id=src.owner_workspace_id,
            changes=audit.relation_endpoints(edge, src.label, dst.label),
            reason="일괄 가져오기",
        )
    audit.record(
        db,
        action="object.relation.import",
        actor=user,
        target_table="object_relations",
        target_id=None,
        target_label=object_type.slug,
        changes={"rows": len(rows), **plan.counts},
    )
    db.commit()
    return plan


def export_relations(db: Session, user: User, object_type: ObjectType) -> list[dict[str, Any]]:
    """이 타입에서 **출발하는** 관계. 끝점은 식별자(없으면 이름)로."""
    src = ObjectInstance
    rows = db.execute(
        select(ObjectRelation, src)
        .join(src, src.id == ObjectRelation.src_object_id)
        .where(
            src.type_id == object_type.id,
            src.deleted_at.is_(None),
            visible_owner_clause(user, src.owner_workspace_id),
        )
        .order_by(ObjectRelation.created_at)
    ).all()
    dst_ids = {edge.dst_object_id for edge, _ in rows}
    dsts = (
        {
            one.id: one
            for one in db.scalars(
                select(ObjectInstance).where(
                    ObjectInstance.id.in_(dst_ids),
                    ObjectInstance.deleted_at.is_(None),
                    visible_owner_clause(user, ObjectInstance.owner_workspace_id),
                )
            )
        }
        if dst_ids
        else {}
    )
    out: list[dict[str, Any]] = []
    for edge, source in rows:
        target = dsts.get(edge.dst_object_id)
        if target is None:
            continue
        out.append(
            {
                "src": source.key or source.label,
                "relation": edge.relation,
                "dst": target.key or target.label,
                "evidence_note": edge.evidence_note or "",
            }
        )
    return out
