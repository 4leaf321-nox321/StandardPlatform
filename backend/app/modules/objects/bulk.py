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

import contextlib
import csv
import io
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, or_, select, true
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.bundles import journal
from app.modules.objects import aliases, humanedits, links, system
from app.modules.objects import keys as key_history
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
)
from app.modules.ontology import conversion, interfaces, managed
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.modules.ontology.services import InvalidValue, merge_properties, validate_properties
from app.shared import audit, tabular
from app.shared.batches import chunks
from app.shared.errors import AppError, Conflict, code
from app.shared.permissions import require_owner_edit, visible_owner_clause
from app.shared.system_sources import SystemRef
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
    "previous_keys",
)

RELATION_MODES = ("add", "replace", "replace_type")
"""관계 파일을 **더할지 맞출지.**

    add            기본 — 더하기만 한다(예전 동작)
    replace        파일에 나온 **(출발 객체 · 관계 종류)** 범위에서 파일에 없는 선을 끊는다
    replace_type   파일에 나온 **(출발 타입 · 관계 종류)** 전체에서 끊는다

`replace` 는 파일에서 **통째로 빠진 객체**의 옛 선을 남긴다 — 그 객체가 파일에 없으니
범위에 안 들어온다. 원천에서 그 객체의 선이 다 사라진 경우가 그것이고, 그때는
`replace_type` 이 맞다. **범위가 넓은 만큼 위험도 크다** — 일부만 담은 파일로 돌리면
나머지 전부가 끊긴다. 그래서 기본이 아니고, 계획에 `unlink` 로 먼저 보인다.

원 표면 선(`object_links`)은 어느 쪽도 건드리지 않는다."""

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


#: 참/거짓 어휘 · 숫자 해석은 종류 변경과 **한 벌**이다(`ontology/conversion.py`) — 두 벌이면
#: 같은 「1,234」 가 파일로는 거절되고 종류 변경으로는 받아진다.
TRUE_WORDS = conversion.TRUE_WORDS
FALSE_WORDS = conversion.FALSE_WORDS


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
    index: Any = None
    """계획이 미리 읽은 것(`_Index`) — **적용이 그대로 쓴다.** 밖으로는 안 나간다
    (API 는 `rows` · `errors` · `counts` 만 본다)."""

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


class MissingRef(Exception):
    """가리키는 것을 **못 찾았다** — 그 칸만 비우고 줄에 적기로 했을 때.

    기본은 줄 오류다(전부 아니면 무). 그런데 백필에서는 **한 줄이 묶음 전체를 막는다** —
    PLM 에 없는 모델을 가리키는 행 하나 때문에 수만 줄이 안 들어간다. 별칭이 겹칠 때와 같은
    규칙으로, 그 칸만 비우고 무엇이 빠졌는지 적는다.
    """

    def __init__(self, text: str, label: str) -> None:
        super().__init__(text)
        self.text = text
        self.label = label


class PendingRef(Exception):
    """**이 묶음이 만들 객체**를 가리킨다 — 지금은 없지만 적용할 때 풀린다.

    가벼운 미리 보기(계획만)에서 쓴다. 예전에는 미리 보기가 **적용을 끝까지 돌린 뒤
    되돌렸다** — 그래야 뒤 묶음이 앞 묶음의 객체를 찾는데, 그 때문에 대량 백필은 시간이
    두 배였다(실측). 계획만 볼 때는 「이 묶음이 만든다」 를 아는 것으로 충분하다.
    """

    def __init__(self, text: str, type_slug: str) -> None:
        super().__init__(text)
        self.text = text
        self.type_slug = type_slug


@dataclass(frozen=True)
class RefMatch:
    """글자 하나를 참조 대상에서 찾은 결과 — 하나로 풀림 · 여럿에 맞음 · 못 찾음."""

    kind: str
    """`one` · `many` · `none`."""
    id: str | None = None
    how: str = ""
    """어디로 맞았나 — `key` · `alias` · `label` · `id`(여럿이면 `key` 는 구현 타입 여럿의
    식별자, `label` 은 같은 이름 여럿)."""
    count: int = 0
    """이름이 여럿에 맞을 때 몇 개에."""
    shadowed: bool = False
    """별칭으로 풀렸는데 그 글자가 **다른 객체의 이름**이기도 하다."""


@dataclass
class _RefIndex:
    """한 참조 대상(타입, 또는 인터페이스의 구현 타입 전부)의 식별자 · 별칭 · 이름 → id."""

    by_key: dict[str, uuid.UUID] = field(default_factory=dict)
    by_alias: dict[str, uuid.UUID] = field(default_factory=dict)
    by_label: dict[str, list[uuid.UUID]] = field(default_factory=dict)
    ids: set[str] = field(default_factory=set)
    clashing: set[str] = field(default_factory=set)
    """**두 구현 타입에 같은 식별자**가 있다 — 식별자는 타입마다 따로라 그 글자만으로는 어느
    것인지 정해지지 않는다. 짐작하지 않고 그 줄을 거절한다."""


class Refs:
    """참조 풀이 — 상대 타입의 식별자·이름을 id 로. 대상마다 한 번만 읽는다.

    대상이 인터페이스면 그것을 구현한 타입 전부에서 찾는다(ADR 0006). 저장할 때의 대상 검사와
    같은 범위다 — 여기서 넓게 찾아 놓고 저장에서 거절하면 사람은 왜 막혔는지 모른다.
    """

    def __init__(
        self,
        db: Session,
        user: User | None,
        pending: dict[str, set[str]] | None = None,
        blank_missing: bool = False,
    ) -> None:
        self.db = db
        self.user = user
        """`None` 이면 보이는 범위를 안 건다 — 종류 변경(시스템 관리자 · 허브의 일)만 그렇게
        쓴다."""
        self.pending = pending or {}
        """타입 slug → **이 묶음이 만들 것**의 식별자 · 이름. 계획만 볼 때만 채운다."""
        self.blank_missing = blank_missing
        """참이면 못 찾은 참조를 **줄 오류로 만들지 않고** 그 칸만 비운다(`MissingRef`)."""
        self.notes: list[str] = []
        """줄에 적을 말 — **별칭으로 풀렸는데 그 글자가 다른 객체의 이름이기도** 할 때.
        부르는 쪽이 줄마다 비우고 읽는다(`_plan_row`)."""
        self.blanked_required: list[tuple[str, str]] = []
        """**필수 참조 칸**인데 가리키는 것을 못 찾았다 — (속성 키, 사람이 읽는 말).

        빈 값으로 둘 수 없는 칸이라(「값이 필요합니다」) 비우기로는 안 풀린다. 부르는 쪽이
        그 줄을 건너뛸지(새로 만드는 줄) 그대로 둘지(이미 값이 있는 줄) 정한다."""
        self.cache: dict[str, _RefIndex] = {}
        self._ends: interfaces.Ends | None = None

    def _members(self, target: str) -> list[str]:
        """대상이 담는 타입들 — 타입이면 그것 하나, 인터페이스면 구현 타입 전부."""
        if self._ends is None:
            self._ends = interfaces.load_ends(self.db)
        if target in self._ends.interfaces:
            return sorted(self._ends.expand([target]) or ())
        return [target]

    def _load_type(self, type_slug: str) -> _RefIndex:
        out = _RefIndex()
        object_type = self.db.scalar(select(ObjectType).where(ObjectType.slug == type_slug))
        if object_type is not None and system.is_system(object_type):
            # 원 표를 비추는 타입 — 식별자는 그 표의 것(부서 slug · 로그인 아이디).
            for ref in system.source_of(object_type).list_all(self.db):
                out.ids.add(str(ref.id))
                out.by_key[ref.key] = ref.id
                out.by_label.setdefault(ref.label.strip(), []).append(ref.id)
        elif object_type is not None:
            # 별칭·외부 식별자도 식별자처럼 — 「앤시스」 로 적어도 「Ansys」 로 풀린다.
            for norm, hits in aliases.index_of(self.db, object_type).items():
                if len(hits) == 1 and norm not in out.by_key:
                    out.by_alias[norm] = hits[0]
            # 칸 셋만 읽는다 — 행 전체(속성 JSONB)를 읽으면 대상이 큰 타입일 때 메모리가 먼저
            # 바닥난다.
            rows = self.db.execute(
                select(ObjectInstance.id, ObjectInstance.key, ObjectInstance.label).where(
                    ObjectInstance.type_id == object_type.id,
                    ObjectInstance.deleted_at.is_(None),
                    true()
                    if self.user is None
                    else visible_owner_clause(self.user, ObjectInstance.owner_workspace_id),
                )
            )
            for row_id, row_key, row_label in rows:
                out.ids.add(str(row_id))
                if row_key:
                    out.by_key[row_key] = row_id
                out.by_label.setdefault(row_label.strip(), []).append(row_id)
        return out

    def _load(self, target: str) -> _RefIndex:
        if target not in self.cache:
            members = self._members(target)
            if len(members) == 1:
                self.cache[target] = self._load_type(members[0])
            else:
                merged = _RefIndex()
                shared_aliases: set[str] = set()
                for one in members:
                    part = self._load_type(one)
                    for key, found in part.by_key.items():
                        if key in merged.by_key and merged.by_key[key] != found:
                            merged.clashing.add(key)
                        merged.by_key.setdefault(key, found)
                    for norm, found in part.by_alias.items():
                        if norm in merged.by_alias and merged.by_alias[norm] != found:
                            shared_aliases.add(norm)
                        merged.by_alias.setdefault(norm, found)
                    for label, hits in part.by_label.items():
                        merged.by_label.setdefault(label, []).extend(hits)
                    merged.ids |= part.ids
                # 두 타입에 같은 별칭이면 별칭으로는 안 정해진다 — 이름 풀이로 넘긴다.
                for norm in shared_aliases:
                    merged.by_alias.pop(norm, None)
                self.cache[target] = merged
        return self.cache[target]

    def missing(self, defs: list[PropertyDef], values: dict[str, Any]) -> list[str]:
        """가리키는 것이 **없는**(또는 대상 밖인) 값들 — 캐시로 본다(질의 없음).

        예전에는 줄마다 `require_refs_exist` 가 질의를 돌았다. 이 캐시는 그 대상의 살아
        있는 id 를 이미 들고 있다(이름 풀이가 그것으로 맞춘다) — 같은 사실을 두 번 묻던
        셈이다.
        """
        out: list[str] = []
        for definition in defs:
            if definition.data_type != "object_ref" or definition.key not in values:
                continue
            raw = values[definition.key]
            items = raw if isinstance(raw, list) else [raw]
            ids = self._load(definition.ref_type_slug or "").ids
            out.extend(str(one) for one in items if one and str(one) not in ids)
        return out

    def classify(self, target: str, raw: str) -> RefMatch:
        """`raw` 가 `target` 의 무엇으로 풀리나 — 식별자 → 별칭 → 이름 → uuid 차례.

        **판정은 여기 한 벌이다.** 넣을 때(`resolve`)도, 표에서 타입을 만들 때 열이 어느 타입을
        가리키나 셀 때(`ontology.linking`)도 이것을 쓴다 — 따로 세면 후보는 97% 라는데 넣으면
        거절된다(ADR 0009)."""
        index = self._load(target)
        text = raw.strip()
        if text in index.clashing:
            return RefMatch("many", how="key")
        if text in index.by_key:
            return RefMatch("one", str(index.by_key[text]), how="key")
        norm = compare_key(text)
        if norm in index.by_alias:
            found = index.by_alias[norm]
            shadowed = any(one != found for one in index.by_label.get(text, []))
            return RefMatch("one", str(found), how="alias", shadowed=shadowed)
        hits = index.by_label.get(text, [])
        if len(hits) == 1:
            return RefMatch("one", str(hits[0]), how="label")
        if len(hits) > 1:
            return RefMatch("many", how="label", count=len(hits))
        # uuid 로 적었어도 **있는 것이어야** 한다 — 모양만 보고 받으면 없는 것을 가리키는
        # 참조가 저장되고, 화면에는 빈 칸으로 뜬다.
        if text in index.ids:
            return RefMatch("one", text, how="id")
        return RefMatch("none")

    def resolve(self, definition: PropertyDef, raw: str) -> str:
        """식별자 → 별칭 → 이름 → uuid 순으로 맞춘다. 이름이 여럿에 맞으면 거절."""
        target = definition.ref_type_slug or ""
        text = raw.strip()
        match = self.classify(target, text)
        if match.kind == "one" and match.id is not None:
            if match.shadowed:
                # **조용히 별칭 쪽에 붙는다** — 이름 풀이는 식별자 → 별칭 → 이름 차례이기
                # 때문이다. 어느 쪽인지 사람이 정해야 할 자리라 줄에 적는다(품질 보고서의
                # `alias_clash` 가 같은 사실을 목록으로 본다).
                self.notes.append(
                    f"{definition.label}: 「{text}」 은 다른 객체의 **이름**이기도 합니다 — "
                    "별칭 쪽에 붙였습니다"
                )
            return match.id
        if match.kind == "many" and match.how == "key":
            raise InvalidValue(
                code("OBJECTS", 41),
                f"{definition.label}: 식별자 「{text}」 이 {target} 를 구현한 타입 여럿에 "
                "있습니다 — 식별자는 타입마다 따로라 어느 것인지 정해지지 않습니다. id 로 "
                "적으세요.",
            )
        if match.kind == "many":
            raise InvalidValue(
                code("OBJECTS", 41),
                f"{definition.label}: 「{text}」 이름이 {match.count}개에 맞습니다. "
                "식별자로 적으세요.",
            )
        if any(text in self.pending.get(one, set()) for one in self._members(target)):
            raise PendingRef(text, target)
        if self.blank_missing:
            raise MissingRef(text, definition.label)
        raise InvalidValue(
            code("OBJECTS", 41),
            f"{definition.label}: 「{text}」 을 {target} 에서 찾을 수 없습니다.",
        )


