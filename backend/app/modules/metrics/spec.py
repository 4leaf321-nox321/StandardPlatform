"""지표 정의 — **계획이 먼저다.**

정의 하나가 계산 · 저장 · 조회 · 건 보기를 만든다. 그래서 저장 전에 **실제로 지어 본다** —
주소가 풀리나, 날짜 칸인가, 거르기가 걸리나, 분모와 짝이 맞나, 셀이 몇 개나 되겠나. 틀린
정의가 저장되면 밤의 타이머가 매일 같은 오류를 내고 아무도 안 본다.

## 정의의 모양(`MetricSpec`)

    measure      count | sum | avg | min | max  (+ measure_field: 자기 숫자 칸)
                 | share — 조건 비율: `share_when` 에 맞는 기록의 몫(분모는 같은 기록 전체)
    time         {address: properties.<날짜 칸>, grain: day|week|month|quarter|year}
    cohort       같은 모양(선택) — 경과(age)는 이 단위. 시간 칸과 단위가 같아야 한다
    dimensions   [{name, address, grain?}] 8개까지 — 주소는 통계 · 조건과 같다(걸음 셋까지)
                 | {name, rules: [{label, when: [{field, op, value}]}], else, label?} — 규칙
                 기준(ADR 0015): 위에서부터 **처음 맞는 규칙**의 이름이 값, 안 맞으면 `else`
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

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
)
from sqlalchemy import (
    Select,
    String,
    and_,
    case,
    false,
    func,
    literal,
    literal_column,
    select,
    text,
)
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
#: 기준은 여덟까지. 셀 수는 기준의 수가 아니라 **실제 조합**으로 막는다 — 계획은 표본의 조합
#: 수로 어림하고 계산은 실제 셀 수로 거절한다(ADR 0023). 표는 그중 몇 개만 골라 묶어 읽는다.
MAX_DIMENSIONS = 8
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
#: 기준마다의 서로 다른 값 수와 셀 조합은 이만큼의 **표본**에서 센다. 200만 건을 기준마다
#: 훑으면 계획 하나에 50초였다(실측) — 어림이 목적이라 표본이면 된다. 행 수는 정확히 센다.
#: 표본은 id(uuid4 — 무작위)의 앞자리로 고른다: 「처음 N 행」 은 시간순으로 쌓인 자료에서 앞
#: 기간에 쏠린다.
SAMPLE_ROWS = 200_000
#: 표본을 겹친 넷(1/8 · 1/4 · 1/2 · 전부)으로 나눠 조합 수가 늘어나는 모양을 맞춘다.
SAMPLE_LEVELS = 4

#: 규칙 기준(ADR 0015)의 값 종류 — 응답의 `kind` 와 같다.
RULE_KIND = "rule"
#: 규칙은 스물까지, 규칙마다 조건은 열까지. 「기타」 의 건 보기가 규칙마다 제외 묶음 하나를
#: 쓰고 (목록 조건의 묶음 번호는 두 자리), 규칙이 그보다 많으면 사람이 차례를 못 읽는다.
MAX_RULES = 20
MAX_RULE_CONDITIONS = 10
#: 성격 이름의 길이 — 셀의 기준 값으로 그대로 저장된다.
RULE_LABEL_MAX = 40
DEFAULT_ELSE = "기타"
#: 「기타」 의 몫이 이보다 크면 계획이 경고한다 — 규칙이 모자란 것이다.
ELSE_SHARE_WARN = 0.2
#: 규칙 조건의 별칭 번호를 규칙마다 이만큼 띄운다 — 한 `CASE` 안의 EXISTS 들이 같은 별칭
#: 이름을 갖지 않게(`conditions.clauses(offset=)`).
_RULE_ALIAS_STEP = 100


class TimeAxisIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(min_length=1, max_length=200)
    grain: str = Field(min_length=1, max_length=10)


class FilterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=200)
    op: str = Field(min_length=1, max_length=12)
    value: str = Field(default="", max_length=2000)


class RuleIn(BaseModel):
    """규칙 하나 — 조건 전부(AND)에 맞으면 이 이름. 이름 · 조건 수의 상한은 계획이
    말한다(여기서 막으면 422 본문이 계획의 오류 목록에 안 실린다)."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="", max_length=200)
    when: list[FilterIn] = Field(default_factory=list, max_length=100)


