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
고른다(§`group_options`). 날짜는 **기간 단위**(`grain`)로 묶는다 — 기본은 해다: 날짜 하나하나로
묶으면 그룹이 수백 개가 되고, 사람이 실제로 묻는 것은 연도다. 월 · 주로 보려면 단위를 고른다
(ADR 0013). 축을 SQL 로 만드는 빌더는 지표와 함께 쓴다(`axes.py`).

## 빈 값은 숨기지 않는다

값이 없는 행은 「(비어 있음)」 한 칸으로 모아 보여 준다. 빼 버리면 막대의 합이 전체와
안 맞는데, **그 차이는 화면 어디에도 안 적힌다** — 그리고 대개 그 빈 칸이 진짜 할 일이다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, func, nulls_last, or_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import axes, paths
from app.modules.objects.axes import (  # noqa: F401 — 통계의 공개 이름으로 다시 내보낸다
    BOOL_LABELS,
    DATE_KINDS,
    EMPTY_LABEL,
    FIXED_FIELDS,
    GRAINS,
    GROUPABLE,
    NUMERIC_RE,
    STATUS_LABELS,
    TYPE_FIELD,
    Axis,
)
from app.modules.objects.models import ObjectInstance
from app.modules.objects.scope import Scope, as_scope
from app.modules.objects.services import count_of
from app.modules.ontology.models import ObjectType, PropertyDef
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

#: 저장된 뷰가 담을 수 있는 그림 모양. 앞의 넷은 `shared/charts` 의 `Chart`,
#: `heatmap` 은 plotly(`LazyPlot`)가 그린다 — **세부 기준이 있을 때만 뜻이 있다.**
CHART_KINDS = ("bar", "line", "area", "pie", "heatmap", "list", "count")
#: 축이 없을 때의 두 모양 — 수 하나이거나, 몇 줄을 늘어놓거나. 「미승인 12건」 은 수가
#: 낫고, 「최근 들어온 것」 은 이름이 보여야 한다.
AXISLESS_KINDS = ("count", "list")

#: 차례. 큰 값부터가 기본이고, 작은 값부터는 「가장 낮은 것」 을 찾을 때 쓴다
#: (불량률이 가장 낮은 공정, 점수가 가장 낮은 공급사). `key` 는 **키 순서** — 날짜 축을
#: 시간순으로 세울 때(값 순이면 꺾은선이 건수 순으로 선다).
ORDERS = ("desc", "asc", "key")

#: 세부 기준에서 돌려줄 값의 수. 이보다 많으면 색이 겹쳐 못 읽는다 —
#: 여덟 색을 돌려 쓰므로 열둘이면 이미 같은 색이 두 번 나온다.
MAX_SPLITS = 12

#: 한 번에 돌려줄 그룹 수. 넘는 것은 「그 밖에」 한 줄로 접는다 — 막대 200개는
#: 읽을 수 없고, 그림이 아니라 벽이 된다.
MAX_BUCKETS = 30


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
    """거르기에 그대로 쓸 수 있는 값. 빈 칸이면 None. 날짜 축이면 **기간의 시작일**."""
    label: str
    count: int
    value: float | None = None
    """`metric` 이 count 가 아닐 때의 값. count 면 None."""
    parts: list[Part] = field(default_factory=list)
    """세부 기준을 줬을 때만 채워진다. 합은 이 칸의 `count` 와 맞는다."""
    range: tuple[str, str] | None = None
    """날짜 축이면 이 칸의 (gte, lt) — 막대를 누르면 **범위 조건** 둘이 된다. 키 하나를 `eq` 로
    걸면 0건이다(저장값은 날짜 하나하나다)."""


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
    grain: str | None = None
    """날짜 축이면 묶은 기간 단위."""
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


def group_options(
    target: ObjectType | Scope,
    defs: list[PropertyDef],
    resolver: paths.Resolver | None = None,
) -> list[GroupOption]:
    out = [
        GroupOption(field=key, label=label, kind="fixed")
        for key, label in FIXED_FIELDS.items()
        # 식별자를 안 쓰는 타입에서는 그 축이 「(비어 있음)」 한 칸이 된다 — 고를 수
        # 있다고 보여 주고 나서 빈 그림을 주지 않는다.
        if not (key == "key" and target.key_policy == "none")
    ]
    if isinstance(target, Scope) and target.is_interface:
        out.insert(0, GroupOption(field=TYPE_FIELD[0], label=TYPE_FIELD[1], kind="fixed"))
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
        itself = paths.ends_at_hop(option.field)
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


