"""기준(축)을 SQL 로 — 통계와 지표가 **같은 빌더**를 쓴다(ADR 0013).

기준 하나는 「식 하나 + 그 식이 필요로 하는 조인들」 이다. 자기 칸은 조인이 없고, 여러 값 칸은
배열을 펼치고(`LEFT JOIN LATERAL`), 이어진 것 너머의 칸은 걸음마다 이어진 객체를 바깥 조인으로
붙인다. 통계(`summary.py`)는 기준 하나 · 세부 기준 하나를 그때그때 세고, 지표(`metrics`)는 기준
여럿을 미리 센다 — 빌더가 둘이면 같은 주소가 다른 수를 낸다.

## 걸음은 이어 붙인다

`ref.model.ref.base.series` 처럼 걸음을 잇는다(`paths.Resolver.parse_chain`). 걸음마다 별칭을
번호로 짓고(`ax0_link` · `ax0_ref` · `ax1_link` …), **같은 접두 걸음은 한 번만 붙인다** — 두
기준이 같은 여럿 걸음(`out.parts.label` 과 `out.parts.ref.vendor.label`)을 따로 붙이면 행이
제곱으로 분다. `JoinPlan` 이 그것을 쥔다.

## 날짜는 단위로 묶는다

JSONB 의 날짜 글자는 정규형이 보장되지 않는다 — 쓰기 검증이 `fromisoformat` 을 통과한
**원문**을 저장하므로 `20240105` 도 있을 수 있다. 그래서 묶기 전에 가드를 걸고(`date_or_null`),
못 읽는 값은 NULL 로 두어 「(비어 있음)」 한 칸에 모은다. 버킷의 키는 **그 기간의 시작일**
(`2026-09-01`)이다 — 조건(`gte` · `lt`)으로 그대로 돌아갈 수 있어야 막대를 눌렀을 때 그 수가
나온다.
"""

from __future__ import annotations

import calendar
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Date, Float, Integer, Select, String, and_, case, cast, func, or_, true
from sqlalchemy.orm import Session, aliased

from app.modules.objects import paths, system
from app.modules.objects.models import ObjectInstance, ObjectRef
from app.modules.objects.scope import Scope, as_scope
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.workspaces.models import Workspace
from app.shared.errors import AppError, code

#: 묶을 수 있는 속성 종류. **긴 글과 파일은 없다** — 그룹이 행 수만큼 나온다.
GROUPABLE = ("text", "enum", "bool", "url", "number", "date", "datetime", "object_ref")
#: 기간 단위로 묶는 종류.
DATE_KINDS = ("date", "datetime")

#: 기간 단위. 통계의 기본은 해다 — 사람이 실제로 묻는 것이 연도일 때가 많고, 날짜 하나하나로
#: 묶으면 그룹이 수백 개다. 지표는 정의가 고른다.
GRAINS = ("day", "week", "month", "quarter", "year")
GRAIN_LABELS = {"day": "일", "week": "주", "month": "월", "quarter": "분기", "year": "해"}
DEFAULT_GRAIN = "year"

#: 속성이 아니라 객체 자신이 가진 축.
#:
#: **이름·식별자도 축이다.** 그룹이 행 수만큼 나온다는 이유로 막아 두면 「점수가 높은
#: 부품 열 개」 같은 **개별 순위** 그림이 아예 안 나온다 — 그런데 사람이 실제로 자주
#: 보는 것이 그것이다. 상한과 정렬이 이미 있으니 그대로 순위가 된다.
FIXED_FIELDS = {
    "label": "이름",
    "key": "식별자",
    "status": "상태",
    "workspace": "소유 부서",
    "created_year": "만든 해",
}

#: 인터페이스 목록에만 있는 축 — 그 줄이 **어느 타입의 객체인가**(ADR 0006).
TYPE_FIELD = ("type", "타입")

STATUS_LABELS = {"active": "사용", "deprecated": "안 씀"}
BOOL_LABELS = {"true": "예", "false": "아니오"}
EMPTY_LABEL = "(비어 있음)"