def _one_from_text(definition: PropertyDef, raw: Any, refs: Refs) -> Any:
    """칸 하나를 그 속성의 모양으로. JSON 으로 온 값(이미 숫자·불)은 그대로 둔다."""
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    kind = definition.data_type
    if kind == "number":
        try:
            return conversion.parse_number(text)
        except conversion.Unconvertible:
            raise InvalidValue(
                code("ONTOLOGY", 11), f"{definition.label}: 숫자여야 합니다: {text!r}"
            ) from None
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


def cell_to_value(definition: PropertyDef, raw: Any, refs: Refs) -> Any:
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


def _only(asked: list[aliases.Incoming], values: list[str]) -> list[aliases.Incoming]:
    """이 값들만 남긴다 — 겹침 판정은 글자로 하고(`split_free`), 출처 · 메모는 여기서
    다시 잇는다."""
    keep = {compare_key(one) for one in values}
    return [one for one in asked if compare_key(one.value) in keep]


def _same_file_note(double: list[str]) -> str:
    """같은 파일의 앞줄이 먼저 쓴 별칭 — **미리 보기에서 말해야** 사람이 파일을 고친다."""
    if not double:
        return ""
    return f"별칭 {len(double)}개는 이 파일의 앞줄이 먼저 씁니다: {', '.join(double)}"


def _joined(*parts: str) -> str:
    """줄에 적을 말 여럿을 한 줄로 — 빈 것은 뺀다."""
    return " / ".join(one for one in parts if one)


def alias_values(raw: Any) -> tuple[list[aliases.Incoming], list[str]]:
    """별칭 칸 → (쓸 수 있는 것, 너무 길어 뺀 것).

    한 칸은 **글자**이거나 **`{value, source, note}`** 다. 뒤쪽은 적재가 「어디서 온
    이름인가」 를 남기는 자리다 — 수천 개가 붙은 뒤에 그것을 물을 자리가 없으면, 사람은
    지워도 되는지 판단할 수 없어서 아무것도 안 지운다.

    ⚠️ **목록으로 오면 쪼개지 않는다.** 예전에는 `str(raw).split(";")` 이라, JSON 으로 온
       `["박리", "코팅 박리"]` 가 `"['박리', '코팅 박리']"` **한 덩어리**로 저장됐다(실측).
    ⚠️ 글 하나로 오면 `;` 로 가른다(CSV 한 칸에 여럿을 적는 길). 그래서 **별칭 안에 `;` 가
       든 것은 목록으로 보내야 살아남는다** — 고장 모드 80건 · 메커니즘 482건이 그렇다.
    """
    if raw is None:
        return [], []
    items = raw if isinstance(raw, list | tuple) else str(raw).split(MULTI_SEP)
    asked: list[aliases.Incoming] = []
    for one in items:
        if isinstance(one, dict):
            value = str(one.get("value") or "").strip()
            kind = str(one.get("kind") or aliases.HUMAN).strip()
            if kind != aliases.HUMAN:
                # 외부 식별자(`source:<slug>`)는 **동기화가 남긴다** — 파일로 받으면 다음
                # 동기화가 그것으로 엉뚱한 객체를 찾는다.
                raise InvalidValue(
                    code("OBJECTS", 91),
                    f"별칭의 kind 는 {aliases.HUMAN} 만 받습니다: {kind}. "
                    "외부 식별자는 데이터 소스가 남깁니다.",
                )
            if value:
                asked.append(
                    aliases.Incoming(
                        value=value,
                        source=str(one.get("source") or "").strip(),
                        note=str(one.get("note") or "").strip(),
                        verified=bool(one.get("verified")),
                    )
                )
            continue
        asked.append(aliases.Incoming(value=str(one)))
    # 빈 것 · 겹치는 것을 거르는 규칙은 한 곳이다(`aliases.clean`) — 여기서 따로 적으면
    # 화면과 파일이 다르게 판정한다.
    keep = {compare_key(one) for one in aliases.clean([one.value for one in asked])}
    kept = [one for one in asked if compare_key(one.value) in keep]
    seen: set[str] = set()
    unique: list[aliases.Incoming] = []
    for one in kept:
        norm = compare_key(one.value)
        if norm in seen:
            continue
        seen.add(norm)
        unique.append(one)
    long = [one.value for one in unique if len(one.value) > aliases.MAX_VALUE]
    return [one for one in unique if len(one.value) <= aliases.MAX_VALUE], long


