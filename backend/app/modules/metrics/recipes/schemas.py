"""분석의 응답 — 머리는 지표 읽기와 같고(계산 시각 · 닫힘 · 겹침 · 분모), 그 위에 방법 · 요청 ·
주의 · 뺀 것 · 보이는 몫이 붙는다(ADR 0014)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel

from app.modules.metrics.schemas import DrillOut, ReadHeader


class CaveatOut(BaseModel):
    """결과와 함께 **그대로 전해야 하는 말** — 코드는 기계가, 말은 사람이 읽는다."""

    code: str
    level: Literal["info", "warn"]
    message: str
    count: int | None = None


class AnalysisHeader(ReadHeader):
    recipe: str
    method: str
    """방법과 판 — 같은 물음의 답이 바뀌면 방법이 바뀐 것인지 자료가 바뀐 것인지 가른다."""
    params: dict[str, Any]
    """기본값을 채운 요청 그대로."""
    run_id: uuid.UUID | None
    caveats: list[CaveatOut]
    excluded: dict[str, int]
    """뺀 기록 수 — 열린 기간 · 분모 없음 · 분모보다 많음 · 음수 경과 …"""
    visible_share: float | None
    """지금 실행의 기록 중 이 사람에게 보이는 몫 — 1 보다 작으면 비율이 낮게 나올 수 있다."""


# --- ⑦ 파레토 · 집중도 ---------------------------------------------------------------


class ParetoItemOut(BaseModel):
    key: str | None
    label: str
    value: float
    count: int
    share: float
    cumulative: float
    cls: Literal["A", "B", "C"]
    ratio: float | None = None
    drill: DrillOut


class ConcentrationOut(BaseModel):
    categories: int
    """본 값의 수 — 한 번도 안 나온 값은 들지 않는다."""
    hhi: float
    hhi_norm: float
    effective: float
    """유효 개수 = 1 / HHI — 「몇 개가 고르게 나눠 가진 셈인가」."""
    gini: float
    cr1: float
    cr3: float
    cr5: float
    cr10: float
    vital_few: int
    """A 등급(누적 80% 에 처음 닿는 값까지)의 수."""
    vital_share: float


class ParetoTrendOut(BaseModel):
    period: str
    label: str
    total: float
    hhi: float
    effective: float
    gini: float
    top_share: float
    closed: bool


class ParetoOut(AnalysisHeader):
    dim: str
    dim_label: str
    basis: Literal["records", "occurrences"]
    """`occurrences` 면 한 기록이 여러 값에 든다(교체 부품) — 몫은 나온 횟수 기준이다."""
    total: float
    empty_count: int
    empty_value: float
    items: list[ParetoItemOut]
    other_categories: int
    other_value: float
    concentration: ConcentrationOut | None
    trend: list[ParetoTrendOut]


# --- ② 수명 · B수명 -----------------------------------------------------------------


class LifeFitOut(BaseModel):
    model: Literal["weibull", "defective"]
    beta: float
    """형상 — 1 보다 작으면 초기 고장, 1 근처면 우발, 크면 마모."""
    beta_ci: list[float] | None
    eta: float
    """척도 — 고장 나는 것들의 63% 가 고장 나는 경과(기간 단위)."""
    eta_ci: list[float] | None
    eta_days: float
    p: float | None
    """결국 고장 나는 비율(결함 모형만)."""
    p_ci: list[float] | None
    loglik: float
    aic: float
    converged: bool
    identifiable: bool
    """정보 행렬이 서서 구간을 낼 수 있었나 — 아니면 구간 없이 값만."""


class BLifeOut(BaseModel):
    q: float
    status: Literal["observed", "extrapolated", "unreachable", "uncertain", "none"]
    age: float | None
    """기간 단위의 경과 — 「이르지 않음」 이면 없다(지어내지 않는다)."""
    ci: list[float] | None
    age_days: float | None
    conditional_age: float | None
    """결국 고장 나는 것들 중 q 가 고장 날 때까지 — 결함 모형에서만, 그렇게 읽는다."""
    extrapolation: float | None
    """관측한 가장 긴 경과의 몇 배 밖인가."""
    observed_age: float | None
    """비모수 곡선이 q 에 닿은 경과 — 닿았을 때만."""


class LifePointOut(BaseModel):
    age: int
    at_risk: float
    failures: float
    hazard: float
    observed: float
    observed_low: float | None
    observed_high: float | None
    fitted: float | None
    """고른 모형의 같은 경과 값(판매일이 달 안에 고르다고 본 경과 확률).

    관측과 같은 자로 견준다.
    """
    cohorts: int


class LifeCurveOut(BaseModel):
    age: float
    standard: float | None
    defective: float | None


class LifeCohortOut(BaseModel):
    cohort: str
    label: str
    units: float
    failures: int
    horizon: int
    drill: DrillOut


class LifeOut(AnalysisHeader):
    basis: Literal["records", "first_visits"]
    time_unit: str
    units: float
    failures: int
    cohorts_used: int
    max_age: int
    reached: float
    """비모수 곡선이 닿은 가장 큰 누적 고장률."""
    fits: list[LifeFitOut]
    chosen: Literal["weibull", "defective"] | None
    lrt_statistic: float | None
    lrt_p_value: float | None
    b_lives: list[BLifeOut]
    points: list[LifePointOut]
    curve: list[LifeCurveOut]
    cohort_rows: list[LifeCohortOut]
    gof_chi2: float | None
    gof_df: int | None
    gof_p_value: float | None
    max_rel_dev: float | None


# --- ③ 관리도 ------------------------------------------------------------------------


class ControlRuleOut(BaseModel):
    number: int
    label: str


class ControlPointOut(BaseModel):
    when: str
    """부분군의 시작일 — 기간 축이면 접수 기간, 코호트 축이면 생산 · 판매 기간."""
    label: str
    count: int
    exposure: float | None
    """분모(대수). 건수 관리도면 없다."""
    rate: float | None
    """건수 / 대수 x per — 건수 관리도면 건수."""
    lcl: float | None
    ucl: float | None
    z: float | None
    """라니 보정까지 한 표준 점수 — ±3 이 관리 한계."""
    closed: bool
    baseline: bool
    """한계를 잡는 데 쓴 점인가."""
    signals: list[int]
    """걸린 넬슨 규칙 번호."""
    drill: DrillOut


class ControlChartOut(BaseModel):
    key: str | None
    label: str
    center: float | None
    sigma_z: float | None
    """한계를 넓힌 배수(라니) — 1 이면 포아송 그대로."""
    sigma_z_raw: float | None
    """이동 범위로 잰 값 그대로 — 1 보다 작아도 한계는 줄이지 않는다."""
    subgroups: int
    """닫힌 부분군 수."""
    baseline_points: int
    signals: int
    """신호가 걸린 점 수."""
    total: int
    points: list[ControlPointOut]


class ControlOut(AnalysisHeader):
    axis: Literal["period", "cohort"]
    """부분군의 축 — 그 단위는 머리의 `grain`(기간) · `cohort_grain`(코호트)."""
    window: int | None
    """코호트 축 — 출고 뒤 몇 기간 안의 건수인가."""
    kind: Literal["u", "c"]
    """u 는 대수당 비율, c 는 건수(분모가 없을 때)."""
    per: float
    split: str | None
    split_label: str | None
    baseline_to: str | None
    rules: list[ControlRuleOut]
    charts: list[ControlChartOut]
    other_groups: int


# --- ⑩ 계절 · 변화점 ------------------------------------------------------------------


class SeasonOut(BaseModel):
    season: int
    label: str
    index: float
    """1 보다 크면 그 계절에 평소보다 많다(로그 평균 0 — 곱하면 1)."""


class SegmentOut(BaseModel):
    start: str
    stop: str
    """이 날 **앞까지**."""
    label: str
    level: float
    """계절을 뺀 수준(비율이면 per 당)."""
    count: int
    points: int


class ChangeOut(BaseModel):
    at: str
    """새 수준의 첫 부분군."""
    label: str
    before: float
    after: float
    ratio: float | None
    ratio_ci: list[float] | None
    provisional: bool
    """새 수준이 몇 점 안 된다 — 더 보고 판단한다."""


class ChangesPointOut(BaseModel):
    when: str
    label: str
    count: int
    exposure: float | None
    rate: float | None
    seasonal: float | None
    adjusted: float | None
    """계절을 뺀 값 = rate / seasonal."""
    level: float | None
    """그 점이 든 구간의 수준 — 열린 점은 없다."""
    closed: bool
    drill: DrillOut


class ChangesOut(AnalysisHeader):
    axis: Literal["period", "cohort"]
    window: int | None
    kind: Literal["rate", "count"]
    per: float
    season_length: int
    seasonal: list[SeasonOut]
    seasonal_p_value: float | None
    dispersion: float | None
    """과분산 φ — 1 이면 포아송 그대로."""
    penalty: float | None
    min_segment: int
    changes: list[ChangeOut]
    segments: list[SegmentOut]
    points: list[ChangesPointOut]