#: 숫자로 읽히는 값. 합·평균을 낼 때 이것에 안 맞는 행은 셈에서 빠진다.
NUMERIC_RE = r"^-?[0-9]+(\.[0-9]+)?$"
#: 날짜로 읽히는 값 — `YYYY-MM-DD` 와 대시 없는 `YYYYMMDD`, 뒤에 시각이 붙어도 된다.
#: `date_or_null` 이 같은 뜻을 정규식 없이 검사한다(비용).
DATE_RE = r"^[0-9]{4}-?[0-9]{2}-?[0-9]{2}([T ].*)?$"

#: 걸음을 몇 번까지 잇나. 넷부터는 조건을 화면에서 읽을 수 없고 질의 비용을 짐작할 수 없다.
MAX_HOPS = 3


@dataclass
class Axis:
    """기준 하나를 SQL 로 — 식·보여 줄 이름·종류, 그리고 바깥 조인으로 붙일 것들."""

    expr: Any
    label: str
    kind: str
    joins: list[tuple[Any, Any]] = field(default_factory=list)
    """(붙일 것, 조건) — 여러 값 칸의 펼친 배열, 참조로 이어진 객체, 관계. `JoinPlan` 이 만든
    것이면 같은 걸음의 조인 객체를 다른 기준과 **공유**한다 — `joined` 가 한 번만 붙인다."""
    multi: bool = False
    """한 행이 여러 막대에 들 수 있나."""
    ref_def: PropertyDef | None = None
    """참조 칸이면 그 정의 — 값(id)을 이름으로 바꿀 때 쓴다."""
    namer: Callable[[list[str]], dict[str, str]] | None = None
    """관계로 이어진 것 자체가 기준이면 — id 를 이름으로."""
    grain: str | None = None
    """날짜 축이면 묶은 기간 단위. 키는 그 기간의 시작일이다."""
    address: str = ""
    """이 축의 주소 — 막대가 조건으로 돌아갈 때 그대로 쓴다."""
    target: str | None = None
    """관계로 이어진 것 **자체**가 기준이면 — 상대 타입이 하나로 정해질 때 그 slug. 값은 그
    타입 객체의 id 라서 같은 타입을 가리키는 참조 칸과 **같은 값**이다(지표의 분모 짝, ADR
    0013)."""


def own_property(defs: list[PropertyDef], field_name: str) -> PropertyDef:
    """`properties.<키>` → 그 정의. 없으면 422."""
    key = field_name.split(".", 1)[1]
    found = next((one for one in defs if one.key == key), None)
    if found is None:
        raise AppError(
            code("OBJECTS", 40),
            f"이 타입에 없는 칸입니다: {key}",
            status=422,
        )
    return found


_prop = own_property


# --- 날짜 -----------------------------------------------------------------------


def date_or_null(column: Any) -> Any:
    """글자 칸을 날짜로 — **못 읽는 값은 NULL.**

    `to_date` 는 `2024-02-30` 같은 값에 오류를 내고, 그 행 하나가 질의를 통째로 죽인다(화면에는
    500). 그래서 모양과 달의 날수를 먼저 보고, 통과한 것만 바꾼다. CASE 는 차례대로 평가되므로
    바꾸는 식은 통과한 값에만 닿는다.

    모양 검사는 정규식이 아니라 `translate` 다 — 앞 열 글자에서 대시를 떼고 여덟 자리 숫자인지
    본다(`DATE_RE` 와 같은 뜻). 정규식 둘이면 200만 건 묶기가 4.9초, 이것이면 2.8초였다(실측,
    ADR 0013) — 기간 단위 묶기와 날짜 범위 조건이 행마다 이 식을 탄다.
    """
    digits = func.substr(func.translate(func.substr(column, 1, 10), "-", ""), 1, 8)
    shaped = and_(func.length(digits) == 8, func.translate(digits, "0123456789", "") == "")
    year = cast(func.substr(digits, 1, 4), Integer)
    month = cast(func.substr(digits, 5, 2), Integer)
    day = cast(func.substr(digits, 7, 2), Integer)
    leap = or_(and_(year % 4 == 0, year % 100 != 0), year % 400 == 0)
    days = case(
        (month == 2, case((leap, 29), else_=28)),
        (month.in_([4, 6, 9, 11]), 30),
        else_=31,
    )
    return case(
        (~shaped, None),
        (or_(year < 1, month < 1, month > 12, day < 1, day > days), None),
        else_=func.to_date(digits, "YYYYMMDD"),
    )


