"""분석의 응답 — 머리는 지표 읽기와 같고(계산 시각 · 닫힘 · 겹침 · 분모), 그 위에 방법 · 요청 ·
주의 · 뺀 것 · 보이는 몫이 붙는다(ADR 0014)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

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


class ParetoCompareItemOut(BaseModel):
    key: str | None
    label: str
    count_a: float
    share_a: float
    count_b: float
    share_b: float
    residual: float
    """뒤 기간 쪽 수정 잔차 — 양수면 뒤 기간에 몫이 커졌다."""
    notable: bool
    """|잔차| > 3 — 몫이 달라진 값."""


class ParetoCompareOut(BaseModel):
    label_a: str
    label_b: str
    total_a: float
    total_b: float
    chi2: float
    df: int
    p_value: float
    items: list[ParetoCompareItemOut]


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
    comparison: ParetoCompareOut | None = None
    """두 기간 비교 — `compare_from` · `compare_to` 를 줬을 때."""


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
    kind: Literal["u", "c", "p"]
    """u — 대수당 비율, c — 건수, p — 조건 비율(같은 기록 중 몫)."""
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


class ScanPeriodOut(BaseModel):
    when: str
    label: str
    closed: bool


class ChangesScanItemOut(BaseModel):
    """값 하나(증상 등)의 변화점 — 같은 대수로 나눈 그 값의 비율에서."""

    key: str | None
    label: str
    total: int
    points: int
    """계산에 쓴 닫힌 부분군 수."""
    direction: Literal["up", "down", "flat"]
    """마지막 변화점의 방향 — 없으면 flat."""
    last: ChangeOut | None
    changes: list[ChangeOut]
    level_now: float | None
    """끝 구간의 수준(계절을 뺀 비율)."""
    dispersion: float | None
    seasonal: bool
    """계절 지수를 썼나."""
    note: str | None
    """계산하지 못한 까닭(점이 모자람 등)."""
    drill: DrillOut


class ChangesScanOut(AnalysisHeader):
    """⑩ 값마다 훑기 — 「계절을 빼면 실제로 늘고 있는 증상은?」 늘어난 것부터."""

    axis: Literal["period", "cohort"]
    window: int | None
    kind: Literal["rate", "count"]
    per: float
    by: str
    by_label: str
    scanned: int
    other_values: int
    """건수가 적어 훑지 않은 값 수."""
    extra_penalty: float
    """여럿을 함께 보느라 변화점 하나에 더한 벌점(2·ln K)."""
    periods: list[ScanPeriodOut]
    items: list[ChangesScanItemOut]


# --- 클레임 예측 ---------------------------------------------------------------------


class ForecastPointOut(BaseModel):
    """달력 기간 하나 — 예측(평균 · 80 · 95% 구간) 또는 실제."""

    period: str
    label: str
    expected: float | None
    low: float | None
    high: float | None
    low80: float | None
    high80: float | None
    actual: float | None


class ForecastTotalOut(BaseModel):
    expected: float
    low: float
    high: float
    """95% 구간."""
    cost: float | None
    cost_low: float | None
    cost_high: float | None


class ForecastBacktestOut(BaseModel):
    """되짚어 보기 — `start` 앞까지만 보고 맞췄다면 그 뒤 `periods` 기간을 얼마나 맞혔나."""

    start: str
    periods: int
    predicted: float
    low: float
    high: float
    actual: float
    within: bool
    points: list[ForecastPointOut]


class ForecastOut(AnalysisHeader):
    """클레임 예측 — 「이미 판 물량에서 앞으로 몇 건(얼마)이 더 들어오나」."""

    model: Literal["weibull", "defective"]
    beta: float
    eta: float
    """척도 — 코호트 기간 단위."""
    p: float
    """결국 고장 나는 비율(표준 와이블이면 1)."""
    horizon: int
    warranty: int | None
    basis: Literal["records", "first_visits"]
    cost: float | None
    units: float
    cohorts: int
    start: str
    """예측의 첫 기간 — 닫히지 않은 첫 기간."""
    total: ForecastTotalOut
    """앞으로 `horizon` 기간의 합."""
    remaining: ForecastTotalOut | None
    """보증 끝까지 남은 총량(보증 기간을 줬을 때)."""
    points: list[ForecastPointOut]
    history: list[ForecastPointOut]
    backtest: ForecastBacktestOut | None


# --- 전후 비교 -----------------------------------------------------------------------


class CutinSideOut(BaseModel):
    """적용일의 한쪽 — 닫힌 부분군의 합."""

    first: str | None
    last: str | None
    subgroups: int
    count: int
    exposure: float | None
    rate: float | None
    """per 대당(조건 비율이면 %)."""
    drill: DrillOut | None


class CutinPointOut(BaseModel):
    when: str
    label: str
    count: int
    exposure: float | None
    rate: float | None
    closed: bool
    side: Literal["before", "after", "skipped", "boundary"]
    """앞 · 뒤, 처음 빼기(`skip_first`), 적용일이 낀 부분군(섞여 있어 뺀다)."""
    drill: DrillOut


class CutinTrendOut(BaseModel):
    """적용 전의 흐름 — 기간마다의 변화(비율의 배수 - 1)와 그 p."""

    change_per_period: float | None
    p_value: float | None


class CutinOut(AnalysisHeader):
    """전후 비교 — 「대책 적용일 뒤에 만든(판) 것부터 줄었나」."""

    at: str
    axis: Literal["period", "cohort"]
    window: int | None
    per: float
    kind: Literal["rate", "count"]
    before: CutinSideOut
    after: CutinSideOut
    open_after: int
    """적용일 뒤에 있으나 창이 아직 안 닫힌 부분군 수 — 계산에 안 넣었다."""
    ratio: float | None
    """뒤 비율 / 앞 비율."""
    ratio_low: float | None
    ratio_high: float | None
    p_value: float | None
    dispersion: float
    effect: float
    """의미 있는 차이(예: 0.2 = 20%) — 「차이 없음」 과 「아직 이르다」 를 가른다."""
    decision: Literal["reduced", "increased", "no_difference", "too_early"]
    more_subgroups: int | None
    """「아직 이르다」 면 그 차이를 가리려고 더 닫혀야 할 뒤의 부분군 수(검정력 80%) — 앞의
    건수가 적어 못 가리면 None."""
    pre_trend: CutinTrendOut
    points: list[CutinPointOut]


# --- ⑤ 집단 비교 ---------------------------------------------------------------------


class GroupRowOut(BaseModel):
    key: str | None
    label: str
    count: int
    exposure: float
    rate: float | None
    """그대로 비율(per 대당) — 작은 집단에서는 우연으로 크게 흔들린다."""
    shrunk: float | None
    """줄인 비율 — 대수가 작을수록 전체 쪽으로 끌린 값. 순위 · 견줌은 이것으로."""
    shrunk_low: float | None
    shrunk_high: float | None
    shrinkage: float
    """줄인 정도 0~1 — 1 이면 전체 비율로 다 끌렸다(대수가 작거나 집단 사이 차이가 없다)."""
    ratio: float | None
    """줄인 비율 / 전체 비율."""
    p_value: float
    q_value: float
    """그 집단 대 나머지 정확 검정의 p 를 BH 로 맞춘 것."""
    flag: Literal["high", "low"] | None
    """q < 0.05 인 집단 — 전체보다 높음 · 낮음."""
    drill: DrillOut


class GroupsOut(AnalysisHeader):
    """⑤ 집단 비교 — 「SKU(색상 · 용량 · 통신사) · 공장 · 기본 모델마다 불량률이 다른가?」."""

    dim: str
    dim_label: str
    axis: Literal["period", "cohort"]
    window: int | None
    per: float
    pooled: float | None
    """전체 비율(per 대당)."""
    groups: int
    heterogeneity_chi2: float | None
    heterogeneity_df: int
    heterogeneity_p: float | None
    """「집단 사이에 우연보다 큰 차이가 있나」 — 포아송 χ²."""
    spread: float | None
    """집단 사이 참 비율의 흔들림 τ / 전체 비율(0 이면 차이가 우연의 흔들림 안)."""
    flagged: int
    rows: list[GroupRowOut]
    other_groups: int
    """계산에는 넣었으나 싣지 않은 집단 수 — 「다름」 을 전체와 먼 것부터, 남으면 줄인 비율
    높은 순으로 싣는다."""


# --- ④ 순차 검정 ---------------------------------------------------------------------


class ReferenceRateOut(BaseModel):
    age: int
    rate: float
    """경과 a 의 건수 / 그 경과까지 본 대수(대수 하나당)."""
    units: float
    records: int


class SprtLookOut(BaseModel):
    k: int
    """출시(새 모델의 첫 코호트)부터 몇 번째 기간."""
    when: str
    label: str
    observed: float
    expected: float
    llr: float
    smr: float | None
    smr_low: float | None
    smr_high: float | None
    decision: Literal["continue", "worse", "not_worse"]
    after_decision: bool
    """결론이 선 뒤의 기간 — 참고로만."""


class SprtCohortOut(BaseModel):
    cohort: str
    label: str
    units: float
    observed: int
    expected: float
    drill: DrillOut


class SprtOut(AnalysisHeader):
    dim: str
    dim_label: str
    target: str
    target_label: str
    reference: str
    reference_label: str
    rho: float
    alpha: float
    beta: float
    upper: float
    lower: float
    dispersion: float = 1.0
    """전작 셀의 과분산 φ — 1 보다 크면 우도비를 φ 로 나눴다(준-포아송)."""
    decision: Literal["continue", "worse", "not_worse"]
    """worse 는 「전작보다 ρ 배 쪽」, not_worse 는 「ρ 배 나쁘지는 않다」, continue 는
    「아직」."""
    decided_at: str | None
    observed: float
    expected: float
    llr: float
    smr: float | None
    smr_low: float | None
    smr_high: float | None
    to_not_worse: float | None
    """「아직」 일 때 — 전작과 같다면 결론까지 더 쌓일 기대 건수."""
    to_worse: float | None
    periods_to_not_worse: float | None
    """요즘 기간마다 쌓이는 기대 건수로 나눈 어림 — 몇 기간 더."""
    periods_to_worse: float | None
    reference_reach: int
    """전작이 닿은 가장 긴 경과 — 그 너머의 새 모델 기록은 견주지 못한다."""
    reference_rates: list[ReferenceRateOut]
    looks: list[SprtLookOut]
    cohort_rows: list[SprtCohortOut]


class SprtScanItemOut(BaseModel):
    """값 하나(증상 등)로 거른 새 모델 vs 전작."""

    key: str
    label: str
    decision: Literal["continue", "worse", "not_worse"]
    decided_at: str | None
    observed: float
    expected: float
    llr: float
    smr: float | None
    smr_low: float | None
    smr_high: float | None
    periods_to_worse: float | None
    periods_to_not_worse: float | None
    new: bool
    """전작에 없던 값 — 기대가 0 이라 비를 낼 수 없다."""


class SprtScanOut(AnalysisHeader):
    """④ 값마다 훑기 — 「출시 N주차, 전작보다 빨리 늘고 있는 증상은?」 「나쁨」 이 선
    것부터."""

    dim: str
    dim_label: str
    by: str
    by_label: str
    target: str
    target_label: str
    reference: str
    reference_label: str
    rho: float
    alpha: float
    alpha_each: float
    """값마다의 유의수준 — 여럿을 함께 보느라 α 를 값의 수로 나눴다(본페로니)."""
    beta: float
    scanned: int
    other_values: int
    items: list[SprtScanItemOut]
    skipped: list[str]


# --- ⑥ 재방문 위험 요인 --------------------------------------------------------------


class LogitLevelOut(BaseModel):
    key: str | None
    label: str
    count: int
    """재방문 창이 닫힌 기록 수(예 + 아니오)."""
    yes: int
    rate: float | None
    odds_ratio: float | None
    """기준 수준 대비 — 기준 수준은 1, 불안정하면 없다."""
    ci: list[float] | None
    reference: bool
    pooled: int
    """「그 밖」 이면 모인 원래 값의 수."""
    unstable: bool


class LogitFactorOut(BaseModel):
    name: str
    label: str
    chi2: float
    """이 요인을 뺐을 때 이탈도가 는 양 — LR 검정 통계량."""
    df: int
    p_value: float
    levels: list[LogitLevelOut]


class LogitOut(AnalysisHeader):
    within_days: int
    repeat_dim: str
    records: int
    yes: int
    no: int
    rate: float | None
    baseline_rate: float | None
    """모든 요인이 기준 수준일 때의 재방문 확률."""
    factors: list[LogitFactorOut]
    deviance: float | None
    null_deviance: float | None
    auc: float | None
    """예측 확률이 재방문 기록과 아닌 기록을 가르는 정도 — 0.5 면 못 가름."""
    converged: bool | None


# --- ⑨ 연관 · 묶음 -------------------------------------------------------------------


class AssocLabelOut(BaseModel):
    key: str
    label: str


class AssocPairOut(BaseModel):
    row: AssocLabelOut
    col: AssocLabelOut
    count: float
    expected: float
    """여백만 보고 우연이면 나올 수."""
    lift: float
    """향상도 — count / expected."""
    share: float
    """행의 값 안에서 이 열의 몫."""
    p_value: float
    q_value: float
    """BH 로 거짓 발견율을 맞춘 p."""
    drill: DrillOut


class AssocDispersionOut(BaseModel):
    """원인분산도 한 줄 — 이 행(증상)의 원인(열)이 얼마나 갈렸나."""

    row: AssocLabelOut
    count: float
    causes: int
    """나온 원인 값 수."""
    effective: float
    """유효 원인 수 1/Σ몫² — 원인이 몇 개에 고르게 퍼진 것과 같은가(1 이면 하나에 몰림)."""
    top: AssocLabelOut
    top_share: float
    """가장 많은 원인의 몫."""


class AssocClusterOut(BaseModel):
    members: list[AssocLabelOut]
    top: list[AssocLabelOut]
    """그 묶음을 가르는 열 — PPMI 가 큰 것부터."""
    count: float


class AssocPointOut(BaseModel):
    key: str
    label: str
    x: float
    y: float
    mass: float


class AssocOut(AnalysisHeader):
    rows: str
    rows_label: str
    cols: str
    cols_label: str
    basis: Literal["records", "occurrences"]
    total: float
    row_values: int
    col_values: int
    tested: int
    """검정한 짝 수(건수가 min_count 이상)."""
    pairs: list[AssocPairOut]
    dispersion: list[AssocDispersionOut] = Field(default_factory=list)
    """원인분산도 — 원인이 많이 갈린 행부터."""
    overall_effective: float | None = None
    """견줄 기준 — 모든 행을 합친 열 몫의 유효 개수."""
    clusters: list[AssocClusterOut]
    silhouette: float | None
    map_rows: list[AssocPointOut]
    map_cols: list[AssocPointOut]
    map_explained: float | None
    """대응 분석 첫 두 축이 설명하는 몫."""