def _is_blank(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


def renamed_history(object_type: ObjectType, row: dict[str, Any]) -> list[str]:
    """이 행이 말하는 **옛 식별자 전부** — 오래된 것부터.

    `renamed_from` 한 칸과 `previous_keys` 목록을 함께 읽는다. 받는 쪽이 오래 잠들어
    있었으면 가진 것은 마지막 옛 식별자가 아니다(A→B→C 에서 A 를 들고 있다).
    """
    out: list[str] = []
    raw = row.get("previous_keys")
    if isinstance(raw, list | tuple):
        out.extend(
            one
            for one in (normalize_key(object_type, str(x)) for x in raw if str(x).strip())
            if one
        )
    last = renamed_from(object_type, row)
    if last and last not in out:
        out.append(last)
    return out


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
    refs: Refs,
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
        try:
            patch[prop_key] = cell_to_value(by_key[prop_key], raw, refs)
        except PendingRef as later:
            # **이 묶음이 만들 것**을 가리킨다 — 계획만 볼 때는 오류가 아니다. 그 칸은
            # 여기서 빼고(값을 지어내지 않는다) 줄에 적는다.
            refs.notes.append(
                f"{by_key[prop_key].label}: 「{later.text}」 은 이 묶음이 만드는 "
                "객체입니다 — 적용할 때 풀립니다"
            )
        except MissingRef as gone:
            if by_key[prop_key].required:
                # **필수 칸은 비울 수 없다.** 비우면 그 줄이 「값이 필요합니다」 오류가 되고,
                # 오류 한 줄이 묶음 전체를 막는다 — `missing_refs=blank` 는 바로 그것을
                # 막으려고 켠 것인데 필수 칸에서는 아무 소용이 없었다(실측). 여기서는 그
                # 칸을 **안 보낸 것으로** 두고, 부르는 쪽이 줄을 건너뛸지 정한다.
                refs.blanked_required.append((prop_key, f"{gone.label}: 「{gone.text}」"))
                continue
            # **한 줄이 묶음 전체를 막지 않게** 그 칸만 비운다 — 무엇이 빠졌는지는 적는다.
            refs.notes.append(f"{gone.label}: 「{gone.text}」 을 찾지 못해 비웁니다")
    return patch


# --- 객체 -----------------------------------------------------------------------


@dataclass
class _Index:
    """파일 한 장을 판정하는 데 필요한 것을 **한 번에 미리 읽는다.**

    ⚠️ 왜 있나: 줄마다 개수 질의를 돌았다 — 2만 줄에서 계획 29초 · 새로 적용 92초 ·
       **다시 적용(전부 그대로) 193초**(실측). 표가 커질수록 느려지고, 재적재는 흔한 일이라
       그 수는 그대로 사람이 기다리는 시간이 된다.

    담는 것은 **이 파일에 나온 것**뿐이다(식별자 · 별칭 값 · 유일 속성 값) — 표 전체를
    들고 오면 객체가 몇십만인 날 이쪽이 죽는다.
    """

    by_key: dict[str, ObjectInstance] = field(default_factory=dict)
    """식별자 → 객체. **부서 범위까지 맞춘 것**(`key_scope`)."""
    by_id: dict[uuid.UUID, ObjectInstance] = field(default_factory=dict)
    visible: set[uuid.UUID] = field(default_factory=set)
    """이 사람이 볼 수 있는 것 — 목록과 같은 규칙."""
    human: dict[uuid.UUID, list[ObjectAlias]] = field(default_factory=dict)
    """객체 → 지금 붙어 있는 사람 별칭(행 그대로). **값만 들고 있으면** 「검수했다」 가
    바뀐 것을 못 본다 — 허브에서 확인한 줄이 쌍둥이에 영영 검수 대기로 남는다."""
    alias_owner: dict[str, uuid.UUID] = field(default_factory=dict)
    """별칭 비교키 → 그것을 쓰는 객체(사람 별칭만)."""
    unique: dict[str, dict[str, list[tuple[uuid.UUID, uuid.UUID | None]]]] = field(
        default_factory=dict
    )
    """유일 속성 키 → 값 → [(객체, 그 객체의 부서)]. **부서는 파이썬에서 가른다** —
    범위(`key_scope`)가 줄마다 다른 객체를 가리킬 수 있어서다."""
    editable: dict[uuid.UUID | None, AppError | None] = field(default_factory=dict)
    """부서마다 한 번만 판정한다 — 같은 부서의 오천 줄에 오천 번 물을 이유가 없다."""
    claimed: dict[str, int] = field(default_factory=dict)
    """이 파일의 **앞줄이 먼저 쓴 별칭** — 비교키 → 그 줄 번호.

    ⚠️ 표의 유일 제약은 「한 별칭은 한 객체」 다. 같은 파일에 같은 별칭이 둘이면 뒷줄 것이
       조용히 빠지는데, 예전에는 **미리 보기에 그 말이 없었다** — 적용하고 나서야 없는 것을
       본다(실측)."""


def _read_index(
    db: Session,
    user: User,
    object_type: ObjectType,
    rows: list[dict[str, Any]],
    defs: list[PropertyDef],
    patches: list[dict[str, Any] | AppError],
    *,
    owner_workspace_id: uuid.UUID | None,
) -> _Index:
    """계획이 물을 것을 미리 한 번에 — 줄마다 돌던 질의를 없앤다."""
    index = _Index()

    keys: set[str] = set()
    ids: set[uuid.UUID] = set()
    for row in rows:
        raw_key = _fixed(row, "key")
        if raw_key not in (_MISSING, None):
            wanted = normalize_key(object_type, str(raw_key))
            if wanted:
                keys.add(wanted)
        for old in renamed_history(object_type, row):
            keys.add(old)
        raw_id = _fixed(row, "id")
        if raw_id not in (_MISSING, None):
            try:
                ids.add(uuid.UUID(str(raw_id)))
            except ValueError:
                continue

    # **나눠 묻는다** — 10만 행 파일이면 식별자 · id 가 바인드 한도(65,535)를 넘어 질의가
    # 보내지지도 않았다(실측).
    alive = (ObjectInstance.type_id == object_type.id, ObjectInstance.deleted_at.is_(None))
    by_id_found: dict[uuid.UUID, ObjectInstance] = {}
    for batch in chunks(sorted(keys)):
        by_key_clause: Any = ObjectInstance.key.in_(batch)
        if object_type.key_scope == "workspace":
            # 범위가 부서면 **그 부서 안에서만** 같은 식별자다.
            by_key_clause = and_(
                by_key_clause,
                ObjectInstance.owner_workspace_id.is_(None)
                if owner_workspace_id is None
                else ObjectInstance.owner_workspace_id == owner_workspace_id,
            )
        for one in db.scalars(select(ObjectInstance).where(*alive, by_key_clause)):
            by_id_found[one.id] = one
    for id_batch in chunks(sorted(ids)):
        for one in db.scalars(
            select(ObjectInstance).where(*alive, ObjectInstance.id.in_(id_batch))
        ):
            by_id_found[one.id] = one
    found = list(by_id_found.values())
    for one in found:
        index.by_id[one.id] = one
        if one.key:
            index.by_key.setdefault(one.key, one)
    if found:
        for found_batch in chunks([one.id for one in found]):
            index.visible.update(
                db.scalars(
                    select(ObjectInstance.id).where(
                        ObjectInstance.id.in_(found_batch),
                        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
                    )
                )
            )
        index.human = {
            object_id: [one for one in rows if one.kind == aliases.HUMAN]
            for object_id, rows in aliases.of(db, [one.id for one in found]).items()
        }

    values: list[str] = []
    for row in rows:
        try:
            asked, _ = alias_values(_fixed(row, "aliases"))
        except AppError:
            # 모양이 틀린 칸은 **줄 오류**다 — 여기서 터뜨리면 파일 전체가 거절되고,
            # 그 줄이 어느 줄인지 아무 데도 안 적힌다. 계획이 줄마다 다시 읽는다.
            continue
        values.extend(one.value for one in asked)
    if values:
        index.alias_owner = aliases.taken_by(db, object_type, aliases.HUMAN, values)

    for definition in [one for one in defs if one.unique]:
        wanted_values = {
            str(patch[definition.key])
            for patch in patches
            if isinstance(patch, dict) and patch.get(definition.key) not in (None, "", [])
        }
        if not wanted_values:
            continue
        hits: dict[str, list[tuple[uuid.UUID, uuid.UUID | None]]] = {}
        for batch in chunks(sorted(wanted_values)):
            for object_id, workspace_id, value in db.execute(
                select(
                    ObjectInstance.id,
                    ObjectInstance.owner_workspace_id,
                    ObjectInstance.properties[definition.key].astext,
                ).where(
                    ObjectInstance.type_id == object_type.id,
                    ObjectInstance.deleted_at.is_(None),
                    ObjectInstance.properties[definition.key].astext.in_(batch),
                )
            ):
                hits.setdefault(str(value), []).append((object_id, workspace_id))
        index.unique[definition.key] = hits
    return index


def _remember(index: _Index, values: list[str], object_id: uuid.UUID) -> None:
    """방금 붙인 별칭을 미리 읽은 것에 적어 둔다 — **같은 파일의 뒷줄이 그것을 봐야 한다.**
    안 적으면 한 파일에 같은 별칭이 둘일 때 둘 다 붙이려다 표의 유일 제약에 걸린다."""
    for value in values:
        index.alias_owner[compare_key(value)] = object_id


def _forget(index: _Index, values: list[str]) -> None:
    """이 파일에서 **뺀** 별칭을 미리 읽은 목록에서도 지운다 — 뒷줄이 그것을 가져간다."""
    for value in values:
        index.alias_owner.pop(compare_key(value), None)


def _require_refs(refs: Refs, defs: list[PropertyDef], values: dict[str, Any]) -> None:
    """가리키는 것이 있나 — 캐시로 본다. 없는 것을 가리키면 화면에 빈 칸으로 나오고,
    그것이 「값 없음」 인지 「사라짐」 인지 구별할 수 없다."""
    missing = refs.missing(defs, values)
    if missing:
        raise InvalidValue(
            code("OBJECTS", 4), f"가리키는 객체를 찾을 수 없습니다: {', '.join(missing)}"
        )


def _split_free(
    index: _Index,
    asked: list[aliases.Incoming],
    *,
    exclude_id: uuid.UUID | None,
    row: int = 0,
) -> tuple[list[str], list[str], list[str]]:
    """쓸 수 있는 별칭 · 남이 쓰는 별칭 · **이 파일의 앞줄이 먼저 쓴 별칭.**

    판정은 `aliases.split_free` 와 **같아야 한다**(비교키로 보고, 자기 것은 뺀다).
    `row` 를 주면 같은 파일 안의 겹침도 가른다 — 계획이 그것을 말해야 사람이 파일을 고친다.
    """
    free: list[str] = []
    taken: list[str] = []
    mine: list[str] = []
    for one in asked:
        norm = compare_key(one.value)
        holder = index.alias_owner.get(norm)
        earlier = index.claimed.get(norm)
        if holder is not None and holder != exclude_id:
            taken.append(one.value)
        elif row and earlier is not None and earlier != row:
            mine.append(f"{one.value}({earlier}행)")
        else:
            if row:
                index.claimed[norm] = row
            free.append(one.value)
    return free, taken, mine


def _require_editable(
    db: Session, user: User, index: _Index, owner_workspace_id: uuid.UUID | None
) -> None:
    """고칠 수 있나 — **부서마다 한 번만** 판정하고 그 답을 되쓴다."""
    if owner_workspace_id not in index.editable:
        try:
            require_owner_edit(
                db, user, owner_workspace_id, what="객체", code_value=code("OBJECTS", 16)
            )
        except AppError as caught:
            index.editable[owner_workspace_id] = caught
        else:
            index.editable[owner_workspace_id] = None
    refused = index.editable[owner_workspace_id]
    if refused is not None:
        raise refused


def _unique_clash(
    object_type: ObjectType,
    defs: list[PropertyDef],
    index: _Index,
    values: dict[str, Any],
    *,
    owner_workspace_id: uuid.UUID | None,
    exclude_id: uuid.UUID | None,
) -> None:
    """유일 속성이 이미 쓰이고 있나 — 미리 읽은 것으로 본다(질의 없음).

    판정 규칙은 `services.require_unique_properties` 와 **같아야 한다** — 범위는
    `key_scope` 를 따르고, 자기 자신은 뺀다.
    """
    for definition in defs:
        if not definition.unique or definition.key not in values:
            continue
        value = values.get(definition.key)
        if value in (None, "", []):
            continue
        for other_id, other_workspace in index.unique.get(definition.key, {}).get(
            str(value), []
        ):
            if exclude_id is not None and other_id == exclude_id:
                continue
            if object_type.key_scope == "workspace" and other_workspace != owner_workspace_id:
                continue
            where = "이 부서에" if object_type.key_scope == "workspace" else "이미"
            raise Conflict(
                code("OBJECTS", 5),
                f"{definition.label}에 같은 값이 {where} 있습니다: {value}. "
                "같은 것이 둘이 되면 둘 다 못 믿게 됩니다 — 찾아서 수정하는 편이 낫습니다.",
            )


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
    pending: dict[str, set[str]] | None = None,
    blank_missing: bool = False,
    human_edits: str = "keep",
    max_rows: int = MAX_ROWS,
    on_progress: Progress = None,
) -> Plan:
    """행마다 무엇이 될지 — **아무것도 안 바꾼다.** `source` 는 허브에서 받는 묶음만 적는다.

    `human_edits` 는 **사람이 화면에서 고친 칸**을 어떻게 할지다. 기본 `keep` 은 비켜 가고 줄에
    적는다 — 원천을 다시 정제해 넣을 때마다 사람의 수정이 사라지면, 사람은 고치기를 그만둔다.
    `overwrite` 는 덮고 표시를 지운다(사람이 그렇게 정했을 때만).
    """
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
    refs = Refs(db, user, pending, blank_missing)

    # 이 파일 안에서 같은 식별자가 둘이면 어느 쪽이 맞는지 알 수 없다.
    seen_keys: dict[str, int] = {}
    seen_ids: dict[str, int] = {}

    # **칸 값을 먼저 한 바퀴 읽는다** — 그래야 유일 속성을 미리 물을 수 있다. 줄마다
    # 나는 오류(값 모양 · 참조 못 찾음)는 그 줄의 것으로 들고 있다가 아래에서 낸다.
    patches: list[dict[str, Any] | AppError] = []
    for row in rows:
        refs.notes.clear()
        refs.blanked_required.clear()
        try:
            patches.append(_patch_of(row, mapping, by_key, refs))
        except AppError as caught:
            patches.append(caught)
    index_data = _read_index(
        db, user, object_type, rows, defs, patches, owner_workspace_id=owner_workspace_id
    )
    plan.index = index_data

    for index, (row, patch) in enumerate(zip(rows, patches, strict=True), start=1):
        _tick(on_progress, "계획", index, len(rows))
        try:
            if isinstance(patch, AppError):
                raise patch
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
                    patch=patch,
                    index_data=index_data,
                    owner_workspace_id=owner_workspace_id,
                    aliases_mode=aliases_mode,
                    seen_keys=seen_keys,
                    seen_ids=seen_ids,
                    human_edits=human_edits,
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
    refs: Refs,
    row: dict[str, Any],
    index: int,
    *,
    patch: dict[str, Any],
    index_data: _Index,
    owner_workspace_id: uuid.UUID | None,
    aliases_mode: str,
    seen_keys: dict[str, int],
    seen_ids: dict[str, int],
    human_edits: str = "keep",
) -> RowPlan:
    # 칸 값은 이미 읽었다 — 그때 붙은 말(별칭이 남의 이름이기도 하다)만 다시 모은다.
    refs.notes.clear()
    refs.blanked_required.clear()
    _patch_of(row, mapping, by_key, refs)

    raw_id = _fixed(row, "id")
    raw_key = _fixed(row, "key")
    raw_label = _fixed(row, "label")
    raw_status = _fixed(row, "status")
    raw_description = _fixed(row, "description")
    raw_from = _fixed(row, "valid_from_year")
    raw_to = _fixed(row, "valid_to_year")
    raw_aliases = _fixed(row, "aliases")
    wanted_aliases: list[aliases.Incoming] | None = None
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
        existing = index_data.by_id.get(wanted)
        if existing is None or existing.id not in index_data.visible:
            raise InvalidValue(code("OBJECTS", 43), f"id 에 맞는 객체가 없습니다: {raw_id}")
    key = normalize_key(object_type, None if raw_key in (_MISSING, None) else str(raw_key))
    if existing is None and key is not None:
        if key in seen_keys:
            raise InvalidValue(
                code("OBJECTS", 44), f"같은 식별자가 {seen_keys[key]}행에도 있습니다: {key}"
            )
        seen_keys[key] = index
        existing = index_data.by_key.get(key)
        if existing is not None and existing.id not in index_data.visible:
            # 같은 식별자가 남의 부서에 있다 — 없는 것과 같은 말로 답하되 만들지도 못한다.
            raise InvalidValue(code("OBJECTS", 3), f"같은 식별자가 이미 있습니다: {key}")

    old_keys = renamed_history(object_type, row)
    old_key = old_keys[-1] if old_keys else None
    if old_key is not None:
        if key is None:
            raise InvalidValue(
                code("OBJECTS", 90),
                "renamed_from 을 적으면 key 에 **새 식별자**를 적어야 합니다.",
            )
        # **이력을 거꾸로 훑는다** — 받는 쪽이 들고 있는 것이 어느 판인지 모른다.
        previous = next(
            (found for one in reversed(old_keys) if (found := index_data.by_key.get(one))),
            None,
        )
        if previous is not None and previous.id not in index_data.visible:
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
        if refs.blanked_required:
            # 새로 만드는 줄은 그 칸에 들어갈 값이 없다 — **이 줄만 건너뛴다.**
            return RowPlan(
                row=index,
                action="unchanged",
                label=str(raw_label).strip(),
                key=key,
                message=_joined(
                    "필수 참조를 찾지 못해 이 줄을 건너뜁니다 — "
                    + " · ".join(text for _key, text in refs.blanked_required),
                    *refs.notes,
                ),
            )
        properties = validate_properties(
            defs, {k: v for k, v in patch.items() if v is not None}, apply_defaults=True
        )
        _require_refs(refs, defs, properties)
        _unique_clash(
            object_type,
            defs,
            index_data,
            properties,
            owner_workspace_id=owner_workspace_id,
            exclude_id=None,
        )
        skipped: list[str] = []
        double: list[str] = []
        if wanted_aliases:
            free, skipped, double = _split_free(
                index_data, wanted_aliases, exclude_id=None, row=index
            )
            wanted_aliases = _only(wanted_aliases, free)
        return RowPlan(
            row=index,
            action="create",
            label=str(raw_label).strip(),
            key=key,
            changes=sorted(properties) + (["aliases"] if wanted_aliases else []),
            message=_joined(
                *refs.notes, _alias_note(skipped, long_aliases), _same_file_note(double)
            ),
        )

    # 고침 — 보낸 것만.
    _require_editable(db, user, index_data, existing.owner_workspace_id)
    if refs.blanked_required:
        current_props = existing.properties or {}
        empty = [
            text
            for prop_key, text in refs.blanked_required
            if current_props.get(prop_key) in (None, "", [])
        ]
        if empty:
            # 지금도 비어 있고 채울 것도 못 찾았다 — **이 줄만 건너뛴다**(필수 칸이라
            # 빈 채로는 저장이 안 된다).
            return RowPlan(
                row=index,
                action="unchanged",
                label=existing.label,
                key=existing.key,
                object_id=existing.id,
                message=_joined(
                    "필수 참조를 찾지 못해 이 줄을 건너뜁니다 — " + " · ".join(empty),
                    *refs.notes,
                ),
            )
        refs.notes.append(
            "찾지 못한 필수 참조는 그대로 둡니다 — "
            + " · ".join(text for _key, text in refs.blanked_required)
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
    alias_double: list[str] = []
    if wanted_aliases is not None:
        free, alias_skipped, alias_double = _split_free(
            index_data, wanted_aliases, exclude_id=existing.id, row=index
        )
        wanted_aliases = _only(wanted_aliases, free)
        current_aliases = index_data.human.get(existing.id, [])
        have = {compare_key(one.value) for one in current_aliases}
        unseen = {compare_key(one.value) for one in current_aliases if one.verified_at is None}
        if aliases_mode == "add":
            if [one for one in wanted_aliases if compare_key(one.value) not in have]:
                changes.append("aliases")
        elif [compare_key(a.value) for a in wanted_aliases] != [
            compare_key(a.value) for a in current_aliases
        ]:
            changes.append("aliases")
        if "aliases" not in changes and [
            one for one in wanted_aliases if one.verified and compare_key(one.value) in unseen
        ]:
            # **보낸 쪽에서 사람이 본 것**이 이쪽에는 아직 검수 대기다 — 값은 같아도 바뀐다.
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
        _require_refs(refs, defs, cleaned)
        _unique_clash(
            object_type,
            defs,
            index_data,
            cleaned,
            owner_workspace_id=existing.owner_workspace_id,
            exclude_id=existing.id,
        )
        current = existing.properties or {}
        for prop_key in patch:
            if current.get(prop_key) != cleaned.get(prop_key):
                changes.append(prop_key)
    # **사람이 고친 칸은 비켜 간다.** 파일로 넣기는 같은 식별자면 덮으므로, 원천을 다시 정제해
    # 넣으면 사람이 화면에서 고친 값이 조용히 사라진다 — 고친 사람은 그 사실을 모른다.
    # 무엇을 안 덮었는지 줄에 적는다(수만 말하면 자기 칸이 그중에 있는지 알 수 없다).
    kept: list[str] = []
    if human_edits == "keep" and changes:
        kept = humanedits.protected(existing, _markers(changes, by_key))
        if kept:
            bare = {_bare(one) for one in kept}
            changes = [one for one in changes if one not in bare]
    return RowPlan(
        row=index,
        action="update" if changes else "unchanged",
        label=existing.label,
        # 바꿀 식별자가 있으면 그것 — 적용이 이 값을 쓴다.
        key=key if key is not None else existing.key,
        object_id=existing.id,
        changes=changes,
        message=_joined(
            rename_note,
            *refs.notes,
            humanedits.note(kept, defs),
            _alias_note(alias_skipped, long_aliases),
            _same_file_note(alias_double),
        ),
    )


def _markers(changes: list[str], by_key: dict[str, PropertyDef]) -> list[str]:
    """바뀔 칸 이름 → **표시의 이름**(`properties.<키>`). 고정 칸 이름이 이긴다 —
    `label` 이라는 속성이 있으면 둘이 같은 글자가 되는데, 그때는 고정 칸 쪽으로 읽는다."""
    out: list[str] = []
    for one in changes:
        if one in humanedits.FIXED:
            out.append(one)
        elif one in by_key:
            out.append(f"{humanedits.PROP}{one}")
    return out


def _bare(marker: str) -> str:
    return marker[len(humanedits.PROP) :] if marker.startswith(humanedits.PROP) else marker


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
    blank_missing: bool = False,
    human_edits: str = "keep",
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
        blank_missing=blank_missing,
        human_edits=human_edits,
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
    refs = Refs(db, user, blank_missing=blank_missing)
    # 계획이 미리 읽은 것을 그대로 쓴다 — 방금 세운 계획이라 같은 트랜잭션의 같은 사실이다.
    index_data = plan.index if isinstance(plan.index, _Index) else _Index()
    # 새로 만든 객체의 별칭은 **모았다가 한 번에** 넣는다(아래) — 둘 사이에 ORM 관계가
    # 없어 차례를 우리가 정해 줘야 한다(객체가 먼저 들어가야 외래키가 선다).
    fresh_aliases: list[tuple[ObjectInstance, list[aliases.Incoming]]] = []

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
                # **id 를 여기서 정한다** — 그래야 별칭 · 감사 기록이 flush 를 기다리지
                # 않는다. 줄마다 flush 하면 2만 줄에 왕복이 2만 번이다(실측).
                id=uuid.uuid4(),
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
            row_plan.object_id = target.id
            # **되돌릴 자리를 적는다** — 판이 열려 있을 때만(묶음). `audit="summary"` 로
            # 줄마다의 기록을 끈 백필도 이것으로 되돌린다.
            journal.created(
                db, "objects", target.id, label=f"{object_type.slug}:{target.label}"
            )
            raw_aliases = _fixed(row, "aliases")
            if raw_aliases is not _MISSING and raw_aliases is not None:
                # 계획에서 가른 대로 — 남이 쓰는 별칭은 여기서도 빠진다. **미리 읽은 것으로**
                # 가르고, 붙인 것은 거기에 적어 둔다: 같은 파일의 뒷줄이 그것을 봐야 한다.
                asked = alias_values(raw_aliases)[0]
                free, _taken, _double = _split_free(index_data, asked, exclude_id=target.id)
                if free:
                    fresh_aliases.append((target, _only(asked, free)))
                    _remember(index_data, free, target.id)
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
        # **계획과 같은 판정을 한다.** 계획이 「그대로 둡니다」 라고 적은 칸을 적용이 덮으면,
        # 사람이 읽은 계획과 들어간 것이 다르다 — 그것이 가장 나쁜 어긋남이다.
        guard: set[str] = set()
        if human_edits == "keep":
            guard = set(
                humanedits.protected(
                    target,
                    [*humanedits.FIXED, *(f"{humanedits.PROP}{one}" for one in patch)],
                )
            )
        if "label" not in guard and raw_label not in (_MISSING, None):
            target.label = str(raw_label).strip()
        if "description" not in guard and raw_description is not _MISSING:
            target.description = raw_description or ""
        if "status" not in guard and raw_status not in (_MISSING, None):
            target.status = str(raw_status)
        if "valid_from_year" not in guard and raw_from is not _MISSING:
            target.valid_from_year = None if raw_from is None else int(raw_from)
        if "valid_to_year" not in guard and raw_to is not _MISSING:
            target.valid_to_year = None if raw_to is None else int(raw_to)
        keep_old_key: str | None = None
        if row_plan.key is not None and "key" in row_plan.changes:
            if target.key:
                # **옛 식별자를 남긴다.** 별칭으로(그 번호로 적힌 문서 · 사람의 기억이 있다)
                # 그리고 목록으로도 — 그래야 쌍둥이 · 코어 API 가 같은 것이 둘이 되지 않게
                # 제 식별자를 옮긴다. 목록인 이유: 동기화 사이에 두 번 바뀔 수 있다.
                key_history.remember(target, target.key)
                if renamed_from(object_type, row) is not None:
                    keep_old_key = target.key
            target.key = row_plan.key
        if "aliases" in row_plan.changes:
            raw_aliases = _fixed(row, "aliases")
            asked = alias_values(raw_aliases)[0]
            free, _taken, _double = _split_free(index_data, asked, exclude_id=target.id)
            before_names = [one.value for one in index_data.human.get(target.id, [])]
            kept = aliases.dump(index_data.human.get(target.id, []))
            _was, became = aliases.set_human(
                db, target, object_type, _only(asked, free), mode=aliases_mode
            )
            # 별칭은 다른 표의 행이라 `audit_state` 에 없다 — 되돌릴 기록에 따로 적는다.
            # 출처 · 메모 · 검수 여부까지 적어야 되돌린 별칭이 「기계가 방금 붙인 것」 으로
            # 되살아나지 않는다.
            journal.changed(
                db,
                "object_aliases",
                target.id,
                before={"aliases": kept},
                after={"aliases": became},
                label=f"{object_type.slug}:{target.label} 별칭",
            )
            _remember(index_data, free, target.id)
            if aliases_mode == "replace":
                # **뺀 별칭은 미리 읽은 목록에서도 뺀다** — 같은 파일의 뒷줄이 그것을
                # 가져갈 수 있어야 한다(예전에는 두 번 넣어야 옮겨졌다).
                _forget(index_data, [one for one in before_names if one not in free])
        if keep_old_key is not None:
            # 별칭을 맞춘 **뒤에** 더한다 — `replace` 면 앞에서 더한 것이 지워진다.
            free, _taken, _double = _split_free(
                index_data, [aliases.Incoming(value=keep_old_key)], exclude_id=target.id
            )
            if free:
                aliases.set_human(db, target, object_type, free, mode="add")
                _remember(index_data, free, target.id)
        if patch:
            guarded = {_bare(one) for one in guard if one.startswith(humanedits.PROP)}
            sending = {key: value for key, value in patch.items() if key not in guarded}
            if sending:
                target.properties = validate_properties(
                    defs, merge_properties(target.properties or {}, sending)
                )
        after = audit_state(target)
        if human_edits == "overwrite":
            # 사람이 「덮어라」 고 정했다 — 덮은 칸은 이제 파일의 값이니 표시를 지운다.
            # 안 지우면 다음 적재가 또 「사람이 고친 칸」 으로 보고 비켜 간다.
            humanedits.release(target, humanedits.changed_fields(before, after))
        moved = audit.diff(before, after)
        journal.changed(
            db,
            "objects",
            target.id,
            before={key: one["before"] for key, one in moved.items()},
            after={key: one["after"] for key, one in moved.items()},
            label=f"{object_type.slug}:{target.label}",
        )
        audit.record(
            db,
            action="object.update",
            actor=user,
            target_table="objects",
            target_id=target.id,
            target_label=f"{object_type.slug}:{target.label}",
            workspace_id=target.owner_workspace_id,
            changes=moved,
            reason="일괄 가져오기",
        )

    if fresh_aliases:
        # **객체를 먼저 넣고** 별칭을 넣는다.
        db.flush()
        for made, values in fresh_aliases:
            aliases.add_fresh(db, made, object_type, values)

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
    # `renamed_from` · `previous_keys` 는 템플릿 · 내보내기에 안 넣는다 — 늘 비는 열이 늘고,
    # 내보낸 것을 그대로 올리면 뜻 없는 칸이 된다. 키를 바꿀 때만 손으로 더하는 열이다.
    fixed = [one for one in FIXED_COLUMNS if one not in ("renamed_from", "previous_keys")]
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


def _relation_defs_all(db: Session, kinds: list[RelationType]) -> dict[str, list[PropertyDef]]:
    """관계 종류들의 속성 정의를 **한 질의로** — slug → 정의(적은 차례)."""
    out: dict[str, list[PropertyDef]] = {one.slug: [] for one in kinds}
    if not kinds:
        return out
    by_id = {one.id: one.slug for one in kinds}
    for row in db.scalars(
        select(PropertyDef)
        .where(PropertyDef.owner_kind == "relation", PropertyDef.owner_id.in_(list(by_id)))
        .order_by(PropertyDef.sort_order, PropertyDef.id)
    ):
        out[by_id[row.owner_id]].append(row)
    return out


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
    db: Session,
    kind: RelationType,
    row: dict[str, Any],
    refs: Refs,
    *,
    fresh: bool = True,
    defs: list[PropertyDef] | None = None,
) -> dict[str, Any]:
    """관계 한 줄의 속성 — 고정 열 밖의 칸을 그 종류의 정의로 읽는다.

    **모르는 키는 거절한다**(정의가 없는 칸). 조용히 버리면 넣은 사람은 들어간 줄 알고,
    그 사실은 아무 데도 안 적힌다.

    ⚠️ **빈 칸은 그 종류의 칸이 아니어도 된다.** 한 파일에 여러 관계 종류가 섞이면 열은
       합집합이라, 다른 종류의 열은 그 줄에서 빈 칸으로 온다 — 그것을 「없는 속성」 으로
       거절하면 섞인 파일을 아예 못 넣는다(실측).
    ⚠️ `fresh` 가 거짓이면(고칠 때) **기본값을 넣지 않는다.** 넣으면 사람이 화면에서 고쳐
       둔 값을 기본값이 조용히 덮는다 — 파일에 그 칸이 없는데도.
    """
    defs = relation_defs(db, kind) if defs is None else defs
    nested = row.get("properties")
    if nested is not None and not isinstance(nested, dict):
        raise InvalidValue(
            code("OBJECTS", 47), "properties 는 키와 값을 담은 객체여야 합니다."
        )
    flat = {key: value for key, value in row.items() if key and key not in RELATION_RESERVED}
    # 펼친 칸이 이긴다 — CSV 에서 온 것이라 사람이 눈으로 본 값이다.
    merged: dict[str, Any] = {**(nested or {}), **flat}
    # **빈 칸을 먼저 걷는다** — 그래야 다른 종류의 열이 이 줄을 막지 않는다.
    extra = {key: value for key, value in merged.items() if not _is_blank(value)}
    by_key = {one.key: one for one in defs}
    unknown = sorted(key for key in extra if key not in by_key)
    if unknown:
        raise InvalidValue(
            code("OBJECTS", 47),
            f"{kind.label}에 없는 속성입니다: {', '.join(unknown)}. "
            f"쓸 수 있는 것: {', '.join(by_key) or '(없음)'}",
        )
    values = {key: cell_to_value(by_key[key], value, refs) for key, value in extra.items()}
    if not fresh and not values:
        # 고칠 때 보낸 칸이 없으면 **아무것도 바꾸지 않는다.**
        return {}
    # 새로 이을 때는 빈 값으로도 부른다 — 필수 속성을 그때 본다(예전에는 칸이 하나도
    # 없으면 검사 자체를 건너뛰어, 필수가 빈 선이 들어갔다).
    return validate_properties(defs, values, apply_defaults=fresh)


