from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.jobs.schemas import JobOut
from app.modules.metrics.spec import MetricSpec
from app.modules.ontology.models import SLUG_MAX


class DimOut(BaseModel):
    name: str
    address: str
    label: str
    kind: str
    multi: bool
    grain: str | None = None
    distinct: int | None = None
    """계획에서만 — 거르기를 통과한 기록에서 서로 다른 값의 수."""
    target: str | None = None
    """축(다른 타입을 가리키는 참조 · 관계)이면 그 타입 — 축 객체의 상세가 자기를 기준으로 가진
    지표를 찾는다(ADR 0022)."""


class AnalysisAvailOut(BaseModel):
    """이 지표에 되는 분석(ADR 0014) — 안 되면 그 이유 한 줄. 화면이 고르개를 끄고 말로
    보인다."""

    recipe: str
    label: str
    ok: bool
    reason: str | None = None


class MetricOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    label: str
    description: str
    source_type_slug: str
    source_type_label: str
    spec: dict[str, Any]
    interval_hours: int
    is_active: bool
    overlap: bool
    measure_label: str
    grain: str | None
    cohort_grain: str | None
    dims: list[DimOut]
    broken: str | None = None
    """정의가 지금 안 지어지는 이유(칸이 지워짐 · 분모가 사라짐). 있으면 계산이 실패한다."""
    analyses: list[AnalysisAvailOut] = Field(default_factory=list)
    """이 지표 위에서 되는 분석과 안 되는 이유(ADR 0014)."""
    current_run_id: uuid.UUID | None
    last_run_at: datetime | None
    last_status: str | None
    last_error: str | None
    cells: int
    stale: bool
    """계산 시각이 주기의 세 배보다 오래됐거나 아직 한 번도 안 셌다."""
    created_at: datetime
    updated_at: datetime


class MetricIn(BaseModel):
    slug: str = Field(min_length=1, max_length=SLUG_MAX)
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    source_type_slug: str = Field(min_length=1, max_length=SLUG_MAX)
    spec: MetricSpec
    interval_hours: int = Field(default=24, ge=0, le=24 * 30)
    is_active: bool = True

    @field_validator("interval_hours")
    @classmethod
    def _whole_days(cls, value: int) -> int:
        return whole_days(value)


def whole_days(value: int) -> int:
    """주기는 **하루 미만(시간마다)이거나 날 단위(N일마다 밤)** 다 — 36시간 같은 값은 「밤에
    센다」 와 「시간마다 센다」 어느 쪽인지 말이 안 된다."""
    if value >= 24 and value % 24:
        raise ValueError(
            "하루 이상 주기는 날 단위입니다(24 · 48 · 72 …) — N일마다 밤에 셉니다. "
            "하루보다 자주 세려면 23시간 이하로."
        )
    return value


class MetricPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    spec: MetricSpec | None = None
    interval_hours: int | None = Field(default=None, ge=0, le=24 * 30)
    is_active: bool | None = None

    @field_validator("interval_hours")
    @classmethod
    def _whole_days(cls, value: int | None) -> int | None:
        return None if value is None else whole_days(value)


class PlanIn(BaseModel):
    slug: str | None = Field(default=None, max_length=SLUG_MAX)
    """고치는 중이면 자기 slug — 자기 자신을 분모로 적은 것을 잡는다."""
    source_type_slug: str = Field(min_length=1, max_length=SLUG_MAX)
    spec: MetricSpec


class PlanOut(BaseModel):
    ok: bool
    errors: list[str]
    warnings: list[str]
    rows: int | None
    """거르기를 통과한 기록 수."""
    estimated_cells: int | None
    overlap: bool
    dims: list[DimOut]
    period_from: str | None
    period_to: str | None


class MetricSavedOut(BaseModel):
    metric: MetricOut
    job: JobOut | None
    """`recompute=true` 로 저장했으면 넣은 작업."""


class MetricRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID | None
    status: str
    watermark: datetime | None
    started_at: datetime
    finished_at: datetime | None
    rows: int
    cells: int
    error: str | None
    stats: dict[str, Any]
    mode: str = "full"
    """`full` 전부 다시 셌다 · `incremental` 바뀐 기간만(셀은 앞선 전량 실행의 것을 이어
    쓴다)."""
    periods: list[str | None] | None = None
    """증분이 다시 센 기간(시작일, 날짜를 못 읽은 칸은 null)."""
    note: str = ""
    """전량이면 왜 전량이었나, 증분이면 바뀐 기록 수."""


# --- 읽기 ---------------------------------------------------------------------------


class DrillOut(BaseModel):
    """이 셀을 이룬 기록의 **목록 조건** — `/api/objects/<타입>?<params>` 에 그대로."""

    type_slug: str
    params: dict[str, str]
    partial: list[str] = Field(default_factory=list)
    """조건으로 못 적은 축(부서 · 못 읽은 날짜 · 걸음 너머의 빈 값). 비어 있지 않으면 목록의
    수가 셀의 수보다 클 수 있다."""


class CellOut(BaseModel):
    dims: dict[str, str | None]
    labels: dict[str, str]
    """기준 값의 이름 — 참조는 상대의 이름, 날짜는 기간, 빈 값은 「(비어 있음)」."""
    period: str | None = None
    period_label: str | None = None
    cohort: str | None = None
    cohort_label: str | None = None
    age: int | None = None
    count: int
    value_count: int
    sum: float | None
    min: float | None
    max: float | None
    avg: float | None
    value: float | None
    """이 지표의 집계(count 면 count, avg 면 sum / value_count)."""
    ratio: float | None = None
    denominator: float | None = None
    closed: bool | None = None
    drill: DrillOut
    value_drill: DrillOut | None = None
    """조건 비율이면 조건에 맞는 기록(값)의 목록 — `drill` 은 셀의 기록 전부(분모)."""