def bucket(date_expr: Any, grain: str) -> Any:
    """기간의 시작일(date). 주는 ISO(월요일 시작) — `date_trunc('week')` 가 그것이다."""
    if grain not in GRAINS:
        raise AppError(
            code("OBJECTS", 98),
            f"기간 단위는 {', '.join(GRAINS)} 중 하나여야 합니다: {grain}",
            status=422,
        )
    return cast(func.date_trunc(grain, date_expr), Date)


def bucket_key(date_expr: Any, grain: str) -> Any:
    """통계의 글자 키 — `YYYY-MM-DD`(기간의 시작일). 조건의 `gte` 값으로 그대로 쓴다."""
    return func.to_char(bucket(date_expr, grain), "YYYY-MM-DD")


def age_expr(later: Any, earlier: Any, grain: str) -> Any:
    """두 기간 시작일의 차 — 「판매 후 몇 개월째」. **달력 차이**라 코호트 행렬(코호트 x
    경과)이 「기간 빼기 코호트」 와 정확히 맞는다. 어느 쪽이 NULL 이면 NULL."""
    months = (func.extract("year", later) - func.extract("year", earlier)) * 12 + (
        func.extract("month", later) - func.extract("month", earlier)
    )
    raw = {
        "month": months,
        "quarter": months / 3,
        "year": func.extract("year", later) - func.extract("year", earlier),
        "week": (later - earlier) / 7,
        "day": later - earlier,
    }[grain]
    return cast(raw, Integer)


def numeric(column: Any) -> Any:
    """글자 칸을 숫자로 — **못 읽는 값은 NULL.** JSONB 는 글자를 담으므로 숫자가 아닌 값이 섞일
    수 있고(옛 데이터 · 손으로 고친 값), 그때 cast 는 그 행 하나 때문에 오류를 낸다."""
    return case((column.op("~")(NUMERIC_RE), cast(column, Float)), else_=None)


def _parse_day(key: str) -> date:
    return date.fromisoformat(key[:10])


def period_label(key: str, grain: str) -> str:
    """버킷 키(시작일) → 사람이 읽는 기간 — 2026 · 2026-Q3 · 2026-09 · 2026-W37 ·
    2026-09-11."""
    try:
        start = _parse_day(key)
    except ValueError:
        return key
    if grain == "year":
        return f"{start.year}"
    if grain == "quarter":
        return f"{start.year}-Q{(start.month - 1) // 3 + 1}"
    if grain == "month":
        return f"{start.year}-{start.month:02d}"
    if grain == "week":
        year, week, _ = start.isocalendar()
        return f"{year}-W{week:02d}"
    return key[:10]


def next_period(start: date, grain: str) -> date:
    """다음 기간의 시작일."""
    if grain == "day":
        return start + timedelta(days=1)
    if grain == "week":
        return start + timedelta(days=7)
    months = {"month": 1, "quarter": 3, "year": 12}[grain]
    total = start.year * 12 + (start.month - 1) + months
    year, month = divmod(total, 12)
    day = min(start.day, calendar.monthrange(year, month + 1)[1])
    return date(year, month + 1, day)


def period_range(key: str, grain: str) -> tuple[str, str] | None:
    """버킷 키 → 조건의 (`gte`, `lt`). 날짜 · 시각 글자는 ISO 라 사전순 비교가 곧 시간순이다 —
    `2026-09-30T23:59` 도 `< 2026-10-01` 에 든다."""
    try:
        start = _parse_day(key)
    except ValueError:
        return None
    return start.isoformat(), next_period(start, grain).isoformat()


# --- 축 빌더 -----------------------------------------------------------------------


