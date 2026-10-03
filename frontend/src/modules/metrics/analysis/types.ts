/**
 * 분석의 응답 — 서버 `metrics/recipes/schemas.py` 와 같은 모양(ADR 0014).
 *
 * 머리는 지표 읽기와 같고(계산 시각 · 닫힘 · 겹침 · 분모), 그 위에 방법 · 요청 · 주의 · 뺀 것 ·
 * 보이는 몫이 붙는다. 화면은 주의(`caveats`)를 숫자보다 먼저, 그대로 보인다.
 */

import type { Drill, ReadHeader } from '@/modules/metrics/api'

export type Recipe = 'pareto' | 'life' | 'control' | 'changes' | 'sprt' | 'logit'

export interface Caveat {
  code: string
  level: 'info' | 'warn'
  message: string
  count: number | null
}

export interface AnalysisHeader extends ReadHeader {
  recipe: Recipe
  /** 방법과 판 — 같은 물음의 답이 바뀌면 방법이 바뀐 것인지 자료가 바뀐 것인지 가른다. */
  method: string
  params: Record<string, unknown>
  run_id: string | null
  caveats: Caveat[]
  /** 뺀 기록 수 — 0 인 것은 안 싣는다. */
  excluded: Record<string, number>
  /** 지금 실행의 기록 중 이 사람에게 보이는 몫. */
  visible_share: number | null
}

// --- ⑦ 파레토 · 집중도 ----------------------------------------------------------

export interface ParetoItem {
  key: string | null
  label: string
  value: number
  count: number
  share: number
  cumulative: number
  cls: 'A' | 'B' | 'C'
  ratio: number | null
  drill: Drill
}

export interface Concentration {
  categories: number
  hhi: number
  hhi_norm: number
  effective: number
  gini: number
  cr1: number
  cr3: number
  cr5: number
  cr10: number
  vital_few: number
  vital_share: number
}

export interface ParetoTrend {
  period: string
  label: string
  total: number
  hhi: number
  effective: number
  gini: number
  top_share: number
  closed: boolean
}

export interface ParetoResult extends AnalysisHeader {
  dim: string
  dim_label: string
  basis: 'records' | 'occurrences'
  total: number
  empty_count: number
  empty_value: number
  items: ParetoItem[]
  other_categories: number
  other_value: number
  concentration: Concentration | null
  trend: ParetoTrend[]
}

// --- ② 수명 · B수명 -------------------------------------------------------------

export type LifeModel = 'weibull' | 'defective'
export type LifeStatus = 'observed' | 'extrapolated' | 'unreachable' | 'uncertain' | 'none'

export interface LifeFit {
  model: LifeModel
  beta: number
  beta_ci: number[] | null
  eta: number
  eta_ci: number[] | null
  eta_days: number
  p: number | null
  p_ci: number[] | null
  loglik: number
  aic: number
  converged: boolean
  identifiable: boolean
}

export interface BLife {
  q: number
  status: LifeStatus
  /** 기간 단위의 경과 — 「이르지 않음」 이면 없다(지어내지 않는다). */
  age: number | null
  ci: number[] | null
  age_days: number | null
  /** 결국 고장 나는 것들 중 q 가 고장 날 때까지 — 결함 모형에서만. */
  conditional_age: number | null
  extrapolation: number | null
  observed_age: number | null
}

export interface LifePoint {
  age: number
  at_risk: number
  failures: number
  hazard: number
  observed: number
  observed_low: number | null
  observed_high: number | null
  fitted: number | null
  cohorts: number
}

export interface LifeCurvePoint {
  age: number
  standard: number | null
  defective: number | null
}

export interface LifeCohort {
  cohort: string
  label: string
  units: number
  failures: number
  horizon: number
  drill: Drill
}

export interface LifeResult extends AnalysisHeader {
  basis: 'records' | 'first_visits'
  time_unit: string
  units: number
  failures: number
  cohorts_used: number
  max_age: number
  reached: number
  fits: LifeFit[]
  chosen: LifeModel | null
  lrt_statistic: number | null
  lrt_p_value: number | null
  b_lives: BLife[]
  points: LifePoint[]
  curve: LifeCurvePoint[]
  cohort_rows: LifeCohort[]
  gof_chi2: number | null
  gof_df: number | null
  gof_p_value: number | null
  max_rel_dev: number | null
}

