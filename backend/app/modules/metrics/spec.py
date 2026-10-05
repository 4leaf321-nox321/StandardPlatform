"""지표 정의 — **계획이 먼저다.**

정의 하나가 계산 · 저장 · 조회 · 건 보기를 만든다. 그래서 저장 전에 **실제로 지어 본다** —
주소가 풀리나, 날짜 칸인가, 거르기가 걸리나, 분모와 짝이 맞나, 셀이 몇 개나 되겠나. 틀린
정의가 저장되면 밤의 타이머가 매일 같은 오류를 내고 아무도 안 본다.

## 정의의 모양(`MetricSpec`)

    measure      count | sum | avg | min | max  (+ measure_field: 자기 숫자 칸)
                 | share — 조건 비율: `share_when` 에 맞는 기록의 몫(분모는 같은 기록 전체)
    time         {address: properties.<날짜 칸>, grain: day|week|month|quarter|year}
    cohort       같은 모양(선택) — 경과(age)는 이 단위. 시간 칸과 단위가 같아야 한다
    dimensions   [{name, address, grain?}] 6개까지 — 주소는 통계 · 조건과 같다(걸음 셋까지)
    filters      [{field, op, value}] — 목록 조건과 같은 뜻
    denominator  {metric, on: [기준 이름], time: period|cohort|null, per}
    settle_days  이만큼 지난 기간은 「닫힘」
    visits       {key: properties.<시리얼 칸>, within_days} — 기준 주소 `visit.number` ·
                 `visit.repeat` 를 연다(같은 시리얼의 차례 · 정한 일수 안 재방문, `visits.py`)
    stay         {periods, periods_from?} — 기록을 그 기간부터 N기간 동안 센다(최근 N기간의
                 합, `stay.py`). 판매 대수 → 보증 중 대수

## 예약어

`period` · `cohort` · `age` · `workspace` 는 셀의 진짜 칸이고 `count` · `sum` … 은 집계라
기준 이름으로 못 쓴다 — 응답에서 같은 자리에 서기 때문이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, and_, case, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.metrics import stay as stay_module
from app.modules.metrics import visits as visits_module
from app.modules.metrics.models import MetricDef
from app.modules.objects import axes, conditions, paths, system
from app.modules.objects.models import ObjectInstance
from app.modules.objects.scope import Scope, of_type
from app.modules.objects.services import count_of
from app.modules.objects.summary import METRIC_LABELS, METRICS
from app.modules.ontology.models import SLUG_MAX, ObjectType, PropertyDef
from app.shared.errors import AppError, code

#: 조건 비율 — 지표에만 있는 집계(그때그때 통계에는 없다).
SHARE = "share"
MEASURES = (*METRICS, SHARE)
MEASURE_LABELS = {**METRIC_LABELS, SHARE: "조건 비율"}
#: 기준은 여섯까지. 일곱부터는 셀 수를 짐작할 수 없고, 사람이 그 표를 읽지 못한다.
MAX_DIMENSIONS = 6
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
#: 응답에서 셀의 칸 · 집계가 서는 이름 — 기준 이름으로 못 쓴다.
RESERVED = frozenset(
    {
        "period",
        "cohort",
        "age",
        "workspace",
        "count",
        "value_count",
        "sum",
        "min",
        "max",
        "avg",
        "value",
        "ratio",
        "denominator",
        "drill",
        "closed",
    }
)
#: 분모의 시간축 — 분모의 기간을 분자의 무엇과 짝짓나. None 이면 기간 없이 전부 합.
DENOMINATOR_TIMES = ("period", "cohort")
#: 셀 추정 질의 하나의 상한(초). 넘기면 「추정 못 함」 경고로 두고 계획은 낸다.
ESTIMATE_SECONDS = 20
#: 자유 글자 기준의 값이 이보다 많으면 경고 — 셀이 행 수만큼 나온다.
MANY_VALUES = 1000
#: 기준마다의 서로 다른 값 수는 이만큼의 **표본**에서 센다. 200만 건을 기준마다 훑으면 계획
#: 하나에 50초였다(실측) — 어림이 목적이라 표본이면 된다. 행 수는 정확히 센다.
SAMPLE_ROWS = 200_000


class TimeAxisIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(min_length=1, max_length=200)
    grain: str = Field(min_length=1, max_length=10)


class DimensionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=32)
    address: str = Field(min_length=1, max_length=200)
    grain: str | None = Field(default=None, max_length=10)


class FilterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=200)
    op: str = Field(min_length=1, max_length=12)
    value: str = Field(default="", max_length=2000)


class DenominatorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1, max_length=SLUG_MAX)
    on: list[str] = Field(default_factory=list, max_length=MAX_DIMENSIONS)
    """양쪽에 같은 이름 · 같은 값 종류로 있는 기준 — 그 값끼리 짝짓는다. 분자에만 있는
    기준은 분모가 펼쳐진다(기본 모델별 판매 대수가 증상마다 같은 값으로)."""
    time: str | None = "period"
    """`period`(분모의 기간 = 분자의 기간) · `cohort`(분모의 기간 = 분자의 코호트 — 판매월
    코호트의 인입률) · null(기간 없이 전부 합 — 설치 대수)."""
    per: float = Field(default=1, gt=0)
    """비율에 곱할 수 — 100 이면 %, 1000 이면 천 대당."""


class MetricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measure: str = "count"
    measure_field: str | None = Field(default=None, max_length=200)
    share_when: list[FilterIn] = Field(default_factory=list, max_length=20)
    """조건 비율(`measure="share"`)의 조건 — 거르기와 같은 꼴, 전부 맞아야(AND) 「그 몫」
    이다."""
    time: TimeAxisIn | None = None
    cohort: TimeAxisIn | None = None
    dimensions: list[DimensionIn] = Field(default_factory=list, max_length=MAX_DIMENSIONS)
    filters: list[FilterIn] = Field(default_factory=list, max_length=50)
    denominator: DenominatorIn | None = None
    settle_days: int = Field(default=0, ge=0, le=3650)
    visits: visits_module.VisitsIn | None = None
    """같은 시리얼의 방문 — 있으면 기준 주소 `visit.number` · `visit.repeat` 를 쓸 수 있다."""
    stay: stay_module.StayIn | None = None
    """머무는 기간 — 기록을 그 기간부터 N기간 동안 센다(기간마다 최근 N기간의 합)."""


# --- 지은 것 ---------------------------------------------------------------------


@dataclass
class TimeAxis:
    address: str
    key: str
    definition: PropertyDef
    grain: str
    expr: Any
    """기간의 시작일(date). 못 읽는 값은 NULL."""


@dataclass
class Dim:
    name: str
    address: str
    grain: str | None
    axis: axes.Axis

    @property
    def signature(self) -> tuple[str, ...]:
        """값의 종류 — 분모와 짝지을 때 이것이 같아야 한다. 참조는 상대 타입, 관계 자체는
        상대가 한 타입이면 그 타입(참조와 같은 값), 아니면 주소, 나머지는 종류와 단위."""
        axis = self.axis
        if axis.kind == "object_ref" and axis.ref_def is not None:
            return ("ref", axis.ref_def.ref_type_slug or "")
        if axis.kind == "related":
            # 관계로 이어진 것 자체 — 상대가 한 타입이면 그 타입을 가리키는 참조와 같은 값(id)
            # 이다. SKU → 기본 모델을 **관계로** 이었어도 기본 모델 참조 칸을 가진 분모(판매
            # 집계)와 짝이 맞는다 — 사내에서는 그 둘을 관계로 잇는다(2026-10-03).
            return ("ref", axis.target) if axis.target else ("related", self.address)
        return (axis.kind, axis.grain or "")


@dataclass
class Built:
    """정의를 SQL 조각으로 — 계산과 읽기가 같은 것을 쓴다."""

    source: ObjectType
    scope: Scope
    spec: MetricSpec
    plan: axes.JoinPlan
    value: Any | None
    """측정값 식. count 면 None."""
    time: TimeAxis | None
    cohort: TimeAxis | None
    dims: list[Dim]
    conds: list[conditions.Condition]
    denominator: MetricDef | None
    visits: visits_module.Visits | None = None
    stay: stay_module.Stay | None = None

    @property
    def overlap(self) -> bool:
        return any(one.axis.multi for one in self.dims)

    @property
    def grain(self) -> str | None:
        return self.time.grain if self.time is not None else None

    def dim(self, name: str) -> Dim | None:
        return next((one for one in self.dims if one.name == name), None)

    def base(self) -> Select[Any]:
        """거르기를 통과한 기록 — 조인 없이(조건은 EXISTS 라 행이 안 분다)."""
        stmt = select(ObjectInstance.id).where(
            ObjectInstance.type_id == self.source.id, ObjectInstance.deleted_at.is_(None)
        )
        filtered: Select[Any] = conditions.apply(
            stmt, self.scope.defs, self.conds, self.plan.resolver
        )
        return filtered


@dataclass
class DimInfo:
    name: str
    address: str
    label: str
    kind: str
    multi: bool
    grain: str | None
    distinct: int | None = None


@dataclass
class Plan:
    ok: bool
    errors: list[str]
    warnings: list[str]
    built: Built | None
    dims: list[DimInfo]
    rows: int | None = None
    estimated_cells: int | None = None
    period_from: str | None = None
    period_to: str | None = None

    @property
    def overlap(self) -> bool:
        return self.built.overlap if self.built is not None else False


def _bad(message: str) -> AppError:
    return AppError(code("METRICS", 3), message, status=422)


def _check_source(source: ObjectType) -> None:
    if system.is_system(source):
        raise _bad(
            f"「{source.label}」 은 다른 표를 비추는 타입이라 행이 없어 셀 수 없습니다."
        )
    if not source.is_active:
        raise _bad(f"「{source.label}」 은 꺼진 타입입니다.")


def _measure(scope: Scope, spec: MetricSpec, plan: axes.JoinPlan) -> Any | None:
    if spec.measure == SHARE:
        return _share(scope, spec, plan)
    if spec.share_when:
        raise _bad("조건(share_when)은 조건 비율(share)에서만 둡니다.")
    if spec.measure not in METRICS:
        raise _bad(f"집계는 {', '.join(MEASURES)} 중 하나여야 합니다: {spec.measure}")
    return axes.metric_expr(scope.defs, spec.measure, spec.measure_field, METRIC_LABELS)


def _share(scope: Scope, spec: MetricSpec, plan: axes.JoinPlan) -> Any:
    """조건 비율 — 값 식이 「조건에 맞으면 1, 아니면 0」 이다. 그러면 계산 문장이 셀마다
    `sum` 에 조건 건수를, `count` 에 전체 건수를 그대로 담는다(칸을 새로 두지 않는다)."""
    if spec.measure_field:
        raise _bad("조건 비율에는 숫자 칸을 고르지 않습니다 — 조건(share_when)으로 셉니다.")
    if not spec.share_when:
        raise _bad("조건 비율은 조건(share_when)이 하나 이상 있어야 합니다 — 무엇의 몫인가.")
    if spec.denominator is not None:
        raise _bad("조건 비율은 같은 기록 전체가 분모입니다 — 분모 지표를 두지 않습니다.")
    conds = [conditions.Condition(one.field, one.op, one.value) for one in spec.share_when]
    found = conditions.clauses(scope.defs, conds, plan.resolver)
    return case((and_(*found), 1), else_=0)


def _time_axis(scope: Scope, axis_in: TimeAxisIn, what: str) -> TimeAxis:
    """시간 · 코호트 칸 — **자기 타입의 날짜 칸만.** 건 보기의 범위 조건과 (나중의) 증분
    워터마크가 자기 칸이어야 한다."""
    if axis_in.grain not in axes.GRAINS:
        raise _bad(
            f"{what}의 기간 단위는 {', '.join(axes.GRAINS)} 중 하나여야 합니다: "
            f"{axis_in.grain}"
        )
    if not axis_in.address.startswith("properties."):
        raise _bad(
            f"{what}은(는) 이 타입 자신의 날짜 칸(properties.<칸>)이어야 합니다: "
            f"{axis_in.address}"
        )
    definition = axes.own_property(scope.defs, axis_in.address)
    if definition.data_type not in axes.DATE_KINDS:
        raise _bad(
            f"{what} 「{definition.label}」 은 날짜 칸이 아닙니다({definition.data_type})."
        )
    if definition.multi:
        raise _bad(f"{what} 「{definition.label}」 은 여러 값 칸이라 시간축이 될 수 없습니다.")
    column = ObjectInstance.properties[definition.key].astext
    expr = axes.bucket(axes.date_or_null(column), axis_in.grain)
    return TimeAxis(axis_in.address, definition.key, definition, axis_in.grain, expr)


def _dimension(
    plan: axes.JoinPlan,
    dim_in: DimensionIn,
    seen: set[str],
    visits: visits_module.Visits | None = None,
) -> Dim:
    name = dim_in.name
    if not NAME_RE.match(name):
        raise _bad(
            f"기준 이름은 소문자로 시작하는 영문·숫자·밑줄 32자 이내여야 합니다: {name!r}"
        )
    if name in RESERVED:
        raise _bad(f"기준 이름으로 쓸 수 없는 예약어입니다: {name}")
    if name in seen:
        raise _bad(f"기준 이름이 겹칩니다: {name}")
    if dim_in.address == axes.TYPE_FIELD[0]:
        raise _bad("「타입」 축은 인터페이스 목록의 것입니다 — 지표의 원천은 타입 하나입니다.")
    if dim_in.address in visits_module.ADDRESSES:
        if visits is None:
            raise _bad(
                f"「{dim_in.address}」 은 방문 기준입니다 — 정의에 visits(시리얼 칸 · 일수)가 "
                "있어야 합니다."
            )
        return Dim(name, dim_in.address, None, visits_module.axis(visits, dim_in.address))
    if paths.is_path(dim_in.address) and any(
        hop.is_back for hop in plan.resolver.parse_chain(dim_in.address).hops
    ):
        # 지표는 기록을 센다 — 나를 가리키는 것으로 거꾸로 가면 한 기록이 가리키는 것마다
        # 불어나 셀의 합이 기록 수와 어긋난다(ADR 0017). 목록의 통계에서는 된다.
        raise _bad(
            f"「{dim_in.address}」 는 나를 가리키는 것(들어오는 참조)을 지나는 기준이라 "
            "지표에는 못 씁니다 — 한 기록이 여러 번 셉니다. 목록의 통계에서 씁니다."
        )
    axis = plan.axis(dim_in.address, grain=dim_in.grain)
    if axis.kind in axes.DATE_KINDS and dim_in.grain is None:
        raise _bad(f"날짜 기준 「{axis.label}」 에는 기간 단위(grain)가 필요합니다.")
    return Dim(name, dim_in.address, axis.grain, axis)


def _filters(
    plan: axes.JoinPlan, scope: Scope, spec: MetricSpec
) -> list[conditions.Condition]:
    conds = [conditions.Condition(one.field, one.op, one.value) for one in spec.filters]
    if conds:
        # 실제로 지어 본다 — 없는 칸 · 안 맞는 연산이 여기서 걸린다.
        conditions.apply(select(ObjectInstance.id), scope.defs, conds, plan.resolver)
    return conds


def _denominator(
    db: Session, spec: MetricSpec, dims: list[Dim], *, self_slug: str | None
) -> MetricDef | None:
    den_in = spec.denominator
    if den_in is None:
        return None
    if self_slug is not None and den_in.metric == self_slug:
        raise _bad("분모가 자기 자신입니다.")
    found = db.scalar(select(MetricDef).where(MetricDef.slug == den_in.metric))
    if found is None:
        raise _bad(f"분모 지표가 없습니다: {den_in.metric}")
    if not found.is_active:
        raise _bad(f"분모 지표가 꺼져 있습니다: {den_in.metric}")
    den_spec = MetricSpec.model_validate(found.spec)
    if den_spec.denominator is not None or den_spec.measure == SHARE:
        raise _bad("분모의 분모는 둘 수 없습니다 — 비율의 비율은 읽을 수 없습니다.")
    if den_in.time is not None and den_in.time not in DENOMINATOR_TIMES:
        raise _bad(
            f"분모의 시간축은 {', '.join(DENOMINATOR_TIMES)} 또는 비움(null)이어야 합니다: "
            f"{den_in.time}"
        )
    if den_in.time is not None:
        if den_spec.time is None:
            raise _bad(
                f"분모 지표 「{found.label}」 에 시간 칸이 없어 기간으로 짝지을 수 없습니다 — "
                "time 을 비웁니다(null)."
            )
        mine = spec.time if den_in.time == "period" else spec.cohort
        what = "시간 칸" if den_in.time == "period" else "코호트 칸"
        if mine is None:
            raise _bad(
                f"분모를 「{den_in.time}」 로 짝지으려면 이 지표에 {what}이 있어야 합니다."
            )
        if mine.grain != den_spec.time.grain:
            raise _bad(
                f"분모의 기간 단위({den_spec.time.grain})가 이 지표의 {what} 단위"
                f"({mine.grain})와 다릅니다."
            )
    den_source = db.get(ObjectType, found.source_type_id)
    if den_source is None:
        raise _bad(f"분모 지표 「{found.label}」 의 원천 타입이 없습니다.")
    den_plan = axes.JoinPlan(db, den_source, prefix="dn")
    den_dims = {one.name: one for one in den_spec.dimensions}
    mine_by_name = {one.name: one for one in dims}
    for name in den_in.on:
        if name not in mine_by_name:
            raise _bad(f"분모와 짝지을 기준이 이 지표에 없습니다: {name}")
        if name not in den_dims:
            raise _bad(f"분모 지표 「{found.label}」 에 그 기준이 없습니다: {name}")
        theirs = den_dims[name]
        their_dim = Dim(
            name,
            theirs.address,
            theirs.grain,
            den_plan.axis(theirs.address, grain=theirs.grain),
        )
        if their_dim.signature != mine_by_name[name].signature:
            raise _bad(
                f"기준 「{name}」 의 값 종류가 분모와 다릅니다 — 양쪽이 같은 타입을 "
                "가리키거나 같은 종류의 칸이어야 짝이 맞습니다."
            )
    return found


def build(
    db: Session, source: ObjectType, spec: MetricSpec, *, self_slug: str | None = None
) -> Built:
    """정의를 SQL 조각으로 — **첫 문제에서 멈춘다**(계산이 부른다). 전부 모으는 것은
    `plan`."""
    _check_source(source)
    scope = of_type(db, source)
    plan = axes.JoinPlan(db, scope, prefix="mx")
    value = _measure(scope, spec, plan)
    time = _time_axis(scope, spec.time, "시간 칸") if spec.time is not None else None
    cohort = _cohort(scope, spec, time)
    if len(spec.dimensions) > MAX_DIMENSIONS:
        raise _bad(f"기준은 {MAX_DIMENSIONS}개까지입니다.")
    conds = _filters(plan, scope, spec)
    visits = _visits(source, scope, spec, time, conds, plan)
    dims: list[Dim] = []
    seen: set[str] = set()
    for one in spec.dimensions:
        dims.append(_dimension(plan, one, seen, visits))
        seen.add(one.name)
    denominator = _denominator(db, spec, dims, self_slug=self_slug)
    stay = _stay(spec, plan, time)
    return Built(
        source, scope, spec, plan, value, time, cohort, dims, conds, denominator, visits, stay
    )


def _visits(
    source: ObjectType,
    scope: Scope,
    spec: MetricSpec,
    time: TimeAxis | None,
    conds: list[conditions.Condition],
    plan: axes.JoinPlan,
) -> visits_module.Visits | None:
    if spec.visits is None:
        return None
    try:
        return visits_module.build(
            source,
            scope.defs,
            spec.visits,
            time.key if time is not None else None,
            conds,
            plan.resolver,
        )
    except visits_module.VisitError as caught:
        raise _bad(str(caught)) from caught


def _stay(
    spec: MetricSpec, plan: axes.JoinPlan, time: TimeAxis | None
) -> stay_module.Stay | None:
    if spec.stay is None:
        return None
    if time is None:
        raise _bad("머무는 기간은 시간 칸이 있어야 둘 수 있습니다 — 무엇의 기간부터 셀지.")
    try:
        return stay_module.build(
            spec.stay, plan, cohort=spec.cohort is not None, visits=spec.visits is not None
        )
    except stay_module.StayError as caught:
        raise _bad(str(caught)) from caught


def _cohort(scope: Scope, spec: MetricSpec, time: TimeAxis | None) -> TimeAxis | None:
    if spec.cohort is None:
        return None
    if time is None:
        raise _bad("코호트 칸은 시간 칸이 있어야 둘 수 있습니다.")
    cohort = _time_axis(scope, spec.cohort, "코호트 칸")
    if cohort.address == time.address:
        raise _bad("코호트 칸이 시간 칸과 같습니다.")
    if cohort.grain != time.grain:
        raise _bad(
            "코호트의 기간 단위는 시간 칸과 같아야 합니다 — 경과(기간 - 코호트)를 그 단위로 "
            "셉니다."
        )
    return cohort


def plan(
    db: Session,
    source: ObjectType,
    spec: MetricSpec,
    *,
    self_slug: str | None = None,
    estimate: bool = True,
) -> Plan:
    """정의를 **전부** 검사하고 셀 수를 어림한다. 오류는 모아서 한 번에 — 하나씩 고치고 다시
    묻게 하면 여섯 번 왕복한다."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        _check_source(source)
    except AppError as caught:
        errors.append(caught.message)
    if source.usage != "log":
        warnings.append(
            f"「{source.label}」 의 쓰임이 「기록」 이 아닙니다 — 축 타입을 세는 지표는 "
            "드뭅니다."
        )
    scope = of_type(db, source)
    join_plan = axes.JoinPlan(db, scope, prefix="mx")
    value: Any | None = None
    try:
        value = _measure(scope, spec, join_plan)
    except AppError as caught:
        errors.append(caught.message)
    time: TimeAxis | None = None
    if spec.time is not None:
        try:
            time = _time_axis(scope, spec.time, "시간 칸")
        except AppError as caught:
            errors.append(caught.message)
    cohort: TimeAxis | None = None
    if spec.cohort is not None:
        try:
            cohort = _cohort(scope, spec, time)
        except AppError as caught:
            if time is not None or spec.time is None:
                errors.append(caught.message)
    if spec.time is None:
        warnings.append("시간 칸이 없습니다 — 추이 · 코호트는 못 보고, 셀에 기간이 없습니다.")
    conds: list[conditions.Condition] = []
    try:
        conds = _filters(join_plan, scope, spec)
    except AppError as caught:
        errors.append(caught.message)
    visits: visits_module.Visits | None = None
    try:
        visits = _visits(source, scope, spec, time, conds, join_plan)
    except AppError as caught:
        if spec.time is None or time is not None:
            errors.append(caught.message)
    dims: list[Dim] = []
    seen: set[str] = set()
    if len(spec.dimensions) > MAX_DIMENSIONS:
        errors.append(f"기준은 {MAX_DIMENSIONS}개까지입니다.")
    for one in spec.dimensions[:MAX_DIMENSIONS]:
        try:
            dims.append(_dimension(join_plan, one, seen, visits))
        except AppError as caught:
            if one.address not in visits_module.ADDRESSES or spec.visits is None:
                errors.append(caught.message)
        seen.add(one.name)
    denominator: MetricDef | None = None
    try:
        denominator = _denominator(db, spec, dims, self_slug=self_slug)
    except AppError as caught:
        errors.append(caught.message)
    stay: stay_module.Stay | None = None
    try:
        stay = _stay(spec, join_plan, time)
    except AppError as caught:
        if spec.time is None or time is not None:
            errors.append(caught.message)
    infos = [
        DimInfo(
            one.name, one.address, one.axis.label, one.axis.kind, one.axis.multi, one.grain
        )
        for one in dims
    ]
    if errors:
        return Plan(False, errors, warnings, None, infos)
    built = Built(
        source,
        scope,
        spec,
        join_plan,
        value,
        time,
        cohort,
        dims,
        conds,
        denominator,
        visits,
        stay,
    )
    out = Plan(True, errors, warnings, built, infos)
    if estimate:
        _estimate(db, built, out)
    return out


