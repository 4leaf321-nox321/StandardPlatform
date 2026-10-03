/**
 * 지표 API — 기록을 미리 세어 둔 값(ADR 0013).
 *
 * 정의는 시스템 관리자가 만들고, 값은 로그인한 누구나 **보이는 것만** 더해 읽는다. 응답마다
 * 계산 시각 · 겹침 · 못 묶은 수 · 잘림이 실려 있다 — 화면은 그것을 숨기지 않는다.
 */

import type { Job } from '@/modules/jobs/api'
import { api } from '@/shared/api/client'

export type Grain = 'day' | 'week' | 'month' | 'quarter' | 'year'
export type Measure = 'count' | 'sum' | 'avg' | 'min' | 'max'

export interface TimeAxis {
  /** `properties.<날짜 칸>` — 자기 타입의 날짜 칸만. */
  address: string
  grain: Grain
}

export interface Dimension {
  name: string
  /** 통계 · 조건과 같은 주소 — `properties.<칸>` · `ref.<칸>.<칸>` · `out.<관계>` … 걸음 셋까지. */
  address: string
  grain?: Grain | null
}

export interface MetricFilter {
  field: string
  op: string
  value: string
}

export interface Denominator {
  metric: string
  /** 양쪽에 같은 이름으로 있는 기준 — 그 값끼리 짝짓는다. */
  on: string[]
  /** `period`(기간끼리) · `cohort`(분모의 기간 = 분자의 코호트) · null(기간 없이 전부 합). */
  time: 'period' | 'cohort' | null
  /** 비율에 곱할 수 — 100 이면 %. */
  per: number
}

export interface MetricSpec {
  measure: Measure
  measure_field?: string | null
  time?: TimeAxis | null
  cohort?: TimeAxis | null
  dimensions: Dimension[]
  filters: MetricFilter[]
  denominator?: Denominator | null
  settle_days: number
  /**
   * 같은 제품의 방문 — 있으면 기준 주소 `visit.number`(차례) · `visit.repeat`(정한 일수 안
   * 재방문)를 쓸 수 있다(ADR 0014).
   */
  visits?: { key: string; within_days: number } | null
}

export interface MetricDim {
  name: string
  address: string
  label: string
  kind: string
  multi: boolean
  grain: string | null
  /** 계획에서만 — 서로 다른 값의 수. */
  distinct?: number | null
}

/** 이 지표에 되는 분석 — 안 되면 그 이유(ADR 0014). */
export interface AnalysisAvail {
  recipe: string
  label: string
  ok: boolean
  reason: string | null
}

export interface Metric {
  id: string
  slug: string
  label: string
  description: string
  source_type_slug: string
  source_type_label: string
  spec: MetricSpec
  interval_hours: number
  is_active: boolean
  /** 한 기록이 여러 셀에 든다 — 셀의 합이 기록 수보다 크다. */
  overlap: boolean
  measure_label: string
  grain: string | null
  cohort_grain: string | null
  dims: MetricDim[]
  /** 정의가 지금 안 지어지는 이유 — 있으면 계산이 실패한다. */
  broken: string | null
  current_run_id: string | null
  last_run_at: string | null
  last_status: string | null
  last_error: string | null
  cells: number
  stale: boolean
  /** 되는 분석과 안 되는 이유 — 서버가 정의를 보고 판단한다. */
  analyses: AnalysisAvail[]
  created_at: string
  updated_at: string
}

export interface MetricWrite {
  slug: string
  label: string
  description?: string
  source_type_slug: string
  spec: MetricSpec
  interval_hours?: number
  is_active?: boolean
}

export interface MetricPatch {
  label?: string
  description?: string
  spec?: MetricSpec
  interval_hours?: number
  is_active?: boolean
}

export interface MetricPlan {
  ok: boolean
  errors: string[]
  warnings: string[]
  rows: number | null
  estimated_cells: number | null
  overlap: boolean
  dims: MetricDim[]
  period_from: string | null
  period_to: string | null
}

export interface MetricSaved {
  metric: Metric
  job: Job | null
}

export interface MetricRun {
  id: string
  job_id: string | null
  status: 'running' | 'ok' | 'failed'
  watermark: string | null
  started_at: string
  finished_at: string | null
  rows: number
  cells: number
  error: string | null
  stats: Record<string, number>
}

/** 셀을 이룬 기록의 목록 조건 — `/o/<타입>?<params>` 에 그대로. */
export interface Drill {
  type_slug: string
  params: Record<string, string>
  /** 조건으로 못 적은 축 — 비어 있지 않으면 목록의 수가 셀의 수보다 클 수 있다. */
  partial: string[]
}

export interface DenominatorInfo {
  metric: string
  label: string
  measure: Measure
  measure_label: string
  time: 'period' | 'cohort' | null
  per: number
  /** 분모가 없거나 0 이라 비율을 못 낸 셀 수. */
  missing: number
  truncated: boolean
}

/** 모든 읽기 응답의 머리 — 계산 시각 · 겹침 · 못 묶은 수 · 잘림. */
export interface ReadHeader {
  slug: string
  label: string
  measure: Measure
  measure_label: string
  grain: string | null
  cohort_grain: string | null
  settle_days: number
  computed_at: string | null
  watermark: string | null
  stale: boolean
  overlap: boolean
  unbucketed: number
  unbucketed_cohort: number
  negative_age: number
  truncated: boolean
  denominator: DenominatorInfo | null
}