def _not_dated(label: str) -> AppError:
    return AppError(
        code("OBJECTS", 98),
        f"「{label}」 은 날짜 칸이 아니라 기간 단위를 둘 수 없습니다.",
        status=422,
    )


def field_axis(
    entity: Any,
    field_name: str,
    definition: PropertyDef | None,
    label: str,
    alias: str,
    joins: list[tuple[Any, Any]],
    *,
    many: bool,
    grain: str | None = None,
    address: str = "",
) -> Axis:
    """`entity`(목록의 객체, 또는 이어진 객체의 별칭)의 칸 하나로 센다."""
    if definition is None:
        fixed = {"label": entity.label, "key": entity.key, "status": entity.status}
        return Axis(
            fixed[field_name],
            label,
            "status" if field_name == "status" else "plain",
            joins,
            many,
            address=address,
        )
    if definition.data_type not in GROUPABLE:
        raise AppError(
            code("OBJECTS", 42),
            f"「{label}」 은 기준으로 쓸 수 없는 종류입니다({definition.data_type}). "
            "긴 글과 파일은 기준이 되지 않습니다 — 그룹이 행 수만큼 나옵니다.",
            status=422,
        )
    joins = list(joins)
    if definition.multi:
        raw = entity.properties[definition.key]
        shape = func.jsonb_typeof(raw)
        # 옛 데이터에는 배열이 아닌 값 하나가 들어 있을 수 있다 — 그것도 한 값으로 센다.
        array = case(
            (shape == "array", raw),
            (func.coalesce(shape, "null") == "null", func.jsonb_build_array()),
            else_=func.jsonb_build_array(raw),
        )
        values = (
            func.jsonb_array_elements_text(array)
            .table_valued("value")
            .lateral(f"{alias}_values")
        )
        joins.append((values, true()))
        column = values.c.value
    else:
        column = entity.properties[definition.key].astext
    ref_def = definition if definition.data_type == "object_ref" else None
    multi = many or definition.multi
    if definition.data_type in DATE_KINDS:
        unit = grain or DEFAULT_GRAIN
        return Axis(
            bucket_key(date_or_null(column), unit),
            f"{label} ({GRAIN_LABELS[unit]})",
            definition.data_type,
            joins,
            multi,
            ref_def,
            grain=unit,
            address=address,
        )
    if grain is not None:
        raise _not_dated(label)
    # 글자 · 고를 값의 빈 글자는 NULL 과 같은 칸 — 「(비어 있음)」 이 둘로 갈리지 않게.
    textual = definition.data_type in ("text", "enum", "url")
    expr = func.nullif(column, "") if textual else column
    return Axis(expr, label, definition.data_type, joins, multi, ref_def, address=address)


