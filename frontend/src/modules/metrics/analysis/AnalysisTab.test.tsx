/**
 * 분석 탭이 지키는 것 — **안 되는 분석은 이유와 함께 보이고, 이르지 않는 B수명은 지어내지
 * 않으며, 「아직」 을 「문제없음」 으로 말하지 않는다.** 숫자마다 건 보기로 돌아간다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Metric, ReadHeader } from '@/modules/metrics/api'
import AnalysisTab from '@/modules/metrics/analysis/AnalysisTab'
import { decisionText } from '@/modules/metrics/analysis/SprtView'
import type { LifeResult, LogitResult, ParetoResult } from '@/modules/metrics/analysis/types'

const metricsApi = vi.hoisted(() => ({ analysis: vi.fn(), dims: vi.fn() }))
vi.mock('@/modules/metrics/api', () => ({ metricsApi }))
vi.mock('@/shared/charts', () => ({ Chart: () => <div>차트</div> }))
vi.mock('@/shared/charts/LazyPlot', () => ({ LazyPlot: () => <div>그림</div> }))

const METRIC = {
  slug: 'cases',
  label: '인입',
  grain: 'month',
  cohort_grain: 'month',
  dims: [
    {
      name: 'symptom',
      address: 'properties.symptom',
      label: '증상',
      kind: 'enum',
      multi: false,
      grain: null,
    },
  ],
  spec: {
    measure: 'count',
    dimensions: [],
    filters: [],
    denominator: { metric: 'sales', on: ['base_model'], time: 'cohort', per: 100 },
    settle_days: 30,
  },
  analyses: [
    { recipe: 'pareto', label: '파레토 · 집중도', ok: true, reason: null },
    { recipe: 'life', label: '수명 · B수명', ok: true, reason: null },
    {
      recipe: 'changes',
      label: '계절 · 변화점',
      ok: false,
      reason: '건수 지표에서만 됩니다 — 변화점은 건수 · 비율의 수준을 봅니다.',
    },
  ],
} as unknown as Metric

const HEADER = {
  slug: 'cases',
  label: '인입',
  measure: 'count',
  measure_label: '건수',
  grain: 'month',
  cohort_grain: 'month',
  settle_days: 30,
  computed_at: '2026-10-04T02:30:00+09:00',
  watermark: '2026-10-04T02:30:00+09:00',
  stale: false,
  overlap: false,
  unbucketed: 0,
  unbucketed_cohort: 0,
  negative_age: 0,
  truncated: false,
  denominator: null,
} satisfies ReadHeader

const LIFE: LifeResult = {
  ...HEADER,
  recipe: 'life',
  method: '와이블 최대우도 · 코호트 구간 중도절단 · 표준/결함 v1',
  params: {},
  run_id: 'r1',
  caveats: [
    {
      code: 'missing_denominator',
      level: 'warn',
      message: '판매 대수가 없는 코호트를 뺐습니다.',
      count: 5,
    },
    { code: 'records_not_units', level: 'info', message: '기록 수를 고장 대수로 봅니다.', count: null },
  ],
  excluded: { missing_denominator: 5 },
  visible_share: 1,
  basis: 'records',
  time_unit: 'month',
  units: 700000,
  failures: 25000,
  cohorts_used: 35,
  max_age: 34,
  reached: 0.043,
  fits: [
    {
      model: 'defective',
      beta: 1.49,
      beta_ci: [1.45, 1.53],
      eta: 13.1,
      eta_ci: [12.8, 13.4],
      eta_days: 399,
      p: 0.044,
      p_ci: [0.0435, 0.0462],
      loglik: -1,
      aic: 8,
      converged: true,
      identifiable: true,
    },
  ],
  chosen: 'defective',
  lrt_statistic: 1116,
  lrt_p_value: 0,
  b_lives: [
    {
      q: 0.01,
      status: 'observed',
      age: 5.21,
      ci: [5.05, 5.38],
      age_days: 158,
      conditional_age: 2.1,
      extrapolation: null,
      observed_age: 5.26,
    },
    {
      q: 0.1,
      status: 'unreachable',
      age: null,
      ci: null,
      age_days: null,
      conditional_age: 3.3,
      extrapolation: null,
      observed_age: null,
    },
  ],
  points: [],
  curve: [],
  cohort_rows: [
    {
      cohort: '2026-01-01',
      label: '2026-01',
      units: 20000,
      failures: 812,
      horizon: 34,
      drill: { type_slug: 'svc_case', params: { 'f.sold.gte': '2026-01-01' }, partial: [] },
    },
  ],
  gof_chi2: null,
  gof_df: null,
  gof_p_value: null,
  max_rel_dev: null,
}

const PARETO: ParetoResult = {
  ...HEADER,
  recipe: 'pareto',
  method: '파레토 · HHI · 지니 · CR v1',
  params: {},
  run_id: 'r1',
  caveats: [],
  excluded: {},
  visible_share: 1,
  dim: 'symptom',
  dim_label: '증상',
  basis: 'records',
  total: 8,
  empty_count: 0,
  empty_value: 0,
  items: [
    {
      key: '소음',
      label: '소음',
      value: 5,
      count: 5,
      share: 0.625,
      cumulative: 0.625,
      cls: 'A',
      ratio: null,
      drill: {
        type_slug: 'svc_case',
        params: { 'f.symptom.eq': '소음' },
        partial: [],
      },
    },
  ],
  other_categories: 0,
  other_value: 0,
  concentration: null,
  trend: [],
}

function show() {
  return render(
    <MemoryRouter>
      <AnalysisTab metric={METRIC} read={{ filters: {} }} />
    </MemoryRouter>,
  )
}

describe('분석 탭', () => {
  beforeEach(() => vi.clearAllMocks())

  it('안 되는 분석은 누를 수 없고 그 이유를 적는다', async () => {
    metricsApi.analysis.mockResolvedValue(LIFE)
    show()
    const changes = screen.getByRole('button', { name: '계절 · 변화점' })
    expect(changes).toBeDisabled()
    expect(screen.getByText(/계절 · 변화점 — 건수 지표에서만 됩니다/)).toBeInTheDocument()
    // 물음 번호 차례 — 수명이 파레토보다 먼저, 처음 되는 것이 골라져 있다.
    const buttons = screen.getAllByRole('button').map((one) => one.textContent)
    expect(buttons.slice(0, 2)).toEqual(['수명 · B수명', '파레토 · 집중도'])
    await waitFor(() => expect(metricsApi.analysis).toHaveBeenCalled())
    expect(metricsApi.analysis.mock.calls[0][1]).toBe('life')
  })

  it('이르지 않는 B10 은 값 대신 이유를, 주의는 숫자보다 먼저', async () => {
    metricsApi.analysis.mockResolvedValue(LIFE)
    show()
    expect(
      await screen.findByText('B10: 판매의 4.4% 만 결국 고장 나 10% 에 이르지 않습니다.'),
    ).toBeInTheDocument()
    expect(screen.getByText(/결국 고장 나는 것들 중 10% 는 3.3개월/)).toBeInTheDocument()
    expect(screen.getByText('B1: 5.2개월 (관측 안)')).toBeInTheDocument()
    const warn = screen.getByText('판매 대수가 없는 코호트를 뺐습니다. (5)')
    expect(warn).toBeInTheDocument()
    expect(screen.getByText(/뺀 기록: 분모 없음 5/)).toBeInTheDocument()
    // 코호트 줄의 수는 그 기록 목록으로 간다.
    expect(screen.getByRole('link', { name: '812건 보기' })).toHaveAttribute(
      'href',
      '/o/svc_case?f.sold.gte=2026-01-01',
    )
  })

  it('파레토를 고르면 몫과 건 보기를 보인다', async () => {
    metricsApi.analysis.mockImplementation((_slug: string, recipe: string) =>
      Promise.resolve(recipe === 'pareto' ? PARETO : LIFE),
    )
    show()
    await userEvent.click(screen.getByRole('button', { name: '파레토 · 집중도' }))
    expect(await screen.findByRole('link', { name: '5건 보기' })).toHaveAttribute(
      'href',
      '/o/svc_case?f.symptom.eq=%EC%86%8C%EC%9D%8C',
    )
    expect(screen.getAllByText('62.5%').length).toBeGreaterThan(0)
  })

  it('순차 검정의 결론 말은 「아직」 을 「문제없음」 으로 읽히지 않게', () => {
    expect(decisionText('continue', 1.5)).toBe(
      '아직 — 결론이 서지 않았습니다(문제없다는 뜻이 아닙니다).',
    )
    expect(decisionText('not_worse', 1.5)).toContain('같다는 뜻은 아닙니다')
    expect(decisionText('worse', 1.5)).toBe('나쁨 — 전작보다 1.5배 쪽입니다.')
  })

  it('위험 요인은 기준 대비 오즈비, 모은 값과 불안정한 값은 그렇게 적는다', async () => {
    const VISITS = {
      ...METRIC,
      dims: [
        { name: 'visit_no', address: 'visit.number', label: '방문 차례', kind: 'visit',
          multi: false, grain: null },
        { name: 'again', address: 'visit.repeat', label: '90일 안 재방문', kind: 'visit',
          multi: false, grain: null },
        { name: 'factory', address: 'properties.factory', label: '공장', kind: 'text',
          multi: false, grain: null },
      ],
      analyses: [
        { recipe: 'life', label: '수명 · B수명', ok: true, reason: null },
        { recipe: 'logit', label: '재방문 위험 요인', ok: true, reason: null },
      ],
    } as unknown as Metric
    const LOGIT: LogitResult = {
      ...HEADER,
      recipe: 'logit',
      method: '묶인 이항 로지스틱(IRLS) · 왈드 구간 · 요인별 LR 검정 v1',
      params: {},
      run_id: 'r1',
      caveats: [],
      excluded: { open: 30 },
      visible_share: 1,
      within_days: 90,
      repeat_dim: 'again',
      records: 3000,
      yes: 350,
      no: 2650,
      rate: 350 / 3000,
      baseline_rate: 0.1,
      factors: [
        {
          name: 'factory',
          label: '공장',
          chi2: 88.1,
          df: 2,
          p_value: 0,
          levels: [
            { key: 'F1', label: 'F1', count: 1000, yes: 100, rate: 0.1, odds_ratio: 1,
              ci: null, reference: true, pooled: 0, unstable: false },
            { key: 'F2', label: 'F2', count: 1000, yes: 200, rate: 0.2, odds_ratio: 2.25,
              ci: [1.8, 2.8], reference: false, pooled: 0, unstable: false },
            { key: '__other__', label: '그 밖', count: 40, yes: 0, rate: 0, odds_ratio: null,
              ci: null, reference: false, pooled: 2, unstable: true },
          ],
        },
      ],
      deviance: 1,
      null_deviance: 90,
      auc: 0.61,
      converged: true,
    }
    metricsApi.analysis.mockImplementation((_slug: string, recipe: string) =>
      Promise.resolve(recipe === 'logit' ? LOGIT : LIFE),
    )
    render(
      <MemoryRouter>
        <AnalysisTab metric={VISITS} read={{ filters: {} }} />
      </MemoryRouter>,
    )
    // 방문 차례가 있으면 수명은 첫 방문으로 센다.
    await waitFor(() => expect(metricsApi.analysis).toHaveBeenCalled())
    const lifeCall = metricsApi.analysis.mock.calls.find((one) => one[1] === 'life')
    expect(lifeCall?.[2]).toMatchObject({ basis: 'first_visits' })
    await userEvent.click(screen.getByRole('button', { name: '재방문 위험 요인' }))
    expect(await screen.findByText('2.25')).toBeInTheDocument()
    expect(screen.getByText('(기준)')).toBeInTheDocument()
    expect(screen.getByText('불안정')).toBeInTheDocument()
    expect(screen.getByText(/— 값 2개/)).toBeInTheDocument()
    const logitCall = metricsApi.analysis.mock.calls.find((one) => one[1] === 'logit')
    expect(logitCall?.[2]).toMatchObject({ factors: 'visit_no' })
  })
})