class DimensionIn(BaseModel):
    """기준 하나 — **주소**(칸)거나 **규칙**(ADR 0015)이다. 규칙 기준은 주소 · 기간 단위 없이
    `rules` · `else`(어느 규칙에도 안 맞을 때의 이름, 기본 「기타」) · `label`(기준의 이름,
    비우면 `name`)을 둔다."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    name: str = Field(min_length=1, max_length=32)
    address: str | None = Field(default=None, max_length=200)
    grain: str | None = Field(default=None, max_length=10)
    rules: list[RuleIn] | None = Field(default=None, max_length=200)
    """위에서부터 **처음 맞는 규칙**이 이긴다 — 차례가 뜻이다."""
    else_: str | None = Field(default=None, alias="else", max_length=200)
    label: str | None = Field(default=None, max_length=RULE_LABEL_MAX)

    @model_serializer(mode="wrap")
    def _compact(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """저장되는 JSON 을 그 종류의 것만으로 — 주소 기준은 예전 모양(`name` · `address` ·
        `grain`) 그대로다. 빈 칸(`rules: null`)을 붙이면 고치지 않은 지표를 다시 저장하기만
        해도 정의 지문이 바뀌어 전량으로 센다(`incremental.spec_hash`)."""
        out = dict(handler(self))
        for key in ("rules", "else", "label"):
            if out.get(key) is None:
                out.pop(key, None)
        if self.rules is not None:
            for key in ("address", "grain"):
                if out.get(key) is None:
                    out.pop(key, None)
        return out

    @property
    def is_rule(self) -> bool:
        return self.rules is not None or self.else_ is not None


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
class RuleSet:
    """규칙 기준을 지은 것 — 규칙마다 이름 · 조건 · 그 조건의 SQL, 「기타」 의 이름, 지문."""

    labels: list[str]
    conds: list[list[conditions.Condition]]
    clauses: list[Any]
    other: str
    fingerprint: str

    def index_of(self, label: str) -> int | None:
        """그 이름의 규칙 차례(0 부터). 「기타」 · 모르는 이름은 None."""
        return self.labels.index(label) if label in self.labels else None


@dataclass
class Dim:
    name: str
    address: str
    grain: str | None
    axis: axes.Axis
    rules: RuleSet | None = None
    """규칙 기준이면 그 규칙들(ADR 0015) — 주소는 비어 있다."""

    @property
    def signature(self) -> tuple[str, ...]:
        """값의 종류 — 분모와 짝지을 때 이것이 같아야 한다. 참조는 상대 타입, 관계 자체는
        상대가 한 타입이면 그 타입(참조와 같은 값), 아니면 주소, 나머지는 종류와 단위. 규칙
        기준은 **같은 규칙**(지문)끼리만 — 이름이 같아도 규칙이 다르면 같은 값이 아니다."""
        if self.rules is not None:
            return (RULE_KIND, self.rules.fingerprint)
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
class RuleCount:
    """계획의 규칙 하나 — 조건에 맞는 기록(`matched`, 앞 규칙과 상관없이)과 그중 이 규칙이
    붙인 기록(`won`, 첫 일치). 차이는 앞 규칙들이 가져간 것(`taken_by` — 규칙 차례 → 건수)."""

    label: str
    matched: int
    won: int
    taken_by: dict[int, int] = field(default_factory=dict)


@dataclass
class RulePlan:
    """계획의 규칙 기준 하나 — 규칙마다의 수 · 「기타」 의 수. 표본이면 `sampled`."""

    name: str
    label: str
    fingerprint: str
    rules: list[RuleCount]
    other: str
    other_count: int
    seen: int
    """센 기록 수(표본이면 표본의 수) — 몫의 분모."""
    sampled: bool


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
    rules: list[RulePlan] = field(default_factory=list)

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
    if dim_in.is_rule:
        return _rule_dimension(plan, dim_in)
    if dim_in.label is not None:
        raise _bad(
            f"기준 「{name}」 의 이름(label)은 규칙 기준에만 둡니다 — 주소 기준의 이름은 칸의 "
            "이름입니다."
        )
    if not dim_in.address:
        raise _bad(f"기준 「{name}」 에 주소(address)나 규칙(rules)이 있어야 합니다.")
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


def rules_fingerprint(dim_in: DimensionIn) -> str:
    """규칙 판의 지문 — 규칙(차례 · 이름 · 조건)과 「기타」 의 이름. 기준 이름 · 보여 줄 이름은
    안 든다(바꿔도 세는 값이 같다)."""
    body = json.dumps(
        {
            "rules": [
                {
                    "label": rule.label.strip(),
                    "when": [[one.field, one.op, one.value] for one in rule.when],
                }
                for rule in dim_in.rules or []
            ],
            "else": (dim_in.else_ if dim_in.else_ is not None else DEFAULT_ELSE).strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _sql_text(value: str) -> Any:
    """글자를 SQL 상수로 — 바인드 매개변수가 아니라. 규칙 기준의 `CASE` 는 고르는 칸과 묶는
    칸에 두 번 서는데, 상수라야 두 식이 글자 그대로 같아 Postgres 가 같은 식으로 맞춰 본다."""
    return literal_column("'" + value.replace("'", "''") + "'", String)


def _rule_dimension(plan: axes.JoinPlan, dim_in: DimensionIn) -> Dim:
    """규칙 기준(ADR 0015) — `CASE WHEN <규칙 1> THEN '…' WHEN <규칙 2> … ELSE '기타' END`.
    **첫 일치가 이긴다.** 조건은 목록 조건과 같은 모양 · 같은 연산이다(`conditions.matches`) —
    이어진 것 너머의 칸(EXISTS)도 기록마다 한 번이라 행이 불지 않는다."""
    name = dim_in.name
    if dim_in.address:
        raise _bad(
            f"규칙 기준 「{name}」 에는 주소(address)를 두지 않습니다 — 값은 규칙이 붙입니다."
        )
    if dim_in.grain:
        raise _bad(f"규칙 기준 「{name}」 에는 기간 단위(grain)가 없습니다.")
    rules = dim_in.rules or []
    if not rules:
        raise _bad(f"규칙 기준 「{name}」 에 규칙이 없습니다 — 하나 이상 둡니다.")
    if len(rules) > MAX_RULES:
        raise _bad(f"규칙 기준 「{name}」 의 규칙은 {MAX_RULES}개까지입니다({len(rules)}개).")
    other = (dim_in.else_ if dim_in.else_ is not None else DEFAULT_ELSE).strip()
    if not other:
        raise _bad(f"규칙 기준 「{name}」 의 「기타」 이름이 비었습니다.")
    if len(other) > RULE_LABEL_MAX:
        raise _bad(f"「기타」 의 이름은 {RULE_LABEL_MAX}자까지입니다: {other!r}")
    labels: list[str] = []
    conds_all: list[list[conditions.Condition]] = []
    clauses: list[Any] = []
    for number, rule in enumerate(rules, 1):
        label = rule.label.strip()
        where = f"규칙 {number}" + (f" 「{label}」" if label else "")
        if not label:
            raise _bad(f"{where} 의 이름(성격)이 비었습니다.")
        if len(label) > RULE_LABEL_MAX:
            raise _bad(f"{where} 의 이름은 {RULE_LABEL_MAX}자까지입니다.")
        if label == other:
            raise _bad(
                f"{where} 의 이름이 「기타」 의 이름과 같습니다 — 기타는 어느 규칙에도 안 "
                "맞는 기록의 값입니다."
            )
        if label in labels:
            raise _bad(
                f"{where} 의 이름이 규칙 {labels.index(label) + 1} 과 같습니다 — 이름마다 "
                "규칙 하나입니다. 한 칸 안의 「또는」 은 「그 중 하나(in)」 로 적습니다(두 "
                "규칙이 한 셀이면 건 보기를 조건으로 정확히 적을 수 없다)."
            )
        if not rule.when:
            raise _bad(
                f"{where} 에 조건이 없습니다 — 조건 없는 규칙은 모든 기록에 맞아 뒤의 규칙이 "
                "다 막힙니다."
            )
        if len(rule.when) > MAX_RULE_CONDITIONS:
            raise _bad(f"{where} 의 조건은 {MAX_RULE_CONDITIONS}개까지입니다.")
        pairs: set[tuple[str, str]] = set()
        for one in rule.when:
            if (one.field, one.op) in pairs:
                raise _bad(
                    f"{where} 에 같은 칸 · 같은 연산이 두 번 있습니다({one.field} {one.op}) — "
                    "하나로 합칩니다(여러 값은 「그 중 하나(in)」)."
                )
            pairs.add((one.field, one.op))
        conds = [conditions.Condition(one.field, one.op, one.value) for one in rule.when]
        try:
            found = conditions.matches(
                plan.scope.defs, conds, plan.resolver, offset=number * _RULE_ALIAS_STEP
            )
        except AppError as caught:
            raise _bad(f"{where}: {caught.message}") from caught
        labels.append(label)
        conds_all.append(conds)
        clauses.append(found)
    expr = case(
        *[(found, _sql_text(label)) for found, label in zip(clauses, labels, strict=True)],
        else_=_sql_text(other),
    )
    ruleset = RuleSet(labels, conds_all, clauses, other, rules_fingerprint(dim_in))
    axis = axes.Axis(expr=expr, label=(dim_in.label or "").strip() or name, kind=RULE_KIND)
    return Dim(name, "", None, axis, rules=ruleset)


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
        if theirs.is_rule:
            their_dim = _rule_dimension(den_plan, theirs)
        else:
            assert theirs.address is not None
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
            if (one.address or "") not in visits_module.ADDRESSES or spec.visits is None:
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


def sample_cut(rows: int) -> uuid.UUID | None:
    """표본의 문턱 — id 가 이보다 작은 행이 약 `SAMPLE_ROWS` 개. 행이 그보다 적으면 None
    (전부)."""
    if rows <= SAMPLE_ROWS:
        return None
    return uuid.UUID(int=int(SAMPLE_ROWS / rows * 2**128))


def extrapolate(points: list[tuple[int, int]], total: int) -> int:
    """(표본의 줄 수, 조합 수) 몇 쌍 → 전체 `total` 줄의 조합 수. 조합은 표본이 커질수록 덜
    늘어난다 — `d = a · n^b`(헵스 법칙)를 로그에서 최소제곱으로 맞춰 늘린다(b 는 0 ~ 1).
    표본이 전부면 그 수 그대로. 줄은 펼친 줄(여러 값 기준이면 값마다 한 줄)이다.

    표본보다 훨씬 멀리 늘리면 크게 넘친다 — 조합은 어느 크기에서 포화하는데 작은 표본 안의
    기울기는 아직 1 에 가깝다(2,000만 건에서 실제의 8.6배, ADR 0023). 그래서 위로 묶는 데만
    쓴다(`estimate_combos`)."""
    found = [(rows, combos) for rows, combos in points if rows > 0 and combos > 0]
    if not found:
        return 0
    last_rows, last = found[-1]
    if total <= last_rows:
        return last
    slope = 1.0
    if len(found) >= 2 and found[0][0] < last_rows:
        xs = [math.log(rows) for rows, _ in found]
        ys = [math.log(combos) for _, combos in found]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        spread = sum((x - mx) ** 2 for x in xs)
        if spread > 0:
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / spread
    slope = min(max(slope, 0.0), 1.0)
    return min(total, math.ceil(last * math.pow(total / last_rows, slope)))


def estimate_combos(points: list[tuple[int, int]], once: int, twice: int, total: int) -> int:
    """표본의 조합 통계 → 전체 `total` 줄의 조합 수(셀 수) 어림.

    표본이 전부면 센 수 그대로. 아니면 서로 다른 값 수 어림 둘의 큰 쪽 — 셋으로 묶는다:

        chao1 = d + f1² / (2 · f2)              f1 · f2 = 표본에서 한 번 · 두 번 나온 조합
        GEE   = √(전체 줄 / 표본 줄) · f1 + (d - f1)

    둘은 서로 다른 자리에서 모자란다(실측 — chao1 은 큰 자료에서 0.64배, GEE 는 중간 자료에서
    0.51배). 큰 쪽은 세 실측에서 0.98 ~ 1.48배였다(ADR 0023). 거듭제곱 늘리기(`extrapolate`) ·
    전체 줄 수를 넘지 않는다."""
    found = [(rows, combos) for rows, combos in points if rows > 0 and combos > 0]
    if not found:
        return 0
    rows, seen = found[-1]
    if total <= rows:
        return seen
    chao1 = seen + once * once / (2 * max(twice, 1))
    gee = math.sqrt(total / rows) * once + (seen - once)
    return min(total, extrapolate(found, total), math.ceil(max(chao1, gee)))


@dataclass
class Combos:
    """표본의 조합 통계 — 겹친 표본마다 (펼친 줄 수, 조합 수)와 전체 표본에서 한 번 · 두 번
    나온 조합 수."""

    points: list[tuple[int, int]]
    once: int
    twice: int


def _combos(db: Session, built: Built, cut: uuid.UUID | None) -> Combos | None:
    """표본에서 (줄 수, 셀 조합 수)를 겹친 표본마다 — 1/8 · 1/4 · 1/2 · 전부. 방문 기준은 창
    함수를 표본마다 돌리지 않고 가짓수를 곱한다(어림이 크게 나올 뿐이다). 시간 상한을 넘기면
    None."""
    keys: list[Any] = [ObjectInstance.owner_workspace_id]
    if built.time is not None:
        keys.append(built.time.expr)
    if built.cohort is not None:
        keys.append(built.cohort.expr)
    joining: list[axes.Axis] = []
    fixed = 1
    for dim in built.dims:
        if dim.address in visits_module.ADDRESSES:
            fixed *= visits_module.CARDINALITY[dim.address] + 1
            continue
        keys.append(dim.axis.expr)
        joining.append(dim.axis)
    if cut is None:
        level: Any = literal(SAMPLE_LEVELS - 1)
    else:
        whens = [
            (ObjectInstance.id < uuid.UUID(int=cut.int >> (SAMPLE_LEVELS - 1 - step)), step)
            for step in range(SAMPLE_LEVELS - 1)
        ]
        level = case(*whens, else_=SAMPLE_LEVELS - 1)
    labelled = [one.label(f"k{index}") for index, one in enumerate(keys)]
    stmt = (
        select(*labelled, level.label("lv"), func.count().label("n"))
        .select_from(ObjectInstance)
        .where(ObjectInstance.type_id == built.source.id, ObjectInstance.deleted_at.is_(None))
    )
    if joining:
        stmt = axes.joined(stmt, *joining)
    stmt = conditions.apply(stmt, built.scope.defs, built.conds, built.plan.resolver)
    if cut is not None:
        stmt = stmt.where(ObjectInstance.id < cut)
    cells = stmt.group_by(*keys, level).cte("est_cells")
    first = (
        select(func.min(cells.c.lv).label("lv"))
        .group_by(*(cells.c[f"k{index}"] for index in range(len(keys))))
        .subquery("est_first")
    )
    rows_by = select(cells.c.lv, func.sum(cells.c.n)).group_by(cells.c.lv)
    combos_by = select(first.c.lv, func.count()).group_by(first.c.lv)
    per_combo = (
        select(func.sum(cells.c.n).label("total"))
        .group_by(*(cells.c[f"k{index}"] for index in range(len(keys))))
        .subquery("est_total")
    )
    tally = select(
        func.count().filter(per_combo.c.total == 1),
        func.count().filter(per_combo.c.total == 2),
    )
    try:
        with db.begin_nested():
            db.execute(text(f"SET LOCAL statement_timeout = {ESTIMATE_SECONDS * 1000}"))
            rows = {int(lv): int(n) for lv, n in db.execute(rows_by)}
            combos = {int(lv): int(n) for lv, n in db.execute(combos_by)}
            once, twice = (int(one or 0) for one in db.execute(tally).one())
    except OperationalError as caught:
        if getattr(caught.orig, "sqlstate", None) == "57014":  # query_canceled
            return None
        raise
    out: list[tuple[int, int]] = []
    seen_rows = seen_combos = 0
    for step in range(SAMPLE_LEVELS):
        seen_rows += rows.get(step, 0)
        seen_combos += combos.get(step, 0)
        if seen_rows:
            out.append((seen_rows, seen_combos * fixed))
    return Combos(out, once * fixed, twice * fixed)


def _estimate(db: Session, built: Built, out: Plan) -> None:
    """셀 수 어림 — 행 수와 기준마다의 서로 다른 값 수. **상한을 넘기면 거절한다.**"""
    settings = get_settings()
    out.rows = count_of(db, built.base())

    cut = sample_cut(out.rows)
    sampled = cut is not None
    if sampled:
        out.warnings.append(
            f"기준의 값 수와 셀 조합은 약 {SAMPLE_ROWS:,}건의 무작위 표본에서 어림했습니다 — "
            "셀 수도 어림입니다(계산이 실제 셀 수로 다시 막는다)."
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
        if cut is not None:
            stmt = stmt.where(ObjectInstance.id < cut)
        return _guarded(db, stmt)

    product = 1
    periods = 0
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
            periods = int(found[0])
            product *= max(periods, 1)
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
    seen = out.rows
    if sampled:
        found = counted(func.count())
        seen = int(found[0]) if found is not None else min(out.rows, SAMPLE_ROWS)
    expanded = out.rows
    multi = [one.axis for one in built.dims if one.axis.multi]
    if multi:
        found = counted(func.count(), extra=multi)
        if found is None:
            unknown = True
        else:
            expanded = round(int(found[0]) * out.rows / max(seen, 1))
    estimated = min(product, expanded)
    # 실제 조합 — 표본에서 셀(부서 · 기간 · 코호트 · 기준 값들)을 세어 늘린다. 곱은 값끼리 함께
    # 나오지 않는 것까지 세어 자료가 크면 늘 기록 수가 되고, 그러면 실제 셀이 적어도 거절했다.
    # 표본의 줄은 이미 펼친 줄이다(조인 뒤에 센다) — 전체도 펼친 줄 수로 늘린다.
    combos = _combos(db, built, cut)
    if combos is None:
        unknown = True
    else:
        estimated = min(
            estimated, estimate_combos(combos.points, combos.once, combos.twice, expanded)
        )
    if built.stay is not None:
        # 머무는 기간 — 셀마다 N기간까지 펼치지만, 이웃한 기간의 펼침은 같은 셀에 겹친다: 기간
        # 말고 다른 값의 조합마다 (기간 수 + N - 1) 개를 넘지 않는다(실측 — 판매 10만 셀 x 24
        # 로 어림하면 240만, 실제는 15만).
        stay_n = built.stay.periods
        spread = estimated * stay_n
        if periods:
            spread = min(spread, product // periods * (periods + stay_n - 1))
        estimated = spread
    out.estimated_cells = estimated
    for dim in built.dims:
        if dim.rules is not None:
            _rule_stats(db, built, dim, out, cut)
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


def _rule_stats(db: Session, built: Built, dim: Dim, out: Plan, cut: uuid.UUID | None) -> None:
    """규칙 기준의 계획 — 규칙마다 **맞는 기록**(앞 규칙과 상관없이)과 **이 규칙이 붙인 기록**
    (첫 일치), 「기타」 의 수. 원천을 한 번 훑으며 규칙마다 맞나(참 · 거짓)를 묶는다 — 조합은
    많아야 기록 수이고 대개 규칙 수 남짓이라, 첫 일치 · 앞 규칙이 가져간 것을 파이썬에서 낸다.
    거르기를 통과한 기록만, 크면 표본에서(셀 어림과 같은 표본)."""
    rules = dim.rules
    assert rules is not None
    hits = [
        func.coalesce(one, false()).label(f"r{index}")
        for index, one in enumerate(rules.clauses)
    ]
    stmt = (
        select(*hits, func.count().label("n"))
        .select_from(ObjectInstance)
        .where(ObjectInstance.type_id == built.source.id, ObjectInstance.deleted_at.is_(None))
    )
    stmt = conditions.apply(stmt, built.scope.defs, built.conds, built.plan.resolver)
    if cut is not None:
        stmt = stmt.where(ObjectInstance.id < cut)
    stmt = stmt.group_by(*(one for one in hits))
    try:
        with db.begin_nested():
            db.execute(text(f"SET LOCAL statement_timeout = {ESTIMATE_SECONDS * 1000}"))
            rows = [tuple(row) for row in db.execute(stmt)]
    except OperationalError as caught:
        if getattr(caught.orig, "sqlstate", None) != "57014":  # query_canceled
            raise
        out.warnings.append(
            f"규칙 기준 「{dim.axis.label}」 의 규칙마다의 수를 세지 못했습니다(질의가 "
            f"{ESTIMATE_SECONDS}초를 넘겼습니다)."
        )
        return
    counts = [RuleCount(label, 0, 0) for label in rules.labels]
    other = seen = 0
    for row in rows:
        flags, n = [bool(one) for one in row[:-1]], int(row[-1])
        seen += n
        first = next((index for index, hit in enumerate(flags) if hit), None)
        if first is None:
            other += n
            continue
        counts[first].won += n
        for index, hit in enumerate(flags):
            if hit:
                counts[index].matched += n
                if index != first:
                    counts[index].taken_by[first] = counts[index].taken_by.get(first, 0) + n
    sampled = cut is not None
    out.rules.append(
        RulePlan(
            name=dim.name,
            label=dim.axis.label,
            fingerprint=rules.fingerprint,
            rules=counts,
            other=rules.other,
            other_count=other,
            seen=seen,
            sampled=sampled,
        )
    )
    where = "표본에서 " if sampled else ""
    for number, one in enumerate(counts, 1):
        if one.matched == 0:
            out.warnings.append(
                f"규칙 {number} 「{one.label}」 에 맞는 기록이 {where}한 건도 없습니다 — 칸 · "
                "값을 확인합니다."
            )
        elif one.won == 0:
            takers = " · ".join(
                f"규칙 {index + 1} 「{rules.labels[index]}」"
                for index in sorted(one.taken_by, key=lambda key: -one.taken_by[key])
            )
            out.warnings.append(
                f"규칙 {number} 「{one.label}」 은 막힌 규칙입니다 — {where}맞는 "
                f"{one.matched:,}건을 앞의 {takers} 이(가) 다 가져가 한 건도 안 남습니다. "
                "차례를 바꾸거나 조건을 수정합니다."
            )
    if seen and other / seen > ELSE_SHARE_WARN:
        out.warnings.append(
            f"「{rules.other}」 가 {where}{other / seen:.0%}입니다({other:,}건) — 어느 "
            "규칙에도 안 맞는 기록이 많습니다. 규칙이 모자란지 봅니다."
        )