class JoinPlan:
    """한 질의의 기준들이 붙일 조인 — **같은 걸음은 한 번만.**

    요청 하나(통계 한 번, 지표 한 번)에 하나 만들어 기준마다 `axis()` 를 부른다. 걸음의 끝
    (이어진 객체의 별칭)과 그 걸음에서 다시 걸을 때 쓸 Resolver 를 접두 경로마다 기억한다.
    """

    def __init__(self, db: Session, target: ObjectType | Scope, *, prefix: str = "ax") -> None:
        self.db = db
        self.scope = as_scope(db, target)
        self.resolver = paths.Resolver(db, self.scope)
        self.prefix = prefix
        self._ends: dict[tuple[tuple[str, str], ...], _End] = {}
        self._count = 0

    def _walk(self, chain: paths.Chain) -> _End:
        entity: Any = ObjectInstance
        owner = self.resolver
        joins: list[tuple[Any, Any]] = []
        end = _End(entity, owner, None, joins)
        for index, hop in enumerate(chain.hops):
            key = tuple((one.kind, one.name) for one in chain.hops[: index + 1])
            if key not in self._ends:
                name = f"{self.prefix}{self._count}"
                self._count += 1
                mine: list[tuple[Any, Any]] = []
                edges = None
                if hop.kind == "ref":
                    # 참조 색인으로 잇는다(ADR 0010) — 예전의 JSONB 포함(`@>`) 조인은 200만
                    # 건에서 2분을 넘겼다. 색인은 원소마다 한 줄이라 단일값 · 여러 값이 같다.
                    link = aliased(ObjectRef, name=f"{name}_link")
                    target = aliased(ObjectInstance, name=f"{name}_ref")
                    mine = [
                        (link, and_(link.src_id == entity.id, link.key == hop.name)),
                        (target, and_(target.id == link.dst_id, target.deleted_at.is_(None))),
                    ]
                else:
                    edges = owner.edges(hop, f"{name}_edges")
                    target = aliased(ObjectInstance, name=f"{name}_obj")
                    mine = [
                        (edges, edges.c.me == entity.id),
                        (
                            target,
                            and_(
                                cast(target.id, String) == edges.c.other,
                                target.deleted_at.is_(None),
                            ),
                        ),
                    ]
                child = owner.child(hop) if hop.target is not None else None
                self._ends[key] = _End(
                    target, child, edges, [*joins, *mine], hop=hop, owner=owner
                )
            end = self._ends[key]
            entity, owner, joins = end.entity, end.resolver, end.joins  # type: ignore[assignment]
        return end

    def axis(self, address: str, *, grain: str | None = None) -> Axis:
        """주소 하나 → 기준. 식은 **글자**를 내놓는다 — 그래야 한 자리에서 상태·부서·속성을
        같은 규칙으로 다룬다. 여러 값 칸과 여럿과 이어진 걸음은 한 행이 여러 막대에 들고, 그
        사실을 `multi` 로 화면에 넘겨 **적게 한다**."""
        defs = self.scope.defs
        if paths.is_path(address):
            chain = self.resolver.parse_chain(address)
            end = self._walk(chain)
            if chain.field is None:
                assert end.edges is not None and end.hop is not None and end.owner is not None
                if grain is not None:
                    raise _not_dated(chain.label)
                hop, owner = end.hop, end.owner
                return Axis(
                    end.edges.c.other,
                    chain.label,
                    "related",
                    end.joins,
                    chain.multi,
                    namer=lambda keys: owner.names(hop, keys),
                    address=address,
                    target=hop.target_slugs[0] if len(hop.target_slugs) == 1 else None,
                )
            return field_axis(
                end.entity,
                chain.field,
                chain.definition,
                chain.label,
                f"{self.prefix}{self._count}",
                end.joins,
                many=chain.many_hops,
                grain=grain,
                address=address,
            )
        if address == TYPE_FIELD[0]:
            if grain is not None:
                raise _not_dated(TYPE_FIELD[1])
            if not self.scope.is_interface:
                raise AppError(
                    code("OBJECTS", 41),
                    "「타입」 축은 인터페이스 목록에서만 씁니다 — 타입 목록은 한 타입뿐입니다",
                    status=422,
                )
            names = {str(one.id): one.label for one in self.scope.types}
            return Axis(
                cast(ObjectInstance.type_id, String),
                TYPE_FIELD[1],
                "type",
                namer=lambda keys: {one: names.get(one, one) for one in keys},
                address=address,
            )
        self._count += 1
        return own_axis(defs, address, grain=grain, alias=f"{self.prefix}{self._count}")


def own_axis(
    defs: list[PropertyDef], address: str, *, grain: str | None = None, alias: str = "g"
) -> Axis:
    """목록의 객체 **자신의** 칸 — 고정 칸이나 `properties.<칸>`. 걸음 · 타입 축은
    `JoinPlan`."""
    if address in FIXED_FIELDS:
        if grain is not None:
            raise _not_dated(FIXED_FIELDS[address])
        label = FIXED_FIELDS[address]
        if address == "label":
            return Axis(ObjectInstance.label, label, "plain", address=address)
        if address == "key":
            return Axis(ObjectInstance.key, label, "plain", address=address)
        if address == "status":
            return Axis(ObjectInstance.status, label, "status", address=address)
        if address == "workspace":
            return Axis(
                cast(ObjectInstance.owner_workspace_id, String),
                label,
                "workspace",
                address=address,
            )
        return Axis(
            func.to_char(ObjectInstance.created_at, "YYYY"), label, "year", address=address
        )
    if not address.startswith("properties."):
        raise AppError(
            code("OBJECTS", 41),
            f"기준으로 쓸 수 없는 칸입니다: {address}. "
            f"쓸 수 있는 것: {', '.join(FIXED_FIELDS)} 또는 properties.<칸>",
            status=422,
        )
    one = own_property(defs, address)
    return field_axis(
        ObjectInstance,
        one.key,
        one,
        one.label,
        alias,
        [],
        many=False,
        grain=grain,
        address=address,
    )


