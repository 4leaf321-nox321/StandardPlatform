"""묶어 보기 — **넣은 다음의 물음에 답한다.**

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
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Float, Select, String, case, cast, func, nulls_last, select
from sqlalchemy.orm import Session

from app.modules.objects import system
from app.modules.objects.models import ObjectInstance
from app.modules.objects.services import count_of, properties_of
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.workspaces.models import Workspace
from app.shared.errors import AppError, code

#: 세는 방법. `count` 는 칸을 안 받고, 나머지는 숫자 속성 하나를 받는다.
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
#: `heatmap` 은 plotly(`LazyPlot`)가 그린다 — **두 축일 때만 뜻이 있다.**
CHART_KINDS = ("bar", "line", "area", "pie", "heatmap", "list", "count")
#: 축이 없을 때의 두 모양 — 수 하나이거나, 몇 줄을 늘어놓거나. 「미승인 12건」 은 수가
#: 낫고, 「최근 들어온 것」 은 이름이 보여야 한다.
AXISLESS_KINDS = ("count", "list")

#: 차례. 많은 것부터가 기본이고, 적은 것부터는 「가장 낮은 것」 을 찾을 때 쓴다
#: (불량률이 가장 낮은 공정, 점수가 가장 낮은 공급사).
ORDERS = ("desc", "asc")

#: 쪼개기(두 번째 축)에서 돌려줄 값의 수. 이보다 많으면 색이 겹쳐 못 읽는다 —
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
    """쪼갠 조각 하나 — 두 번째 축의 값별로."""

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
    """쪼개기를 줬을 때만 채워진다. 합은 이 칸의 `count` 와 맞는다."""


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
    """쪼갠 값들의 **차례.** 화면이 계열 순서를 여기서 가져가야 그림마다 같은 값이
    같은 자리에 선다."""
    other_splits: int = 0
    """상한을 넘어 빠진 쪼갠 값의 수."""
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


def group_options(object_type: ObjectType, defs: list[PropertyDef]) -> list[GroupOption]:
    out = [
        GroupOption(field=key, label=label, kind="fixed")
        for key, label in FIXED_FIELDS.items()
        # 식별자를 안 쓰는 타입에서는 그 축이 「(비어 있음)」 한 칸이 된다 — 고를 수
        # 있다고 보여 주고 나서 빈 그림을 주지 않는다.
        if not (key == "key" and object_type.key_policy == "none")
    ]
    for one in defs:
        # 여러 값을 담는 칸은 아직 안 묶는다 — JSONB 배열을 펼쳐 세야 하고, 그러면
        # 막대의 합이 행 수보다 커진다(한 행이 여러 막대에 든다). 그 사실을 화면이
        # 설명하지 못하면 사람은 그것을 오류로 읽는다.
        if one.data_type not in GROUPABLE or one.multi:
            continue
        out.append(
            GroupOption(field=f"properties.{one.key}", label=one.label, kind=one.data_type)
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


def _group_expr(defs: list[PropertyDef], field_name: str) -> tuple[Any, str, str]:
    """(SQL 식, 보여 줄 이름, 종류). 식은 **글자**를 내놓는다 — 그래야 한 자리에서
    상태·부서·속성을 같은 규칙으로 다룬다."""
    if field_name in FIXED_FIELDS:
        if field_name == "label":
            return ObjectInstance.label, FIXED_FIELDS[field_name], "plain"
        if field_name == "key":
            return ObjectInstance.key, FIXED_FIELDS[field_name], "plain"
        if field_name == "status":
            return ObjectInstance.status, FIXED_FIELDS[field_name], "status"
        if field_name == "workspace":
            return (
                cast(ObjectInstance.owner_workspace_id, String),
                FIXED_FIELDS[field_name],
                "workspace",
            )
        return (
            func.to_char(ObjectInstance.created_at, "YYYY"),
            FIXED_FIELDS[field_name],
            "year",
        )
    if not field_name.startswith("properties."):
        raise AppError(
            code("OBJECTS", 41),
            f"묶을 수 없는 축입니다: {field_name}. "
            f"쓸 수 있는 것: {', '.join(FIXED_FIELDS)} 또는 properties.<칸>",
            status=422,
        )
    one = _prop(defs, field_name)
    if one.data_type not in GROUPABLE:
        raise AppError(
            code("OBJECTS", 42),
            f"「{one.label}」 은 묶을 수 없는 종류입니다({one.data_type}). "
            "긴 글과 파일로는 묶지 않습니다 — 그룹이 행 수만큼 나옵니다.",
            status=422,
        )
    if one.multi:
        raise AppError(
            code("OBJECTS", 43),
            f"「{one.label}」 은 여러 값을 담는 칸이라 아직 못 묶습니다. "
            "한 행이 여러 막대에 들어가 합이 전체와 안 맞습니다.",
            status=422,
        )
    column = ObjectInstance.properties[one.key].astext
    if one.data_type in BY_YEAR:
        # 날짜 하나하나로 묶으면 그룹이 수백 개다. 사람이 묻는 것은 연도다.
        return func.substr(column, 1, 4), f"{one.label} (해)", "year"
    return column, one.label, one.data_type


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


def _labels(
    db: Session,
    kind: str,
    keys: list[str],
    defs: list[PropertyDef],
    field_name: str,
) -> dict[str, str]:
    """그룹 값을 사람이 읽는 이름으로. **모르는 값은 그대로 보여 준다** — 빈 칸으로
    두면 데이터가 없는 것처럼 읽히는데, 실제로는 표에 없는 새 값이 들어온 것이다."""
    if kind == "status":
        return {one: STATUS_LABELS.get(one, one) for one in keys}
    if kind == "bool":
        return {one: BOOL_LABELS.get(one.lower(), one) for one in keys}
    if kind == "workspace":
        found = {
            str(row.id): row.name
            for row in db.scalars(
                select(Workspace).where(
                    Workspace.id.in_([uuid.UUID(one) for one in keys if _is_uuid(one)])
                )
            )
        }
        return {one: found.get(one, "(지워진 부서)") for one in keys}
    if kind == "object_ref":
        one = _prop(defs, field_name)
        rows = [{one.key: key} for key in keys if _is_uuid(key)]
        names = system.ref_labels(db, [one], rows)
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
    """목록과 **같은 거르기** 위에서 묶어 센다. `split_by` 를 주면 두 축으로 쪼갠다.

    두 축이 필요한 이유: 「부서별 몇 건」 다음에 오는 물음이 거의 언제나 「그 안에서
    등급은 어떻게 되나」 이기 때문이다. 그때 거르기를 등급마다 바꿔 가며 여섯 번 세는
    것이 지금까지의 방법이었고, 그것은 사람이 그 답을 포기하게 만든다.
    """
    if metric not in METRICS:
        raise AppError(
            code("OBJECTS", 46),
            f"세는 방법은 {', '.join(METRICS)} 중 하나여야 합니다: {metric}",
            status=422,
        )
    if order not in ORDERS:
        raise AppError(
            code("OBJECTS", 52),
            f"차례는 {', '.join(ORDERS)} 중 하나여야 합니다: {order}",
            status=422,
        )
    defs = properties_of(db, object_type.id)
    key_expr, group_label, kind = _group_expr(defs, group_by)
    value_expr = _metric_expr(defs, metric, metric_field)

    total = count_of(db, filtered)
    counted = func.count().label("n")
    columns: list[Any] = [key_expr.label("k"), counted]
    if value_expr is not None:
        columns.append(getattr(func, metric)(value_expr).label("v"))
    grouped = filtered.with_only_columns(*columns).group_by(key_expr)

    # 그룹이 몇 개인지 **먼저** 센다. 상한을 넘는 축(자유 글자 칸)에서 전부 읽어 오면
    # 응답이 수만 줄이 되고, 화면은 그것을 그리지도 못한다.
    distinct = (
        db.scalar(select(func.count()).select_from(grouped.order_by(None).subquery())) or 0
    )
    measured = counted if value_expr is None else columns[-1]
    ordering = measured.asc() if order == "asc" else measured.desc()
    rows = list(
        db.execute(grouped.order_by(nulls_last(ordering), key_expr).limit(MAX_BUCKETS))
    )

    keys = [str(row.k) for row in rows if row.k is not None]
    names = _labels(db, kind, keys, defs, group_by)
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
    if split_by:
        split_label, splits, other_splits = _split(
            db,
            defs,
            filtered,
            buckets=buckets,
            key_expr=key_expr,
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
        group_label=group_label,
        metric=metric,
        metric_field=metric_field,
        metric_label=METRIC_LABELS[metric],
        total=total,
        buckets=buckets,
        other_groups=max(0, int(distinct) - len(buckets)),
        other_count=max(0, total - shown),
    )


def _split(
    db: Session,
    defs: list[PropertyDef],
    filtered: Select[Any],
    *,
    buckets: list[Bucket],
    key_expr: Any,
    split_by: str,
    metric: str,
    value_expr: Any,
) -> tuple[str, list[str], int]:
    """두 번째 축으로 쪼갠다 — 이미 고른 칸들 **안에서만.**

    쪼갠 조각을 칸마다 채우고, 계열의 차례를 돌려준다. 차례를 서버가 정하는 이유:
    화면이 칸마다 나오는 순서대로 계열을 만들면 첫 칸에 없던 값이 뒤에서 튀어나와
    **색이 밀린다** — 같은 값이 그림 안에서 두 색을 갖는다.
    """
    split_expr, split_label, split_kind = _group_expr(defs, split_by)
    counted = func.count().label("n")
    columns: list[Any] = [key_expr.label("k"), split_expr.label("s"), counted]
    if value_expr is not None:
        columns.append(getattr(func, metric)(value_expr).label("v"))
    wanted = [one.key for one in buckets]
    grouped = (
        filtered.with_only_columns(*columns)
        # 보여 줄 칸 안에서만 쪼갠다. 전부 쪼개면 접힌 그룹의 조각까지 실려 응답이
        # 몇 배가 되고, 화면은 그것을 안 쓴다.
        .where(key_expr.in_([one for one in wanted if one is not None]))
        .group_by(key_expr, split_expr)
    )
    rows = list(db.execute(grouped))

    # 계열의 차례 — 전체에서 많은 것부터. 상한을 넘으면 자른다(색이 여덟이라 열둘이면
    # 이미 같은 색이 두 번 나온다).
    weight: dict[str | None, int] = {}
    for row in rows:
        key = None if row.s is None or str(row.s) == "" else str(row.s)
        weight[key] = weight.get(key, 0) + int(row.n)
    ordered = sorted(weight, key=lambda one: (-weight[one], one or ""))
    kept = ordered[:MAX_SPLITS]
    names = _labels(db, split_kind, [one for one in kept if one is not None], defs, split_by)
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
    return split_label, [label_of[one] for one in kept], max(0, len(ordered) - len(kept))


def _float(raw: Any) -> float | None:
    if raw is None:
        return None
    return float(raw)


def check_group(defs: list[PropertyDef], field_name: str) -> None:
    """이 축으로 묶을 수 있나. **저장할 때 부른다** — 안 하면 열었을 때 그림만 안 뜨고,
    무엇이 잘못됐는지 말할 자리가 없다."""
    _group_expr(defs, field_name)


def check_metric(defs: list[PropertyDef], metric: str, metric_field: str | None) -> None:
    """이 방법·이 칸으로 셀 수 있나."""
    if metric not in METRICS:
        raise AppError(
            code("OBJECTS", 46),
            f"세는 방법은 {', '.join(METRICS)} 중 하나여야 합니다: {metric}",
            status=422,
        )
    _metric_expr(defs, metric, metric_field)
