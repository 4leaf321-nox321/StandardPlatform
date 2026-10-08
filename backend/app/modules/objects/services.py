"""객체의 정합 — **DB 제약으로 못 거는 것들.**

`key` 의 유니크 범위가 타입마다 다르고(`key_scope`), 참조가 가리키는 객체는
JSONB 안에 있다. 둘 다 DB 가 안 잡아 주므로 여기서 잡는다.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Numeric,
    Select,
    Text,
    and_,
    case,
    cast,
    func,
    or_,
    select,
    text,
    union,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.orm import Session, aliased

from app.modules.accounts.models import User
from app.modules.objects import system
from app.modules.objects.models import (
    ObjectAlias,
    ObjectInstance,
    ObjectLink,
    ObjectYear,
    SavedView,
)
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.ontology.services import InvalidValue, object_ref_ids
from app.modules.workspaces.models import Workspace
from app.shared import extensions
from app.shared.errors import Conflict, code

if TYPE_CHECKING:  # 실행 때는 안 읽는다 — scope 가 이 모듈을 읽는다(방향은 한쪽).
    from app.modules.objects.scope import Scope


def properties_of(db: Session, type_id: uuid.UUID) -> list[PropertyDef]:
    return list(
        db.scalars(
            select(PropertyDef)
            .where(PropertyDef.owner_kind == "type", PropertyDef.owner_id == type_id)
            .order_by(PropertyDef.sort_order, PropertyDef.label)
        )
    )


#: 식별자 · 이름의 길이 — 표의 칸(`objects.key` String(120) · `label` String(200))과 같다.
#: 여기서 안 보면 넘친 값이 DB 에서 터져 500 이 되고, 파일 가져오기는 계획을 통과한 뒤
#: 적용에서 작업이 통째로 실패한다(2026-10-08).
KEY_MAX = 120
LABEL_MAX = 200


def normalize_key(object_type: ObjectType, key: str | None) -> str | None:
    """타입의 `key_policy` 에 비추어 식별자를 받아들이거나 거절한다."""
    key = (key or "").strip() or None

    if object_type.key_policy == "none":
        if key is not None:
            raise InvalidValue(
                code("OBJECTS", 1),
                f"{object_type.label}은 식별자를 쓰지 않는 타입입니다. 이름만 입력하세요.",
            )
        return None

    if object_type.key_policy == "required" and key is None:
        raise InvalidValue(
            code("OBJECTS", 2), f"{object_type.label}은 식별자가 반드시 있어야 합니다."
        )
    if key is not None and len(key) > KEY_MAX:
        raise InvalidValue(
            code("OBJECTS", 6),
            f"식별자는 {KEY_MAX}자까지입니다({len(key)}자): {key[:40]}…",
        )
    return key


def require_label_fits(label: str) -> None:
    """이름이 칸에 들어가나 — 화면 요청은 스키마가 보고, 파일 가져오기가 이것을 부른다."""
    if len(label) > LABEL_MAX:
        raise InvalidValue(
            code("OBJECTS", 7),
            f"이름은 {LABEL_MAX}자까지입니다({len(label)}자): {label[:40]}…",
        )


def stored_text(value: Any) -> str:
    """값 하나가 **JSONB 에서 글자로 꺼냈을 때**(`properties ->> 키`)의 모양.

    유일 속성의 겹침을 이 글자로 견준다. `str()` 로 견주면 예/아니오(`True` ↔ `true`)와 여러
    값(`['a']` ↔ `["a"]`)이 저장된 글자와 영영 안 맞아, 그 칸들은 유일 검사가 아예 안
    걸렸다(2026-10-08). 글자 · 숫자는 예전과 같다.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def require_key_free(
    db: Session,
    object_type: ObjectType,
    key: str | None,
    *,
    owner_workspace_id: uuid.UUID | None,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """같은 식별자가 이미 있는가.

    **범위가 타입마다 달라 DB 유니크로 못 건다.** `global` 은 전사에서 하나,
    `workspace` 는 부서 안에서 하나다. 외부 시스템과 맞물리는 축은 `global`
    이어야 한다 — 부서마다 같은 부품번호가 다른 것을 가리키면, 그 데이터로는
    아무 질문에도 답할 수 없다.
    """
    if key is None:
        return

    stmt = select(ObjectInstance.id).where(
        ObjectInstance.type_id == object_type.id,
        ObjectInstance.key == key,
        ObjectInstance.deleted_at.is_(None),
    )
    if object_type.key_scope == "workspace":
        if owner_workspace_id is None:
            stmt = stmt.where(ObjectInstance.owner_workspace_id.is_(None))
        else:
            stmt = stmt.where(ObjectInstance.owner_workspace_id == owner_workspace_id)
    if exclude_id is not None:
        stmt = stmt.where(ObjectInstance.id != exclude_id)

    if db.scalar(stmt) is not None:
        where = "이 부서에" if object_type.key_scope == "workspace" else "이미"
        raise Conflict(
            code("OBJECTS", 3),
            f"같은 식별자가 {where} 있습니다: {key}. "
            "찾아서 수정하는 편이 낫습니다 — 같은 것이 둘이 되면 둘 다 못 믿게 됩니다.",
        )


def require_refs_exist(
    db: Session,
    defs: list[PropertyDef],
    values: dict[str, Any],
    before: dict[str, Any] | None = None,
    *,
    user: User | None = None,
) -> None:
    """참조가 가리키는 객체가 실제로 있고, **그 칸의 대상 타입인가.**

    **없는 것을 가리키는 참조를 저장하면 화면에 빈 칸으로 나오고**, 그것이
    「값이 없음」 인지 「가리키던 것이 사라짐」 인지 구별할 수 없다. 대상 밖의 것을
    가리키면 「공급사」 칸에 부품이 들어가고, 그 칸으로 거르거나 세는 모든 답이 틀린다.
    대상 검사는 **새로 적힌 값만** 본다(`before` — 고치기 전 값).

    `user` 를 주면 새로 적힌 값이 **그 사람이 볼 수 있는 객체**여야 한다 — 없는 것과 같은 말로
    거절한다. 안 보면 id 만 알면 남의 부서 객체를 칸에 넣을 수 있었고, 상세의 이름표가 그
    이름을 보였다(2026-10-08). 파일 가져오기 · 관계 잇기는 이미 보이는 것만 찾는다.
    """
    if not object_ref_ids(defs, values):
        return
    # 상대가 system 타입이면 원 표에서, 아니면 객체 표에서 — `system.py` 가 가른다.
    missing = system.missing_refs(db, defs, values)
    if user is not None:
        hidden = system.hidden_objects(db, user, _fresh_refs(defs, values, before))
        missing += [str(one) for one in sorted(hidden) if str(one) not in missing]
    if missing:
        raise InvalidValue(
            code("OBJECTS", 4),
            f"가리키는 객체를 찾을 수 없습니다: {', '.join(missing)}",
        )
    wrong = system.wrong_type_refs(db, defs, values, before)
    if wrong:
        raise InvalidValue(
            code("OBJECTS", 94), f"참조 칸의 대상이 아닌 것을 가리킵니다: {'; '.join(wrong)}"
        )


def _fresh_refs(
    defs: list[PropertyDef], values: dict[str, Any], before: dict[str, Any] | None
) -> set[uuid.UUID]:
    """참조 칸에 **새로 적힌** id — 이미 있던 값은 그 사이 상대가 다른 부서로 옮겨 갔을 수
    있다. 그 칸을 안 건드리는 저장까지 막으면 사람은 무엇을 고쳐야 할지 모른다."""

    def ids(raw: Any) -> set[uuid.UUID]:
        out: set[uuid.UUID] = set()
        for item in raw if isinstance(raw, list) else [raw]:
            if isinstance(item, str) and item:
                try:
                    out.add(uuid.UUID(item))
                except ValueError:
                    continue
        return out

    fresh: set[uuid.UUID] = set()
    for definition in defs:
        if definition.data_type != "object_ref" or definition.key not in values:
            continue
        fresh |= ids(values[definition.key]) - ids((before or {}).get(definition.key))
    return fresh


def require_unique_properties(
    db: Session,
    object_type: ObjectType,
    defs: list[PropertyDef],
    values: dict[str, Any],
    *,
    owner_workspace_id: uuid.UUID | None,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """`unique` 인 속성이 이미 쓰이고 있는가.

    **DB 유니크로 못 건다** — 값이 JSONB 안에 있고, 범위가 타입마다 다르다.
    범위는 `key_scope` 를 따른다: 두 벌의 규칙을 만들면 「식별자는 전사인데
    시리얼은 부서」 같은 상태가 생기고, 그것을 기억할 사람이 없다.

    **같은 것이 둘이 되면 둘 다 못 믿게 된다** — 어느 쪽이 맞는지 알 방법이 없다.
    """
    unique_keys = [d.key for d in defs if d.unique and d.key in values]
    if not unique_keys:
        return

    for key in unique_keys:
        value = values.get(key)
        if value is None or value == "" or value == []:
            continue
        stmt = select(ObjectInstance.id).where(
            ObjectInstance.type_id == object_type.id,
            ObjectInstance.deleted_at.is_(None),
            ObjectInstance.properties[key].astext == stored_text(value),
        )
        if object_type.key_scope == "workspace":
            if owner_workspace_id is None:
                stmt = stmt.where(ObjectInstance.owner_workspace_id.is_(None))
            else:
                stmt = stmt.where(ObjectInstance.owner_workspace_id == owner_workspace_id)
        if exclude_id is not None:
            stmt = stmt.where(ObjectInstance.id != exclude_id)

        if db.scalar(stmt) is not None:
            label = next(d.label for d in defs if d.key == key)
            where = "이 부서에" if object_type.key_scope == "workspace" else "이미"
            raise Conflict(
                code("OBJECTS", 5),
                f"{label}에 같은 값이 {where} 있습니다: {value}. "
                "같은 것이 둘이 되면 둘 다 못 믿게 됩니다 — 찾아서 수정하는 편이 낫습니다.",
            )


#: 두 글자 조각이 이 비율보다 많은 행에 들어 있으면 조각 인덱스를 안 건다 — 어차피 표를 다
#: 읽는데, 거기에 칸마다 조각을 만드는 값이 더해져 두 배로 느려졌다(실측: 「C0」 이 기록 200만
#: 건 전부에 들어 있어 3.1 → 12.8초).
BIGRAM_BROAD = 0.02

_BIGRAM_INDEX = {
    "label": "ix_objects_label_bigram",
    "key": "ix_objects_key_bigram",
    "alias": "ix_object_aliases_value_bigram",
}
_BIGRAM_FREQ = text(
    """
    SELECT s.tablename, x.f
    FROM pg_stats s,
         unnest(s.most_common_elems::text::text[], s.most_common_elem_freqs) AS x(e, f)
    WHERE s.schemaname = current_schema() AND s.tablename = ANY(:names) AND x.e = :pair
    """
)


def bigram_plan(db: Session, term: str | None) -> dict[str, str]:
    """두 글자로 찾을 때 조각 인덱스를 걸 자리 — {`label` · `key` · `alias`: 조각}.

    trigram 은 세 글자부터라 두 글자는 조각 인덱스(`sp_bigrams`, 0054)로 거른다. 다만 **흔한
    조각**이면 안 건다 — 그 비율은 Postgres 가 그 인덱스에 모아 둔 통계(`pg_stats` 의 자주
    나오는 원소)로 안다. 통계에 없으면 드문 것이다."""
    pair = (term or "").strip().lower()
    if len(pair) != 2:
        return {}
    broad = {
        name
        for name, freq in db.execute(
            _BIGRAM_FREQ, {"names": list(_BIGRAM_INDEX.values()), "pair": pair}
        )
        if freq is not None and freq > BIGRAM_BROAD
    }
    return {where: pair for where, index in _BIGRAM_INDEX.items() if index not in broad}


def _bigram(column: Any, pair: str | None) -> tuple[Any, ...]:
    """두 글자로 찾을 때 — 두 글자 조각 인덱스로 먼저 거른다(`sp_bigrams`, 0054). 같은 말을
    `ILIKE` 가 다시 본다."""
    if pair is None:
        return ()
    return (func.sp_bigrams(column).op("@>")(cast(array([pair]), ARRAY(Text))),)


def containing_ids(
    pattern: str,
    *,
    type_clause: Any = None,
    alias_type_clause: Any = None,
    fields: tuple[str, ...] = ("label", "key"),
    escape: str | None = None,
    bigrams: dict[str, str] | None = None,
) -> Any:
    """「이 글자가 들어간 것」 의 id — 이름 · 식별자 · 속성 칸 · 별칭을 **따로 묻고
    합친다**(UNION).

    한 `WHERE` 에 `label ILIKE … OR key ILIKE … OR id IN (별칭)` 으로 묶으면 Postgres 가
    trigram 인덱스를 못 쓰고 표 전체를 읽는다 — 별칭 부분 질의가 비트맵 OR 을 막아서다
    (실측: 기록 200만 건에서 627ms, 따로 묻고 합치면 9ms, ADR 0010).

    `bigrams`(`bigram_plan`)가 있으면 두 글자 조각 인덱스를 함께 건다 — trigram 은 세
    글자부터라 「소음」 은 표 전체를 읽었다."""
    plan = bigrams or {}
    where = () if type_clause is None else (type_clause,)
    parts: list[Any] = []
    for field in fields:
        if field == "label":
            column: Any = ObjectInstance.label
            indexed = _bigram(column, plan.get("label"))
        elif field == "key":
            column = ObjectInstance.key
            indexed = _bigram(column, plan.get("key"))
        elif field.startswith("properties."):
            column = ObjectInstance.properties[field.split(".", 1)[1]].astext
            indexed = ()
        else:
            continue
        parts.append(
            select(ObjectInstance.id).where(
                *where, *indexed, column.ilike(pattern, escape=escape)
            )
        )
    # 별칭에도 걸린다 — 「앤시스」 로 찾아도 「Ansys」 가 나와야 새로 만들지 않는다.
    alias_where = () if alias_type_clause is None else (alias_type_clause,)
    parts.append(
        select(ObjectAlias.object_id).where(
            *alias_where,
            *_bigram(ObjectAlias.value, plan.get("alias")),
            ObjectAlias.value.ilike(pattern, escape=escape),
        )
    )
    return union(*parts)


def apply_search(db: Session, stmt: Select[Any], scope: Scope, term: str) -> Select[Any]:
    """`list_view.search` 가 가리키는 자리들을 훑는다.

    안 정해 뒀으면 이름과 식별자를 본다 — **빈 결과보다 그럴듯한 기본이 낫다.** 인터페이스
    목록이면 그 인터페이스의 `list_view` 와 구현 타입 전부의 별칭을 본다.

    정해 둔 자리가 **모두 글자로 못 훑는 것**(상태 · 만든 때처럼 뷰 검증은 받는 자리)이면
    이름과 식별자로 떨어진다 — 예전에는 검색어를 말없이 버려 목록 전체가 「검색 결과」 로
    나왔다(2026-10-08). 검색어의 `%` · `_` 는 글자 그대로 찾는다(통합 검색과 같다).
    """
    view = scope.list_view
    fields = tuple(
        one
        for one in (view.get("search") or ["label", "key"])
        if one in ("label", "key") or one.startswith("properties.")
    ) or ("label", "key")
    matched = containing_ids(
        f"%{like_escape(term)}%",
        type_clause=scope.clause(),
        alias_type_clause=scope.clause(ObjectAlias.type_id),
        fields=fields,
        escape="\\",
        bigrams=bigram_plan(db, term),
    )
    return stmt.where(ObjectInstance.id.in_(matched))


def like_escape(text: str) -> str:
    """`LIKE` 의 와일드카드(`%` · `_`)와 이스케이프 글자를 글자 그대로 — `escape="\\\\"` 와
    함께 쓴다. 안 그러면 「50%」 가 「50 뒤에 아무거나」 로, 「A_1」 이 「A 와 1 사이 아무
    글자」 로 읽혀 엉뚱한 것이 걸린다."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def apply_property_filters(stmt: Select[Any], filters: dict[str, str]) -> Select[Any]:
    """`?p.<key>=<값>` 으로 온 거르기. **문자열 비교만 한다** — 범위 질의는
    아직 없다. 없는 것을 있는 척하지 않는다."""
    for key, value in filters.items():
        stmt = stmt.where(ObjectInstance.properties[key].astext == value)
    return stmt


def apply_sort(stmt: Select[Any], view: dict[str, Any]) -> Select[Any]:
    """`list_view.sort` 대로. 안 정해 뒀으면 이름순.

    **마지막에 id 로 한 번 더 세운다.** 이름이 같은 줄의 차례가 정해지지 않으면 쪽마다 달라질
    수 있다 — 쪽을 넘기면 어떤 것은 두 번 나오고 어떤 것은 안 나와, 목록에 있는데 못 찾는다
    (맞는 줄을 먼저 모아 정렬하는 길 `page_rows` 에서 실제로 그랬다)."""
    sort = view.get("sort") or {}
    field = sort.get("field") or "label"
    descending = (sort.get("dir") or "asc") == "desc"

    # 뷰 검증이 정렬 칸으로 받는 것(`ontology/views.BUILT_IN_FIELDS`)은 **모두 정렬된다** —
    # 상태 · 만든 때 · 소유 부서가 말없이 이름순으로 떨어지던 것(2026-10-08).
    first: list[Any] = []
    if field == "label":
        column: Any = ObjectInstance.label
    elif field == "key":
        column = ObjectInstance.key
    elif field == "updated_at":
        column = ObjectInstance.updated_at
    elif field == "created_at":
        column = ObjectInstance.created_at
    elif field == "status":
        column = ObjectInstance.status
    elif field == "owner_workspace":
        # 부서는 **이름으로** — id 순은 아무 뜻이 없다. 전역(없음)은 끝에.
        name = (
            select(Workspace.name)
            .where(Workspace.id == ObjectInstance.owner_workspace_id)
            .scalar_subquery()
        )
        first = [name.desc().nulls_last() if descending else name.asc().nulls_last()]
        column = ObjectInstance.label
    elif field.startswith("properties."):
        raw = ObjectInstance.properties[field.split(".", 1)[1]]
        # **숫자는 숫자로** — 글자로 세우면 10 · 100 · 9 가 된다. 숫자로 저장된 값만 숫자로
        # 읽고(못 읽는 값 · 빈 값은 끝으로), 같은 수 · 글자 칸은 글자로 한 번 더 세운다.
        number = case(
            (func.jsonb_typeof(raw) == "number", cast(raw.astext, Numeric)), else_=None
        )
        first = [number.desc().nulls_last() if descending else number.asc().nulls_last()]
        column = raw.astext
    else:
        column = ObjectInstance.label

    return stmt.order_by(
        *first,
        column.desc() if descending else column.asc(),
        ObjectInstance.id.desc() if descending else ObjectInstance.id.asc(),
    )


def audit_state(row: ObjectInstance) -> dict[str, Any]:
    """감사 기록이 비교하는 **객체의 값 전체** — 고치는 길이 모두 이것 하나를 쓴다.

    길마다 칸 목록을 따로 적었더니 설명·연도·소유 부서가 빠졌고, 그 칸을 바꾼 기록은
    diff 가 비어 「고침」 만 남았다. 누가 설명을 바꿨는지 답할 수 없고, 지켜보는 사람의
    알림에도 칸 이름이 없고, 그 시점으로 되돌릴 수도 없었다. 한 벌이면 새 칸이 생겨도
    한 곳만 고친다.
    """
    return {
        "key": row.key,
        "label": row.label,
        "description": row.description or "",
        "status": row.status,
        "valid_from_year": row.valid_from_year,
        "valid_to_year": row.valid_to_year,
        "owner_workspace_id": str(row.owner_workspace_id) if row.owner_workspace_id else None,
        "properties": dict(row.properties or {}),
    }


#: 맞는 줄이 이 수 이하면 **먼저 다 모으고 정렬한다**(`page_rows`). 모으는 값은 맞는 줄 수에
#: 비례하고, 정렬 색인을 따라 걷는 값은 「타입의 줄 수 x 쪽 크기 / 맞는 줄 수」 에 비례한다 —
#: 200만 건 타입에서 둘이 만나는 자리가 만 언저리다.
GATHER_BELOW = 10_000


def page_rows(
    db: Session,
    stmt: Select[Any],
    view: dict[str, Any],
    *,
    total: int,
    limit: int,
    offset: int,
) -> list[ObjectInstance]:
    """목록 한 쪽 — **방금 센 수를 보고 길을 고른다.**

    「이름순 50개」 를 뽑을 때 플래너는 이름 색인을 따라 걸으며 조건을 한 줄씩 대 보는 길을
    즐겨 고른다 — 맞는 줄이 많으면 50줄을 금방 채워 빠르다. 그런데 맞는 줄이 적거나 없으면
    타입을 끝까지 걷는다: 기록 200만 건에서 「모델의 과제 비어 있음」(0건) 53초, 「서비스일 <
    2019-01-08」(0건) 10.6초 — 같은 조건을 세는 데는 2초 · 4초였다(실측). 플래너는 맞는 줄 수를
    어림으로만 알지만 우리는 방금 셌다.

    - 쪽이 끝 너머면(0건 포함) 묻지 않는다.
    - `GATHER_BELOW` 이하면 맞는 줄의 id 를 먼저 다 모으고(`MATERIALIZED` — 세기와 같은 계획을
      탄다) 그것만 정렬한다.
    - 그 위면 정렬 색인을 따라 걷는다(맞는 줄이 촘촘해 금방 찬다).
    """
    if total <= offset:
        return []
    if total <= GATHER_BELOW:
        hit = stmt.with_only_columns(ObjectInstance.id).cte("hit").prefix_with("MATERIALIZED")
        stmt = select(ObjectInstance).join(hit, hit.c.id == ObjectInstance.id)
    return list(db.scalars(apply_sort(stmt, view).limit(limit).offset(offset)))


def count_of(db: Session, stmt: Select[Any]) -> int:
    """**total 을 함께 준다.** 없으면 화면이 「다음 쪽이 있는지」 를 알려고 한 건
    더 요청하는 편법을 쓰고, 그 편법은 화면마다 달라진다."""
    return int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)


def workspace_reference(
    db: Session, workspace_id: uuid.UUID
) -> list[extensions.WorkspaceReference]:
    """부서 삭제 확인에 뜨는 줄.

    **안 걸면 부서를 지울 때 이 표가 목록에 안 나타나고**, 사람은 아무것도 안
    걸린 줄 안다. FK 가 RESTRICT 라 그때 서버가 500 을 낸다 — 화면은 지울 수
    있다고 말해 놓고서.

    지운 객체(`deleted_at`)도 센다. **행이 남아 있으면 FK 는 그대로 붙든다** —
    사람 눈에 안 보이는 것과 DB 가 놓아 주는 것은 다른 일이다.
    """
    count = (
        db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(ObjectInstance.owner_workspace_id == workspace_id)
        )
        or 0
    )
    # 온톨로지가 이 부서를 **가리키는** 것 — 관계 선(`object_links`)과 참조 칸. FK 가
    # 없어 DB 는 안 막지만, 지우면 「담당 부서」 가 빈 칸이 되고 그 이유는 안 뜬다.
    linked = (
        db.scalar(
            select(func.count())
            .select_from(ObjectLink)
            .where((ObjectLink.src_id == workspace_id) | (ObjectLink.dst_id == workspace_id))
        )
        or 0
    )
    referring = (
        db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(
                ObjectInstance.deleted_at.is_(None),
                ObjectInstance.properties.cast(Text).contains(str(workspace_id)),
            )
        )
        or 0
    )
    return [
        extensions.WorkspaceReference(
            table="objects", label="객체", count=count, blocks_delete=True
        ),
        extensions.WorkspaceReference(
            table="object_links",
            label="이 부서와 이은 객체(관계)",
            count=int(linked),
            blocks_delete=True,
        ),
        extensions.WorkspaceReference(
            table="objects.properties",
            label="이 부서를 가리키는 객체(속성)",
            count=int(referring),
            blocks_delete=True,
        ),
    ]


#: 통폐합을 거절할 때 이름을 몇 개까지 적나 — 나머지는 수로.
CLASH_SHOWN = 5


def _scope_clashes(db: Session, source: uuid.UUID, target: uuid.UUID) -> list[str]:
    """두 부서를 합치면 **부서 안에서 하나여야 할 것**이 겹치는 자리 — 유일 범위가 부서인
    타입(`key_scope="workspace"`)의 식별자와 유일 속성. 살아 있는 것끼리만 본다(지운 것은
    `require_key_free` 도 안 센다)."""
    scoped = {
        row.id: row.label
        for row in db.scalars(select(ObjectType).where(ObjectType.key_scope == "workspace"))
    }
    if not scoped:
        return []
    mine = aliased(ObjectInstance, name="moving")
    theirs = aliased(ObjectInstance, name="staying")
    paired = (
        theirs.type_id == mine.type_id,
        theirs.owner_workspace_id == target,
        theirs.deleted_at.is_(None),
    )
    alive = (
        mine.type_id.in_(list(scoped)),
        mine.owner_workspace_id == source,
        mine.deleted_at.is_(None),
    )
    found: list[str] = []
    for type_id, key in db.execute(
        select(mine.type_id, mine.key)
        .join(theirs, and_(theirs.key == mine.key, *paired))
        .where(*alive, mine.key.is_not(None))
        .limit(CLASH_SHOWN + 1)
    ):
        found.append(f"{scoped[type_id]} 식별자 「{key}」")
    for definition in db.scalars(
        select(PropertyDef).where(
            PropertyDef.owner_kind == "type",
            PropertyDef.owner_id.in_(list(scoped)),
            PropertyDef.unique.is_(True),
        )
    ):
        held = mine.properties[definition.key].astext
        for (value,) in db.execute(
            select(held)
            .join(theirs, and_(theirs.properties[definition.key].astext == held, *paired))
            .where(*alive, mine.type_id == definition.owner_id, held.not_in(("", "[]")))
            .limit(CLASH_SHOWN + 1)
        ):
            found.append(f"{scoped[definition.owner_id]} {definition.label} 「{value}」")
    return found


def _move_objects(db: Session, source: uuid.UUID, target: uuid.UUID) -> int:
    """소유 부서를 바꾼다. **지운 객체도 함께 옮긴다** — 행이 남아 있으면 FK 는
    그대로 붙들고, 그러면 원본 부서를 끝내 지울 수 없다.

    **부서 안에서 하나여야 할 것이 겹치면 통째로 거절한다**(`_scope_clashes`) — 원 SQL 로 한
    번에 옮기므로 줄마다 볼 자리가 없다. 안 보면 두 부서에서 각자 하나이던 「A-1」 이 한
    부서에 둘이 되고, 그때부터 그 식별자로는 어느 것인지 정해지지 않는다(2026-10-08).
    통폐합은 한 트랜잭션이라 여기서 멈추면 아무것도 안 옮겨진다."""
    clashes = _scope_clashes(db, source, target)
    if clashes:
        shown = ", ".join(clashes[:CLASH_SHOWN])
        more = " 외 더 있음" if len(clashes) > CLASH_SHOWN else ""
        raise Conflict(
            code("OBJECTS", 8),
            f"옮겨 갈 부서에 같은 것이 이미 있어 객체를 옮기지 않습니다: {shown}{more}. "
            "부서 안에서 하나여야 하는 값이라 — 먼저 합치거나 값을 바꾸세요.",
        )
    done = db.execute(
        update(ObjectInstance)
        .where(ObjectInstance.owner_workspace_id == source)
        .values(owner_workspace_id=target)
    )
    return extensions.rows_changed(done)


def _move_saved_views(db: Session, source: uuid.UUID, target: uuid.UUID) -> int:
    done = db.execute(
        update(SavedView).where(SavedView.workspace_id == source).values(workspace_id=target)
    )
    return extensions.rows_changed(done)


def workspace_content(
    db: Session, workspace_id: uuid.UUID
) -> list[extensions.WorkspaceContent]:
    """부서 통폐합 때 **옮길 수 있는 것.** 참조(`workspace_reference`)와 다르다 —
    저기는 「가리키는 것」 이고 여기는 「가진 것」 이다.

    관계 선과 속성 참조는 여기 없다. 그것들은 이 부서를 **값으로** 가리키는 것이라,
    옮기는 것이 아니라 고쳐야 한다 — 자동으로 바꾸면 「담당 부서」 가 사람 모르게
    바뀐다.
    """
    objects = (
        db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(ObjectInstance.owner_workspace_id == workspace_id)
        )
        or 0
    )
    views = (
        db.scalar(
            select(func.count())
            .select_from(SavedView)
            .where(SavedView.workspace_id == workspace_id)
        )
        or 0
    )
    return [
        extensions.WorkspaceContent(
            kind="objects", label="객체", count=int(objects), move=_move_objects
        ),
        extensions.WorkspaceContent(
            kind="saved_views", label="저장된 뷰", count=int(views), move=_move_saved_views
        ),
    ]


def apply_year(db: Session, stmt: Select[Any], scope: Scope, year: int) -> Select[Any]:
    """축의 **시간 정책**대로 연도를 거른다.

        evergreen  필터를 무시한다 (기본값)
        lifecycle  valid_from_year ~ valid_to_year 에 그 해가 들면. NULL 끝 = 열림
        yearly     object_years 에 그 해가 배정돼 있으면 (불연속 가능)
        derived    도메인이 답한다 — 등록이 없으면 무시한다

    **`derived` 에서 등록이 없을 때 빈 목록을 주지 않는 이유**: 빈 목록은
    「데이터가 없다」 로 읽히고, 그러면 사람은 없는 것을 새로 만든다. 필터가
    작동하지 않는 것과 데이터가 없는 것은 다른 일이다.

    **인터페이스 목록은 구현 타입마다 정책이 다를 수 있다** — 정책별로 그 타입들의 조건을 세워
    OR 로 묶는다(상시 타입은 거르지 않고 그대로 든다). 하나로 몰면 상시 타입이 통째로 빠지거나,
    기간 타입이 거르지 않은 채 섞인다.
    """
    by_kind: dict[str, list[uuid.UUID]] = {}
    for one in scope.types:
        by_kind.setdefault(one.temporal_kind, []).append(one.id)
    if len(by_kind) <= 1:
        kind = next(iter(by_kind), "evergreen")
        clause = _year_clause(db, stmt, kind, year)
        return stmt if clause is None else stmt.where(clause)
    parts = []
    for kind, ids in by_kind.items():
        mine = ObjectInstance.type_id.in_(ids)
        clause = _year_clause(db, stmt.where(mine), kind, year)
        parts.append(mine if clause is None else and_(mine, clause))
    return stmt.where(or_(*parts))


def _year_clause(db: Session, stmt: Select[Any], kind: str, year: int) -> Any:
    """그 정책의 연도 조건 — 거르지 않으면 None."""
    if kind == "lifecycle":
        return and_(
            or_(
                ObjectInstance.valid_from_year.is_(None),
                ObjectInstance.valid_from_year <= year,
            ),
            or_(
                ObjectInstance.valid_to_year.is_(None),
                ObjectInstance.valid_to_year >= year,
            ),
        )

    if kind == "yearly":
        assigned = select(ObjectYear.object_id).where(ObjectYear.year == year)
        return ObjectInstance.id.in_(assigned)

    if kind == "derived":
        # 도메인이 등록한 것이 없으면 **거르지 않는다.**
        if not extensions.has_temporal_source():
            return None
        candidates = list(db.scalars(stmt.with_only_columns(ObjectInstance.id)))
        years = extensions.temporal_years(db, candidates)
        wanted = [one for one in candidates if year in years.get(one, set())]
        return ObjectInstance.id.in_(wanted)

    # evergreen — 연도 무관.
    return None