@dataclass
class _RelIndex:
    """관계 파일 한 장이 물을 것을 **한 번만** 묻는다.

    ⚠️ 왜: 줄마다 끝점을 찾고(식별자 → 별칭 → 이름, 최대 네 질의씩 둘), 관계 종류의 속성
       정의를 다시 읽고, 이미 이어진 선을 또 물었다. 같은 출발점이 수십 줄에 되풀이되는
       파일(한 과제에 모델 여럿)에서는 그 대부분이 **같은 물음**이다.
    """

    defs: dict[str, list[PropertyDef]] = field(default_factory=dict)
    """관계 종류 slug → 그 종류의 속성 정의."""
    ends: dict[tuple[str, str], Any] = field(default_factory=dict)
    """(무엇을 찾았나, 어디서) → 찾은 것. 못 찾은 것도 기억한다(그 줄의 오류로 다시 낸다)."""
    pool: dict[tuple[uuid.UUID, ...], dict[str, list[ObjectInstance]]] = field(
        default_factory=dict
    )
    """허용 타입 묶음 → **파일에 나온 글자들**로 미리 읽어 둔 끝점 후보(`_ends_of`)."""
    edges: dict[tuple[uuid.UUID, str, uuid.UUID], ObjectRelation] = field(default_factory=dict)
    """이미 이어진 선 — (출발 · 종류 · 도착)."""
    editable: dict[uuid.UUID | None, AppError | None] = field(default_factory=dict)
    loaded_src: set[uuid.UUID] = field(default_factory=set)
    """선을 **미리 읽어 둔** 출발점들. 여기 있는 출발점은 `edges` 에 없으면 정말 없는 것이다
    — 예전에는 그때도 한 번 더 물어서, **새로 잇는 선마다** 질의가 하나씩 붙었다(백필의
    대부분이 새 선이다)."""
    types: dict[str, ObjectType] = field(default_factory=dict)
    """slug → 타입. 끝 타입 검사와 원 표 찾기가 줄마다 이 표를 읽던 자리다."""
    pending: dict[str, set[str]] = field(default_factory=dict)
    """타입 slug → **이 묶음이 만들 것**의 식별자 · 이름(가벼운 미리 보기에서만)."""
    skip_missing: bool = False
    """참이면 끝점을 못 찾은 줄을 **오류로 만들지 않고 건너뛴다**(줄에 적는다). 선은 값이
    아니라 있음/없음이라 「빈칸으로 넣기」 가 없다 — 건너뛰는 것이 그에 해당한다."""
    end_types: interfaces.Ends | None = None
    """끝에 적힌 slug(타입 · 인터페이스)를 타입으로 펴는 표와 그 이름. 끝 타입 검사 · 오류
    문구가 줄마다 이 표를 만들던 자리다."""
    system_refs: dict[str, list[SystemRef]] = field(default_factory=dict)
    """원 표(부서 · 계정)를 비추는 타입 slug → 그 표 전부. **글자마다 한 질의**를 돌던
    자리다 — 도착 타입에 원 표가 섞여 있으면 객체 표에서 못 찾은 글자마다 그 표를 다시
    읽었다(백필에서는 아직 없는 객체가 대부분이라 거의 모든 줄이 그쪽으로 떨어진다)."""
    file_by_src: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    """(관계 종류, 출발 표) → 이 파일이 그 출발점에 이은 **도착 표**들.

    ⚠️ 줄마다 앞줄 전체를 훑던 자리다 — 2만 줄이면 마지막 줄이 1만 9999줄을 본다(제곱).
       표는 풀린 끝점이면 `id:<uuid>`, 아직 없는 끝점이면 `txt:<글자>` 다. 아직 없는 것은
       id 가 없어 글자로 세는데, 같은 객체를 한 줄은 식별자로 한 줄은 이름으로 적으면 둘로
       센다 — 적용은 표를 보고 다시 판정하므로 잘못 들어가지는 않는다."""
    file_by_dst: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    """(관계 종류, 도착 표) → 그 도착점을 받은 **출발 표**들. 도착 쪽 개수 제약이 본다."""


