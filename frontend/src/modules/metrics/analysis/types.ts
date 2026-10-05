/**
 * 분석의 응답 — 서버 `metrics/recipes/schemas.py` 와 같은 모양(ADR 0014).
 *
 * 머리는 지표 읽기와 같고(계산 시각 · 닫힘 · 겹침 · 분모), 그 위에 방법 · 요청 · 주의 · 뺀 것 ·
 * 보이는 몫이 붙는다. 화면은 주의(`caveats`)를 숫자보다 먼저, 그대로 보인다.
 */

import type { Drill, ReadHeader } from '@/modules/metrics/api'

export type Recipe =
  | 'pareto'
  | 'life'
  | 'control'
  | 'changes'
  | 'sprt'
  | 'groups'
  | 'logit'
  | 'assoc'

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

export interface ParetoCompareItem {
  key: string | null
  label: string
  count_a: number
  share_a: number
  count_b: number
  share_b: number
  /** 뒤 기간 쪽 수정 잔차 — 양수면 뒤 기간에 몫이 커졌다. */
  residual: number
  /** |잔차| > 3 — 몫이 달라진 값. */
  notable: boolean
}

export interface ParetoCompare {
  label_a: string
  label_b: string
  total_a: number
  total_b: number
  chi2: number
  df: number
  p_value: number
  items: ParetoCompareItem[]
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
  comparison?: ParetoCompare | null
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
  /** u — 대수당 비율, c — 건수, p — 조건 비율(같은 기록 중 몫, %). */
  kind: 'u' | 'c' | 'p'
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

// --- 값마다 훑기(④ · ⑩) -------------------------------------------------------------

export interface ScanPeriod {
  when: string
  label: string
  closed: boolean
}

/** ⑩ 값 하나(증상 등)의 변화점 — 같은 대수로 나눈 그 값의 비율에서. */
export interface ChangesScanItem {
  key: string | null
  label: string
  total: number
  points: number
  /** 마지막 변화점의 방향 — 없으면 flat. */
  direction: 'up' | 'down' | 'flat'
  last: Change | null
  changes: Change[]
  /** 끝 구간의 수준(계절을 뺀 비율). */
  level_now: number | null
  dispersion: number | null
  seasonal: boolean
  /** 계산하지 못한 까닭. */
  note: string | null
  drill: Drill
}

export interface ChangesScanResult extends AnalysisHeader {
  axis: 'period' | 'cohort'
  window: number | null
  kind: 'rate' | 'count'
  per: number
  by: string
  by_label: string
  scanned: number
  other_values: number
  /** 여럿을 함께 보느라 변화점 하나에 더한 벌점(2·ln K). */
  extra_penalty: number
  periods: ScanPeriod[]
  items: ChangesScanItem[]
}

/** ④ 값 하나(증상 등)로 거른 새 모델 vs 전작. */
export interface SprtScanItem {
  key: string
  label: string
  decision: 'continue' | 'worse' | 'not_worse'
  decided_at: string | null
  observed: number
  expected: number
  llr: number
  smr: number | null
  smr_low: number | null
  smr_high: number | null
  periods_to_worse: number | null
  periods_to_not_worse: number | null
  /** 전작에 없던 값 — 기대가 0 이라 비를 낼 수 없다. */
  new: boolean
}

export interface SprtScanResult extends AnalysisHeader {
  dim: string
  dim_label: string
  by: string
  by_label: string
  target: string
  target_label: string
  reference: string
  reference_label: string
  rho: number
  alpha: number
  /** 값마다의 유의수준 — α 를 값의 수로 나눴다(본페로니). */
  alpha_each: number
  beta: number
  scanned: number
  other_values: number
  items: SprtScanItem[]
  skipped: string[]
}

// --- ⑤ 집단 비교 ---------------------------------------------------------------

export interface GroupRow {
  key: string | null
  label: string
  count: number
  exposure: number
  /** 그대로 비율(per 대당) — 작은 집단에서는 우연으로 크게 흔들린다. */
  rate: number | null
  /** 줄인 비율 — 대수가 작을수록 전체 쪽으로 끌린 값. 순위 · 견줌은 이것으로. */
  shrunk: number | null
  shrunk_low: number | null
  shrunk_high: number | null
  /** 줄인 정도 0~1. */
  shrinkage: number
  /** 줄인 비율 / 전체 비율. */
  ratio: number | null
  p_value: number
  q_value: number
  /** q < 0.05 인 집단 — 전체보다 높음 · 낮음. */
  flag: 'high' | 'low' | null
  drill: Drill
}

export interface GroupsResult extends AnalysisHeader {
  dim: string
  dim_label: string
  axis: 'period' | 'cohort'
  window: number | null
  per: number
  /** 전체 비율(per 대당). */
  pooled: number | null
  groups: number
  heterogeneity_chi2: number | null
  heterogeneity_df: number
  heterogeneity_p: number | null
  /** 집단 사이 참 비율의 흔들림 τ / 전체 비율. */
  spread: number | null
  flagged: number
  rows: GroupRow[]
  other_groups: number
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

// --- ⑨ 연관 · 묶음 ---------------------------------------------------------------

export interface AssocLabel {
  key: string
  label: string
}

export interface AssocPair {
  row: AssocLabel
  col: AssocLabel
  count: number
  expected: number
  /** 향상도 — count / expected. */
  lift: number
  /** 행의 값 안에서 이 열의 몫. */
  share: number
  p_value: number
  q_value: number
  drill: Drill
}

/** 원인분산도 한 줄 — 이 행(증상)의 원인(열)이 얼마나 갈렸나. */
export interface AssocDispersion {
  row: AssocLabel
  count: number
  /** 나온 원인 값 수. */
  causes: number
  /** 유효 원인 수 1/Σ몫² — 원인이 몇 개에 고르게 퍼진 것과 같은가(1 이면 하나에 몰림). */
  effective: number
  top: AssocLabel
  top_share: number
}

export interface AssocCluster {
  members: AssocLabel[]
  /** 그 묶음을 가르는 열. */
  top: AssocLabel[]
  count: number
}

export interface AssocPoint {
  key: string
  label: string
  x: number
  y: number
  mass: number
}

export interface AssocResult extends AnalysisHeader {
  rows: string
  rows_label: string
  cols: string
  cols_label: string
  basis: 'records' | 'occurrences'
  total: number
  row_values: number
  col_values: number
  tested: number
  pairs: AssocPair[]
  /** 원인이 많이 갈린 행부터. */
  dispersion?: AssocDispersion[]
  /** 견줄 기준 — 모든 행을 합친 열 몫의 유효 개수. */
  overall_effective?: number | null
  clusters: AssocCluster[]
  silhouette: number | null
  map_rows: AssocPoint[]
  map_cols: AssocPoint[]
  map_explained: number | null
}
