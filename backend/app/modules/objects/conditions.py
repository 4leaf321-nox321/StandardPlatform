"""조건 거르기 — **칸 안에서는 OR, 칸끼리는 AND.**

「같음」 하나로는 「무게 10kg 넘고, 공급사가 비어 있고, 영남이거나 호남」 을 못 거른다.
그래서 칸의 종류에 맞는 연산을 둔다:

    숫자·날짜     gt · gte · lt · lte · eq · ne · in
    글자·주소     eq · ne · contains · starts · in
    선택·참조     eq · ne · in           (여러 값이면 「그 중 하나」)
    참조 · 자신   under                  (「하위 전체」 — 그 객체이거나 트리로 그 아래 전부)
    예/아니오     eq
    모두          empty · notempty
    계보(자신)    eq · ne                (`lineage` — 낡음 `stale` · 바로 낡음 `direct` ·
                                          낡지 않음 `fresh`, ADR 0041)

전체를 OR 로 잇는 트리는 안 만든다 — 화면에 그릴 수 없고, 그려도 사람이 못 읽는다.
「같은 칸 안 OR(`in`) + 칸끼리 AND」 가 대부분의 물음을 덮는다.

## 주소에 남는 모양

    ?f.weight.gte=10&f.region.in=영남|호남&f.vendor.empty=

`f.<칸>.<연산>=<값>`. 붙여 넣으면 같은 목록이 선다. 값 여럿은 `|` 로 가른다.
고정 칸은 `label` · `key` · `status`, 나머지는 속성 키다.

## 제외 조건 — 묶음 하나를 부정한다

    ?f.parts.notempty=&nf1.action.starts=SW&nf2.diag.in=C01|C02

`nf<번호>.<칸>.<연산>=<값>` — 같은 번호끼리 AND 로 묶어 **그 묶음에 맞는 것을
뺀다**(NOT). 묶음은 여럿이고 다른 조건 · 다른 묶음과는 AND 다. 지표의 규칙 기준(ADR
0015)이 「규칙 k 의 조건 AND 앞 규칙들 어느 것에도 안 맞음」 을 이것으로 적는다 — 그래서
건 보기의 수가 셀의 수와 **정확히** 같다(「≈」 로 넘기지 않는다, 2026-10-10).

모양을 고른 까닭: 조건 하나의 꼴(`칸.연산=값`)은 그대로 두고 앞머리만 다르게 하면 옛
주소 · 옛 저장된 뷰 · 옛 클라이언트가 하나도 안 깨진다. 트리(`not(and(...))`)를 JSON 으로
받는 길은 주소에 붙여 넣을 수 없고 화면의 칩으로도 못 그린다. 저장된 뷰 · API 의 조건에는
`exclude`(묶음 번호, 0 은 보통 조건) 한 칸이 붙는다.

**비어 있는 칸은 「안 맞음」 이다.** `ILIKE` · 비교가 NULL 이면 그 기록은 묶음에 안
맞으므로 남는다(`NOT coalesce(묶음, false)`). 지표의 `CASE WHEN` 이 NULL 을 「안 맞음」
으로 넘기는 것과 같은 뜻이다 — 맨 `NOT` 이면 NULL 의 부정이 NULL 이라 그 기록이 양쪽에서
다 빠진다.

## 같은 관계 묶음 — 같은 선 하나에서 함께 맞는다(ADR 0043)

    ?fe1.out.bom.eq=<X>&fe1.out.bom~qty.gte=4

이어진 것 너머의 조건은 하나하나가 「그중 하나라도」 다 — 그대로 두면 「X 가 1개, 다른 부품이
5개」 인 제품이 `out.bom = X` 와 `out.bom~qty ≥ 4` 에 **다른 선으로** 걸린다. 그 뜻은 바꾸지
않는다(저장된 뷰 · 지표 · 규칙이 그 뜻으로 저장돼 있다). 대신 `fe<번호>.` 로 **묶은** 조건들은
그 번호의 묶음이 함께 지나는 **마지막 관계 걸음의 선 하나**(그 선의 칸 · 상대 · 상대의 칸,
그 너머는 그 상대에서 「하나라도」)에서 모두 맞아야 걸린다 — `EXISTS(선 e: 조건1(e) AND
조건2(e) …)`. API · 저장된 뷰의 조건에는 `edge`(묶음 번호, 0 은 묶지 않음) 한 칸이 붙는다.

제외 묶음과 함께면 `nf<n>.fe<m>.<칸>.<연산>` — 같은 관계 묶음은 **그 제외 묶음 안의 한 조건**이
된다(「X 가 4개 이상 든 것은 뺀다」 = `NOT EXISTS(선: X AND 수량 ≥ 4)`). 같은 관계 묶음 번호는
한 자리(보통 조건 또는 제외 묶음 하나)에만 선다 — 한 선이 걸리면서 빠질 수는 없다.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import (
    ARRAY,
    Numeric,
    String,
    and_,
    any_,
    case,
    cast,
    distinct,
    false,
    func,
    literal,
    not_,
    or_,
    select,
    true,
)
from sqlalchemy.orm import aliased

from app.modules.objects import axes, graph, lineage, paths
from app.modules.objects.models import ObjectInstance, ObjectRef
from app.modules.ontology import conversion
from app.modules.ontology.models import DETACHED_KINDS, PropertyDef
from app.modules.ontology.services import InvalidValue
from app.shared.errors import AppError, code
from app.shared.text import like_escape

OPS_ORDER: tuple[str, ...] = ("eq", "ne", "gt", "gte", "lt", "lte", "in")
OPS_TEXT: tuple[str, ...] = ("eq", "ne", "contains", "starts", "in")
OPS_CHOICE: tuple[str, ...] = ("eq", "ne", "in")
OPS_BOOL: tuple[str, ...] = ("eq",)
OPS_ANY: tuple[str, ...] = ("empty", "notempty")
#: 「하위 전체」(ADR 0035) — 그 객체이거나, 그 타입의 트리(`list_view.tree`)로 그 아래 전부.
#: 값은 객체 id, 여럿이면 `|`(어느 하나의 아래). 목록의 객체 **자신**에 걸 때는 칸이 `id` 다.
OPS_TREE: tuple[str, ...] = ("under",)
#: 「위 전부」 — 그 객체이거나, **값의 타입의 트리**로 그 위 전부(「이 부품을 담은 모델」).
#: 목록의 객체 자신(`id`)에만 건다. 값은 객체 id, 여럿이면 `|`(어느 하나의 위).
OPS_UP: tuple[str, ...] = ("above",)
#: 「이어진 것의 개수」 — 참조 칸 · 관계 자체(`out.<관계>` · `in.<관계>`) · 나를 가리키는 것
#: (`in.<타입>:<칸>`)에. 값은 0 이상의 정수, 서로 다른 상대를 센다. 같은 관계 묶음(`edge`)에
#: 넣으면 **묶음의 다른 조건에 맞는 것만** 센다 — 「불합격 시험이 8건 이상인 모델」.
OPS_COUNT: tuple[str, ...] = ("count_eq", "count_gte", "count_lte")
SELF_FIELD = "id"
#: 「낡은 것만」(ADR 0041) — 목록의 객체 **자신**이 계보로 낡았나. 값은
#: `lineage.CONDITION_VALUES`. 그 타입에 같은 키의 속성이 있으면 속성이 이긴다(예전 조건이 안
#: 바뀌게) — 속성 키 `lineage` 는 드물다.
LINEAGE_FIELD = "lineage"

#: (타입 slug, 뿌리들) → 뿌리와 그 아래 전부, 트리가 없으면 None. 조건은 DB 없이 짓는다 —
#: 트리를 아는 쪽(`paths.Resolver.subtree_of`)이 넘긴다.
SubtreeOf = Callable[[str | None, list[uuid.UUID]], list[uuid.UUID] | None]

FIXED_FIELDS = ("label", "key", "status")
MULTI_SEP = "|"
PREFIX = "f."
#: 제외 묶음의 앞머리 — `nf<번호>.<칸>.<연산>`.
EXCLUDE_PREFIX = "nf"
#: 제외 묶음 번호는 두 자리까지 — 규칙 기준의 「기타」 건 보기가 규칙마다 하나씩 쓴다
#: (규칙 20개).
MAX_EXCLUDE = 99
_EXCLUDE_KEY = re.compile(r"^nf([0-9]{1,2})\.(.+)$")
#: 같은 관계 묶음의 앞머리 — `fe<번호>.<칸>.<연산>`(ADR 0043). 제외 묶음 안이면
#: `nf<n>.fe<m>.<칸>.<연산>`(제외 묶음이 먼저).
EDGE_PREFIX = "fe"
MAX_EDGE = 99
_EDGE_KEY = re.compile(r"^fe([0-9]{1,2})\.(.+)$")


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: str
    exclude: int = 0
    """0 이면 보통 조건. 1 이상이면 그 번호의 **제외 묶음** — 같은 번호끼리 AND 로 묶어 그
    묶음에 맞는 것을 뺀다."""
    edge: int = 0
    """0 이면 묶지 않음. 1 이상이면 그 번호의 **같은 관계 묶음** — 같은 번호의 조건은 같은 선
    하나에서 함께 맞아야 걸린다(ADR 0043)."""


def _detached(definition: PropertyDef) -> str:
    """값이 속성 칸에 없는 칸(파일 · 문서)은 조건으로 걸 수 없다 — 그 말."""
    return "파일 칸은 못 겁니다." if definition.data_type == "file" else "문서 칸은 못 겁니다."


def ops_for(data_type: str) -> tuple[str, ...]:
    """이 종류의 칸에 걸 수 있는 연산. 화면도 같은 표를 쓴다(스키마로 내보낸다)."""
    base: tuple[str, ...]
    if data_type in ("number", "date", "datetime"):
        base = OPS_ORDER
    elif data_type in ("text", "text_long", "url"):
        base = OPS_TEXT
    elif data_type == "object_ref":
        base = (*OPS_CHOICE, *OPS_TREE, *OPS_COUNT)
    elif data_type == "enum":
        base = OPS_CHOICE
    elif data_type == "bool":
        base = OPS_BOOL
    else:
        base = ()
    return (*base, *OPS_ANY)


def parse(params: Any) -> list[Condition]:
    """`f.<칸>.<연산>=<값>` · `nf<번호>.<칸>.<연산>=<값>`(제외 묶음) 쿼리 파라미터 전부. 같은
    키가 여럿이면 전부(AND)."""
    out: list[Condition] = []
    for key, value in (
        params.multi_items() if hasattr(params, "multi_items") else params.items()
    ):
        exclude = edge = 0
        bound = False
        if key.startswith(PREFIX):
            rest = key[len(PREFIX) :]
        elif (found := _EDGE_KEY.match(key)) is not None:
            bound, edge, rest = True, int(found.group(1)), found.group(2)
        else:
            found = _EXCLUDE_KEY.match(key)
            if found is None:
                continue
            exclude, rest = int(found.group(1)), found.group(2)
            if exclude < 1:
                raise InvalidValue(
                    code("OBJECTS", 70),
                    f"제외 묶음 번호는 1 부터입니다: {key} (nf<번호>.<칸>.<연산>)",
                )
            if (inner := _EDGE_KEY.match(rest)) is not None:
                bound, edge, rest = True, int(inner.group(1)), inner.group(2)
        if bound and edge < 1:
            raise InvalidValue(
                code("OBJECTS", 70),
                f"같은 관계 묶음 번호는 1 부터입니다: {key} (fe<번호>.<칸>.<연산>)",
            )
        if _EXCLUDE_KEY.match(rest) is not None:
            raise InvalidValue(
                code("OBJECTS", 70),
                f"조건의 모양이 틀렸습니다: {key} — 제외 묶음이 앞에 섭니다"
                "(nf<번호>.fe<번호>.<칸>.<연산>).",
            )
        if "." not in rest:
            head = f"{'nf<번호>.' if exclude else ''}{'fe<번호>' if bound else ''}"
            raise InvalidValue(
                code("OBJECTS", 70),
                f"조건의 모양이 틀렸습니다: {key} ({head.rstrip('.') or 'f'}.<칸>.<연산>)",
            )
        field, op = rest.rsplit(".", 1)
        out.append(Condition(field=field, op=op, value=value, exclude=exclude, edge=edge))
    return out


def key_of(condition: Condition) -> str:
    """조건 하나의 쿼리 키 — `parse` 의 역. 제외 묶음이면 `nf<번호>.`, 같은 관계 묶음이면
    `fe<번호>.` 로 시작한다(둘 다면 `nf<n>.fe<m>.`)."""
    head = f"{EXCLUDE_PREFIX}{condition.exclude}." if condition.exclude else ""
    if condition.edge:
        head += f"{EDGE_PREFIX}{condition.edge}."
    return f"{head or PREFIX}{condition.field}.{condition.op}"


def _column(field: str, entity: Any = ObjectInstance) -> Any:
    if field == "label":
        return entity.label
    if field == "key":
        return entity.key
    if field == "status":
        return entity.status
    return entity.properties[field].astext


def split_values(raw: str) -> list[str]:
    """`in` 의 값 여럿(`A|B`) — 빈 것은 뺀다."""
    return [one.strip() for one in raw.split(MULTI_SEP) if one.strip()]


_values = split_values


def _number(field: str, raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        raise InvalidValue(code("OBJECTS", 71), f"{field}: 숫자여야 합니다: {raw!r}") from None


def _date(field: str, raw: str) -> date:
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        raise InvalidValue(
            code("OBJECTS", 71), f"{field}: 날짜는 YYYY-MM-DD 여야 합니다: {raw!r}"
        ) from None


def clause(
    condition: Condition,
    definition: PropertyDef | None,
    entity: Any = ObjectInstance,
    label: str | None = None,
    *,
    subtree_of: SubtreeOf | None = None,
    fixed: bool = True,
) -> Any:
    """조건 하나 → SQL. `entity` 는 보통 목록의 객체이고, 이어진 것 너머의 칸이면 그
    이어진 객체(별칭)다 — 같은 규칙을 두 벌로 적지 않는다. 지표의 규칙 기준(ADR 0015)도 이
    식을 `CASE WHEN` 안에 넣는다(칸 · 연산 · 값을 따로 배우지 않는다) — 이어진 것 너머까지 한
    번에 다루는 입구는 `clauses` · `matches` 다.

    `fixed=False` 면 고정 칸(이름 · 식별자 · 상태)이 없는 자리다 — 선의 칸(ADR 0043)은
    `properties` 하나뿐이라, 키가 `status` 인 관계 속성도 속성으로 읽는다."""
    field, op, raw = condition.field, condition.op, condition.value
    is_fixed = fixed and field in FIXED_FIELDS
    data_type = "text" if is_fixed else (definition.data_type if definition else "")
    multi = bool(definition and definition.multi)
    if is_fixed and field == "status":
        data_type = "enum"
    allowed = ops_for(data_type)
    label = label or (definition.label if definition else field)
    if op not in allowed:
        raise InvalidValue(
            code("OBJECTS", 72),
            f"{label}: 「{op}」 는 이 칸에 못 겁니다. 되는 것: {', '.join(allowed)}",
        )

    if data_type == "object_ref":
        if op in OPS_COUNT:
            return _ref_clause(entity, field, op, raw, label=label)
        _refuse_names(raw, op, label, definition)
        if op in OPS_TREE:
            assert definition is not None
            roots = _ids(_values(raw))
            found = subtree_of(definition.ref_type_slug, roots) if subtree_of else None
            return _ref_clause(
                entity, field, op, raw, subtree=_subtree_or_refuse(found, label)
            )
        return _ref_clause(entity, field, op, raw)

    column = _column(field, entity)
    json_col = entity.properties[field]

    if op == "empty":
        if is_fixed:
            return or_(column.is_(None), column == "")
        return or_(json_col.is_(None), column == "", column == "[]", column == "null")
    if op == "notempty":
        if is_fixed:
            return and_(column.is_not(None), column != "")
        return and_(json_col.is_not(None), column != "", column != "[]", column != "null")

    if data_type == "number":
        if multi:
            # 여러 값 칸은 배열이라 숫자로 못 바꾼다 — 「담고 있나」 만 묻는다.
            if op in ("gt", "gte", "lt", "lte"):
                raise InvalidValue(
                    code("OBJECTS", 72),
                    f"{label}: 여러 값 칸에는 범위를 못 겁니다. 같음·그 중 하나·비어 있음만.",
                )
            numbers = [_number(label, one) for one in (_values(raw) if op == "in" else [raw])]
            if not numbers:
                return false()
            has_any = or_(*[json_col.contains([one]) for one in numbers])
            # 「다름」 은 칸이 비어 있는 것도 포함한다 — NULL 의 부정은 NULL 이라 따로 적는다.
            return or_(json_col.is_(None), ~has_any) if op == "ne" else has_any
        # **읽을 수 있는 값만 숫자로** — 맨 cast 는 숫자 아닌 글자(옛 데이터 · 손으로 고친 값)
        # 하나에 질의 전체가 오류(500)였다. 통계의 숫자 가드(`axes.numeric`)와 같은 모양이고,
        # 정밀도를 지키려 Numeric 으로 바꾼다.
        numeric = case((column.op("~")(axes.NUMERIC_RE), cast(column, Numeric)), else_=None)
        if op == "in":
            picked = [_number(label, one) for one in _values(raw)]
            return or_(*[numeric == one for one in picked]) if picked else false()
        number = _number(label, raw)
        return {
            "eq": numeric == number,
            # 「다름」 은 비어 있는 것(못 읽는 값 포함)도 든다 — 여러 값 숫자 · 참조 · 글자와
            # 같은 뜻이다. 예전에는 단일 숫자 칸만 빈 것을 뺐다(2026-10-08).
            "ne": or_(numeric.is_(None), numeric != number),
            "gt": numeric > number,
            "gte": numeric >= number,
            "lt": numeric < number,
            "lte": numeric <= number,
        }[op]

    if data_type == "bool":
        # 참/거짓 어휘는 일괄 입력 · 종류 변경과 한 벌이다(`conversion.TRUE_WORDS`).
        wanted = raw.strip().lower() in conversion.TRUE_WORDS
        return column == ("true" if wanted else "false")

    if multi:
        # 여러 값 칸 — 「그 값을 담고 있나」. contains 는 JSONB 배열에 대한 포함이다.
        if op == "eq":
            return json_col.contains([raw])
        if op == "ne":
            return or_(json_col.is_(None), ~json_col.contains([raw]))
        if op == "in":
            values = _values(raw)
            return or_(*[json_col.contains([one]) for one in values]) if values else false()
        # 포함 · 시작은 **원소마다** 본다 — 배열을 통째 글자(`["가나", "다라"]`)로 견주면
        # 「시작」 은 늘 `["` 로 시작해 0건이었고, 「포함」 은 원소 사이의 `", "` 에도 걸렸다
        # (2026-10-08).
        if op == "contains":
            return _any_element(json_col, f"%{like_escape(raw)}%")
        if op == "starts":
            return _any_element(json_col, f"{like_escape(raw)}%")

    if op == "in":
        values = _values(raw)
        return column.in_(values) if values else false()
    if op == "contains":
        return column.ilike(f"%{like_escape(raw)}%", escape="\\")
    if op == "starts":
        return column.ilike(f"{like_escape(raw)}%", escape="\\")
    if op == "ne":
        return or_(column != raw, column.is_(None))
    if data_type == "date" and op in ("gt", "gte", "lt", "lte"):
        # **날짜는 읽어서 견준다** — 통계 · 지표의 버킷과 같은 가드(`axes.date_or_null`)로.
        # 글자로 견주면 `20260310`(정규형이 아닌 유효한 날짜)은 범위 밖이고 `2026-02-30`(못
        # 읽는 값)은 안이라, 막대를 눌러 나온 목록의 수가 막대의 수와 어긋난다.
        when = _date(label, raw)
        normalized = axes.date_or_null(column)
        return {
            "gt": normalized > when,
            "gte": normalized >= when,
            "lt": normalized < when,
            "lte": normalized <= when,
        }[op]
    if op in ("gt", "gte", "lt", "lte"):
        # 시각은 ISO 문자열이라 사전순이 곧 시간순이다(날짜 경계 `< 2026-03-01` 도 맞는다).
        return {
            "gt": column > raw,
            "gte": column >= raw,
            "lt": column < raw,
            "lte": column <= raw,
        }[op]
    return column == raw


def _any_element(json_col: Any, pattern: str) -> Any:
    """여러 값 칸의 **원소 중 하나라도** 그 모양(`ILIKE`, `\\` 이스케이프)에 맞나. 옛 데이터에
    배열이 아닌 값 하나가 든 칸도 한 원소로 본다(통계의 여러 값 펼치기와 같은 가드)."""
    shape = func.jsonb_typeof(json_col)
    array = case((shape == "array", json_col), else_=func.jsonb_build_array(json_col))
    items = func.jsonb_array_elements_text(array).table_valued("value")
    return (
        select(literal(1))
        .select_from(items)
        .where(items.c.value.ilike(pattern, escape="\\"))
        .exists()
    )


def _subtree_or_refuse(found: list[uuid.UUID] | None, label: str) -> list[uuid.UUID]:
    if found is None:
        raise InvalidValue(
            code("OBJECTS", 72),
            f"{label}: 「하위 전체」 는 트리가 정의된 타입에만 걸립니다 — 그 타입의 "
            "「목록 화면」 에서 트리로 쓸 관계를 선택하세요.",
        )
    return found


def _ref_clause(
    entity: Any,
    field: str,
    op: str,
    raw: str,
    *,
    subtree: list[uuid.UUID] | None = None,
    label: str = "",
) -> Any:
    """참조 칸 — **참조 색인**으로 묻는다(ADR 0010). JSONB 를 펴서 견주면 그 타입을 통째로
    읽는다(200만 건에서 1.3초, 실측) — 색인은 (누가 · 칸 · 누구를) 인덱스 하나다.

    단일값 · 여러 값이 같은 모양이다(색인에는 원소마다 한 줄). 「다름」 은 비어 있는 것도
    포함한다 — 화면의 다른 칸과 같은 뜻."""
    linked = select(literal(1)).where(ObjectRef.src_id == entity.id, ObjectRef.key == field)
    if op in OPS_COUNT:
        return count_clause(linked, ObjectRef.dst_id, op, raw, label or field)
    if op == "empty":
        return ~linked.exists()
    if op == "notempty":
        return linked.exists()
    wanted = _ids(_values(raw) if op in ("in", "under") else [raw])
    if not wanted:
        # 모양이 id 가 아닌 값은 아무것도 가리키지 않는다 — 같음은 없고 다름은 전부다.
        return true() if op == "ne" else false()
    if op == "under":
        assert subtree is not None
        return linked.where(graph.any_of(ObjectRef.dst_id, subtree)).exists()
    # 값 여럿은 배열 하나로(`any_of`) — 부분 목록(`@<번호>`)이면 id 가 수만 개다.
    hit = linked.where(graph.any_of(ObjectRef.dst_id, wanted)).exists()
    return ~hit if op == "ne" else hit


def count_number(label: str, raw: str) -> int:
    """「개수」 의 값 — 0 이상의 정수."""
    try:
        number = int(raw.strip())
    except ValueError:
        number = -1
    if number < 0:
        raise InvalidValue(
            code("OBJECTS", 71), f"{label}: 개수는 0 이상의 정수여야 합니다: {raw!r}"
        )
    return number


def count_clause(linked: Any, far: Any, op: str, raw: str, label: str) -> Any:
    """`linked`(이어진 것을 찾는 몸통 — `select(1) …`)가 닿는 **서로 다른 상대**(`far`)의 수를
    견준다. 0 도 된다 — 「공급사가 없는 부품」 은 `count_eq 0`(= `empty`)."""
    number = count_number(label, raw)
    counted = linked.with_only_columns(func.count(distinct(far))).scalar_subquery()
    return {
        "count_eq": counted == number,
        "count_gte": counted >= number,
        "count_lte": counted <= number,
    }[op]


def _refuse_names(raw: str, op: str, label: str, definition: PropertyDef | None) -> None:
    """**참조 칸은 id 로 거른다** — 이름을 넣으면 거절하고 이름 풀이를 먼저 하라고 말한다.

    예전에는 id 모양이 아닌 값을 조용히 「아무것도 안 가리킴」 으로 읽어, 같음은 0건 · 다름은
    전부가 됐다 — AI 가 「모델 = S26」 으로 묻고 0건을 「없다」 로 옮겼다(AO 와 견준 조사 —
    2026-11-08). 빈 값은 그대로 둔다(화면이 지운 칸)."""
    if op in ("empty", "notempty"):
        return
    values = _values(raw) if op in ("in", "under") else [raw]
    named = [one.strip() for one in values if one.strip() and not _ids([one])]
    if not named:
        return
    target = (definition.ref_type_slug if definition else None) or "<그 타입>"
    raise InvalidValue(
        code("OBJECTS", 152),
        f"{label}: 「{named[0]}」 은(는) 객체 id 가 아닙니다 — 참조 칸은 가리키는 객체의 "
        f"id 로 거릅니다. 이름이면 이름 풀이로 id 를 먼저 찾으세요(MCP `object_resolve` · "
        f"`GET /api/objects/{target}/resolve?name=…`).",
    )


def _ids(raw: list[str]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for one in raw:
        try:
            out.append(uuid.UUID(one.strip()))
        except ValueError:
            continue
    return out


def apply(
    stmt: Any,
    defs: list[PropertyDef],
    conditions: list[Condition],
    resolver: paths.Resolver | None = None,
) -> Any:
    """조건 전부를 AND 로 건다. 없는 칸은 거절.

    이어진 것 너머의 칸(`ref.`·`out.`·`in.`)은 `resolver` 가 풀어 준다 — 없으면 그런 칸은
    없는 칸이다.
    """
    if not conditions:
        return stmt
    return stmt.where(*clauses(defs, conditions, resolver))


def clauses(
    defs: list[PropertyDef],
    conditions: list[Condition],
    resolver: paths.Resolver | None = None,
    *,
    offset: int = 0,
) -> list[Any]:
    """조건마다 WHERE 식 하나, 같은 관계 묶음마다 하나, 제외 묶음마다 하나 — `apply` 가
    문장에 걸고, 지표의 조건 비율 · 규칙 기준은 `CASE WHEN` 안에 넣는다(`matches`). 검사는
    `apply` 와 같다.

    `offset` 은 이어진 것 너머 조건의 별칭 번호를 어디서 시작하나 — 한 문장에 조건 묶음을 여럿
    넣을 때(규칙 열 개) 별칭 이름이 겹치지 않게."""
    by_key = {d.key: d for d in defs}
    out: list[Any] = []
    groups: dict[int, list[Any]] = {}
    bound: dict[tuple[int, int], list[tuple[int, Condition]]] = {}
    seat: dict[int, int] = {}
    for index, condition in enumerate(conditions, offset):
        if condition.exclude < 0 or condition.exclude > MAX_EXCLUDE:
            raise InvalidValue(
                code("OBJECTS", 70),
                f"제외 묶음 번호는 1 ~ {MAX_EXCLUDE} 입니다: {condition.exclude}",
            )
        if condition.edge < 0 or condition.edge > MAX_EDGE:
            raise InvalidValue(
                code("OBJECTS", 70),
                f"같은 관계 묶음 번호는 1 ~ {MAX_EDGE} 입니다: {condition.edge}",
            )
        if condition.edge:
            # 같은 관계 묶음은 한 자리에만 — 한 선이 걸리면서 빠질 수는 없다.
            first = seat.setdefault(condition.edge, condition.exclude)
            if first != condition.exclude:
                where = {0: "보통 조건"}
                raise InvalidValue(
                    code("OBJECTS", 146),
                    f"같은 관계 묶음 {condition.edge} 이 "
                    f"{where.get(first, f'제외 묶음 {first}')}과 "
                    f"{where.get(condition.exclude, f'제외 묶음 {condition.exclude}')}에 "
                    "함께 있습니다 — 같은 관계 묶음 하나는 한 자리(보통 조건 또는 제외 묶음 "
                    "하나)에만 둡니다. 다른 자리면 번호를 다르게 적습니다.",
                )
            bound.setdefault((condition.exclude, condition.edge), []).append(
                (index, condition)
            )
            continue
        found = _one(by_key, resolver, condition, index)
        if condition.exclude:
            groups.setdefault(condition.exclude, []).append(found)
        else:
            out.append(found)
    for (exclude, edge), members in sorted(bound.items()):
        found = _bound_clause(resolver, members, edge)
        if exclude:
            groups.setdefault(exclude, []).append(found)
        else:
            out.append(found)
    out.extend(excluded(groups[number]) for number in sorted(groups))
    return out


def matches(
    defs: list[PropertyDef],
    conditions: list[Condition],
    resolver: paths.Resolver | None = None,
    *,
    offset: int = 0,
) -> Any:
    """조건 전부(AND)에 맞나 — 식 하나. 지표의 조건 비율 · 규칙 기준이 `CASE WHEN` 에 넣는다.
    조건이 없으면 늘 참."""
    found = clauses(defs, conditions, resolver, offset=offset)
    return and_(*found) if found else true()


def excluded(found: list[Any]) -> Any:
    """제외 묶음 — 묶음 전부(AND)에 **맞는 것을 뺀다.** 비교가 NULL(칸이 비었다)이면 「안
    맞음」 이라 남는다. 맨 `NOT` 이면 NULL 의 부정이 NULL 이라 그 기록이 묶음에 맞는 쪽 ·
    안 맞는 쪽 어디에도 안 든다 — 규칙 기준의 셀(`CASE WHEN` 은 NULL 을 「안 맞음」 으로
    넘긴다)과 건 보기의 수가 어긋난다."""
    return not_(func.coalesce(and_(*found), false()))


def _one(
    by_key: dict[str, PropertyDef],
    resolver: paths.Resolver | None,
    condition: Condition,
    index: int,
) -> Any:
    if paths.is_path(condition.field):
        if resolver is None:
            raise InvalidValue(code("OBJECTS", 73), f"없는 칸입니다: {condition.field}")
        return _hop_clause(resolver, condition, index)
    if condition.field == SELF_FIELD:
        return _self_clause(resolver, condition, index)
    if condition.field == LINEAGE_FIELD and LINEAGE_FIELD not in by_key:
        if resolver is None:
            raise InvalidValue(
                code("OBJECTS", 132),
                "계보 조건(낡은 것만)은 목록 · 통계 · 내보내기 · 지표에서만 걸립니다.",
            )
        return lineage.condition(resolver.db, ObjectInstance, condition.op, condition.value)
    definition = by_key.get(condition.field)
    if condition.field not in FIXED_FIELDS and definition is None:
        raise InvalidValue(code("OBJECTS", 73), f"없는 칸입니다: {condition.field}")
    if definition is not None and definition.data_type in DETACHED_KINDS:
        raise InvalidValue(code("OBJECTS", 73), f"{definition.label}: {_detached(definition)}")
    return clause(condition, definition, subtree_of=resolver.subtree_of if resolver else None)


def _self_clause(resolver: paths.Resolver | None, condition: Condition, index: int) -> Any:
    """목록의 객체 **자신**이 그 객체이거나 그 아래 전부인가 — `f.id.under=<id>`. 목록의
    「하위 포함」 과 같은 뜻을 조건으로 — 통계 · 지표 · 저장된 뷰에도 걸린다.

    `f.id.above=<id>` 는 거울이다 — 그 객체이거나 그 **위** 전부(「이 부품을 담은 모델」).
    트리는 값의 타입의 것이라(부품의 구성) 목록의 타입(모델)에 트리가 없어도 된다."""
    if condition.op not in (*OPS_TREE, *OPS_UP, "in"):
        raise InvalidValue(
            code("OBJECTS", 72),
            "객체 자신(id)에는 「하위 전체」(under) · 「위 전부」(above) · 「그 중 하나」(in)"
            f"만 걸립니다: {condition.op}",
        )
    scope_type = resolver.scope.object_type if resolver else None
    roots = _ids(_values(condition.value))
    if condition.op == "in":
        return graph.any_of(ObjectInstance.id, roots)
    if condition.op in OPS_UP:
        if resolver is None:
            raise InvalidValue(
                code("OBJECTS", 72), "「위 전부」 는 목록 · 통계 · 지표에서만 걸립니다."
            )
        above = resolver.supertree_of(scope_type.slug if scope_type else None, roots)
        if above is None:
            raise InvalidValue(
                code("OBJECTS", 72),
                "「위 전부」 는 트리가 정의된 타입의 객체에만 걸립니다 — 값(아래의 것)의 타입 "
                "또는 이 목록의 타입의 「목록 화면」 에서 트리로 쓸 관계를 선택하세요.",
            )
        return graph.any_of(ObjectInstance.id, above)
    found = _subtree_or_refuse(
        resolver.subtree_of(scope_type.slug, roots) if resolver and scope_type else None,
        scope_type.label if scope_type else "이 목록",
    )
    return graph.any_of(ObjectInstance.id, found)


def chain_of(resolver: paths.Resolver, address: str) -> paths.Chain:
    """주소 → 걸음들. 못 풀면 조건의 말(422)로 — 「없는 칸」 과 같은 번호다."""
    try:
        return resolver.parse_chain(address)
    except AppError as caught:
        raise InvalidValue(code("OBJECTS", 73), caught.message) from caught


@dataclass
class Walked:
    """걸음을 이은 EXISTS 의 몸통 — 마지막 관계 걸음의 선 · 마지막 「나를 가리키는 것」 의 참조
    색인 줄 · 마지막 걸음의 상대 객체(관계 자체로 끝나면 None)."""

    linked: Any
    edges: Any
    back: Any
    target: Any


def walk(
    hops: list[paths.Hop],
    owners: list[paths.Resolver],
    start: Any,
    name: str,
    *,
    stop_at_edge: bool = False,
    edge: bool = False,
) -> Walked:
    """걸음을 잇는다 — 첫 걸음은 `start`(목록의 객체, 또는 바깥 질의의 별칭)와 상관 관계로
    묶이고, 다음 걸음은 앞 걸음의 끝(이어진 객체)에서 나간다. 이어진 칸(목록의 열)도 같은
    몸통을 쓴다 — 조건과 열이 같은 주소를 다르게 걸으면 「조건에는 걸리는데 열에는 없는」 줄이
    생긴다.

    `stop_at_edge` 면 마지막 관계 걸음의 상대 객체를 잇지 않는다(관계로 이어진 것 자체 · 선의
    칸 — 선만 본다). `edge` 면 마지막 관계 걸음의 선이 선의 칸(`props` · `valid_from` ·
    `valid_to`)을 싣는다."""
    linked: Any = None
    previous: Any = start
    last_edges: Any = None
    last_back: Any = None
    last_target: Any = None
    for depth, (hop, owner) in enumerate(zip(hops, owners, strict=True)):
        step_name = f"{name}_{depth}"
        final = depth == len(hops) - 1
        if hop.is_back:
            # 나를 가리키는 것(ADR 0017) — 참조 색인을 거꾸로(`dst_id` 색인). 그 객체는 보는
            # 사람이 볼 수 있는 것만 — 남의 부서 기록이 있다는 사실이 새지 않게.
            link = aliased(ObjectRef, name=f"{step_name}_back")
            target = aliased(ObjectInstance, name=f"{step_name}_obj")
            step = and_(
                link.dst_id == previous.id,
                link.key == hop.back_key,
                link.src_type_id == hop.back_type_id,
            )
            if linked is None:
                linked = select(literal(1)).select_from(link).where(step)
            else:
                linked = linked.join(link, step)
            seen = owner.seen(target)
            linked = linked.join(
                target, and_(target.id == link.src_id, target.deleted_at.is_(None), *seen)
            )
            last_target, last_back = target, link
        elif hop.kind == "ref":
            # 참조 색인으로 잇는다 — 예전의 JSONB 포함(`@>`) 조인은 타입 제한이 없는 비등가
            # 조인이라 200만 건에서 2분을 넘겼다(실측).
            link = aliased(ObjectRef, name=f"{step_name}_ref")
            target = aliased(ObjectInstance, name=f"{step_name}_obj")
            step = and_(link.src_id == previous.id, link.key == hop.name)
            if linked is None:
                linked = select(literal(1)).select_from(link).where(step)
            else:
                linked = linked.join(link, step)
            # 가리키는 객체도 **보이는 것만** — 들어오는 참조와 같은 규칙(`Resolver.seen`).
            linked = linked.join(
                target,
                and_(
                    target.id == link.dst_id, target.deleted_at.is_(None), *owner.seen(target)
                ),
            )
            last_target = target
        else:
            # 관계 걸음 — 저쪽 끝은 선(`edges`)에서 이미 보이는 것만 남는다. 관계로 이어진 것
            # **자체**(있음 · 없음 · 특정 객체)도 그래서 못 보는 객체로는 안 걸린다.
            edges = owner.edges(hop, f"{step_name}_edges", edge=edge and final)
            step = edges.c.me == previous.id
            if linked is None:
                linked = select(literal(1)).select_from(edges).where(step)
            else:
                linked = linked.join(edges, step)
            last_edges = edges
            if final and stop_at_edge:
                last_target = None
                break
            target = aliased(ObjectInstance, name=f"{step_name}_obj")
            linked = linked.join(
                target,
                and_(
                    paths.reaches(target, edges),
                    target.deleted_at.is_(None),
                    *owner.seen(target),
                ),
            )
            last_target = target
        previous = last_target
    return Walked(linked, last_edges, last_back, last_target)


def _hop_clause(resolver: paths.Resolver, condition: Condition, index: int) -> Any:
    """이어진 것 너머의 조건 — **「이어진 것 중 하나라도 맞으면」** (`EXISTS`).

    조인으로 걸면 이어진 것이 여럿인 객체가 목록에 여러 번 선다. EXISTS 는 몇 개가
    이어져 있든 한 번이다. 걸음이 여럿이면(`ref.model.ref.base.series`) EXISTS 안에서 걸음을
    이어 붙인다 — 뜻은 그대로 「그 길로 닿는 것 중 하나라도」 다.
    """
    chain = chain_of(resolver, condition.field)
    return _chain_clause(
        resolver, chain, condition.op, condition.value, ObjectInstance, f"hop{index}"
    )


def _chain_clause(
    resolver: paths.Resolver,
    chain: paths.Chain,
    op: str,
    raw: str,
    start: Any,
    name: str,
    *,
    from_depth: int = 0,
) -> Any:
    """`start` 에서 `chain` 의 `from_depth` 번째 걸음부터 끝까지 — 「그 길로 닿는 것 중
    하나라도」. 같은 관계 묶음은 묶은 선의 상대에서 나머지 걸음을 이것으로 잇는다."""
    whole = chain.field is None

    if whole:
        # 관계로 이어진 것 자체 — 누구와 이어졌나, 또는 이어져 있기는 한가.
        allowed = (
            (*OPS_CHOICE, *OPS_TREE, *OPS_ANY, *OPS_COUNT)
            if chain.data_type == "object_ref"
            else (*OPS_ANY, *OPS_COUNT)
        )
        if op not in allowed:
            raise InvalidValue(
                code("OBJECTS", 72),
                f"{chain.label}: 「{op}」 는 이 칸에 못 겁니다. 되는 것: {', '.join(allowed)}",
            )
    elif chain.edge:
        require_edge_usable(chain)
    elif chain.definition is not None and chain.definition.data_type in DETACHED_KINDS:
        raise InvalidValue(
            code("OBJECTS", 73), f"{chain.label}: {_detached(chain.definition)}"
        )

    walked = walk(
        chain.hops[from_depth:],
        chain.owners[from_depth:],
        start,
        name,
        # 관계 자체 · 선의 칸은 선만 본다 — 상대 객체를 잇지 않는다(예전과 같은 질의).
        stop_at_edge=whole or chain.edge,
        edge=chain.edge,
    )
    linked = walked.linked

    if whole and chain.hop.is_back:
        # 나를 가리키는 것 자체(**마지막 걸음이** 그것일 때) — 있음 · 없음, 또는 특정 객체가
        # 가리키나. 앞 걸음이 그것이고 끝이 관계면 아래 관계 길이다.
        if op in OPS_TREE:
            raise InvalidValue(
                code("OBJECTS", 72),
                f"{chain.label}: 나를 가리키는 것에는 「하위 전체」 를 못 겁니다.",
            )
        if op in OPS_COUNT:
            return count_clause(linked, walked.back.src_id, op, raw, chain.label)
        if op == "empty":
            return ~linked.exists()
        if op == "notempty":
            return linked.exists()
        picked = _ids(_values(raw) if op == "in" else [raw])
        hit = linked.where(graph.any_of(walked.back.src_id, picked)).exists()
        return ~hit if op == "ne" else hit
    if whole:
        assert walked.edges is not None
        if op in OPS_COUNT:
            return count_clause(linked, walked.edges.c.other, op, raw, chain.label)
        if op == "empty":
            return ~linked.exists()
        if op == "notempty":
            return linked.exists()
        return _other_clause(resolver, chain, op, raw, walked.edges, linked)

    assert chain.field is not None
    if chain.edge:
        inner = _edge_clause(Condition(chain.field, op, raw), chain, walked.edges)
    else:
        assert walked.target is not None
        inner = clause(
            Condition(chain.field, op, raw),
            chain.definition,
            walked.target,
            label=chain.label,
            subtree_of=resolver.subtree_of,
        )
    return linked.where(inner).exists()


def _other_clause(
    resolver: paths.Resolver,
    chain: paths.Chain,
    op: str,
    raw: str,
    edges: Any,
    linked: Any,
) -> Any:
    """관계로 이어진 것 **자체**가 그 객체인가(`eq` · `ne` · `in` · `under`) — 선의 저쪽 끝."""
    if op in OPS_TREE:
        far = chain.hop.target.object_type if chain.hop.target else None
        roots = _ids(_values(raw))
        found = _subtree_or_refuse(
            resolver.subtree_of(far.slug, roots) if far else None,
            far.label if far else chain.label,
        )
        return linked.where(_other_any(edges.c.other, found)).exists()
    # 사람이 준 값(id 글자)과 견준다 — 선은 uuid 라 글자로 바꿔 본다(id 가 아닌 글자도
    # 오류 없이 「안 맞음」 이 되게).
    other = cast(edges.c.other, String)
    if op == "eq":
        return linked.where(other == raw).exists()
    if op == "ne":
        return ~linked.where(other == raw).exists()
    return linked.where(_other_any(edges.c.other, _ids(_values(raw)))).exists()


def _other_match(
    chain: paths.Chain, resolver: paths.Resolver, op: str, raw: str, edges: Any
) -> Any:
    """같은 관계 묶음 안에서 — **이 선의** 저쪽 끝이 그 객체인가(묶은 선 위의 식)."""
    if op in ("empty", "notempty"):
        raise InvalidValue(
            code("OBJECTS", 146),
            f"{chain.label}: 같은 관계 묶음 안에서는 관계 자체에 「비어 있음 · 비어 있지 "
            "않음」 을 걸지 않습니다 — 묶음은 이미 그 관계가 있다는 뜻입니다. 「없는 것」 은 "
            "묶지 않은 조건으로 겁니다.",
        )
    if op in OPS_TREE:
        far = chain.hop.target.object_type if chain.hop.target else None
        roots = _ids(_values(raw))
        found = _subtree_or_refuse(
            resolver.subtree_of(far.slug, roots) if far else None,
            far.label if far else chain.label,
        )
        return _other_any(edges.c.other, found)
    other = cast(edges.c.other, String)
    if op == "eq":
        return other == raw
    if op == "ne":
        return other != raw
    return _other_any(edges.c.other, _ids(_values(raw)))


def _other_any(column: Any, picked: list[uuid.UUID]) -> Any:
    """선의 저쪽 끝이 그 중 하나인가 — 배열 하나로. 끝이 **글자**인 선(방향 없는 관계 · 원 표와
    섞인 끝 — `Resolver.edges`)은 글자 배열과, uuid 인 선은 uuid 배열과 견준다(형이 다르면
    Postgres 가 `character varying = uuid` 로 거절한다)."""
    if not picked:
        return false()
    if isinstance(column.type, String):
        return column == any_(literal([str(one) for one in picked], ARRAY(String)))
    return graph.any_of(column, picked)


def require_edge_usable(chain: paths.Chain) -> None:
    """선의 칸 중 참조 · 파일은 못 건다 — 참조 색인은 객체의 칸만 담는다."""
    if chain.definition is not None and chain.definition.data_type in paths.EDGE_UNUSABLE:
        raise InvalidValue(
            code("OBJECTS", 73),
            f"{chain.label}: 관계 속성 중 참조 · 파일 칸은 조건 · 기준으로 쓰지 않습니다.",
        )


class _EdgeRow:
    """선의 칸을 `clause` 에 넘기는 모양 — `properties` 가 그 선의 관계 속성(JSONB)이다."""

    def __init__(self, edges: Any) -> None:
        self.properties = edges.c.props


def _edge_clause(condition: Condition, chain: paths.Chain, edges: Any) -> Any:
    """선의 칸 하나(ADR 0043) — 관계 속성은 객체의 칸과 **같은 형별 규칙**(`clause`)으로,
    시작일 · 끝날은 날짜 칸으로 견준다."""
    require_edge_usable(chain)
    if chain.definition is None:
        return _period_clause(
            edges.c[condition.field], condition.op, condition.value, chain.label
        )
    return clause(condition, chain.definition, _EdgeRow(edges), label=chain.label, fixed=False)


def _period_clause(column: Any, op: str, raw: str, label: str) -> Any:
    """선의 시작일 · 끝날(`date` 칸, 비면 그쪽 끝이 열린 것) — 날짜 칸과 같은 연산."""
    allowed = (*OPS_ORDER, *OPS_ANY)
    if op not in allowed:
        raise InvalidValue(
            code("OBJECTS", 72),
            f"{label}: 「{op}」 는 이 칸에 못 겁니다. 되는 것: {', '.join(allowed)}",
        )
    if op == "empty":
        return column.is_(None)
    if op == "notempty":
        return column.is_not(None)
    if op == "in":
        days = [_date(label, one) for one in _values(raw)]
        return column.in_(days) if days else false()
    when = _date(label, raw)
    return {
        "eq": column == when,
        # 「다름」 은 비어 있는 것(열린 끝)도 든다 — 다른 칸의 「다름」 과 같은 뜻.
        "ne": or_(column.is_(None), column != when),
        "gt": column > when,
        "gte": column >= when,
        "lt": column < when,
        "lte": column <= when,
    }[op]


def _bound_clause(
    resolver: paths.Resolver | None, members: list[tuple[int, Condition]], number: int
) -> Any:
    """같은 관계 묶음 하나(ADR 0043) — 묶음의 주소들이 **함께 지나는 마지막 관계 걸음**의 선
    하나에서 조건이 모두 맞나: `EXISTS(선 e: 조건1(e) AND 조건2(e) …)`.

    조건마다 묶은 선에서 보는 것이 다르다 — 선의 칸(`out.bom~qty`), 관계 자체(`out.bom` 의
    같음 · 그 중 하나 · 하위 전체 — 이 선의 상대가 그 객체인가), 상대의 칸(`out.bom.label`),
    그 너머(`out.bom.ref.vendor.country` — 이 선의 상대에서 「하나라도」)."""
    if resolver is None:
        raise InvalidValue(
            code("OBJECTS", 146),
            f"같은 관계 묶음 {number}: 이어진 것 너머의 조건이 걸리지 않는 자리입니다.",
        )
    chains: list[paths.Chain] = []
    for _index, condition in members:
        if not paths.is_path(condition.field):
            raise InvalidValue(
                code("OBJECTS", 146),
                f"같은 관계 묶음 {number}: 「{condition.field}」 는 관계 걸음을 지나는 주소가 "
                "아닙니다 — 묶음에는 out.<관계> · in.<관계> 를 지나는 주소(관계 속성 "
                "out.<관계>~<관계 속성> 포함)만 넣습니다.",
            )
        chains.append(chain_of(resolver, condition.field))
    paths_of = [[(hop.kind, hop.name) for hop in chain.hops] for chain in chains]
    common = 0
    while (
        all(len(one) > common for one in paths_of)
        and len({one[common] for one in paths_of}) == 1
    ):
        common += 1
    # 묶음이 함께 지나는 **마지막 관계 걸음** — 같은 선 하나. 관계 걸음이 없으면 마지막
    # **나를 가리키는 걸음**(`in.<타입>:<칸>`) — 같은 기록 하나(「불합격 시험이 8건 이상」 의
    # 「불합격인 같은 시험」). 관계가 있으면 늘 관계다 — 예전에 저장된 묶음(관계 걸음 뒤에 나를
    # 가리키는 걸음이 이어지는 것)의 뜻이 바뀌지 않게. 관계도 그것도 없는 묶음은 예전에
    # 거절했다.
    hops = chains[0].hops
    at = next((depth for depth in range(common - 1, -1, -1) if hops[depth].is_relation), None)
    if at is None:
        at = next((depth for depth in range(common - 1, -1, -1) if hops[depth].is_back), None)
    if at is None:
        said = ", ".join(condition.field for _index, condition in members)
        raise InvalidValue(
            code("OBJECTS", 146),
            f"같은 관계 묶음 {number}: 조건들이 함께 지나는 관계 걸음이 없습니다({said}) — "
            "묶음의 주소는 같은 관계 걸음(out.<관계> · in.<관계>) 또는 같은 나를 가리키는 "
            "것(in.<타입>:<칸>)으로 시작해야 같은 선 · 같은 기록을 가리킵니다.",
        )
    depth = at + 1
    head = chains[0]
    back = head.hops[at].is_back
    # 상대 객체가 필요한 조건(상대의 칸 · 그 너머)이 있을 때만 상대를 잇는다 — 선의 칸 · 관계
    # 자체만이면 선만 본다(묶지 않은 조건과 같은 질의).
    needs_target = any(
        len(chain.hops) > depth or (not chain.edge and chain.field is not None)
        for chain in chains
    )
    first = members[0][0]
    walked = walk(
        head.hops[:depth],
        head.owners[:depth],
        ObjectInstance,
        f"bnd{first}",
        stop_at_edge=not needs_target,
        edge=True,
    )
    parts: list[Any] = []
    counts: list[tuple[str, str, str]] = []
    for (index, condition), chain in zip(members, chains, strict=True):
        if condition.op in OPS_COUNT:
            if len(chain.hops) != depth or chain.field is not None:
                raise InvalidValue(
                    code("OBJECTS", 146),
                    f"같은 관계 묶음 {number}: 개수({condition.op})는 묶음이 함께 지나는 "
                    "걸음 자체(그 관계 · 나를 가리키는 것)에 겁니다 — "
                    f"{condition.field} 는 그 너머의 칸입니다.",
                )
            counts.append((condition.op, condition.value, chain.label))
            continue
        if len(chain.hops) > depth:
            assert walked.target is not None
            parts.append(
                _chain_clause(
                    resolver,
                    chain,
                    condition.op,
                    condition.value,
                    walked.target,
                    f"hop{index}",
                    from_depth=depth,
                )
            )
        elif chain.edge:
            parts.append(
                _edge_clause(
                    Condition(chain.field or "", condition.op, condition.value),
                    chain,
                    walked.edges,
                )
            )
        elif chain.field is None:
            if back or chain.data_type != "object_ref" or chain.hop.is_back:
                raise InvalidValue(
                    code("OBJECTS", 146),
                    f"{chain.label}: 상대가 하나로 정해지지 않은 관계 · 나를 가리키는 것 "
                    "자체는 같은 관계 묶음에 넣지 않습니다 — 상대의 칸이나 관계 속성으로 "
                    "겁니다.",
                )
            parts.append(
                _other_match(chain, resolver, condition.op, condition.value, walked.edges)
            )
        else:
            if chain.definition is not None and chain.definition.data_type in DETACHED_KINDS:
                raise InvalidValue(
                    code("OBJECTS", 73), f"{chain.label}: {_detached(chain.definition)}"
                )
            assert walked.target is not None
            parts.append(
                clause(
                    Condition(chain.field, condition.op, condition.value),
                    chain.definition,
                    walked.target,
                    label=chain.label,
                    subtree_of=resolver.subtree_of,
                )
            )
    if counts:
        # 묶음의 다른 조건에 맞는 선(기록)만 — 그 상대의 수를 센다.
        assert back or walked.edges is not None
        far = walked.back.src_id if back else walked.edges.c.other
        body = walked.linked.where(and_(*parts)) if parts else walked.linked
        return and_(*[count_clause(body, far, op, raw, label) for op, raw, label in counts])
    return walked.linked.where(and_(*parts)).exists()


def of(one: Any) -> Condition:
    """저장된 · 받은 조건 하나(스키마 · 저장된 JSON) → `Condition`. 제외 묶음 · 같은 관계 묶음
    번호를 함께 옮긴다 — 자리마다 손으로 옮기면 한 자리만 묶음을 잃고, 그 자리의 목록만 뜻이
    달라진다."""
    if isinstance(one, dict):
        return Condition(
            str(one.get("field", "")),
            str(one.get("op", "")),
            str(one.get("value", "")),
            int(one.get("exclude") or 0),
            int(one.get("edge") or 0),
        )
    return Condition(
        one.field,
        one.op,
        one.value,
        int(getattr(one, "exclude", 0) or 0),
        int(getattr(one, "edge", 0) or 0),
    )


def to_query(conditions: list[Condition]) -> dict[str, str]:
    """저장된 뷰 → 쿼리 파라미터. `parse` 의 역."""
    return {key_of(c): c.value for c in conditions}