// --- ③ 관리도 ------------------------------------------------------------------

export interface ControlRule {
  number: number
  label: string
}

export interface ControlPoint {
  when: string
  label: string
  count: number
  exposure: number | null
  rate: number | null
  lcl: number | null
  ucl: number | null
  z: number | null
  closed: boolean
  baseline: boolean
  signals: number[]
  drill: Drill
}

export interface ControlChart {
  key: string | null
  label: string
  center: number | null
  sigma_z: number | null
  sigma_z_raw: number | null
  subgroups: number
  baseline_points: number
  signals: number
  total: number
  points: ControlPoint[]
}

export interface ControlResult extends AnalysisHeader {
  axis: 'period' | 'cohort'
  window: number | null
  kind: 'u' | 'c'
  per: number
  split: string | null
  split_label: string | null
  baseline_to: string | null
  rules: ControlRule[]
  charts: ControlChart[]
  other_groups: number
}

// --- ⑩ 계절 · 변화점 -------------------------------------------------------------

export interface Season {
  season: number
  label: string
  index: number
}

export interface Segment {
  start: string
  stop: string
  label: string
  level: number
  count: number
  points: number
}

export interface Change {
  at: string
  label: string
  before: number
  after: number
  ratio: number | null
  ratio_ci: number[] | null
  provisional: boolean
}

export interface ChangesPoint {
  when: string
  label: string
  count: number
  exposure: number | null
  rate: number | null
  seasonal: number | null
  adjusted: number | null
  level: number | null
  closed: boolean
  drill: Drill
}

export interface ChangesResult extends AnalysisHeader {
  axis: 'period' | 'cohort'
  window: number | null
  kind: 'rate' | 'count'
  per: number
  season_length: number
  seasonal: Season[]
  seasonal_p_value: number | null
  dispersion: number | null
  penalty: number | null
  min_segment: number
  changes: Change[]
  segments: Segment[]
  points: ChangesPoint[]
}

// --- ④ 순차 검정 ---------------------------------------------------------------

export type SprtDecision = 'continue' | 'worse' | 'not_worse'

export interface ReferenceRate {
  age: number
  rate: number
  units: number
  records: number
}

export interface SprtLook {
  k: number
  when: string
  label: string
  observed: number
  expected: number
  llr: number
  smr: number | null
  smr_low: number | null
  smr_high: number | null
  decision: SprtDecision
  after_decision: boolean
}

export interface SprtCohort {
  cohort: string
  label: string
  units: number
  observed: number
  expected: number
  drill: Drill
}

export interface SprtResult extends AnalysisHeader {
  dim: string
  dim_label: string
  target: string
  target_label: string
  reference: string
  reference_label: string
  rho: number
  alpha: number
  beta: number
  upper: number
  lower: number
  decision: SprtDecision
  decided_at: string | null
  observed: number
  expected: number
  llr: number
  smr: number | null
  smr_low: number | null
  smr_high: number | null
  to_not_worse: number | null
  to_worse: number | null
  periods_to_not_worse: number | null
  periods_to_worse: number | null
  reference_reach: number
  reference_rates: ReferenceRate[]
  looks: SprtLook[]
  cohort_rows: SprtCohort[]
}

// --- ⑥ 재방문 위험 요인 ----------------------------------------------------------

export interface LogitLevel {
  key: string | null
  label: string
  /** 재방문 창이 닫힌 기록 수(예 + 아니오). */
  count: number
  yes: number
  rate: number | null
  /** 기준 수준 대비 — 기준 수준은 1, 불안정하면 없다. */
  odds_ratio: number | null
  ci: number[] | null
  reference: boolean
  /** 「그 밖」 이면 모인 원래 값의 수. */
  pooled: number
  unstable: boolean
}

export interface LogitFactor {
  name: string
  label: string
  chi2: number
  df: number
  p_value: number
  levels: LogitLevel[]
}

export interface LogitResult extends AnalysisHeader {
  within_days: number
  repeat_dim: string
  records: number
  yes: number
  no: number
  rate: number | null
  /** 모든 요인이 기준 수준일 때의 재방문 확률. */
  baseline_rate: number | null
  factors: LogitFactor[]
  deviance: number | null
  null_deviance: number | null
  auc: number | null
  converged: boolean | null
}