def _guarded(db: Session, stmt: Select[Any]) -> Any | None:
    """추정 질의 하나 — 상한을 넘기면 None. 저장점 안에서 돌려 취소돼도 트랜잭션은 산다."""
    try:
        with db.begin_nested():
            db.execute(text(f"SET LOCAL statement_timeout = {ESTIMATE_SECONDS * 1000}"))
            return db.execute(stmt).one()
    except OperationalError as caught:
        if getattr(caught.orig, "sqlstate", None) == "57014":  # query_canceled
            return None
        raise


def _estimate(db: Session, built: Built, out: Plan) -> None:
    """셀 수 어림 — 행 수와 기준마다의 서로 다른 값 수. **상한을 넘기면 거절한다.**"""
    settings = get_settings()
    out.rows = count_of(db, built.base())

    sampled = out.rows > SAMPLE_ROWS
    if sampled:
        out.warnings.append(
            f"기준의 값 수는 {SAMPLE_ROWS:,}건 표본에서 어림했습니다 — 셀 수도 어림입니다."
        )

    def counted(*columns: Any, axis: axes.Axis | None = None, extra: Any = ()) -> Any | None:
        stmt = (
            select(*columns)
            .select_from(ObjectInstance)
            .where(
                ObjectInstance.type_id == built.source.id, ObjectInstance.deleted_at.is_(None)
            )
        )
        joining = [*([axis] if axis is not None else []), *extra]
        if joining:
            stmt = axes.joined(stmt, *joining)
        stmt = conditions.apply(stmt, built.scope.defs, built.conds, built.plan.resolver)
        if sampled:
            stmt = stmt.where(ObjectInstance.id.in_(built.base().limit(SAMPLE_ROWS)))
        return _guarded(db, stmt)

    product = 1
    unknown = False
    workspaces = counted(func.count(func.distinct(ObjectInstance.owner_workspace_id)))
    product *= max(int(workspaces[0]), 1) if workspaces is not None else 1
    if built.time is not None:
        found = counted(
            func.count(func.distinct(built.time.expr)),
            func.min(built.time.expr),
            func.max(built.time.expr),
        )
        if found is None:
            unknown = True
        else:
            product *= max(int(found[0]), 1)
            out.period_from = found[1].isoformat() if found[1] is not None else None
            out.period_to = (
                axes.next_period(found[2], built.time.grain).isoformat()
                if found[2] is not None
                else None
            )
    if built.cohort is not None:
        found = counted(func.count(func.distinct(built.cohort.expr)))
        if found is None:
            unknown = True
        else:
            product *= max(int(found[0]), 1)
    for info, dim in zip(out.dims, built.dims, strict=True):
        if dim.address in visits_module.ADDRESSES:
            # 값의 가짓수가 정해져 있다 — 창 함수를 표본마다 돌리지 않는다.
            info.distinct = visits_module.CARDINALITY[dim.address]
            product *= info.distinct + 1
            continue
        found = counted(func.count(func.distinct(dim.axis.expr)), axis=dim.axis)
        if found is None:
            unknown = True
            continue
        info.distinct = int(found[0])
        product *= info.distinct + 1
        if dim.axis.kind in ("text", "url", "plain") and info.distinct > MANY_VALUES:
            out.warnings.append(
                f"기준 「{info.label}」 의 값이 {info.distinct:,}가지입니다 — 자유 글자로 "
                "묶으면 셀이 행 수만큼 나옵니다."
            )
    if unknown:
        out.warnings.append(
            f"셀 수를 다 어림하지 못했습니다(추정 질의가 {ESTIMATE_SECONDS}초를 넘겼습니다)."
        )
    # 셀은 (펼친) 기록 줄보다 많을 수 없다. 여러 값 기준(또는 여럿과 이어진 걸음)은 한 기록이
    # 여러 줄로 펼쳐지므로 펼친 줄 수를 표본에서 세어 상한으로 쓴다 — 곱만 쓰면 기본 모델 x
    # 부품 x 월이 4억 셀로 어림됐다(실측, 실제 펼친 줄은 300만).
    expanded = out.rows
    multi = [one.axis for one in built.dims if one.axis.multi]
    if multi:
        found = counted(func.count(), extra=multi)
        if found is None:
            unknown = True
        else:
            seen = min(out.rows, SAMPLE_ROWS) if sampled else out.rows
            expanded = round(int(found[0]) * out.rows / max(seen, 1))
    estimated = min(product, expanded)
    if built.stay is not None:
        # 머무는 기간 — 셀마다 N기간까지 펼친다(계산 시점 뒤의 미래는 안 만들어 이보다 적다).
        estimated *= built.stay.periods
    out.estimated_cells = estimated
    if estimated > settings.metrics_max_cells:
        out.ok = False
        out.errors.append(
            f"셀이 {estimated:,}개로 어림됩니다 — 상한 {settings.metrics_max_cells:,}개를 "
            "넘습니다. 기준을 줄이거나 거르기를 더합니다."
        )
    elif estimated > settings.metrics_max_cells // 2:
        out.warnings.append(f"셀이 {estimated:,}개로 어림됩니다 — 상한의 절반을 넘습니다.")
    if built.overlap:
        out.warnings.append(
            "여러 값 기준(또는 여럿과 이어진 걸음)이 있어 한 기록이 여러 셀에 듭니다 — 셀의 "
            "합이 기록 수보다 큽니다(겹침)."
        )