@dataclass
class _End:
    """걸음의 끝 — 이어진 객체의 별칭과 거기서 다시 걸을 Resolver, 여기까지의 조인 전부."""

    entity: Any
    resolver: paths.Resolver | None
    edges: Any
    joins: list[tuple[Any, Any]]
    hop: paths.Hop | None = None
    owner: paths.Resolver | None = None


def joined(stmt: Select[Any], *axes: Axis) -> Select[Any]:
    """기준이 붙일 것을 **바깥 조인**으로 붙인다 — 안쪽 조인이면 값·이어진 것이 없는 행이
    사라져 「(비어 있음)」 이 안 서고, 막대의 합이 전체보다 작아진다. 같은 조인(공유한 걸음)은
    한 번만."""
    seen: set[int] = set()
    for axis in axes:
        for target, onclause in axis.joins:
            if id(target) in seen:
                continue
            seen.add(id(target))
            stmt = stmt.outerjoin_from(ObjectInstance, target, onclause)
    return stmt


def metric_expr(
    defs: list[PropertyDef], metric: str, metric_field: str | None, labels_of: dict[str, str]
) -> Any:
    """합 · 평균을 낼 숫자 식. count 면 None."""
    if metric == "count":
        return None
    if not metric_field:
        raise AppError(
            code("OBJECTS", 44),
            f"{labels_of[metric]}을(를) 내려면 숫자 칸을 골라야 합니다.",
            status=422,
        )
    one = _prop(defs, metric_field)
    if one.data_type != "number" or one.multi:
        raise AppError(
            code("OBJECTS", 45),
            f"「{one.label}」 은 숫자 칸이 아니라 {labels_of[metric]}을(를) 낼 수 없습니다.",
            status=422,
        )
    return numeric(ObjectInstance.properties[one.key].astext)


def _is_uuid(raw: str) -> bool:
    try:
        uuid.UUID(raw)
        return True
    except ValueError:
        return False


def labels(db: Session, axis: Axis, keys: list[str]) -> dict[str, str]:
    """그룹 값을 사람이 읽는 이름으로. **모르는 값은 그대로 보여 준다** — 빈 칸으로
    두면 데이터가 없는 것처럼 읽히는데, 실제로는 표에 없는 새 값이 들어온 것이다."""
    if axis.namer is not None:
        return axis.namer(keys)
    if axis.grain is not None:
        return {one: period_label(one, axis.grain) for one in keys}
    if axis.kind == "status":
        return {one: STATUS_LABELS.get(one, one) for one in keys}
    if axis.kind == "bool":
        return {one: BOOL_LABELS.get(one.lower(), one) for one in keys}
    if axis.kind == "workspace":
        found = {
            str(row.id): row.name
            for row in db.scalars(
                select_workspaces([uuid.UUID(one) for one in keys if _is_uuid(one)])
            )
        }
        return {one: found.get(one, "(지워진 부서)") for one in keys}
    if axis.kind == "object_ref" and axis.ref_def is not None:
        ref = axis.ref_def
        rows = [{ref.key: key} for key in keys if _is_uuid(key)]
        names = system.ref_labels(db, [ref], rows)
        return {key: names.get(uuid.UUID(key), key) for key in keys if _is_uuid(key)}
    return {}


def select_workspaces(ids: list[uuid.UUID]) -> Select[tuple[Workspace]]:
    from sqlalchemy import select

    return select(Workspace).where(Workspace.id.in_(ids))
