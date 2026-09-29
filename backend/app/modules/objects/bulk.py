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
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import aliases, links, system
from app.modules.objects import relations as rel
from app.modules.objects.models import (
    OBJECT_STATUSES,
    ObjectAlias,
    ObjectInstance,
    ObjectLink,
    ObjectRelation,
)
from app.modules.objects.services import (
    audit_state,
    normalize_key,
    properties_of,
    require_key_free,
    require_refs_exist,
    require_unique_properties,
)
from app.modules.ontology import managed
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.modules.ontology.services import InvalidValue, merge_properties, validate_properties
from app.shared import audit, tabular
from app.shared.errors import AppError, code
from app.shared.permissions import require_owner_edit, visible_owner_clause
from app.shared.text import compare_key

#: 비움 표시. 빈 칸은 「안 보냄」 이라, 지우려면 이것을 적는다.
NULL_MARK = "\\null"
#: 여러 값의 구분자.
MULTI_SEP = ";"
FIXED_COLUMNS = (
    "id",
    "key",
    "label",
    "description",
    "aliases",
    "status",
    "valid_from_year",
    "valid_to_year",
    "renamed_from",
)

RELATION_MODES = ("add", "replace")
"""관계 파일을 **더할지 맞출지.** 기본은 `add` — 가져오기는 더하기만 했다. `replace` 는
파일에 나온 (출발 객체 · 관계 종류) 범위에서 **파일에 없는 선을 끊는다**(계획에 `unlink` 로
올라오고, 사람이 보고 적용한다). 원 표면 선(`object_links`)은 건드리지 않는다."""

ALIAS_MODES = ("add", "replace")
"""별칭 칸을 **더할지 맞출지.** 기본은 `add` — 다시 적재할 때 사람이 화면에서 붙인 별칭이
조용히 사라지지 않게. 파일을 정본으로 보는 자리(허브 → 쌍둥이)만 `replace` 를 적는다."""
#: 한 파일의 상한. 넘으면 나눠 올린다 — 한 트랜잭션이 너무 커지면 실패했을 때
#: 되돌리는 시간도 그만큼 길어진다.
MAX_ROWS = 5000
"""요청 경로의 행 상한. 워커 경로(`jobs`)는 `max_rows` 로 더 크게 넘긴다 — 이 수는
요청 시간 때문이지 트랜잭션 크기 때문이 아니다."""

#: (단계, 처리한 수, 전체) — 워커가 진행률을 표에 쓴다. 없으면 조용히.
Progress = Callable[[str, int, int], None] | None
PROGRESS_EVERY = 200


def _tick(on_progress: Progress, stage: str, done: int, total: int) -> None:
    if on_progress is not None and (done % PROGRESS_EVERY == 0 or done == total):
        on_progress(stage, done, total)


TRUE_WORDS = {"true", "1", "y", "yes", "예", "참", "o"}
FALSE_WORDS = {"false", "0", "n", "no", "아니오", "거짓", "x"}


@dataclass
class RowPlan:
    row: int
    """파일의 몇 번째 행인가(헤더 다음이 1)."""
    action: str
    """`create` · `update` · `unchanged` · `unlink` · `error`.

    `unlink` 는 **파일에 없어 끊을 선**이다 — 파일에서 온 줄이 아니라 `row` 가 0 이다."""
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
        # 네 가지는 **없어도 0 으로** 온다 — 화면이 「새로 0 · 고침 0」 을 그리려면 키가
        # 있어야 한다. 그 밖의 것(`merge` · `deprecate` 처럼 무덤 단계가 쓰는 말)은 있을
        # 때만 붙는다 — 여기에 다 적어 두면 새 말을 더할 때마다 이 줄을 고쳐야 한다.
        out = {"create": 0, "update": 0, "unchanged": 0, "unlink": 0, "error": 0}
        for one in self.rows:
            out[one.action] = out.get(one.action, 0) + 1
        return out

    @property
    def ok(self) -> bool:
        return not self.errors and self.counts["error"] == 0


# --- 파일 읽기 ----------------------------------------------------------------