class DenominatorOut(BaseModel):
    metric: str
    label: str
    measure: str
    measure_label: str
    time: str | None
    per: float
    missing: int
    """분모가 없거나 0 이라 비율을 못 낸 셀 수."""
    truncated: bool


class StayOut(BaseModel):
    """머무는 기간 — 기간마다의 값이 최근 `periods` 기간의 합이다(ADR 0023)."""

    periods: int
    periods_from: str | None
    """기록마다 기간 수를 읽은 칸의 주소 — 있으면 기록마다 다르다(최대 `periods`)."""
    periods_from_label: str | None


class ReadHeader(BaseModel):
    """모든 읽기 응답의 머리 — **계산 시각 · 겹침 · 못 묶은 수 · 잘림을 숨기지 않는다.**"""

    slug: str
    label: str
    measure: str
    measure_label: str
    grain: str | None
    cohort_grain: str | None
    settle_days: int
    computed_at: datetime | None
    watermark: datetime | None
    stale: bool
    overlap: bool
    unbucketed: int
    """시간 칸을 못 읽어 기간이 없는 기록 수(전체 — 가시성과 무관)."""
    unbucketed_cohort: int
    negative_age: int
    truncated: bool
    denominator: DenominatorOut | None
    stay: StayOut | None = None
    """머무는 기간이 있는 지표 — 기간의 값은 그 기간에 생긴 것이 아니라 최근 N기간의 합이다."""


class TableOut(ReadHeader):
    dims: list[str]
    by: list[str]
    cells: list[CellOut]
    total_count: int
    total_value: float | None


class PointOut(BaseModel):
    period: str
    label: str
    count: int
    value: float | None
    ratio: float | None
    prev: float | None
    """바로 앞 기간의 값."""
    yoy: float | None
    """한 해 전 같은 기간의 값."""
    closed: bool
    drill: DrillOut


class LineOut(BaseModel):
    key: str | None
    label: str
    points: list[PointOut]


class SeriesOut(ReadHeader):
    split: str | None
    lines: list[LineOut]
    lines_truncated: bool


class CohortCellOut(BaseModel):
    age: int
    count: int
    value: float | None
    cumulative: float | None
    ratio: float | None
    closed: bool
    drill: DrillOut


class CohortRowOut(BaseModel):
    cohort: str
    label: str
    denominator: float | None
    cells: list[CohortCellOut]


class CohortOut(ReadHeader):
    cumulative: bool
    ages: list[int]
    rows: list[CohortRowOut]


class DimValueOut(BaseModel):
    value: str | None
    label: str
    count: int


class DimValuesOut(BaseModel):
    name: str
    label: str
    kind: str
    values: list[DimValueOut]
    truncated: bool


# --- 경보(ADR 0016) ---------------------------------------------------------------------


class AlertIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    recipe: str = Field(description="sprt · control · changes")
    params: dict[str, str] = Field(
        default_factory=dict,
        description="분석 경로의 쿼리 그대로 — 거르기는 `d.<기준>`, 경보만의 것은 `recent` · "
        "`launched_within` · `notify_not_worse`",
    )


class AlertPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None


class AlertFindingOut(BaseModel):
    key: str
    title: str
    detail: dict[str, Any]
    new: bool
    """이 경보가 아직 안 본 것 — 다음 계산에서 알림이 된다."""


class AlertCheckOut(BaseModel):
    """지금 확인 — 알리지도 적지도 않는다. 만들 때는 이것이 「처음부터 있던 것」 이 된다."""

    run_id: uuid.UUID | None
    findings: list[AlertFindingOut]
    notes: list[str]
    """건너뛴 것 · 보정 — 「전작을 못 찾은 모델 2개」, 「α 를 모델 12개로 나눴습니다」."""


class AlertOut(BaseModel):
    id: uuid.UUID
    metric: str
    metric_label: str
    name: str
    recipe: str
    recipe_label: str
    params: dict[str, str]
    is_active: bool
    last_checked_at: datetime | None
    last_status: str | None
    last_error: str | None
    events: int
    created_at: datetime
    link: str
    """분석 탭을 이 인자로 연다 — 알림의 링크와 같다."""


class AlertSavedOut(AlertOut):
    baseline: AlertCheckOut | None = None
    """만들 때만 — 처음 확인에서 본 것(알리지 않았다)."""


class AlertEventOut(BaseModel):
    id: uuid.UUID
    alert_id: uuid.UUID
    alert_name: str
    metric: str
    metric_label: str
    recipe: str
    key: str
    title: str
    detail: dict[str, Any]
    baseline: bool
    run_id: uuid.UUID | None
    created_at: datetime
    link: str


class HomePinIn(BaseModel):
    """부서 홈에 올린다 — 이미 있으면 나눌 기준 · 자리만 고친다."""

    workspace_slug: str = Field(min_length=1, max_length=SLUG_MAX)
    split: str | None = Field(default=None, max_length=32)
    """선을 나눌 기준 이름 — 비우면 합계 한 줄."""
    position: int | None = Field(default=None, ge=0)
    """그 부서 홈에서 몇 번째 자리로(뷰와 같은 줄). 비우면 지금 자리 · 새것은 맨 끝."""


class HomePinOut(BaseModel):
    id: uuid.UUID
    workspace_slug: str
    workspace_name: str
    split: str | None
    home_order: int