def _existing_edge(
    db: Session, memo: _RelIndex, src_id: uuid.UUID, slug: str, dst_id: uuid.UUID
) -> ObjectRelation | None:
    """이미 이어진 선 — **이 파일의 출발점들 것을 한 번에 읽어**(`_load_edges`) 두고 본다."""
    key = (src_id, slug, dst_id)
    if key in memo.edges:
        return memo.edges[key]
    if src_id in memo.loaded_src:
        # 그 출발점의 선은 **전부** 읽어 뒀다 — 여기 없으면 정말 없다. 다시 묻지 않는다.
        return None
    # 미리 읽지 못한 출발점(나중에 알게 된 것)만 한 번 묻는다.
    found = db.scalar(
        select(ObjectRelation).where(
            ObjectRelation.src_object_id == src_id,
            ObjectRelation.dst_object_id == dst_id,
            ObjectRelation.relation == slug,
        )
    )
    if found is not None:
        memo.edges[key] = found
    return found


def _load_pool(
    db: Session,
    user: User,
    object_type: ObjectType,
    kinds: dict[str, RelationType],
    rows: list[dict[str, Any]],
    memo: _RelIndex,
) -> None:
    """파일에 나온 끝점 글자를 **허용 타입 묶음마다 한 질의로** 미리 읽는다."""
    wanted: dict[tuple[uuid.UUID, ...], set[str]] = {}
    by_slug = {one.slug: one for one in db.scalars(select(ObjectType))}
    memo.types = by_slug
    # 끝 타입 표와 이름 — **파일마다 한 번**. 줄마다 만들면 타입 마흔 개짜리 설치에서 2만 줄에
    # 80만 번 담는다.
    memo.end_types = ends = interfaces.load_ends(db)
    for row in rows:
        src_text = str(row.get("src") or "").strip()
        if src_text:
            wanted.setdefault((object_type.id,), set()).add(src_text)
        dst_text = str(row.get("dst") or "").strip()
        slug = str(row.get("relation") or "").strip()
        kind = kinds.get(slug)
        if not dst_text or kind is None:
            continue
        allowed = ends.expand(kind.dst_type_slugs)
        plain = tuple(
            sorted(
                by_slug[one].id
                for one in allowed or ()
                if one in by_slug and not system.is_system(by_slug[one])
            )
        )
        if allowed is not None and not plain:
            # 객체 표에서 찾을 것이 없다(원 표만, 또는 구현 타입이 없는 인터페이스) — 빈 묶음은
            # 「아무 타입이나」 라서 담으면 안 된다.
            continue
        # 도착 타입을 안 정한 관계는 **아무 타입이나** 될 수 있다 — 빈 묶음으로 담는다.
        wanted.setdefault(plain, set()).add(dst_text)
    for key, texts in wanted.items():
        memo.pool[key] = _ends_of(db, user, list(key) or None, texts)


