"""조건 거르기 — **칸 안에서는 OR, 칸끼리는 AND.**

「같음」 하나로는 「무게 10kg 넘고, 공급사가 비어 있고, 영남이거나 호남」 을 못 거른다.
그래서 칸의 종류에 맞는 연산을 둔다:

    숫자·날짜     gt · gte · lt · lte · eq · ne · in
    글자·주소     eq · ne · contains · starts · in
    선택·참조     eq · ne · in           (여러 값이면 「그 중 하나」)
    예/아니오     eq
    모두          empty · notempty

전체를 OR 로 잇는 트리는 안 만든다 — 화면에 그릴 수 없고, 그려도 사람이 못 읽는다.
「같은 칸 안 OR(`in`) + 칸끼리 AND」 가 대부분의 물음을 덮는다.

## 주소에 남는 모양

    ?f.weight.gte=10&f.region.in=영남|호남&f.vendor.empty=

`f.<칸>.<연산>=<값>`. 붙여 넣으면 같은 목록이 선다. 값 여럿은 `|` 로 가른다.
고정 칸은 `label` · `key` · `status`, 나머지는 속성 키다.

## 모르는 것은 거절한다

없는 칸·안 맞는 연산·못 읽는 값은 422 로 **무엇이 틀렸는지** 말한다. 조용히
무시하면 거르기가 안 걸린 목록이 「전부」 로 읽힌다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Numeric, String, and_, cast, false, func, literal, or_, select
from sqlalchemy.orm import aliased

from app.modules.objects import paths
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import PropertyDef
from app.modules.ontology.services import InvalidValue
from app.shared.errors import AppError, code

OPS_ORDER: tuple[str, ...] = ("eq", "ne", "gt", "gte", "lt", "lte", "in")
OPS_TEXT: tuple[str, ...] = ("eq", "ne", "contains", "starts", "in")
OPS_CHOICE: tuple[str, ...] = ("eq", "ne", "in")
OPS_BOOL: tuple[str, ...] = ("eq",)
OPS_ANY: tuple[str, ...] = ("empty", "notempty")

FIXED_FIELDS = ("label", "key", "status")
MULTI_SEP = "|"
PREFIX = "f."


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: str


def ops_for(data_type: str) -> tuple[str, ...]:
    """이 종류의 칸에 걸 수 있는 연산. 화면도 같은 표를 쓴다(스키마로 내보낸다)."""
    base: tuple[str, ...]
    if data_type in ("number", "date", "datetime"):
        base = OPS_ORDER
    elif data_type in ("text", "text_long", "url"):
        base = OPS_TEXT
    elif data_type in ("enum", "object_ref"):
        base = OPS_CHOICE
    elif data_type == "bool":
        base = OPS_BOOL
    else:
        base = ()
    return (*base, *OPS_ANY)


def parse(params: Any) -> list[Condition]:
    """`f.<칸>.<연산>=<값>` 쿼리 파라미터 전부. 같은 키가 여럿이면 전부(AND)."""
    out: list[Condition] = []
    for key, value in (
        params.multi_items() if hasattr(params, "multi_items") else params.items()
    ):
        if not key.startswith(PREFIX):
            continue
        rest = key[len(PREFIX) :]
        if "." not in rest:
            raise InvalidValue(
                code("OBJECTS", 70), f"조건의 모양이 틀렸습니다: {key} (f.<칸>.<연산>)"
            )
        field, op = rest.rsplit(".", 1)
        out.append(Condition(field=field, op=op, value=value))
    return out


def _column(field: str, entity: Any = ObjectInstance) -> Any:
    if field == "label":
        return entity.label
    if field == "key":
        return entity.key
    if field == "status":
        return entity.status
    return entity.properties[field].astext


def _values(raw: str) -> list[str]:
    return [one.strip() for one in raw.split(MULTI_SEP) if one.strip()]


def _number(field: str, raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        raise InvalidValue(code("OBJECTS", 71), f"{field}: 숫자여야 합니다: {raw!r}") from None


def _clause(
    condition: Condition,
    definition: PropertyDef | None,
    entity: Any = ObjectInstance,
    label: str | None = None,
) -> Any:
    """조건 하나 → SQL. `entity` 는 보통 목록의 객체이고, 이어진 것 너머의 칸이면 그
    이어진 객체(별칭)다 — 같은 규칙을 두 벌로 적지 않는다."""
    field, op, raw = condition.field, condition.op, condition.value
    is_fixed = field in FIXED_FIELDS
    data_type = "text" if is_fixed else (definition.data_type if definition else "")
    multi = bool(definition and definition.multi)
    if field == "status":
        data_type = "enum"
    allowed = ops_for(data_type)
    label = label or (definition.label if definition else field)
    if op not in allowed:
        raise InvalidValue(
            code("OBJECTS", 72),
            f"{label}: 「{op}」 는 이 칸에 못 겁니다. 되는 것: {', '.join(allowed)}",
        )

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
        numeric = cast(column, Numeric)
        if op == "in":
            picked = [_number(label, one) for one in _values(raw)]
            return or_(*[numeric == one for one in picked]) if picked else false()
        number = _number(label, raw)
        return {
            "eq": numeric == number,
            "ne": numeric != number,
            "gt": numeric > number,
            "gte": numeric >= number,
            "lt": numeric < number,
            "lte": numeric <= number,
        }[op]

    if data_type == "bool":
        wanted = raw.strip().lower() in ("true", "1", "예", "y", "yes")
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
        if op == "contains":
            return column.ilike(f"%{raw}%")
        if op == "starts":
            return column.ilike(f"{raw}%")

    if op == "in":
        values = _values(raw)
        return column.in_(values) if values else false()
    if op == "contains":
        return column.ilike(f"%{raw}%")
    if op == "starts":
        return column.ilike(f"{raw}%")
    if op == "ne":
        return or_(column != raw, column.is_(None))
    if op in ("gt", "gte", "lt", "lte"):
        # 날짜·시각은 ISO 문자열이라 사전순이 곧 시간순이다.
        return {
            "gt": column > raw,
            "gte": column >= raw,
            "lt": column < raw,
            "lte": column <= raw,
        }[op]
    return column == raw


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
    by_key = {d.key: d for d in defs}
    for index, condition in enumerate(conditions):
        if paths.is_path(condition.field):
            if resolver is None:
                raise InvalidValue(code("OBJECTS", 73), f"없는 칸입니다: {condition.field}")
            stmt = stmt.where(_hop_clause(resolver, condition, index))
            continue
        definition = by_key.get(condition.field)
        if condition.field not in FIXED_FIELDS and definition is None:
            raise InvalidValue(code("OBJECTS", 73), f"없는 칸입니다: {condition.field}")
        if definition is not None and definition.data_type == "file":
            raise InvalidValue(
                code("OBJECTS", 73), f"{definition.label}: 파일 칸은 못 겁니다."
            )
        stmt = stmt.where(_clause(condition, definition))
    return stmt


def _hop_clause(resolver: paths.Resolver, condition: Condition, index: int) -> Any:
    """이어진 것 너머의 조건 — **「이어진 것 중 하나라도 맞으면」** (`EXISTS`).

    조인으로 걸면 이어진 것이 여럿인 객체가 목록에 여러 번 선다. EXISTS 는 몇 개가
    이어져 있든 한 번이다.
    """
    try:
        found = resolver.parse(condition.field)
    except AppError as caught:
        raise InvalidValue(code("OBJECTS", 73), caught.message) from caught
    hop, op, raw = found.hop, condition.op, condition.value

    if found.field is None:
        # 관계로 이어진 것 자체 — 누구와 이어졌나, 또는 이어져 있기는 한가.
        allowed = (*OPS_CHOICE, *OPS_ANY) if found.data_type == "object_ref" else OPS_ANY
        if op not in allowed:
            raise InvalidValue(
                code("OBJECTS", 72),
                f"{found.label}: 「{op}」 는 이 칸에 못 겁니다. 되는 것: {', '.join(allowed)}",
            )
        edges = resolver.edges(hop, f"hop{index}_edges")
        linked = select(literal(1)).select_from(edges).where(edges.c.me == ObjectInstance.id)
        if op == "empty":
            return ~linked.exists()
        if op == "notempty":
            return linked.exists()
        if op == "eq":
            return linked.where(edges.c.other == raw).exists()
        if op == "ne":
            return ~linked.where(edges.c.other == raw).exists()
        values = _values(raw)
        return linked.where(edges.c.other.in_(values)).exists() if values else false()

    if found.definition is not None and found.definition.data_type == "file":
        raise InvalidValue(code("OBJECTS", 73), f"{found.label}: 파일 칸은 못 겁니다.")
    target = aliased(ObjectInstance, name=f"hop{index}_obj")
    inner = _clause(
        Condition(found.field, op, raw), found.definition, target, label=found.label
    )
    alive = target.deleted_at.is_(None)
    if hop.kind == "ref":
        # 참조 칸은 id 하나(글자) 또는 id 의 배열 — `@>` 는 둘 다에 맞다.
        pointed = ObjectInstance.properties[hop.name].op("@>", is_comparison=True)(
            func.to_jsonb(cast(target.id, String))
        )
        return select(literal(1)).select_from(target).where(pointed, alive, inner).exists()
    edges = resolver.edges(hop, f"hop{index}_edges")
    return (
        select(literal(1))
        .select_from(edges.join(target, cast(target.id, String) == edges.c.other))
        .where(edges.c.me == ObjectInstance.id, alive, inner)
        .exists()
    )


def to_query(conditions: list[Condition]) -> dict[str, str]:
    """저장된 뷰 → 쿼리 파라미터. `parse` 의 역."""
    return {f"{PREFIX}{c.field}.{c.op}": c.value for c in conditions}