def parse_file(name: str, raw: bytes) -> list[dict[str, Any]]:
    """CSV·탭 구분·JSON 을 행 목록으로 — `shared/tabular.py` 가 읽고, 여기서는 오류 코드만
    붙인다."""
    try:
        return tabular.parse_rows(name, raw)
    except tabular.TabularError as caught:
        raise InvalidValue(code("OBJECTS", 40), str(caught)) from None


# --- 값 읽기 -------------------------------------------------------------------


class _Refs:
    """참조 풀이 — 상대 타입의 식별자·이름을 id 로. 타입마다 한 번만 읽는다."""

    def __init__(self, db: Session, user: User) -> None:
        self.db = db
        self.user = user
        self.cache: dict[
            str,
            tuple[
                dict[str, uuid.UUID],
                dict[str, uuid.UUID],
                dict[str, list[uuid.UUID]],
                set[str],
            ],
        ] = {}

    def _load(
        self, type_slug: str
    ) -> tuple[
        dict[str, uuid.UUID], dict[str, uuid.UUID], dict[str, list[uuid.UUID]], set[str]
    ]:
        if type_slug not in self.cache:
            object_type = self.db.scalar(
                select(ObjectType).where(ObjectType.slug == type_slug)
            )
            by_key: dict[str, uuid.UUID] = {}
            by_alias: dict[str, uuid.UUID] = {}
            by_label: dict[str, list[uuid.UUID]] = {}
            ids: set[str] = set()
            if object_type is not None and system.is_system(object_type):
                # 원 표를 비추는 타입 — 식별자는 그 표의 것(부서 slug · 로그인 아이디).
                for ref in system.source_of(object_type).list_all(self.db):
                    ids.add(str(ref.id))
                    by_key[ref.key] = ref.id
                    by_label.setdefault(ref.label.strip(), []).append(ref.id)
            elif object_type is not None:
                # 별칭·외부 식별자도 식별자처럼 — 「앤시스」 로 적어도 「Ansys」 로 풀린다.
                for norm, hits in aliases.index_of(self.db, object_type).items():
                    if len(hits) == 1 and norm not in by_key:
                        by_alias[norm] = hits[0]
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
            self.cache[type_slug] = (by_key, by_alias, by_label, ids)
        return self.cache[type_slug]

    def resolve(self, definition: PropertyDef, raw: str) -> str:
        """식별자 → 별칭 → 이름 → uuid 순으로 맞춘다. 이름이 여럿에 맞으면 거절."""
        target = definition.ref_type_slug or ""
        by_key, by_alias, by_label, ids = self._load(target)
        text = raw.strip()
        if text in by_key:
            return str(by_key[text])
        if compare_key(text) in by_alias:
            return str(by_alias[compare_key(text)])
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


def _alias_note(taken: list[str], long: list[str]) -> str:
    """빠진 별칭을 계획에 적는 말 — **조용히 버리지 않는다.**

    사람은 넣은 것이 다 들어간 줄 알고, 나중에 「그 이름으로 검색이 안 된다」 로 만난다.
    막지는 않는다(파일 하나가 한 별칭 때문에 통째로 거절되면 수백 줄이 안 들어간다) —
    대신 뺀 것을 줄마다 적는다.
    """
    parts: list[str] = []
    if taken:
        parts.append(
            f"별칭 {len(taken)}개는 다른 객체가 쓰고 있어 건너뜁니다: {', '.join(taken)}"
        )
    if long:
        parts.append(
            f"별칭 {len(long)}개는 {aliases.MAX_VALUE}자를 넘어 건너뜁니다: "
            + ", ".join(f"{one[:20]}…({len(one)}자)" for one in long)
        )
    return " / ".join(parts)


def alias_values(raw: Any) -> tuple[list[str], list[str]]:
    """별칭 칸 → (쓸 수 있는 것, 너무 길어 뺀 것).

    ⚠️ **목록으로 오면 쪼개지 않는다.** 예전에는 `str(raw).split(";")` 이라, JSON 으로 온
       `["박리", "코팅 박리"]` 가 `"['박리', '코팅 박리']"` **한 덩어리**로 저장됐다(실측).
    ⚠️ 글 하나로 오면 `;` 로 가른다(CSV 한 칸에 여럿을 적는 길). 그래서 **별칭 안에 `;` 가
       든 것은 목록으로 보내야 살아남는다** — 고장 모드 80건 · 메커니즘 482건이 그렇다.
    """
    if raw is None:
        return [], []
    if isinstance(raw, list | tuple):
        return aliases.split_long(aliases.clean([str(one) for one in raw]))
    return aliases.split_long(aliases.clean(str(raw).split(MULTI_SEP)))