def _pending_end(
    memo: _RelIndex,
    object_type: ObjectType,
    kind: RelationType,
    src_text: str,
    dst_text: str,
) -> tuple[str, str]:
    """**못 찾은 끝점**이 이 묶음이 만들 것인가 — `(까닭, 도착 타입 slug)`, 아니면 `("", "")`.

    찾은 끝점의 글자는 빈 값으로 온다 — 부르는 쪽이 먼저 풀어 보고, **안 풀린 것만** 여기서
    묻는다. 하나라도 이 묶음이 만드는 것이 아니면 `("", "")` 다: 그때는 정말 없는 것이고,
    그 줄은 오류(또는 `skip_missing` 이면 건너뛰기)다.

    도착 타입을 함께 돌려주는 이유: 그 객체가 아직 없어도 **어느 타입이 될지는 안다**(묶음의
    그 배치가 말한다). 그러면 끝 타입 검사를 지금 할 수 있다.
    """
    if not memo.pending:
        return "", ""
    waiting: list[str] = []
    dst_slug = ""
    if src_text:
        if src_text not in memo.pending.get(object_type.slug, set()):
            return "", ""
        waiting.append(src_text)
    if dst_text:
        ends = memo.end_types or interfaces.Ends()
        for slug in memo.pending:
            if ends.allows(kind.dst_type_slugs, slug) and dst_text in memo.pending[slug]:
                dst_slug = slug
                break
        if not dst_slug:
            return "", ""
        waiting.append(dst_text)
    if not waiting:
        return "", ""
    return (
        f"끝점 {', '.join(waiting)} 은 이 묶음이 만드는 객체입니다 — 적용할 때 이어집니다",
        dst_slug,
    )


def _endpoint_maker(
    db: Session,
    user: User,
    object_type: ObjectType,
    text: str,
    memo: _RelIndex | None = None,
) -> Callable[[], Any]:
    """출발점을 찾는 일 하나 — 미리 한 바퀴 돌 때와 줄에서 볼 때가 **같은 것**이어야 한다."""

    def find() -> Any:
        pool = memo.pool.get((object_type.id,)) if memo is not None else None
        return _find_endpoint(db, user, text, [object_type.id], pool)

    return find


def _require_file_twice(kind: RelationType, memo: _RelIndex, src: str, dst: str) -> None:
    """같은 선이 이 파일에 두 번 있나 — **표로 본다**(앞줄을 훑지 않는다)."""
    if dst in memo.file_by_src.get((kind.slug, src), frozenset()):
        raise InvalidValue(code("OBJECTS", 44), "같은 관계가 이 파일에 두 번 있습니다.")


def _require_file_limits(kind: RelationType, memo: _RelIndex, src: str, dst: str) -> None:
    """**이 파일의 앞 줄이 이미 채웠나** — 개수 제약과 순환을 표만 보고 판정하면 못 본다.

    계획은 아무것도 쓰지 않으므로 표에는 앞 줄의 선이 없다. 그 줄들은 `file_by_src` ·
    `file_by_dst` 에 있다 — 적용은 쓰면서 보지만(그래서 거기서 거절된다), **계획이
    통과시키면 사람은 되는 줄 안다.**

    ⚠️ 순환은 **이 파일 안의 길만** 본다. 표에 있는 선을 거쳐 돌아오는 길은 풀린 끝점에서
       `require_no_cycle` 가 보고, 파일과 표를 번갈아 지나는 길은 적용이 쓰면서 본다
       (`_guarded` 가 먼저 flush 한다) — 계획에 안 보일 수는 있어도 들어가지는 않는다.
    """
    if kind.cardinality in ("one_to_one", "many_to_one") and memo.file_by_src.get(
        (kind.slug, src)
    ):
        raise Conflict(
            code("OBJECTS", 23),
            f"{kind.label}은 하나만 맺을 수 있습니다 — 이 파일의 앞 줄이 이미 맺었습니다.",
        )
    if kind.cardinality in ("one_to_one", "one_to_many") and memo.file_by_dst.get(
        (kind.slug, dst)
    ):
        raise Conflict(
            code("OBJECTS", 24),
            f"{kind.label}의 도착 쪽은 하나만 받을 수 있습니다 — 이 파일의 앞 줄이 "
            "이미 맺었습니다.",
        )
    if not kind.acyclic:
        return
    if src == dst:
        raise Conflict(code("OBJECTS", 25), "자기 자신과는 맺을 수 없습니다.")
    if _file_reaches(memo, kind.slug, dst, src):
        raise Conflict(
            code("OBJECTS", 26),
            f"이으면 {kind.label}에 순환이 생깁니다 — 이 파일 안에 돌아오는 길이 있습니다.",
        )


def _file_reaches(memo: _RelIndex, slug: str, start: str, goal: str) -> bool:
    """이 파일의 선만 따라 `start` 에서 `goal` 에 닿나."""
    seen = {start}
    stack = [start]
    while stack:
        for nxt in memo.file_by_src.get((slug, stack.pop()), ()):
            if nxt == goal:
                return True
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def _remember_file_edge(kind: RelationType, memo: _RelIndex, src: str, dst: str) -> None:
    """뒷줄이 보게 남긴다 — **검사를 지난 뒤에** 넣는다(제 줄을 세지 않게)."""
    memo.file_by_src.setdefault((kind.slug, src), set()).add(dst)
    memo.file_by_dst.setdefault((kind.slug, dst), set()).add(src)


def _end_token(text: str, end: Any) -> str:
    """끝점 하나의 **표** — 있는 것은 id 로, 이 묶음이 만들 것은 글자로."""
    return f"txt:{text}" if isinstance(end, AppError) else f"id:{end.id}"


def _guarded(kind: RelationType) -> bool:
    """이 관계 종류에 **표를 봐야 하는 제약**이 있나 — 개수 제약이나 순환 금지.

    있으면 넣기 전에 쌓인 것을 먼저 쓴다(그래야 검사가 앞 줄의 선을 본다). 없으면 쓰지
    않는다 — 대다수(`many_to_many` · 순환 허용)가 그쪽이고, 그 길이 빨라야 백필이 돈다.
    """
    return kind.cardinality != "many_to_many" or bool(kind.acyclic)