def summarize(
    db: Session,
    target: ObjectType | Scope,
    filtered: Select[Any],
    *,
    group_by: str,
    split_by: str | None = None,
    metric: str = "count",
    metric_field: str | None = None,
    order: str = "desc",
    grain: str | None = None,
    viewer: User | None = None,
) -> Summary:
    """목록과 **같은 거르기** 위에서 센다. `split_by`(세부 기준)를 주면 한 번 더 나눈다.

    세부 기준이 필요한 이유: 「부서별 몇 건」 다음에 오는 물음이 거의 언제나 「그 안에서
    등급은 어떻게 되나」 이기 때문이다. 그때 거르기를 등급마다 바꿔 가며 여섯 번 세는
    것이 지금까지의 방법이었고, 그것은 사람이 그 답을 포기하게 만든다.

    `grain` 은 날짜 축의 기간 단위(기본 해) — 기준 · 세부 기준 중 날짜인 쪽에 건다.

    **여럿으로 이어지는 기준은 객체마다 한 번 센다**(ADR 0017) — 같은 증상의 기록이 300건인
    모델은 그 막대에서 1 이다(이어진 줄마다 세면 300 이었다). 합계 · 평균도 객체마다 한 번.
    `viewer` 는 들어오는 참조 걸음이 그 사람이 볼 수 있는 객체만 잇게 한다.
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
    scope = as_scope(db, target)
    defs = scope.defs
    plan = axes.JoinPlan(db, scope, prefix="g", viewer=viewer)
    # 기간 단위는 날짜인 축에 건다 — 화면의 고르개 하나가 기준 · 세부 기준 중 날짜인 쪽의
    # 것이다. 기준이 날짜가 아니고 세부 기준이 날짜면 세부 기준만 받는다. 둘 다 아니면 기준이
    # 받아 「날짜 칸이 아니다」 로 거절한다(조용히 버리면 고른 단위가 무시된 그림이 「월별」 로
    # 읽힌다).
    split_dated = bool(split_by) and plan.dated(split_by or "")
    group_grain = None if split_dated and not plan.dated(group_by) else grain
    group = plan.axis(group_by, grain=group_grain)
    value_expr = axes.metric_expr(defs, metric, metric_field, METRIC_LABELS)

    total = count_of(db, filtered)
    grouped = _grouped(filtered, [group], metric, value_expr)

    # 그룹이 몇 개인지, 그리고 **센 줄이 모두 몇인지** 먼저 센다. 상한을 넘는 축(자유
    # 글자 칸)에서 전부 읽어 오면 응답이 수만 줄이 된다. 센 줄의 합은 여러 값 칸이면
    # 전체 행 수보다 크다 — 「그 밖에 M건」 은 그 합에서 뺀다.
    #
    # **한 번에 센다** — 그룹 수와 합을 창 함수로 같이 낸다. 예전에는 같은 조인을 두 번 돌았고,
    # 참조 너머 칸으로 묶으면 기록 200만 건에서 그 한 번이 3초였다(실측, ADR 0010).
    every = grouped.order_by(None).subquery()
    measured = every.c.n if value_expr is None else every.c.v
    if order == "key":
        ordering = every.c.k.asc()
    else:
        ordering = measured.asc() if order == "asc" else measured.desc()
    rows = list(
        db.execute(
            select(
                every,
                func.count().over().label("groups"),
                func.sum(every.c.n).over().label("all_rows"),
            )
            .order_by(nulls_last(ordering), every.c.k)
            .limit(MAX_BUCKETS)
        )
    )
    distinct = int(rows[0].groups) if rows else 0
    counted_rows = int(rows[0].all_rows or 0) if rows else 0

    keys = [str(row.k) for row in rows if row.k is not None]
    names = axes.labels(db, group, keys)
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
            range=(
                axes.period_range(str(row.k), group.grain)
                if group.grain is not None and row.k is not None
                else None
            ),
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
            filtered,
            buckets=buckets,
            group=group,
            plan=plan,
            split_by=split_by,
            grain=grain,
            metric=metric,
            value_expr=value_expr,
        )
    return Summary(
        order=order,
        grain=group.grain,
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


def _grouped(
    filtered: Select[Any],
    axes_: list[Axis],
    metric: str,
    value_expr: Any,
    within: Any = None,
) -> Select[Any]:
    """기준(과 세부 기준)마다 센 질의 — 열은 `k`(· `s`) · `n` · `v`.

    기준 중 하나라도 여럿으로 이어지면(여러 값 칸 · 여럿 걸음) **(객체, 막대)를 한 번씩**
    남긴 뒤 센다. 이어진 줄마다 세면 한 객체가 같은 막대에서 여러 번 센다 — 모델 하나를
    가리키는 같은 증상의 기록 300건이 그 모델을 300 으로 만든다(ADR 0017)."""
    names = ["k", "s"][: len(axes_)]
    keys = [axis.expr.label(name) for axis, name in zip(axes_, names, strict=True)]
    if not any(axis.multi for axis in axes_):
        columns: list[Any] = [*keys, func.count().label("n")]
        if value_expr is not None:
            columns.append(getattr(func, metric)(value_expr).label("v"))
        stmt = axes.joined(filtered.with_only_columns(*columns), *axes_)
        if within is not None:
            stmt = stmt.where(within)
        return stmt.group_by(*[axis.expr for axis in axes_])
    picked: list[Any] = [ObjectInstance.id.label("oid"), *keys]
    if value_expr is not None:
        picked.append(value_expr.label("val"))
    inner = axes.joined(filtered.with_only_columns(*picked), *axes_)
    if within is not None:
        inner = inner.where(within)
    pairs = inner.distinct().subquery("pairs")
    columns = [*[pairs.c[name].label(name) for name in names], func.count().label("n")]
    if value_expr is not None:
        columns.append(getattr(func, metric)(pairs.c.val).label("v"))
    return select(*columns).group_by(*[pairs.c[name] for name in names])


def _split(
    db: Session,
    filtered: Select[Any],
    *,
    buckets: list[Bucket],
    group: Axis,
    plan: axes.JoinPlan,
    split_by: str,
    grain: str | None,
    metric: str,
    value_expr: Any,
) -> tuple[str, list[str], int, bool]:
    """세부 기준으로 나눈다 — 이미 고른 칸들 **안에서만.**

    나눈 조각을 칸마다 채우고, 계열의 차례를 돌려준다. 차례를 서버가 정하는 이유:
    화면이 칸마다 나오는 순서대로 계열을 만들면 첫 칸에 없던 값이 뒤에서 튀어나와
    **색이 밀린다** — 같은 값이 그림 안에서 두 색을 갖는다.
    """
    # 기간 단위는 날짜 기준의 것 — 세부 기준은 그것도 날짜일 때만 받는다.
    split = plan.axis(split_by, grain=grain if plan.dated(split_by) else None)
    key_expr = group.expr
    wanted = [one.key for one in buckets if one.key is not None]
    # 보여 줄 칸 안에서만 나눈다. 전부 나누면 접힌 그룹의 조각까지 실려 응답이 몇 배가
    # 되고, 화면은 그것을 안 쓴다. **「(비어 있음)」 칸도 보여 주는 칸이다** — IN 에는
    # NULL 이 안 걸리므로 따로 적는다(안 적으면 그 칸만 조각이 비어 0 으로 그려진다).
    within = key_expr.in_(wanted) if wanted else None
    if any(one.key is None for one in buckets):
        within = key_expr.is_(None) if within is None else or_(within, key_expr.is_(None))
    rows = list(db.execute(_grouped(filtered, [group, split], metric, value_expr, within)))

    # 계열의 차례 — 전체에서 큰 값부터. 상한을 넘으면 자른다(색이 여덟이라 열둘이면
    # 이미 같은 색이 두 번 나온다).
    weight: dict[str | None, int] = {}
    for row in rows:
        key = None if row.s is None or str(row.s) == "" else str(row.s)
        weight[key] = weight.get(key, 0) + int(row.n)
    ordered = sorted(weight, key=lambda one: (-weight[one], one or ""))
    kept = ordered[:MAX_SPLITS]
    names = axes.labels(db, split, [one for one in kept if one is not None])
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
    defs: list[PropertyDef],
    field_name: str,
    resolver: paths.Resolver | None = None,
    grain: str | None = None,
) -> None:
    """이 축으로 묶을 수 있나. **저장할 때 부른다** — 안 하면 열었을 때 그림만 안 뜨고,
    무엇이 잘못됐는지 말할 자리가 없다."""
    if resolver is None:
        if paths.is_path(field_name) or field_name == TYPE_FIELD[0]:
            raise AppError(
                code("OBJECTS", 41), f"기준으로 쓸 수 없는 칸입니다: {field_name}", status=422
            )
        axes.own_axis(defs, field_name, grain=grain)
        return
    axes.JoinPlan(resolver.db, resolver.scope).axis(field_name, grain=grain)


def check_grain(grain: str | None) -> None:
    if grain is not None and grain not in GRAINS:
        raise AppError(
            code("OBJECTS", 98),
            f"기간 단위는 {', '.join(GRAINS)} 중 하나여야 합니다: {grain}",
            status=422,
        )


def check_metric(defs: list[PropertyDef], metric: str, metric_field: str | None) -> None:
    """이 방법·이 칸으로 셀 수 있나."""
    if metric not in METRICS:
        raise AppError(
            code("OBJECTS", 46),
            f"집계 방식은 {', '.join(METRICS)} 중 하나여야 합니다: {metric}",
            status=422,
        )
    axes.metric_expr(defs, metric, metric_field, METRIC_LABELS)


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
    one = axes.own_property(defs, field_name)
    if one.data_type != "number" or one.multi:
        raise AppError(
            code("OBJECTS", 57),
            f"「{one.label}」 은 숫자 칸이 아니라 분포를 그릴 수 없습니다.",
            status=422,
        )
    return one


def points(
    db: Session,
    target: ObjectType | Scope,
    filtered: Select[Any],
    *,
    x: str,
    y: str | None = None,
    group_by: str | None = None,
    grain: str | None = None,
    viewer: User | None = None,
) -> Points:
    """고른 것들의 **원값**을 그대로. 상자 그림은 x 하나와 기준, 산점도는 x·y 둘."""
    scope = as_scope(db, target)
    defs = scope.defs
    x_def = _number_def(defs, x)
    y_def = _number_def(defs, y) if y else None
    axis = None
    if group_by:
        plan = axes.JoinPlan(db, scope, prefix="g", viewer=viewer)
        # 분포의 묶음은 화면의 세부 기준이다 — 날짜일 때만 기간 단위를 받는다.
        axis = plan.axis(group_by, grain=grain if plan.dated(group_by) else None)
    group_expr = axis.expr if axis else None
    group_label = axis.label if axis else ""

    x_expr = axes.numeric(ObjectInstance.properties[x_def.key].astext)
    columns: list[Any] = [
        ObjectInstance.id.label("id"),
        ObjectInstance.label.label("label"),
        x_expr.label("x"),
    ]
    if y_def is not None:
        columns.append(axes.numeric(ObjectInstance.properties[y_def.key].astext).label("y"))
    if group_expr is not None:
        columns.append(group_expr.label("g"))

    # 값이 없는 행은 점이 될 수 없다 — 0 으로 채우면 없는 점이 원점에 모여 그림이
    # 거짓말을 한다.
    stmt = filtered.with_only_columns(*columns).where(x_expr.is_not(None))
    if axis is not None:
        # 여러 값 기준이면 값마다 점이 하나씩 — 상자 그림에서는 그 값의 상자에 든다. 같은
        # 값으로 여럿 이어져도 그 상자에 한 번이다(ADR 0017).
        stmt = axes.joined(stmt, axis)
        if axis.multi:
            stmt = stmt.distinct()
    total = count_of(db, stmt)
    rows = list(db.execute(stmt.limit(MAX_POINTS)))

    keys = [str(row.g) for row in rows if group_expr is not None and row.g is not None]
    names = axes.labels(db, axis, keys) if axis is not None else {}
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