def _is_blank(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


def renamed_from(object_type: ObjectType, row: dict[str, Any]) -> str | None:
    """이 행이 **바꾸려는 옛 식별자** — 없으면 None.

    키 체계가 바뀌는 일은 실제로 일어난다(`FM-001` → `FM-BRK-001`). 옛 식별자로 찾을 길이
    없으면 같은 것이 새 객체로 하나 더 생기고, 그때 관계 · 참조는 옛 쪽에 남는다.
    """
    raw = _fixed(row, "renamed_from")
    if raw in (_MISSING, None) or not str(raw).strip():
        return None
    return normalize_key(object_type, str(raw))


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
    source: str = "",
    aliases_mode: str = "add",
    max_rows: int = MAX_ROWS,
    on_progress: Progress = None,
) -> Plan:
    """행마다 무엇이 될지 — **아무것도 안 바꾼다.** `source` 는 허브에서 받는 묶음만 적는다."""
    plan = Plan()
    if len(rows) > max_rows:
        plan.errors.append(
            f"한 번에 {max_rows}행까지 넣습니다 (넣은 행 {len(rows)}). 나눠 올리세요."
        )
        return plan
    if not object_type.is_active or object_type.kind_class == "system":
        plan.errors.append(f"{object_type.label}에는 파일로 넣지 않습니다.")
        return plan
    refused = managed.objects_refusal(object_type, source=source, what="넣지")
    if refused:
        plan.errors.append(refused)
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
        _tick(on_progress, "계획", index, len(rows))
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
                    aliases_mode=aliases_mode,
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
    aliases_mode: str,
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
    raw_aliases = _fixed(row, "aliases")
    wanted_aliases: list[str] | None = None
    long_aliases: list[str] = []
    if raw_aliases is not _MISSING:
        wanted_aliases, long_aliases = alias_values(raw_aliases)

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

    old_key = renamed_from(object_type, row)
    if old_key is not None:
        if key is None:
            raise InvalidValue(
                code("OBJECTS", 90),
                "renamed_from 을 적으면 key 에 **새 식별자**를 적어야 합니다.",
            )
        previous = db.scalar(
            select(ObjectInstance).where(
                ObjectInstance.type_id == object_type.id,
                ObjectInstance.key == old_key,
                ObjectInstance.deleted_at.is_(None),
            )
        )
        if previous is not None and not _can_see(db, user, previous):
            raise InvalidValue(
                code("OBJECTS", 90), f"옛 식별자의 객체를 볼 수 없습니다: {old_key}"
            )
        if existing is not None and previous is not None and previous.id != existing.id:
            # 둘을 하나로 만드는 것은 **합치기**가 할 일이다 — 파일이 조용히 하면 안 된다.
            raise InvalidValue(
                code("OBJECTS", 90),
                f"{old_key} 와 {key} 가 서로 다른 객체입니다 — 합치기(merge)로 하세요.",
            )
        if existing is None and previous is not None:
            existing = previous

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
        skipped: list[str] = []
        if wanted_aliases:
            wanted_aliases, skipped = aliases.split_free(
                db, object_type, wanted_aliases, exclude_id=None
            )
        return RowPlan(
            row=index,
            action="create",
            label=str(raw_label).strip(),
            key=key,
            changes=sorted(properties) + (["aliases"] if wanted_aliases else []),
            message=_alias_note(skipped, long_aliases),
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
    alias_skipped: list[str] = []
    if wanted_aliases is not None:
        wanted_aliases, alias_skipped = aliases.split_free(
            db, object_type, wanted_aliases, exclude_id=existing.id
        )
        current_aliases = aliases.human_of(db, [existing.id]).get(existing.id, [])
        if aliases_mode == "add":
            have = {compare_key(one) for one in current_aliases}
            if [one for one in wanted_aliases if compare_key(one) not in have]:
                changes.append("aliases")
        elif [compare_key(a) for a in wanted_aliases] != [
            compare_key(a) for a in current_aliases
        ]:
            changes.append("aliases")
    rename_note = ""
    if key is not None and key != existing.key:
        require_key_free(
            db,
            object_type,
            key,
            owner_workspace_id=existing.owner_workspace_id,
            exclude_id=existing.id,
        )
        changes.append("key")
        if old_key is not None:
            rename_note = (
                f"식별자를 {existing.key} → {key} 로 바꾸고 옛 것을 별칭으로 남깁니다"
            )
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
        message=" / ".join(
            one for one in (rename_note, _alias_note(alias_skipped, long_aliases)) if one
        ),
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
    source: str = "",
    aliases_mode: str = "add",
    max_rows: int = MAX_ROWS,
    on_progress: Progress = None,
    before_apply: Callable[[Plan], None] | None = None,
) -> Plan:
    """계획을 다시 세우고, 오류가 없을 때만 **한 트랜잭션으로** 넣는다.

    계획을 다시 세우는 이유: 미리 보기와 적용 사이에 다른 사람이 무엇을 바꿨을 수
    있다. 그때 옛 계획대로 넣으면 그 사람의 변경이 조용히 덮인다. `before_apply` 는
    그 새 계획을 **쓰기 전에** 보는 자리다 — 워커가 미리 본 지문과 견준다. 끝에서
    커밋하므로, 쓰고 난 뒤에 견주면 늦다.
    """
    plan = plan_objects(
        db,
        user,
        object_type,
        rows,
        owner_workspace_id=owner_workspace_id,
        source=source,
        aliases_mode=aliases_mode,
        max_rows=max_rows,
        on_progress=on_progress,
    )
    if not plan.ok:
        return plan
    if before_apply is not None:
        before_apply(plan)

    defs = [d for d in properties_of(db, object_type.id) if d.data_type != "file"]
    by_key = {d.key: d for d in defs}
    mapping, _ = _column_map(defs, {key for row in rows for key in row})
    refs = _Refs(db, user)

    for row_plan, row in zip(plan.rows, rows, strict=True):
        _tick(on_progress, "적용", row_plan.row, len(rows))
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
            raw_aliases = _fixed(row, "aliases")
            if raw_aliases is not _MISSING and raw_aliases is not None:
                # 계획에서 가른 대로 — 남이 쓰는 별칭은 여기서도 빠진다.
                free, _taken = aliases.split_free(
                    db, object_type, alias_values(raw_aliases)[0], exclude_id=target.id
                )
                if free:
                    aliases.set_human(db, target, object_type, free)
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
        before = audit_state(target)
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
        keep_old_key: str | None = None
        if row_plan.key is not None and "key" in row_plan.changes:
            if renamed_from(object_type, row) is not None and target.key:
                # **옛 식별자를 별칭으로 남긴다** — 그 번호로 적힌 문서 · 사람의 기억이 있다.
                keep_old_key = target.key
            target.key = row_plan.key
        if "aliases" in row_plan.changes:
            raw_aliases = _fixed(row, "aliases")
            free, _taken = aliases.split_free(
                db, object_type, alias_values(raw_aliases)[0], exclude_id=target.id
            )
            aliases.set_human(db, target, object_type, free, mode=aliases_mode)
        if keep_old_key is not None:
            # 별칭을 맞춘 **뒤에** 더한다 — `replace` 면 앞에서 더한 것이 지워진다.
            free, _taken = aliases.split_free(
                db, object_type, [keep_old_key], exclude_id=target.id
            )
            if free:
                aliases.set_human(db, target, object_type, free, mode="add")
        if patch:
            target.properties = validate_properties(
                defs, merge_properties(target.properties or {}, patch)
            )
        after = audit_state(target)
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
    # `renamed_from` 은 템플릿 · 내보내기에 안 넣는다 — 늘 비는 열이 하나 늘고, 내보낸 것을
    # 그대로 올리면 뜻 없는 칸이 된다. 키를 바꿀 때만 손으로 더하는 열이다.
    fixed = [one for one in FIXED_COLUMNS if one != "renamed_from"]
    return [*fixed, *(d.key for d in defs if d.data_type != "file")]


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

    names_of = aliases.human_of(db, [row.id for row in rows])
    out: list[dict[str, Any]] = []
    for row in rows:
        record: dict[str, Any] = {
            "id": str(row.id),
            "key": row.key or "",
            "label": row.label,
            "description": row.description or "",
            "aliases": MULTI_SEP.join(names_of.get(row.id, [])),
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
"""관계 파일의 **고정 열.** 그 밖의 열은 **관계 종류의 속성**으로 읽는다 —
`property_defs.owner_kind='relation'` 이 모양을 정한다(근거 건수 · 근거 종류처럼)."""

RELATION_RESERVED = (*RELATION_COLUMNS, "properties")
"""속성으로 읽지 않는 이름 — CSV 는 칸을 펼쳐 적고(`n`, `basis`), JSON · 허브 묶음은
`properties` 객체로 적는다. **같은 정의로 같게 검사한다.**"""


def relation_defs(db: Session, kind: RelationType) -> list[PropertyDef]:
    """이 관계 종류의 속성 정의 — 적은 차례로."""
    return list(
        db.scalars(
            select(PropertyDef)
            .where(PropertyDef.owner_kind == "relation", PropertyDef.owner_id == kind.id)
            .order_by(PropertyDef.sort_order, PropertyDef.id)
        )
    )


def _relation_properties(
    db: Session, kind: RelationType, row: dict[str, Any], refs: _Refs
) -> dict[str, Any]:
    """관계 한 줄의 속성 — 고정 열 밖의 칸을 그 종류의 정의로 읽는다.

    **모르는 키는 거절한다**(정의가 없는 칸). 조용히 버리면 넣은 사람은 들어간 줄 알고,
    그 사실은 아무 데도 안 적힌다.
    """
    defs = relation_defs(db, kind)
    nested = row.get("properties")
    if nested is not None and not isinstance(nested, dict):
        raise InvalidValue(
            code("OBJECTS", 47), "properties 는 키와 값을 담은 객체여야 합니다."
        )
    flat = {key: value for key, value in row.items() if key and key not in RELATION_RESERVED}
    # 펼친 칸이 이긴다 — CSV 에서 온 것이라 사람이 눈으로 본 값이다.
    extra: dict[str, Any] = {**(nested or {}), **flat}
    if not extra:
        return {}
    by_key = {one.key: one for one in defs}
    unknown = sorted(key for key in extra if key not in by_key)
    if unknown:
        raise InvalidValue(
            code("OBJECTS", 47),
            f"{kind.label}에 없는 속성입니다: {', '.join(unknown)}. "
            f"쓸 수 있는 것: {', '.join(by_key) or '(없음)'}",
        )
    values = {
        key: cell_to_value(by_key[key], value, refs)
        for key, value in extra.items()
        if not _is_blank(value)
    }
    return validate_properties(defs, values, apply_defaults=True)


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
    # 별칭(외부 식별자 포함)으로도 — 「앤시스」 로 적어도 「Ansys」 로 풀린다.
    alias_stmt = stmt.where(
        ObjectInstance.id.in_(
            select(ObjectAlias.object_id).where(ObjectAlias.norm == compare_key(text))
        )
    )
    by_alias = list(db.scalars(alias_stmt))
    if len(by_alias) == 1:
        return by_alias[0]
    if len(by_alias) > 1:
        raise InvalidValue(
            code("OBJECTS", 46),
            f"「{text}」 별칭이 {len(by_alias)}개에 맞습니다. 식별자로 적으세요.",
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


def _find_dst(db: Session, user: User, text: str, kind: RelationType) -> system.End:
    """도착점 — 객체 표 먼저, 그다음 관계가 허용한 system 타입의 원 표.

    system 끝은 관계 종류가 도착 타입을 **정해 뒀을 때만** 본다. 안 정했으면 원 표를
    전부 뒤지게 되고, 「부서 slug 를 적었더니 계정이 걸렸다」 같은 일이 생긴다.
    """
    types = system.types_by_slug(db)
    allowed = kind.dst_type_slugs or []
    plain = [types[s].id for s in allowed if s in types and not system.is_system(types[s])]
    systemic = [types[s] for s in allowed if s in types and system.is_system(types[s])]
    if not allowed or plain:
        try:
            found = _find_endpoint(db, user, text, plain or None)
            found_type = next(t for t in types.values() if t.id == found.type_id)
            return system.end_of(found, found_type)
        except InvalidValue:
            if not systemic:
                raise
    for target in systemic:
        refs = system.source_of(target).list_all(db)
        by_key = [r for r in refs if r.key == text]
        by_label = [r for r in refs if r.label.strip() == text]
        hit = by_key[0] if len(by_key) == 1 else by_label[0] if len(by_label) == 1 else None
        if hit is None and len(by_label) > 1:
            raise InvalidValue(
                code("OBJECTS", 46),
                f"「{text}」 이름이 {len(by_label)}개에 맞습니다. 식별자로 적으세요.",
            )
        if hit is None:
            hit = next((r for r in refs if str(r.id) == text), None)
        if hit is not None:
            return system.End(
                id=hit.id, type_slug=target.slug, label=hit.label, is_system=True
            )
    raise InvalidValue(code("OBJECTS", 46), f"「{text}」 을 찾을 수 없습니다.")


def plan_relations(
    db: Session,
    user: User,
    object_type: ObjectType,
    rows: list[dict[str, Any]],
    *,
    source: str = "",
    mode: str = "add",
    max_rows: int = MAX_ROWS,
    on_progress: Progress = None,
) -> Plan:
    """관계 파일 — `src, relation, dst, evidence_note` 와 그 종류의 속성 열.

    이미 이어진 것은 `unchanged`(속성·근거가 다르면 `update`). 그래서 같은 파일을 두 번
    올려도 선이 두 겹이 안 된다.

    `mode="replace"` 면 **파일에 나온 (출발 객체 · 관계 종류) 범위**에서 파일에 없는 선을
    `unlink` 로 계획에 올린다 — 사라진 관계를 정리할 길이 이것 말고 없었다(하나씩 화면에서
    끊는 것뿐).
    """
    plan = Plan()
    if len(rows) > max_rows:
        plan.errors.append(
            f"한 번에 {max_rows}행까지 넣습니다 (넣은 행 {len(rows)}). 나눠 올리세요."
        )
        return plan
    kinds = {row.slug: row for row in db.scalars(select(RelationType))}
    # 관계 종류의 속성 키도 열로 받는다 — 어느 종류의 것인지는 줄마다 다시 본다.
    known_props = {one.key for kind in kinds.values() for one in relation_defs(db, kind)}
    headers = {key for row in rows for key in row} - {""}
    unknown = sorted(headers - set(RELATION_RESERVED) - known_props)
    if unknown:
        plan.errors.append(
            f"모르는 열: {', '.join(unknown)}. "
            f"관계 파일의 열은 {', '.join(RELATION_COLUMNS)} 과 관계 종류의 속성입니다."
        )
        return plan

    seen: set[tuple[uuid.UUID, str, uuid.UUID]] = set()
    for index, row in enumerate(rows, start=1):
        _tick(on_progress, "계획", index, len(rows))
        try:
            plan.rows.append(
                _plan_relation(db, user, object_type, kinds, row, index, seen, source)
            )
        except AppError as caught:
            plan.rows.append(RowPlan(row=index, action="error", message=caught.message))
    if mode == "replace" and plan.ok:
        plan.rows.extend(_unlinks(db, seen))
    return plan


def _unlinks(db: Session, seen: set[tuple[uuid.UUID, str, uuid.UUID]]) -> list[RowPlan]:
    """파일에 없어 **끊을 선** — 파일에 나온 (출발 객체 · 관계 종류) 범위만 본다.

    범위를 그 쌍으로 잡는 이유: 파일에 아예 안 나온 객체의 선을 끊으면, 한 타입의 일부만
    담은 파일이 나머지 전부를 지운다. 그것은 되돌릴 수 없는 종류의 사고다.
    """
    pairs = {(src, kind) for src, kind, _ in seen}
    if not pairs:
        return []
    edges = db.scalars(
        select(ObjectRelation).where(
            ObjectRelation.src_object_id.in_({src for src, _ in pairs}),
            ObjectRelation.relation.in_({kind for _, kind in pairs}),
        )
    )
    doomed = [
        edge
        for edge in edges
        if (edge.src_object_id, edge.relation) in pairs
        and (edge.src_object_id, edge.relation, edge.dst_object_id) not in seen
    ]
    if not doomed:
        return []
    names = {
        row.id: row.label
        for row in db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.id.in_(
                    [one.src_object_id for one in doomed]
                    + [one.dst_object_id for one in doomed]
                )
            )
        )
    }
    return [
        RowPlan(
            row=0,
            action="unlink",
            label=f"{names.get(edge.src_object_id, '?')} -{edge.relation}-> "
            f"{names.get(edge.dst_object_id, '?')}",
            object_id=edge.id,
            message="파일에 없어 끊습니다",
        )
        for edge in doomed
    ]