def _load_edges(db: Session, memo: _RelIndex) -> None:
    """미리 찾아 둔 출발점들의 선을 **한 질의로** 읽는다."""
    starts = {one.id for one in memo.ends.values() if isinstance(one, ObjectInstance)}
    if not starts:
        return
    for edge in db.scalars(
        select(ObjectRelation).where(ObjectRelation.src_object_id.in_(starts))
    ):
        memo.edges[(edge.src_object_id, edge.relation, edge.dst_object_id)] = edge
    memo.loaded_src |= starts


def _try_end(index: _RelIndex, key: tuple[str, str], make: Callable[[], Any]) -> Any:
    """같은 물음은 한 번만 — 못 찾았으면 **그 오류를 돌려준다**(내지 않는다).

    부르는 쪽이 먼저 볼 것이 있다: 이 묶음이 만들 끝점인가(가벼운 미리 보기), 건너뛸
    것인가. 예외로 내 버리면 그 판단이 `except` 안으로 밀려 들어간다.
    """
    if key not in index.ends:
        try:
            index.ends[key] = make()
        except AppError as caught:
            index.ends[key] = caught
    return index.ends[key]


def _memo_end(index: _RelIndex, key: tuple[str, str], make: Callable[[], Any]) -> Any:
    """같은 물음은 한 번만 — 못 찾은 것(예외)도 기억해 그 줄에서 다시 낸다."""
    found = _try_end(index, key, make)
    if isinstance(found, AppError):
        raise found
    return found


def _ends_of(
    db: Session,
    user: User,
    allowed_types: list[uuid.UUID] | None,
    texts: set[str],
) -> dict[str, list[ObjectInstance]]:
    """이 글자들에 맞을 수 있는 객체를 **한 질의로** — `"key:<값>"` · `"alias:<비교키>"` ·
    `"label:<값>"` · `"id:<uuid>"` 로 나눠 담는다.

    ⚠️ 줄마다 최대 네 질의를 돌던 자리다. 관계 파일은 도착점이 줄마다 다르므로 기억해 두는
       것만으로는 줄지 않았다 — **파일에 나온 글자 전부**를 한 번에 묻는다.
    """
    out: dict[str, list[ObjectInstance]] = {}
    if not texts:
        return out
    norms = {compare_key(one) for one in texts}
    ids: set[uuid.UUID] = set()
    for one in texts:
        try:
            ids.add(uuid.UUID(one))
        except ValueError:
            continue
    alias_owners = select(ObjectAlias.object_id).where(ObjectAlias.norm.in_(norms))
    stmt = select(ObjectInstance).where(
        ObjectInstance.deleted_at.is_(None),
        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
    )
    if allowed_types:
        stmt = stmt.where(ObjectInstance.type_id.in_(allowed_types))
    wanted = [
        ObjectInstance.key.in_(texts),
        ObjectInstance.label.in_(texts),
        ObjectInstance.id.in_(alias_owners),
    ]
    if ids:
        wanted.append(ObjectInstance.id.in_(ids))
    rows = list(db.scalars(stmt.where(or_(*wanted))))
    if not rows:
        return out
    by_object: dict[uuid.UUID, set[str]] = {}
    for object_id, norm in db.execute(
        select(ObjectAlias.object_id, ObjectAlias.norm).where(
            ObjectAlias.object_id.in_([one.id for one in rows]), ObjectAlias.norm.in_(norms)
        )
    ):
        by_object.setdefault(object_id, set()).add(norm)
    for row in rows:
        if row.key:
            out.setdefault(f"key:{row.key}", []).append(row)
        out.setdefault(f"label:{row.label}", []).append(row)
        out.setdefault(f"id:{row.id}", []).append(row)
        for norm in by_object.get(row.id, set()):
            out.setdefault(f"alias:{norm}", []).append(row)
    return out


def _pick_end(ends: dict[str, list[ObjectInstance]], text: str) -> ObjectInstance:
    """식별자 → 별칭 → 이름 → uuid. **순서와 말은 예전 그대로다** — 판정이 달라지면
    「파일로는 되는데 화면으로는 안 되는」 상태가 생긴다."""
    for prefix, what, hint in (
        (f"key:{text}", "식별자", "관계 종류의 타입을 좁히세요."),
        (f"alias:{compare_key(text)}", "별칭", "식별자로 적으세요."),
        (f"label:{text}", "이름", "식별자로 적으세요."),
        (f"id:{text}", "id", ""),
    ):
        found = ends.get(prefix) or []
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            where = "개 타입에 있습니다" if what == "식별자" else "개에 맞습니다"
            raise InvalidValue(
                code("OBJECTS", 46), f"「{text}」 {what}가 {len(found)}{where}. {hint}".strip()
            )
    raise InvalidValue(code("OBJECTS", 46), f"「{text}」 을 찾을 수 없습니다.")


def _find_endpoint(
    db: Session,
    user: User,
    text: str,
    allowed_types: list[uuid.UUID] | None,
    ends: dict[str, list[ObjectInstance]] | None = None,
) -> ObjectInstance:
    """식별자 → 별칭 → 이름 → uuid. 이름이 여럿에 맞으면 거절.

    `ends` 는 **파일 전체를 미리 읽어 둔 것**(`_ends_of`) — 없으면 이 글자 하나만 읽는다.
    """
    found = ends if ends is not None else _ends_of(db, user, allowed_types, {text})
    return _pick_end(found, text)


def _type_ids(db: Session, slugs: list[str] | None) -> list[uuid.UUID] | None:
    if not slugs:
        return None
    return list(db.scalars(select(ObjectType.id).where(ObjectType.slug.in_(slugs))))


def _dst_maker(
    db: Session, user: User, text: str, kind: RelationType, memo: _RelIndex
) -> Callable[[], Any]:
    def find() -> Any:
        return _find_dst(db, user, text, kind, memo)

    return find


def _find_dst(
    db: Session,
    user: User,
    text: str,
    kind: RelationType,
    memo: _RelIndex | None = None,
) -> system.End:
    """도착점 — 객체 표 먼저, 그다음 관계가 허용한 system 타입의 원 표.

    system 끝은 관계 종류가 도착 타입을 **정해 뒀을 때만** 본다. 안 정했으면 원 표를
    전부 뒤지게 되고, 「부서 slug 를 적었더니 계정이 걸렸다」 같은 일이 생긴다.
    """
    types = memo.types if memo is not None and memo.types else system.types_by_slug(db)
    ends = memo.end_types if memo is not None and memo.end_types else interfaces.load_ends(db)

    def all_of(target: ObjectType) -> list[SystemRef]:
        """원 표 전부 — **타입마다 한 번**(같은 파일 안에서)."""
        if memo is None:
            return system.source_of(target).list_all(db)
        if target.slug not in memo.system_refs:
            memo.system_refs[target.slug] = system.source_of(target).list_all(db)
        return memo.system_refs[target.slug]

    # 끝에 적힌 인터페이스는 구현 타입으로 편다. **`None` 만 「아무 타입이나」 다** — 구현
    # 타입이 없는 인터페이스는 빈 집합이고, 그것을 「제약 없음」 으로 읽으면 아무것이나 잇는다.
    allowed = ends.expand(kind.dst_type_slugs)
    if allowed is not None and not allowed:
        raise InvalidValue(
            code("OBJECTS", 46),
            f"「{text}」 을 이을 수 없습니다 — {kind.label}의 도착"
            f"({ends.describe(kind.dst_type_slugs or [])})에 해당하는 타입이 없습니다.",
        )
    candidates = [types[one] for one in sorted(allowed or ()) if one in types]
    plain = [one.id for one in candidates if not system.is_system(one)]
    systemic = [one for one in candidates if system.is_system(one)]
    if allowed is None or plain:
        pool = memo.pool.get(tuple(sorted(plain))) if memo is not None else None
        try:
            found = _find_endpoint(db, user, text, plain or None, pool)
            found_type = next(t for t in types.values() if t.id == found.type_id)
            return system.end_of(found, found_type)
        except InvalidValue:
            if not systemic:
                raise
    for target in systemic:
        refs = all_of(target)
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
    pending: dict[str, set[str]] | None = None,
    skip_missing: bool = False,
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
    # **속성 정의는 한 번에 읽는다** — 줄마다 읽으면 오천 줄에 오천 질의다.
    index_data = _RelIndex(defs=_relation_defs_all(db, list(kinds.values())))
    index_data.pending = pending or {}
    index_data.skip_missing = skip_missing
    known_props = {one.key for rows in index_data.defs.values() for one in rows}
    headers = {key for row in rows for key in row} - {""}
    unknown = sorted(headers - set(RELATION_RESERVED) - known_props)
    if unknown:
        plan.errors.append(
            f"모르는 열: {', '.join(unknown)}. "
            f"관계 파일의 열은 {', '.join(RELATION_COLUMNS)} 과 관계 종류의 속성입니다."
        )
        return plan

    # **끝점을 파일 단위로 미리 읽는다.** 출발점은 이 타입에서, 도착점은 그 관계 종류가
    # 허락한 타입에서 — 도착점은 줄마다 다르므로 기억해 두는 것만으로는 안 준다.
    _load_pool(db, user, object_type, kinds, rows, index_data)
    # 그러고 나서 출발점을 한 바퀴 찾는다 — 그래야 이미 이어진 선을 한 번에 읽는다.
    # 줄마다 나는 오류는 아래에서 그 줄의 것으로 다시 난다(`_memo_end` 가 기억한다).
    for row in rows:
        text = str(row.get("src") or "").strip()
        if not text:
            continue
        with contextlib.suppress(AppError):
            _memo_end(
                index_data,
                ("src", text),
                _endpoint_maker(db, user, object_type, text, index_data),
            )
    _load_edges(db, index_data)

    seen: set[tuple[uuid.UUID, str, uuid.UUID]] = set()
    for index, row in enumerate(rows, start=1):
        _tick(on_progress, "계획", index, len(rows))
        try:
            plan.rows.append(
                _plan_relation(
                    db, user, object_type, kinds, row, index, seen, source, index_data
                )
            )
        except AppError as caught:
            plan.rows.append(RowPlan(row=index, action="error", message=caught.message))
    if mode in ("replace", "replace_type") and plan.ok:
        plan.rows.extend(_unlinks(db, user, object_type, seen, wide=mode == "replace_type"))
    plan.index = index_data
    return plan