export interface MetricCell {
  dims: Record<string, string | null>
  labels: Record<string, string>
  period: string | null
  period_label: string | null
  cohort: string | null
  cohort_label: string | null
  age: number | null
  count: number
  value_count: number
  sum: number | null
  min: number | null
  max: number | null
  avg: number | null
  value: number | null
  ratio: number | null
  denominator: number | null
  closed: boolean | null
  drill: Drill
}

export interface MetricTable extends ReadHeader {
  dims: string[]
  by: string[]
  cells: MetricCell[]
  total_count: number
  total_value: number | null
}

export interface MetricPoint {
  period: string
  label: string
  count: number
  value: number | null
  ratio: number | null
  prev: number | null
  yoy: number | null
  closed: boolean
  drill: Drill
}

export interface MetricLine {
  key: string | null
  label: string
  points: MetricPoint[]
}

export interface MetricSeries extends ReadHeader {
  split: string | null
  lines: MetricLine[]
  lines_truncated: boolean
}

export interface CohortCell {
  age: number
  count: number
  value: number | null
  cumulative: number | null
  ratio: number | null
  closed: boolean
  drill: Drill
}

export interface CohortRow {
  cohort: string
  label: string
  denominator: number | null
  cells: CohortCell[]
}

export interface MetricCohort extends ReadHeader {
  cumulative: boolean
  ages: number[]
  rows: CohortRow[]
}

export interface DimValue {
  value: string | null
  label: string
  count: number
}

export interface DimValues {
  name: string
  label: string
  kind: string
  values: DimValue[]
  truncated: boolean
}

export interface ReadOptions {
  /** 묶을 기준 이름들. */
  dims?: string[]
  /** `period` · `cohort` · `age` 중 묶을 것. */
  by?: ('period' | 'cohort' | 'age')[]
  /** 기준 값으로 거르기 — null 은 「(비어 있음)」. */
  filters?: Record<string, string | null>
  period_from?: string
  /** 이 날 **앞까지**. */
  period_to?: string
  cohort_from?: string
  cohort_to?: string
}

function readParams(opts: ReadOptions, extra: Record<string, string | undefined> = {}) {
  const params = new URLSearchParams()
  if (opts.dims?.length) params.set('dims', opts.dims.join(','))
  if (opts.by?.length) params.set('by', opts.by.join(','))
  for (const key of ['period_from', 'period_to', 'cohort_from', 'cohort_to'] as const) {
    const value = opts[key]
    if (value) params.set(key, value)
  }
  for (const [name, value] of Object.entries(opts.filters ?? {})) {
    params.set(`d.${name}`, value ?? '')
  }
  for (const [key, value] of Object.entries(extra)) {
    if (value !== undefined && value !== '') params.set(key, value)
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

export const metricsApi = {
  list: () => api.get<Metric[]>('/metrics'),
  get: (slug: string) => api.get<Metric>(`/metrics/${slug}`),
  /** 저장하지 않고 지어만 본다 — 오류 전부 · 경고 · 어림한 셀 수. */
  plan: (body: { slug?: string | null; source_type_slug: string; spec: MetricSpec }) =>
    api.post<MetricPlan>('/metrics/plan', body),
  create: (body: MetricWrite, recompute = false) =>
    api.post<MetricSaved>(`/metrics${recompute ? '?recompute=true' : ''}`, body),
  update: (slug: string, body: MetricPatch, recompute = false) =>
    api.patch<MetricSaved>(`/metrics/${slug}${recompute ? '?recompute=true' : ''}`, body),
  remove: (slug: string) => api.delete<void>(`/metrics/${slug}`),
  /** 다시 센다 — 작업이 된다. 같은 지표의 작업이 줄에 있으면 그것을 돌려준다. */
  recompute: (slug: string) => api.post<Job>(`/metrics/${slug}/recompute`),
  runs: (slug: string) => api.get<MetricRun[]>(`/metrics/${slug}/runs`),
  values: (slug: string, opts: ReadOptions = {}) =>
    api.get<MetricTable>(`/metrics/${slug}/values${readParams(opts)}`),
  series: (slug: string, opts: ReadOptions & { split?: string } = {}) =>
    api.get<MetricSeries>(`/metrics/${slug}/series${readParams(opts, { split: opts.split })}`),
  cohort: (slug: string, opts: ReadOptions & { cumulative?: boolean } = {}) =>
    api.get<MetricCohort>(
      `/metrics/${slug}/cohort${readParams(opts, { cumulative: opts.cumulative ? 'true' : undefined })}`,
    ),
  dims: (slug: string, name: string, q?: string) =>
    api.get<DimValues>(`/metrics/${slug}/dims${readParams({}, { name, q })}`),
  /**
   * 분석 — 세어 둔 셀 위의 통계(ADR 0014). `options` 는 레시피마다의 질의, 기준 거르기는
   * 읽기와 같은 `filters`. 빈 값은 보내지 않는다.
   */
  analysis: <T>(
    slug: string,
    recipe: string,
    options: Record<string, string | number | boolean | null | undefined>,
    opts: ReadOptions = {},
  ) => {
    const extra: Record<string, string | undefined> = {}
    for (const [key, value] of Object.entries(options)) {
      if (value === null || value === undefined || value === '') continue
      extra[key] = typeof value === 'boolean' ? (value ? 'true' : 'false') : String(value)
    }
    return api.get<T>(`/metrics/${slug}/analysis/${recipe}${readParams(opts, extra)}`)
  },
}