def _plan_relation(
    db: Session,
    user: User,
    object_type: ObjectType,
    kinds: dict[str, RelationType],
    row: dict[str, Any],
    index: int,
    seen: set[tuple[uuid.UUID, str, uuid.UUID]],
    source: str = "",
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
    managed.require_relation_editable(kind, source=source)

    src = _find_endpoint(db, user, src_text, [object_type.id])
    dst = _find_dst(db, user, dst_text, kind)
    require_owner_edit(
        db, user, src.owner_workspace_id, what="객체", code_value=code("OBJECTS", 27)
    )
    rel.require_end_types_allowed(db, kind, object_type.slug, dst.type_slug)

    triple = (src.id, kind.slug, dst.id)
    if triple in seen:
        raise InvalidValue(code("OBJECTS", 44), "같은 관계가 이 파일에 두 번 있습니다.")
    seen.add(triple)
    label = f"{src.label} -{kind.label}-> {dst.label}"
    wanted = _relation_properties(db, kind, row, _Refs(db, user))
    if dst.is_system:
        # 한쪽 끝이 원 표면 선은 `object_links` 에 있다(속성은 안 받는다).
        found = links.existing(db, src.id, dst.id, kind.slug)
        if found is not None:
            return RowPlan(row=index, action="unchanged", label=label, object_id=found.id)
        rel.require_cardinality(db, kind, src.id, dst.id)
        return RowPlan(row=index, action="create", label=label)
    found_edge = db.scalar(
        select(ObjectRelation).where(
            ObjectRelation.src_object_id == src.id,
            ObjectRelation.dst_object_id == dst.id,
            ObjectRelation.relation == kind.slug,
        )
    )
    if found_edge is not None:
        # **이미 이어진 선이라도 속성 · 근거가 다르면 고친다.** 예전에는 늘 `unchanged` 라,
        # 근거 건수처럼 관계에 붙는 값을 나중에 채울 길이 없었다.
        note = str(row.get("evidence_note") or "").strip()
        current = found_edge.properties or {}
        changed = [key for key, value in wanted.items() if current.get(key) != value]
        if note and note != (found_edge.evidence_note or ""):
            changed.append("evidence_note")
        return RowPlan(
            row=index,
            action="update" if changed else "unchanged",
            label=label,
            object_id=found_edge.id,
            changes=sorted(changed),
        )
    rel.require_cardinality(db, kind, src.id, dst.id)
    rel.require_no_cycle(db, kind, src.id, dst.id)
    return RowPlan(row=index, action="create", label=label)


def apply_relations(
    db: Session,
    user: User,
    object_type: ObjectType,
    rows: list[dict[str, Any]],
    *,
    source: str = "",
    mode: str = "add",
    max_rows: int = MAX_ROWS,
    on_progress: Progress = None,
    before_apply: Callable[[Plan], None] | None = None,
) -> Plan:
    plan = plan_relations(
        db,
        user,
        object_type,
        rows,
        source=source,
        mode=mode,
        max_rows=max_rows,
        on_progress=on_progress,
    )
    if not plan.ok:
        return plan
    if before_apply is not None:
        before_apply(plan)
    kinds = {row.slug: row for row in db.scalars(select(RelationType))}
    by_label = {row.label: row for row in kinds.values()}
    for row_plan in plan.rows:
        _tick(on_progress, "적용", row_plan.row, len(rows))
        if row_plan.action == "unlink" and row_plan.object_id is not None:
            edge = db.get(ObjectRelation, row_plan.object_id)
            if edge is None:  # pragma: no cover - 방금 계획에서 찾았다
                continue
            audit.record(
                db,
                action="object.relation.remove",
                actor=user,
                target_table="object_relations",
                target_id=edge.id,
                target_label=row_plan.label,
                changes={"reason": "파일에 없어 끊음"},
                reason="일괄 가져오기 — 파일대로 맞춤",
            )
            db.delete(edge)
            continue
        # 파일에서 온 줄이다 — `row` 는 1부터.
        row = rows[row_plan.row - 1]
        if row_plan.action == "update" and row_plan.object_id is not None:
            edge = db.get(ObjectRelation, row_plan.object_id)
            if edge is None:  # pragma: no cover - 방금 계획에서 찾았다
                continue
            kind = kinds.get(edge.relation) or by_label[edge.relation]
            before = dict(edge.properties or {})
            wanted = _relation_properties(db, kind, row, _Refs(db, user))
            if wanted:
                edge.properties = validate_properties(
                    relation_defs(db, kind), {**before, **wanted}
                )
            note = str(row.get("evidence_note") or "").strip()
            if note:
                edge.evidence_note = note
            db.flush()
            audit.record(
                db,
                action="object.relation.update",
                actor=user,
                target_table="object_relations",
                target_id=edge.id,
                target_label=row_plan.label,
                changes={"properties": {"before": before, "after": edge.properties}},
                reason="일괄 가져오기",
            )
            continue
        if row_plan.action != "create":
            continue
        slug = str(row.get("relation") or "").strip()
        kind = kinds.get(slug) or by_label[slug]
        src = _find_endpoint(db, user, str(row.get("src") or "").strip(), [object_type.id])
        dst = _find_dst(db, user, str(row.get("dst") or "").strip(), kind)
        if dst.is_system:
            # `links.add` 가 개수 제약을 넣기 직전에 다시 본다 — 앞 행이 채웠을 수 있다.
            link = links.add(
                db,
                user,
                kind,
                system.end_of(src, object_type),
                dst,
                evidence_note=str(row.get("evidence_note") or "").strip(),
                reason="일괄 가져오기",
            )
            row_plan.object_id = link.id
            continue
        # 앞 행이 만든 관계가 카디널리티를 채웠을 수 있다 — 넣기 직전에 한 번 더.
        rel.require_cardinality(db, kind, src.id, dst.id)
        rel.require_no_cycle(db, kind, src.id, dst.id)
        edge = ObjectRelation(
            src_object_id=src.id,
            dst_object_id=dst.id,
            relation=kind.slug,
            properties=_relation_properties(db, kind, row, _Refs(db, user)),
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
        # **관계에 붙은 속성도 함께 낸다.** 안 실으면 내려받아 고쳐 다시 넣는 길에서
        # 그 값만 조용히 사라진다(근거 건수 · 근거 종류처럼).
        out.append(
            {
                "src": source.key or source.label,
                "relation": edge.relation,
                "dst": target.key or target.label,
                "evidence_note": edge.evidence_note or "",
                **{
                    key: MULTI_SEP.join(str(one) for one in value)
                    if isinstance(value, list)
                    else value
                    for key, value in (edge.properties or {}).items()
                    if value is not None and value != []
                },
            }
        )
    # 원 표(system)로 가는 선도 같은 파일에 — 도착점은 그 표의 식별자(부서 slug 등)로.
    # 안 실으면 내보낸 파일을 다시 넣었을 때 그 선만 빠지고, 빠진 것은 안 보인다.
    types = system.types_by_slug(db)
    srcs = {
        one.id: one
        for one in db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.type_id == object_type.id,
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
    }
    if srcs:
        link_rows = list(
            db.scalars(
                select(ObjectLink)
                .where(ObjectLink.src_id.in_(srcs))
                .order_by(ObjectLink.created_at)
            )
        )
        for link in link_rows:
            target_type = types.get(link.dst_type)
            if target_type is None or not system.is_system(target_type):
                continue
            ref = system.find(db, target_type, link.dst_id)
            if ref is None:
                continue
            origin = srcs[link.src_id]
            out.append(
                {
                    "src": origin.key or origin.label,
                    "relation": link.relation,
                    "dst": ref.key,
                    "evidence_note": link.evidence_note or "",
                }
            )
    return out