def _unlinks(
    db: Session,
    user: User,
    object_type: ObjectType,
    seen: set[tuple[uuid.UUID, str, uuid.UUID]],
    *,
    wide: bool,
) -> list[RowPlan]:
    """파일에 없어 **끊을 선.**

    좁은 범위(기본)는 파일에 나온 **(출발 객체 · 관계 종류)** 만 본다 — 파일에 아예 안 나온
    객체의 선을 끊으면, 한 타입의 일부만 담은 파일이 나머지 전부를 지운다. 그것은 되돌릴 수
    없는 종류의 사고다.

    `wide` 면 **(출발 타입 · 관계 종류)** 전체를 본다 — 원천에서 어떤 객체의 선이 통째로
    사라진 경우가 그것이다. 파일이 그 타입의 **전부**일 때만 쓴다.
    """
    pairs = {(src, kind) for src, kind, _ in seen}
    if not pairs:
        return []
    kinds = {kind for _, kind in pairs}
    stmt = select(ObjectRelation).where(ObjectRelation.relation.in_(kinds))
    if wide:
        stmt = stmt.join(
            ObjectInstance, ObjectInstance.id == ObjectRelation.src_object_id
        ).where(
            ObjectInstance.type_id == object_type.id,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    else:
        stmt = stmt.where(ObjectRelation.src_object_id.in_({src for src, _ in pairs}))
    edges = db.scalars(stmt)
    doomed = [
        edge
        for edge in edges
        if (wide or (edge.src_object_id, edge.relation) in pairs)
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
    index_data: _RelIndex | None = None,
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

    memo = index_data if index_data is not None else _RelIndex()
    # **먼저 있는 것으로 풀어 본다.** 예전에는 「이 묶음이 만들 것」 목록을 먼저 봐서 두 가지가
    # 틀렸다: 새 출발점에 **이미 있는 도착점**을 이은 줄이 도착 타입을 출발 타입으로 짐작해
    # 거짓 오류가 났고, 같은 파일을 두 번째로 넣을 때는(객체가 이미 있는데) 전부 「새로
    # 만든다」 로 나왔다 — 그 계획으로 「파일대로 맞춤」 을 미리 보면 있는 선을 전부 끊는
    # 것으로 보였다. **같은 끝점은 한 번만 찾는다** — 한 과제에 모델이 여럿이면 그 과제를
    # 수십 번 찾는다.
    src_end = _try_end(
        memo, ("src", src_text), _endpoint_maker(db, user, object_type, src_text, memo)
    )
    dst_end = _try_end(
        memo, (f"dst:{kind.slug}", dst_text), _dst_maker(db, user, dst_text, kind, memo)
    )
    src_gone = src_end if isinstance(src_end, AppError) else None
    dst_gone = dst_end if isinstance(dst_end, AppError) else None
    if src_gone is not None or dst_gone is not None:
        later, pending_slug = _pending_end(
            memo,
            object_type,
            kind,
            src_text if src_gone is not None else "",
            dst_text if dst_gone is not None else "",
        )
        if later:
            # **이 묶음이 만들 끝점**을 가리킨다 — 계획만 볼 때는 「없다」 가 아니다.
            # 그래도 **볼 수 있는 것은 본다**: 끝 타입이 되는지, 붙는 값의 모양이 맞는지,
            # 이 파일 안에 같은 선이 두 번 있거나 개수 제약 · 순환을 깨지 않는지. 예전에는
            # 여기서 바로 통과시켜 관계줄이 사실상 검사되지 않았고, 오류는 적용에서 묶음
            # 전체를 되돌렸다(실측).
            src_name = src_text if src_gone is not None else src_end.label
            dst_name = dst_text if dst_gone is not None else dst_end.label
            label = f"{src_name} -{kind.label}-> {dst_name}"
            rel.require_end_types_allowed(
                db,
                kind,
                object_type.slug,
                pending_slug or dst_end.type_slug,
                memo.end_types,
            )
            _relation_properties(db, kind, row, Refs(db, user), defs=memo.defs.get(kind.slug))
            src_token = _end_token(src_text, src_end)
            dst_token = _end_token(dst_text, dst_end)
            _require_file_twice(kind, memo, src_token, dst_token)
            _require_file_limits(kind, memo, src_token, dst_token)
            _remember_file_edge(kind, memo, src_token, dst_token)
            return RowPlan(row=index, action="create", label=label, message=later)
        gone = src_gone or dst_gone
        assert gone is not None
        if not memo.skip_missing:
            raise gone
        # **한 줄이 묶음 전체를 막지 않게** 건너뛴다 — 무엇이 빠졌는지는 줄에 적는다.
        return RowPlan(
            row=index,
            action="unchanged",
            label=f"{src_text} -{kind.label}-> {dst_text}",
            message=f"끝점을 찾지 못해 건너뜁니다 — {gone.message}",
        )
    src, dst = src_end, dst_end
    if src.owner_workspace_id not in memo.editable:
        try:
            require_owner_edit(
                db, user, src.owner_workspace_id, what="객체", code_value=code("OBJECTS", 27)
            )
        except AppError as caught:
            memo.editable[src.owner_workspace_id] = caught
        else:
            memo.editable[src.owner_workspace_id] = None
    refused = memo.editable[src.owner_workspace_id]
    if refused is not None:
        raise refused
    rel.require_end_types_allowed(db, kind, object_type.slug, dst.type_slug, memo.end_types)

    src_token, dst_token = f"id:{src.id}", f"id:{dst.id}"
    _require_file_twice(kind, memo, src_token, dst_token)
    # **파일에 있는 선**이다 — 「파일대로 맞춤」 이 끊지 않는다(이미 이어진 것도 여기 든다).
    seen.add((src.id, kind.slug, dst.id))
    label = f"{src.label} -{kind.label}-> {dst.label}"
    wanted = _relation_properties(db, kind, row, Refs(db, user), defs=memo.defs.get(kind.slug))
    if dst.is_system:
        # 한쪽 끝이 원 표면 선은 `object_links` 에 있다(속성은 안 받는다).
        found = links.existing(db, src.id, dst.id, kind.slug)
        if found is not None:
            _remember_file_edge(kind, memo, src_token, dst_token)
            return RowPlan(row=index, action="unchanged", label=label, object_id=found.id)
        rel.require_cardinality(db, kind, src.id, dst.id)
        _require_file_limits(kind, memo, src_token, dst_token)
        _remember_file_edge(kind, memo, src_token, dst_token)
        return RowPlan(row=index, action="create", label=label)
    found_edge = _existing_edge(db, memo, src.id, kind.slug, dst.id)
    if found_edge is not None:
        # **이미 이어진 선이라도 속성 · 근거가 다르면 고친다.** 예전에는 늘 `unchanged` 라,
        # 근거 건수처럼 관계에 붙는 값을 나중에 채울 길이 없었다.
        note = str(row.get("evidence_note") or "").strip()
        current = found_edge.properties or {}
        changed = [key for key, value in wanted.items() if current.get(key) != value]
        if note and note != (found_edge.evidence_note or ""):
            changed.append("evidence_note")
        _remember_file_edge(kind, memo, src_token, dst_token)
        return RowPlan(
            row=index,
            action="update" if changed else "unchanged",
            label=label,
            object_id=found_edge.id,
            changes=sorted(changed),
        )
    rel.require_cardinality(db, kind, src.id, dst.id)
    rel.require_no_cycle(db, kind, src.id, dst.id)
    _require_file_limits(kind, memo, src_token, dst_token)
    _remember_file_edge(kind, memo, src_token, dst_token)
    return RowPlan(row=index, action="create", label=label)


def apply_relations(
    db: Session,
    user: User,
    object_type: ObjectType,
    rows: list[dict[str, Any]],
    *,
    source: str = "",
    mode: str = "add",
    skip_missing: bool = False,
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
        skip_missing=skip_missing,
        max_rows=max_rows,
        on_progress=on_progress,
    )
    if not plan.ok:
        return plan
    if before_apply is not None:
        before_apply(plan)
    kinds = {row.slug: row for row in db.scalars(select(RelationType))}
    by_label = {row.label: row for row in kinds.values()}
    # **계획이 미리 읽은 것을 그대로 쓴다** — 끝점 · 속성 정의 · 이미 이어진 선. 예전에는
    # 적용이 그것을 처음부터 다시 물었다(줄마다 넷). 방금 세운 계획이라 같은 사실이다.
    memo = plan.index if isinstance(plan.index, _RelIndex) else _RelIndex()
    refs = Refs(db, user)
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
            journal.removed(
                db,
                "object_relations",
                edge.id,
                before=_edge_state(edge),
                label=row_plan.label,
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
            was_note = edge.evidence_note or ""
            defs = memo.defs.get(kind.slug) or relation_defs(db, kind)
            wanted = _relation_properties(db, kind, row, refs, fresh=False, defs=defs)
            if wanted:
                edge.properties = validate_properties(defs, {**before, **wanted})
            note = str(row.get("evidence_note") or "").strip()
            if note:
                edge.evidence_note = note
            journal.changed(
                db,
                "object_relations",
                edge.id,
                before={"properties": before, "evidence_note": was_note},
                after={
                    "properties": dict(edge.properties or {}),
                    "evidence_note": edge.evidence_note or "",
                },
                label=row_plan.label,
            )
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
        src_text = str(row.get("src") or "").strip()
        dst_text = str(row.get("dst") or "").strip()
        src = _memo_end(
            memo, ("src", src_text), _endpoint_maker(db, user, object_type, src_text, memo)
        )
        dst = _memo_end(
            memo, (f"dst:{kind.slug}", dst_text), _dst_maker(db, user, dst_text, kind, memo)
        )
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
            journal.created(db, "object_links", link.id, label=row_plan.label)
            continue
        # **앞 행이 만든 선이 제약을 어길 수 있다.** 검사는 표를 보므로, 아직 안 쓴 선이
        # 있으면 그것을 못 본다 — 줄마다 쓰던 것을 없앤 뒤 이 구멍이 생겼다(실측).
        # 제약이 걸린 종류에서만 먼저 쓴다(대다수는 제약이 없어 그대로 빠르다).
        if _guarded(kind):
            db.flush()
        rel.require_cardinality(db, kind, src.id, dst.id)
        rel.require_no_cycle(db, kind, src.id, dst.id)
        edge = ObjectRelation(
            # **id 를 여기서 정한다** — 감사 기록이 flush 를 기다리지 않는다. 줄마다 flush
            # 하면 수만 줄에 왕복이 수만 번이다.
            id=uuid.uuid4(),
            src_object_id=src.id,
            dst_object_id=dst.id,
            relation=kind.slug,
            properties=_relation_properties(
                db, kind, row, refs, defs=memo.defs.get(kind.slug)
            ),
            evidence_note=str(row.get("evidence_note") or "").strip(),
            created_by_id=user.id,
        )
        db.add(edge)
        row_plan.object_id = edge.id
        journal.created(db, "object_relations", edge.id, label=row_plan.label)
        # 뒤 줄이 「이미 있다」 로 보게 한다 — 같은 파일에 같은 선이 두 번 나올 수 있다.
        memo.edges[(src.id, kind.slug, dst.id)] = edge
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


def _edge_state(edge: ObjectRelation) -> dict[str, Any]:
    """선 하나를 **되살릴 수 있는 만큼** — 되돌릴 기록에 담는 모양."""
    return {
        "src": str(edge.src_object_id),
        "dst": str(edge.dst_object_id),
        "relation": edge.relation,
        "properties": dict(edge.properties or {}),
        "evidence_note": edge.evidence_note or "",
    }


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
