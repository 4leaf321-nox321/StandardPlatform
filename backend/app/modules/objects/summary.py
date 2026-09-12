"""통계 — **넣은 다음의 물음에 답한다.**

정의하면 화면이 생기고 데이터가 정확히 쌓인다. 그다음 사람이 묻는 것은 언제나 같다:
「그래서 부서별로 몇 건인데」 「등급별로는」 「올해 들어온 게 몇 개지」.

그 답이 없으면 사람은 CSV 로 내려받아 엑셀에서 피벗을 돌린다. 그러면 **플랫폼 밖에
두 번째 진실이 생기고**, 그 표는 만든 날짜에 멈춘 채 메일로 돌아다닌다.

## 목록과 같은 거르기를 쓴다

`_filtered` 가 만든 질의를 그대로 받아 열만 바꾼다(`with_only_columns`). 따로 적으면
「목록에는 12건인데 묶어 보면 15건」 이 되고, 그때 어느 쪽이 맞는지 아무도 모른다.

## 세는 축은 정의가 허락한 것만

아무 칸으로나 묶게 두면 긴 글 칸으로 묶었을 때 그룹이 행 수만큼 나온다 — 답이 아니라
목록을 다시 보는 것이고, DB 에는 부담만 남는다. 그래서 묶을 수 있는 칸을 정의에서
고른다(§`group_options`). 날짜는 **해로** 묶는다: 날짜 하나하나로 묶으면 그룹이
수백 개가 되고, 사람이 실제로 묻는 것은 연도다.

## 빈 값은 숨기지 않는다

값이 없는 행은 「(비어 있음)」 한 칸으로 모아 보여 준다. 빼 버리면 막대의 합이 전체와
안 맞는데, **그 차이는 화면 어디에도 안 적힌다** — 그리고 대개 그 빈 칸이 진짜 할 일이다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import (
    Float,
    Select,
    String,
    and_,
    case,
    cast,
    func,
    nulls_last,
    or_,
    select,
    true,
)
from sqlalchemy.orm import Session, aliased

from app.modules.objects import paths, system
from app.modules.objects.models import ObjectInstance
from app.modules.objects.services import count_of, properties_of
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.workspaces.models import Workspace
from app.shared.errors import AppError, code

#: 집계 방식. `count` 는 칸을 안 받고, 나머지는 숫자 속성 하나를 받는다.
METRICS = ("count", "sum", "avg", "min", "max")
METRIC_LABELS = {
    "count": "건수",
    "sum": "합계",
    "avg": "평균",
    "min": "최솟값",
    "max": "최댓값",
}

#: 속성이 아니라 객체 자신이 가진 축.
#:
#: **이름·식별자도 축이다.** 그룹이 행 수만큼 나온다는 이유로 막아 두면 「점수가 높은
#: 부품 열 개」 같은 **개별 순위** 그림이 아예 안 나온다 — 그런데 사람이 실제로 자주
#: 보는 것이 그것이다. 상한(`MAX_BUCKETS`)과 정렬이 이미 있으니 그대로 순위가 된다.
FIXED_FIELDS = {
    "label": "이름",
    "key": "식별자",
    "status": "상태",
    "workspace": "소유 부서",
    "created_year": "만든 해",
}

#: 묶을 수 있는 속성 종류. **긴 글과 파일은 없다** — 그룹이 행 수만큼 나온다.
GROUPABLE = ("text", "enum", "bool", "url", "number", "date", "datetime", "object_ref")
#: 해로 묶는 종류.
BY_YEAR = ("date", "datetime")

#: 저장된 뷰가 담을 수 있는 그림 모양. 앞의 넷은 `shared/charts` 의 `Chart`,
#: `heatmap` 은 plotly(`LazyPlot`)가 그린다 — **세부 기준이 있을 때만 뜻이 있다.**
CHART_KINDS = ("bar", "line", "area", "pie", "heatmap", "list", "count")
#: 축이 없을 때의 두 모양 — 수 하나이거나, 몇 줄을 늘어놓거나. 「미승인 12건」 은 수가
#: 낫고, 「최근 들어온 것」 은 이름이 보여야 한다.
AXISLESS_KINDS = ("count", "list")

#: 차례. 큰 값부터가 기본이고, 작은 값부터는 「가장 낮은 것」 을 찾을 때 쓴다
#: (불량률이 가장 낮은 공정, 점수가 가장 낮은 공급사).
ORDERS = ("desc", "asc")

#: 세부 기준에서 돌려줄 값의 수. 이보다 많으면 색이 겹쳐 못 읽는다 —
#: 여덟 색을 돌려 쓰므로 열둘이면 이미 같은 색이 두 번 나온다.
MAX_SPLITS = 12

#: 한 번에 돌려줄 그룹 수. 넘는 것은 「그 밖에」 한 줄로 접는다 — 막대 200개는
#: 읽을 수 없고, 그림이 아니라 벽이 된다.
MAX_BUCKETS = 30

STATUS_LABELS = {"active": "사용", "deprecated": "안 씀"}
BOOL_LABELS = {"true": "예", "false": "아니오"}
EMPTY_LABEL = "(비어 있음)"

#: 숫자로 읽히는 값. 합·평균을 낼 때 이것에 안 맞는 행은 셈에서 빠진다.
NUMERIC_RE = r"^-?[0-9]+(\.[0-9]+)?$"


@dataclass
class Part:
    """세부 기준으로 나눈 조각 하나 — 세부 기준의 값별로."""

    key: str | None
    label: str
    count: int
    value: float | None = None


@dataclass
class Bucket:
    key: str | None
    """거르기에 그대로 쓸 수 있는 값. 빈 칸이면 None."""
    label: str
    count: int
    value: float | None = None
    """`metric` 이 count 가 아닐 때의 값. count 면 None."""
    parts: list[Part] = field(default_factory=list)
    """세부 기준을 줬을 때만 채워진다. 합은 이 칸의 `count` 와 맞는다."""


@dataclass
class Summary:
    group_field: str
    group_label: str
    metric: str
    metric_field: str | None
    metric_label: str
    total: int
    """거르기를 통과한 **전체 행 수.** 막대의 합과 다르면 「그 밖에」 가 그 차이다."""
    order: str = "desc"
    split_field: str = ""
    split_label: str = ""
    splits: list[str] = field(default_factory=list)
    """세부 기준 값들의 **차례.** 화면이 계열 순서를 여기서 가져가야 그림마다 같은 값이
    같은 자리에 선다."""
    other_splits: int = 0
    """상한을 넘어 빠진 세부 기준 값의 수."""
    group_multi: bool = False
    split_multi: bool = False
    """기준·세부 기준이 여러 값 칸이면 True — **한 행이 여러 막대에 든다.** 막대의 합이
    전체보다 클 수 있다는 것을 화면과 파일이 적는다."""
    buckets: list[Bucket] = field(default_factory=list)
    other_groups: int = 0
    """상한을 넘어 접힌 그룹 수."""
    other_count: int = 0
    """접힌 그룹들의 행 수 합."""


@dataclass
class GroupOption:
    """이 타입에서 묶을 수 있는 축 하나. 화면의 고르개가 이것만 보여 준다."""

    field: str
    label: str
    kind: str
    """fixed 이거나 속성의 data_type."""
    multi: bool = False
    """여러 값 칸 — 한 행이 여러 막대에 든다."""
    heading: str = ""
    """이어진 것 너머의 기준이면 그 제목 — 「개발사 (시뮬레이션 기업)」.

    참조 칸 「개발사」 와 관계 「개발사」 가 둘 다 있으면 같은 「개발사 › 국가」 가 두 번
    서는데, 제목이 그 둘을 가른다."""


@dataclass
class Axis:
    """기준 하나를 SQL 로 — 식·보여 줄 이름·종류, 그리고 바깥 조인으로 붙일 것들."""

    expr: Any
    label: str
    kind: str
    joins: list[tuple[Any, Any]] = field(default_factory=list)
    """(붙일 것, 조건) — 여러 값 칸의 펼친 배열, 참조로 이어진 객체, 관계."""
    multi: bool = False
    """한 행이 여러 막대에 들 수 있나."""
    ref_def: PropertyDef | None = None
    """참조 칸이면 그 정의 — 값(id)을 이름으로 바꿀 때 쓴다."""
    namer: Callable[[list[str]], dict[str, str]] | None = None
    """관계로 이어진 것 자체가 기준이면 — id 를 이름으로."""


def group_options(
    object_type: ObjectType,
    defs: list[PropertyDef],
    resolver: paths.Resolver | None = None,
) -> list[GroupOption]:
    out = [
        GroupOption(field=key, label=label, kind="fixed")
        for key, label in FIXED_FIELDS.items()
        # 식별자를 안 쓰는 타입에서는 그 축이 「(비어 있음)」 한 칸이 된다 — 고를 수
        # 있다고 보여 주고 나서 빈 그림을 주지 않는다.
        if not (key == "key" and object_type.key_policy == "none")
    ]
    for one in defs:
        if one.data_type not in GROUPABLE:
            continue
        out.append(
            GroupOption(
                field=f"properties.{one.key}",
                label=one.label,
                kind=one.data_type,
                multi=one.multi,
            )
        )
    # **이어진 것 너머** — 「개발사 › 국가」, 「사용 부서」. 자기 칸 뒤에 선다.
    for option in resolver.options(for_group=True) if resolver is not None else []:
        itself = option.field.count(".") == 1
        if not itself and option.data_type not in GROUPABLE:
            continue
        out.append(
            GroupOption(
                field=option.field,
                label=option.label,
                kind="related" if itself else option.data_type,
                multi=option.multi,
                heading=option.heading,
            )
        )
    return out


def metric_options(defs: list[PropertyDef]) -> list[GroupOption]:
    """합·평균을 낼 수 있는 칸 — 숫자이고 여러 값이 아닌 것."""
    return [
        GroupOption(field=f"properties.{one.key}", label=one.label, kind=one.data_type)
        for one in defs
        if one.data_type == "number" and not one.multi
    ]


def _prop(defs: list[PropertyDef], field_name: str) -> PropertyDef:
    key = field_name.split(".", 1)[1]
    found = next((one for one in defs if one.key == key), None)
    if found is None:
        raise AppError(
            code("OBJECTS", 40),
            f"이 타입에 없는 칸입니다: {key}",
            status=422,
        )
    return found


def _group_expr(
    defs: list[PropertyDef],
    field_name: str,
    alias: str = "g",
    resolver: paths.Resolver | None = None,
) -> Axis:
    """기준 하나. 식은 **글자**를 내놓는다 — 그래야 한 자리에서 상태·부서·속성을 같은
    규칙으로 다룬다.

    **여러 값 칸은 배열을 펼쳐** 값마다 한 줄로 센다(`LEFT JOIN LATERAL`). **이어진 것 너머의
    칸**(`ref.`·`out.`·`in.`)은 이어진 객체를 바깥 조인으로 붙여 그 칸으로 센다. 둘 다 한
    행이 여러 막대에 들 수 있고, 그 사실을 `multi` 로 화면에 넘겨 **적게 한다** — 안 적으면
    사람은 합이 안 맞는 것을 오류로 읽는다. 바깥 조인이라 이어진 것·값이 없는 행은
    「(비어 있음)」 한 칸으로 남는다.
    """
    if paths.is_path(field_name):
        if resolver is None:
            raise AppError(
                code("OBJECTS", 41), f"기준으로 쓸 수 없는 칸입니다: {field_name}", status=422
            )
        return _hop_axis(resolver, resolver.parse(field_name), alias)
    if field_name in FIXED_FIELDS:
        label = FIXED_FIELDS[field_name]
        if field_name == "label":
            return Axis(ObjectInstance.label, label, "plain")
        if field_name == "key":
            return Axis(ObjectInstance.key, label, "plain")
        if field_name == "status":
            return Axis(ObjectInstance.status, label, "status")
        if field_name == "workspace":
            return Axis(cast(ObjectInstance.owner_workspace_id, String), label, "workspace")
        return Axis(func.to_char(ObjectInstance.created_at, "YYYY"), label, "year")
    if not field_name.startswith("properties."):
        raise AppError(
            code("OBJECTS", 41),
            f"기준으로 쓸 수 없는 칸입니다: {field_name}. "
            f"쓸 수 있는 것: {', '.join(FIXED_FIELDS)} 또는 properties.<칸>",
            status=422,
        )
    one = _prop(defs, field_name)
    return _field_axis(ObjectInstance, one.key, one, one.label, alias, [], many=False)


def _field_axis(
    entity: Any,
    field_name: str,
    definition: PropertyDef | None,
    label: str,
    alias: str,
    joins: list[tuple[Any, Any]],
    *,
    many: bool,
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
    if definition.data_type in BY_YEAR:
        # 날짜 하나하나로 묶으면 그룹이 수백 개다. 사람이 묻는 것은 연도다.
        return Axis(func.substr(column, 1, 4), f"{label} (해)", "year", joins, multi, ref_def)
    return Axis(column, label, definition.data_type, joins, multi, ref_def)


def _hop_axis(resolver: paths.Resolver, found: paths.PathField, alias: str) -> Axis:
    """이어진 것 너머의 기준 — 이어진 객체를 **바깥 조인**으로 붙인다."""
    hop = found.hop
    if hop.kind == "ref":
        target = aliased(ObjectInstance, name=f"{alias}_ref")
        # 참조 칸은 id 하나(글자) 또는 id 의 배열 — `@>` 는 둘 다에 맞다.
        pointed = ObjectInstance.properties[hop.name].op("@>", is_comparison=True)(
            func.to_jsonb(cast(target.id, String))
        )
        return _field_axis(
            target,
            found.field or "label",
            found.definition,
            found.label,
            alias,
            [(target, and_(pointed, target.deleted_at.is_(None)))],
            many=hop.many,
        )
    edges = resolver.edges(hop, f"{alias}_edges")
    joins: list[tuple[Any, Any]] = [(edges, edges.c.me == ObjectInstance.id)]
    if found.field is None:
        return Axis(
            edges.c.other,
            found.label,
            "related",
            joins,
            hop.many,
            namer=lambda keys: resolver.names(hop, keys),
        )
    target = aliased(ObjectInstance, name=f"{alias}_obj")
    joins.append(
        (target, and_(cast(target.id, String) == edges.c.other, target.deleted_at.is_(None)))
    )
    return _field_axis(
        target, found.field, found.definition, found.label, alias, joins, many=hop.many
    )


def _joined(stmt: Select[Any], *axes: Axis) -> Select[Any]:
    """기준이 붙일 것을 **바깥 조인**으로 붙인다 — 안쪽 조인이면 값·이어진 것이 없는 행이
    사라져 「(비어 있음)」 이 안 서고, 막대의 합이 전체보다 작아진다."""
    for axis in axes:
        for target, onclause in axis.joins:
            stmt = stmt.outerjoin_from(ObjectInstance, target, onclause)
    return stmt


def _metric_expr(defs: list[PropertyDef], metric: str, metric_field: str | None) -> Any:
    if metric == "count":
        return None
    if not metric_field:
        raise AppError(
            code("OBJECTS", 44),
            f"{METRIC_LABELS[metric]}을(를) 내려면 숫자 칸을 골라야 합니다.",
            status=422,
        )
    one = _prop(defs, metric_field)
    if one.data_type != "number" or one.multi:
        raise AppError(
            code("OBJECTS", 45),
            f"「{one.label}」 은 숫자 칸이 아니라 "
            f"{METRIC_LABELS[metric]}을(를) 낼 수 없습니다.",
            status=422,
        )
    # **행 하나가 질의를 통째로 죽인다.** JSONB 는 글자를 담으므로 숫자가 아닌 값이
    # 섞일 수 있고(옛 데이터·손으로 고친 값·가져오기 이전의 것), 그때 cast 는 그 행
    # 하나 때문에 오류를 낸다 — 화면에는 「합계를 낼 수 없음」 이 아니라 500 이 뜬다.
    # 숫자로 읽히는 것만 숫자로 보고 나머지는 NULL 로 둔다(집계 함수가 건너뛴다).
    text = ObjectInstance.properties[one.key].astext
    return case((text.op("~")(NUMERIC_RE), cast(text, Float)), else_=None)


def _labels(db: Session, axis: Axis, keys: list[str]) -> dict[str, str]:
    """그룹 값을 사람이 읽는 이름으로. **모르는 값은 그대로 보여 준다** — 빈 칸으로
    두면 데이터가 없는 것처럼 읽히는데, 실제로는 표에 없는 새 값이 들어온 것이다."""
    if axis.namer is not None:
        return axis.namer(keys)
    if axis.kind == "status":
        return {one: STATUS_LABELS.get(one, one) for one in keys}
    if axis.kind == "bool":
        return {one: BOOL_LABELS.get(one.lower(), one) for one in keys}
    if axis.kind == "workspace":
        found = {
            str(row.id): row.name
            for row in db.scalars(
                select(Workspace).where(
                    Workspace.id.in_([uuid.UUID(one) for one in keys if _is_uuid(one)])
                )
            )
        }
        return {one: found.get(one, "(지워진 부서)") for one in keys}
    if axis.kind == "object_ref" and axis.ref_def is not None:
        ref = axis.ref_def
        rows = [{ref.key: key} for key in keys if _is_uuid(key)]
        names = system.ref_labels(db, [ref], rows)
        return {key: names.get(uuid.UUID(key), key) for key in keys if _is_uuid(key)}
    return {}


def _is_uuid(raw: str) -> bool:
    try:
        uuid.UUID(raw)
        return True
    except ValueError:
        return False


def summarize(
    db: Session,
    object_type: ObjectType,
    filtered: Select[Any],
    *,
    group_by: str,
    split_by: str | None = None,
    metric: str = "count",
    metric_field: str | None = None,
    order: str = "desc",
) -> Summary:
    """목록과 **같은 거르기** 위에서 센다. `split_by`(세부 기준)를 주면 한 번 더 나눈다.

    세부 기준이 필요한 이유: 「부서별 몇 건」 다음에 오는 물음이 거의 언제나 「그 안에서
    등급은 어떻게 되나」 이기 때문이다. 그때 거르기를 등급마다 바꿔 가며 여섯 번 세는
    것이 지금까지의 방법이었고, 그것은 사람이 그 답을 포기하게 만든다.
    """
    if metric not in METRICS:
        raise AppError(
            code("OBJECTS", 46),
            f"집계 방식은 {', '.join(METRICS)} 중 하나여야 합니다: {metric}",
            status=422,
        )
    if order not in ORDERS:
        raise AppError(
            code("OBJECTS", 52),
            f"차례는 {', '.join(ORDERS)} 중 하나여야 합니다: {order}",
            status=422,
        )
    defs = properties_of(db, object_type.id)
    resolver = paths.Resolver(db, object_type)
    group = _group_expr(defs, group_by, "g", resolver)
    key_expr = group.expr
    value_expr = _metric_expr(defs, metric, metric_field)

    total = count_of(db, filtered)
    counted = func.count().label("n")
    columns: list[Any] = [key_expr.label("k"), counted]
    if value_expr is not None:
        columns.append(getattr(func, metric)(value_expr).label("v"))
    grouped = _joined(filtered.with_only_columns(*columns), group).group_by(key_expr)

    # 그룹이 몇 개인지, 그리고 **센 줄이 모두 몇인지** 먼저 센다. 상한을 넘는 축(자유
    # 글자 칸)에서 전부 읽어 오면 응답이 수만 줄이 된다. 센 줄의 합은 여러 값 칸이면
    # 전체 행 수보다 크다 — 「그 밖에 M건」 은 그 합에서 뺀다.
    every = grouped.order_by(None).subquery()
    distinct, counted_rows = db.execute(
        select(func.count(), func.coalesce(func.sum(every.c.n), 0)).select_from(every)
    ).one()
    measured = counted if value_expr is None else columns[-1]
    ordering = measured.asc() if order == "asc" else measured.desc()
    rows = list(
        db.execute(grouped.order_by(nulls_last(ordering), key_expr).limit(MAX_BUCKETS))
    )

    keys = [str(row.k) for row in rows if row.k is not None]
    names = _labels(db, group, keys)
    buckets = [
        Bucket(
            key=None if row.k is None else str(row.k),
            label=(
                EMPTY_LABEL
                if row.k is None or str(row.k) == ""
                else names.get(str(row.k), str(row.k))
            ),
            count=int(row.n),
            value=(None if value_expr is None else _float(row.v)),
        )
        for row in rows
    ]
    shown = sum(one.count for one in buckets)
    split_label = ""
    splits: list[str] = []
    other_splits = 0
    split_multi = False
    if split_by:
        split_label, splits, other_splits, split_multi = _split(
            db,
            defs,
            filtered,
            buckets=buckets,
            group=group,
            resolver=resolver,
            split_by=split_by,
            metric=metric,
            value_expr=value_expr,
        )
    return Summary(
        order=order,
        split_field=split_by or "",
        split_label=split_label,
        splits=splits,
        other_splits=other_splits,
        group_field=group_by,
        group_label=group.label,
        metric=metric,
        metric_field=metric_field,
        metric_label=METRIC_LABELS[metric],
        total=total,
        buckets=buckets,
        other_groups=max(0, int(distinct) - len(buckets)),
        other_count=max(0, int(counted_rows) - shown),
        group_multi=group.multi,
        split_multi=split_multi,
    )


def _split(
    db: Session,
    defs: list[PropertyDef],
    filtered: Select[Any],
    *,
    buckets: list[Bucket],
    group: Axis,
    resolver: paths.Resolver,
    split_by: str,
    metric: str,
    value_expr: Any,
) -> tuple[str, list[str], int, bool]:
    """세부 기준으로 나눈다 — 이미 고른 칸들 **안에서만.**

    나눈 조각을 칸마다 채우고, 계열의 차례를 돌려준다. 차례를 서버가 정하는 이유:
    화면이 칸마다 나오는 순서대로 계열을 만들면 첫 칸에 없던 값이 뒤에서 튀어나와
    **색이 밀린다** — 같은 값이 그림 안에서 두 색을 갖는다.
    """
    split = _group_expr(defs, split_by, "s", resolver)
    key_expr = group.expr
    counted = func.count().label("n")
    columns: list[Any] = [key_expr.label("k"), split.expr.label("s"), counted]
    if value_expr is not None:
        columns.append(getattr(func, metric)(value_expr).label("v"))
    wanted = [one.key for one in buckets if one.key is not None]
    # 보여 줄 칸 안에서만 나눈다. 전부 나누면 접힌 그룹의 조각까지 실려 응답이 몇 배가
    # 되고, 화면은 그것을 안 쓴다. **「(비어 있음)」 칸도 보여 주는 칸이다** — IN 에는
    # NULL 이 안 걸리므로 따로 적는다(안 적으면 그 칸만 조각이 비어 0 으로 그려진다).
    within = key_expr.in_(wanted) if wanted else None
    if any(one.key is None for one in buckets):
        within = key_expr.is_(None) if within is None else or_(within, key_expr.is_(None))
    stmt = _joined(filtered.with_only_columns(*columns), group, split)
    if within is not None:
        stmt = stmt.where(within)
    rows = list(db.execute(stmt.group_by(key_expr, split.expr)))

    # 계열의 차례 — 전체에서 큰 값부터. 상한을 넘으면 자른다(색이 여덟이라 열둘이면
    # 이미 같은 색이 두 번 나온다).
    weight: dict[str | None, int] = {}
    for row in rows:
        key = None if row.s is None or str(row.s) == "" else str(row.s)
        weight[key] = weight.get(key, 0) + int(row.n)
    ordered = sorted(weight, key=lambda one: (-weight[one], one or ""))
    kept = ordered[:MAX_SPLITS]
    names = _labels(db, split, [one for one in kept if one is not None])
    label_of = {one: (EMPTY_LABEL if one is None else names.get(one, one)) for one in kept}

    by_key = {one.key: one for one in buckets}
    for row in rows:
        bucket = by_key.get(None if row.k is None else str(row.k))
        if bucket is None:
            continue
        key = None if row.s is None or str(row.s) == "" else str(row.s)
        if key not in label_of:
            continue
        bucket.parts.append(
            Part(
                key=key,
                label=label_of[key],
                count=int(row.n),
                value=(None if value_expr is None else _float(row.v)),
            )
        )
    for bucket in buckets:
        bucket.parts.sort(key=lambda one: kept.index(one.key))
    return (
        split.label,
        [label_of[one] for one in kept],
        max(0, len(ordered) - len(kept)),
        split.multi,
    )


def _float(raw: Any) -> float | None:
    if raw is None:
        return None
    return float(raw)


def check_group(
    defs: list[PropertyDef], field_name: str, resolver: paths.Resolver | None = None
) -> None:
    """이 축으로 묶을 수 있나. **저장할 때 부른다** — 안 하면 열었을 때 그림만 안 뜨고,
    무엇이 잘못됐는지 말할 자리가 없다."""
    _group_expr(defs, field_name, "g", resolver)


def check_metric(defs: list[PropertyDef], metric: str, metric_field: str | None) -> None:
    """이 방법·이 칸으로 셀 수 있나."""
    if metric not in METRICS:
        raise AppError(
            code("OBJECTS", 46),
            f"집계 방식은 {', '.join(METRICS)} 중 하나여야 합니다: {metric}",
            status=422,
        )
    _metric_expr(defs, metric, metric_field)


# --- 원값 뽑기(분포·산점도) ----------------------------------------------------
#
# 집계는 「몇 건인가」 에 답한다. 그런데 **분포는 집계로 안 보인다** — 평균이 같은 두
# 공정이 전혀 다른 모양일 수 있고, 그 차이가 대개 문제의 자리다. 상자 그림과 산점도가
# 그것을 보여 주는데, 둘 다 **원값**이 필요하다.
#
# 그래서 여기서는 안 센다. 고른 칸의 값을 그대로 내보내되 상한을 둔다 — 브라우저가
# 그릴 수 있는 점의 수에는 끝이 있고, 그 위로는 그림이 아니라 얼룩이 된다.

#: 한 번에 내보낼 점의 수. 넘으면 잘랐다고 말한다.
MAX_POINTS = 3000


@dataclass
class Point:
    id: uuid.UUID
    label: str
    group: str
    """세부 기준의 값(상자 그림의 상자 하나, 산점도의 색). 없으면 빈 글자."""
    x: float | None
    y: float | None = None


@dataclass
class Points:
    x_label: str = ""
    y_label: str = ""
    group_label: str = ""
    rows: list[Point] = field(default_factory=list)
    total: int = 0
    truncated: bool = False


def _number_def(defs: list[PropertyDef], field_name: str) -> PropertyDef:
    """숫자 칸만. **글자 칸으로 분포를 그릴 수는 없다** — 고를 수 있다고 보여 주고
    나서 빈 그림을 주지 않는다."""
    one = _prop(defs, field_name)
    if one.data_type != "number" or one.multi:
        raise AppError(
            code("OBJECTS", 57),
            f"「{one.label}」 은 숫자 칸이 아니라 분포를 그릴 수 없습니다.",
            status=422,
        )
    return one


def points(
    db: Session,
    object_type: ObjectType,
    filtered: Select[Any],
    *,
    x: str,
    y: str | None = None,
    group_by: str | None = None,
) -> Points:
    """고른 것들의 **원값**을 그대로. 상자 그림은 x 하나와 기준, 산점도는 x·y 둘."""
    defs = properties_of(db, object_type.id)
    x_def = _number_def(defs, x)
    y_def = _number_def(defs, y) if y else None
    axis = (
        _group_expr(defs, group_by, "g", paths.Resolver(db, object_type)) if group_by else None
    )
    group_expr = axis.expr if axis else None
    group_label = axis.label if axis else ""

    x_expr = case(
        (
            ObjectInstance.properties[x_def.key].astext.op("~")(NUMERIC_RE),
            cast(ObjectInstance.properties[x_def.key].astext, Float),
        ),
        else_=None,
    )
    columns: list[Any] = [
        ObjectInstance.id.label("id"),
        ObjectInstance.label.label("label"),
        x_expr.label("x"),
    ]
    if y_def is not None:
        columns.append(
            case(
                (
                    ObjectInstance.properties[y_def.key].astext.op("~")(NUMERIC_RE),
                    cast(ObjectInstance.properties[y_def.key].astext, Float),
                ),
                else_=None,
            ).label("y")
        )
    if group_expr is not None:
        columns.append(group_expr.label("g"))

    # 값이 없는 행은 점이 될 수 없다 — 0 으로 채우면 없는 점이 원점에 모여 그림이
    # 거짓말을 한다.
    stmt = filtered.with_only_columns(*columns).where(x_expr.is_not(None))
    if axis is not None:
        # 여러 값 기준이면 값마다 점이 하나씩 — 상자 그림에서는 그 값의 상자에 든다.
        stmt = _joined(stmt, axis)
    total = count_of(db, stmt)
    rows = list(db.execute(stmt.limit(MAX_POINTS)))

    keys = [str(row.g) for row in rows if group_expr is not None and row.g is not None]
    names = _labels(db, axis, keys) if axis is not None else {}
    out = Points(
        x_label=x_def.label,
        y_label=y_def.label if y_def else "",
        group_label=group_label,
        total=total,
        truncated=total > MAX_POINTS,
    )
    for row in rows:
        raw = getattr(row, "g", None) if group_expr is not None else None
        out.rows.append(
            Point(
                id=row.id,
                label=row.label,
                group=(
                    EMPTY_LABEL
                    if group_expr is not None and (raw is None or str(raw) == "")
                    else names.get(str(raw), str(raw))
                    if raw is not None
                    else ""
                ),
                x=_float(row.x),
                y=_float(getattr(row, "y", None)) if y_def is not None else None,
            )
        )
    return out


# --- 파일로 내보내기 -------------------------------------------------------------
#
# 화면의 그림과 **같은 숫자**여야 한다. 그래서 새로 세지 않고 `summarize`·`points` 가 낸
# 것을 표로 펴기만 한다 — 따로 세면 「화면에는 12 인데 파일에는 15」 가 된다.

#: 표에 적는 「그 밖에」 줄 — 상한을 넘어 접힌 것. 숨기면 합이 전체와 안 맞는다.
OTHER_LABEL = "그 밖에"
TOTAL_LABEL = "전체"


def summary_table(found: Summary) -> tuple[list[str], list[list[Any]]]:
    """묶어 센 결과 → (머리줄, 행들).

    쪼갰으면 세부 기준 값이 열이 된다(그림의 계열 차례 그대로). 건수로 셌을 때만 합계 열과
    「전체」 줄을 붙인다 — 평균·최솟값은 더하면 뜻이 없다.
    """
    counting = found.metric == "count"
    # 여러 값 칸이면 칸의 합이 전체보다 크다 — 「전체」 가 무엇을 센 것인지 적는다.
    total_label = (
        f"{TOTAL_LABEL} (객체 수)" if found.group_multi or found.split_multi else TOTAL_LABEL
    )
    header: list[str] = [found.group_label]
    rows: list[list[Any]] = []

    if not found.splits:
        header += ["건수"] if counting else [found.metric_label, "건수"]
        for bucket in found.buckets:
            rows.append(
                [bucket.label, bucket.count]
                if counting
                else [bucket.label, bucket.value, bucket.count]
            )
        if found.other_groups:
            label = f"{OTHER_LABEL} {found.other_groups}종류"
            rows.append(
                [label, found.other_count] if counting else [label, None, found.other_count]
            )
        rows.append(
            [total_label, found.total] if counting else [total_label, None, found.total]
        )
        return header, rows

    header += list(found.splits)
    # 세부 기준이 여러 값이면 조각의 합이 칸보다 커서 「그 밖에」 를 뺄셈으로 못 낸다.
    folded = counting and found.other_splits > 0 and not found.split_multi
    if folded:
        header.append(f"{OTHER_LABEL} {found.other_splits}종류")
    if counting:
        header.append("합계")
    for bucket in found.buckets:
        by_label = {part.label: part for part in bucket.parts}
        line: list[Any] = [bucket.label]
        for label in found.splits:
            part = by_label.get(label)
            if counting:
                line.append(part.count if part else 0)
            else:
                line.append(part.value if part else None)
        if folded:
            line.append(bucket.count - sum(part.count for part in bucket.parts))
        if counting:
            line.append(bucket.count)
        rows.append(line)
    if counting:
        blanks = [None] * (len(header) - 2)
        if found.other_groups:
            rows.append(
                [f"{OTHER_LABEL} {found.other_groups}종류", *blanks, found.other_count]
            )
        rows.append([total_label, *blanks, found.total])
    return header, rows


def points_table(found: Points) -> tuple[list[str], list[list[Any]]]:
    """원값 → (머리줄, 행들). 행 하나가 객체 하나."""
    header = ["이름", found.x_label]
    if found.y_label:
        header.append(found.y_label)
    if found.group_label:
        header.append(found.group_label)
    rows: list[list[Any]] = []
    for one in found.rows:
        line: list[Any] = [one.label, one.x]
        if found.y_label:
            line.append(one.y)
        if found.group_label:
            line.append(one.group)
        rows.append(line)
    if found.truncated:
        rows.append(
            [
                f"(앞의 {len(found.rows):,}건만 실었습니다 — 전체 {found.total:,}건. "
                "조건으로 좁히세요)"
            ]
        )
    return header, rows
