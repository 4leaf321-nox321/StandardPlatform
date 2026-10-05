/**
 * 분석 탭이 지키는 것 — **안 되는 분석은 이유와 함께 보이고, 이르지 않는 B수명은 지어내지
 * 않으며, 「아직」 을 「문제없음」 으로 말하지 않는다.** 숫자마다 건 보기로 돌아간다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Metric, ReadHeader } from '@/modules/metrics/api'
import AnalysisTab from '@/modules/metrics/analysis/AnalysisTab'
import { decisionText } from '@/modules/metrics/analysis/SprtView'
import type {
  AssocResult,
  ChangesScanResult,
  CoverageResult,
  CutinResult,
  ForecastResult,
  GroupsResult,
  LifeResult,
  LogitResult,
  ParetoResult,
  RecurrenceResult,
  SprtScanResult,
} from '@/modules/metrics/analysis/types'

const metricsApi = vi.hoisted(() => ({
  analysis: vi.fn(),
  dims: vi.fn(),
  createAlert: vi.fn(),
  coverageWays: vi.fn(),
}))
vi.mock('@/modules/metrics/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/metrics/api')>()),
  metricsApi,
}))
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

  it('연관은 짝과 묶음을, 파레토는 두 기간의 몫 변화를 적는다', async () => {
    const TWO = {
      ...METRIC,
      dims: [
        ...METRIC.dims,
        { name: 'part', address: 'properties.parts', label: '교체 부품', kind: 'object_ref',
          multi: true, grain: null },
      ],
      analyses: [
        { recipe: 'pareto', label: '파레토 · 집중도', ok: true, reason: null },
        { recipe: 'assoc', label: '연관 · 묶음', ok: true, reason: null },
      ],
    } as unknown as Metric
    const ASSOC: AssocResult = {
      ...HEADER,
      recipe: 'assoc',
      method: '향상도 · 초기하 정확 검정 · BH · PPMI 코사인 평균 연결 · 대응 분석 v1',
      params: {},
      run_id: 'r1',
      caveats: [],
      excluded: {},
      visible_share: 1,
      rows: 'symptom',
      rows_label: '증상',
      cols: 'part',
      cols_label: '교체 부품',
      basis: 'occurrences',
      total: 352,
      row_values: 4,
      col_values: 4,
      tested: 16,
      pairs: [
        {
          row: { key: 'S-a', label: '소음' },
          col: { key: 'P-1', label: '팬' },
          count: 40,
          expected: 22,
          lift: 1.82,
          share: 0.45,
          p_value: 0.0001,
          q_value: 0.0004,
          drill: { type_slug: 'svc_case', params: { 'f.symptom.eq': 'S-a' }, partial: [] },
        },
      ],
      dispersion: [
        { row: { key: 'S-c', label: '발열' }, count: 88, causes: 4, effective: 3.6,
          top: { key: 'P-3', label: '전원부' }, top_share: 0.3 },
      ],
      overall_effective: 4,
      clusters: [
        { members: [{ key: 'S-a', label: '소음' }, { key: 'S-b', label: '진동' }],
          top: [{ key: 'P-1', label: '팬' }], count: 176 },
      ],
      silhouette: 0.71,
      map_rows: [],
      map_cols: [],
      map_explained: null,
    }
    const COMPARED: ParetoResult = {
      ...PARETO,
      comparison: {
        label_a: '2026-01-01 ~ 2026-02-01',
        label_b: '2026-03-01 ~ 2026-04-01',
        total_a: 1000,
        total_b: 1000,
        chi2: 35.2,
        df: 2,
        p_value: 0.000001,
        items: [
          { key: '누수', label: '누수', count_a: 200, share_a: 0.2, count_b: 300, share_b: 0.3,
            residual: 5.2, notable: true },
        ],
      },
    }
    metricsApi.analysis.mockImplementation((_slug: string, recipe: string) =>
      Promise.resolve(recipe === 'assoc' ? ASSOC : COMPARED),
    )
    render(
      <MemoryRouter>
        <AnalysisTab metric={TWO} read={{ filters: {} }} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('늘었다')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '연관 · 묶음' }))
    expect(await screen.findByText('1.82배')).toBeInTheDocument()
    expect(screen.getByText('소음 · 진동')).toBeInTheDocument()
    // 원인분산도 — 원인이 여럿에 갈린 증상이 먼저, 가장 많은 원인과 그 몫까지.
    const spread = screen.getByRole('region', { name: '원인분산도' })
    expect(within(spread).getByText('발열')).toBeInTheDocument()
    expect(within(spread).getByText('3.6')).toBeInTheDocument()
    expect(within(spread).getByText('전원부')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '40건 보기' })).toHaveAttribute(
      'href',
      '/o/svc_case?f.symptom.eq=S-a',
    )
    const assocCall = metricsApi.analysis.mock.calls.find((one) => one[1] === 'assoc')
    expect(assocCall?.[2]).toMatchObject({ rows: 'symptom', cols: 'part' })
  })

  it('링크가 연 순차 검정은 그 인자로 묻고, 경보 저장은 같은 인자를 저장한다', async () => {
    const metric = {
      ...METRIC,
      analyses: [
        { recipe: 'pareto', label: '파레토 · 집중도', ok: true, reason: null },
        { recipe: 'sprt', label: '순차 검정(전작 대비)', ok: true, reason: null },
      ],
    } as unknown as Metric
    metricsApi.dims.mockResolvedValue({ name: 'base_model', values: [], truncated: false })
    metricsApi.analysis.mockResolvedValue({
      ...HEADER,
      recipe: 'sprt',
      method: '포아송 SPRT v1',
      params: {},
      run_id: 'r1',
      excluded: {},
      visible_share: 1,
      caveats: [],
      decision: 'continue',
      decided_at: null,
      target_label: 'S기본',
      reference_label: 'A기본',
      rho: 2,
      looks: [],
      cohort_rows: [],
    })
    metricsApi.createAlert.mockResolvedValue({
      id: 'a1',
      name: '인입 · 순차 검정',
      baseline: {
        run_id: 'r1',
        findings: [{ key: 'worse:S', title: 'S기본: 전작 A기본 보다 나쁨', detail: {}, new: false }],
        notes: [],
      },
    })
    render(
      <MemoryRouter>
        <AnalysisTab
          metric={metric}
          read={{ filters: { symptom: '소음', base_model: 'X' } }}
          initial={{ recipe: 'sprt', params: { target: 'S', reference: 'A', rho: '2' } }}
        />
      </MemoryRouter>,
    )
    // 모델 기준의 거르기는 target · reference 가 대신한다 — 보내지 않는다.
    await waitFor(() =>
      expect(metricsApi.analysis).toHaveBeenCalledWith(
        'cases',
        'sprt',
        { target: 'S', reference: 'A', reference_via: undefined, dim: 'base_model', rho: '2' },
        { filters: { symptom: '소음' } },
      ),
    )
    await userEvent.click(await screen.findByRole('button', { name: /경보 저장/ }))
    expect(screen.getByLabelText('이름')).toHaveValue('인입 · 순차 검정')
    // 전작을 값으로 골랐으면 「최근 출시 모델 전부」 는 없다 — 모델마다 전작이 다르다.
    expect(screen.queryByText(/최근 출시 모델 전부/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(metricsApi.createAlert).toHaveBeenCalledWith('cases', {
        name: '인입 · 순차 검정',
        recipe: 'sprt',
        params: {
          'd.symptom': '소음',
          target: 'S',
          reference: 'A',
          dim: 'base_model',
          rho: '2',
        },
      }),
    )
    expect(await screen.findByText(/은 알리지 않습니다/)).toBeInTheDocument()
    expect(screen.getByText('S기본: 전작 A기본 보다 나쁨')).toBeInTheDocument()
  })

  it('증상마다 훑은 순차 검정은 나빠진 증상부터 보이고, 한 값만 보기로 오간다', async () => {
    const metric = {
      ...METRIC,
      analyses: [{ recipe: 'sprt', label: '순차 검정(전작 대비)', ok: true, reason: null }],
    } as unknown as Metric
    metricsApi.dims.mockResolvedValue({ name: 'base_model', values: [], truncated: false })
    const SCAN: SprtScanResult = {
      ...HEADER,
      recipe: 'sprt',
      method: '포아송 SPRT v1 · 값마다 훑기(본페로니)',
      params: {},
      run_id: 'r1',
      caveats: [],
      excluded: {},
      visible_share: 1,
      dim: 'base_model',
      dim_label: '기본 모델',
      by: 'symptom',
      by_label: '증상',
      target: 'S',
      target_label: 'S기본',
      reference: 'A',
      reference_label: 'A기본',
      rho: 1.5,
      alpha: 0.05,
      alpha_each: 0.05 / 3,
      beta: 0.1,
      scanned: 3,
      other_values: 0,
      skipped: [],
      items: [
        { key: '발열', label: '발열', decision: 'worse', decided_at: '2026-03', observed: 300,
          expected: 100, llr: 9, smr: 3, smr_low: 2.6, smr_high: 3.4, periods_to_worse: null,
          periods_to_not_worse: null, new: false },
        { key: '누수', label: '누수', decision: 'worse', decided_at: '2026-04', observed: 40,
          expected: 0, llr: 6, smr: null, smr_low: null, smr_high: null, periods_to_worse: null,
          periods_to_not_worse: null, new: true },
        { key: '소음', label: '소음', decision: 'not_worse', decided_at: '2026-05',
          observed: 100, expected: 100, llr: -3, smr: 1, smr_low: 0.8, smr_high: 1.2,
          periods_to_worse: null, periods_to_not_worse: null, new: false },
      ],
    }
    metricsApi.analysis.mockImplementation((_slug: string, recipe: string) =>
      Promise.resolve(
        recipe === 'sprt/scan'
          ? SCAN
          : { ...HEADER, recipe: 'sprt', method: 'x', params: {}, run_id: 'r1', excluded: {},
              visible_share: 1, caveats: [], decision: 'worse', decided_at: '2026-03',
              target_label: 'S기본', reference_label: 'A기본', rho: 1.5, looks: [],
              cohort_rows: [] },
      ),
    )
    render(
      <MemoryRouter>
        <AnalysisTab
          metric={metric}
          read={{ filters: {} }}
          initial={{ recipe: 'sprt', params: { target: 'S', reference: 'A', by: 'symptom' } }}
        />
      </MemoryRouter>,
    )
    const table = await screen.findByRole('region', { name: '값마다 훑기' })
    expect(within(table).getByText('나쁨 (2026-03)')).toBeInTheDocument()
    expect(within(table).getByText('전작에 없던 값')).toBeInTheDocument()
    expect(screen.getByText(/3개 중 2개가 전작보다 1.5배 쪽/)).toBeInTheDocument()
    const scanCall = metricsApi.analysis.mock.calls.find((one) => one[1] === 'sprt/scan')
    expect(scanCall?.[2]).toMatchObject({ target: 'S', reference: 'A', by: 'symptom' })

    await userEvent.click(within(table).getAllByRole('button', { name: '이 값만 보기' })[0])
    expect(await screen.findByText(/만 보는 중입니다/)).toBeInTheDocument()
    await waitFor(() =>
      expect(metricsApi.analysis).toHaveBeenCalledWith(
        'cases',
        'sprt',
        expect.objectContaining({ target: 'S' }),
        { filters: { symptom: '발열' } },
      ),
    )
    await userEvent.click(screen.getByRole('button', { name: '훑기로 돌아가기' }))
    expect(await screen.findByRole('region', { name: '값마다 훑기' })).toBeInTheDocument()
  })

  it('증상마다 훑은 변화점은 오른 증상부터 그 시점과 비를 적는다', async () => {
    const metric = {
      ...METRIC,
      analyses: [{ recipe: 'changes', label: '계절 · 변화점', ok: true, reason: null }],
    } as unknown as Metric
    const change = (label: string, ratio: number) => ({
      at: '2028-07-01', label, before: 7.5, after: 7.5 * ratio, ratio, ratio_ci: [1.3, 1.5],
      provisional: false,
    })
    const drill = { type_slug: 'svc_case', params: { 'f.symptom.eq': '발열' }, partial: [] }
    const SCAN: ChangesScanResult = {
      ...HEADER,
      recipe: 'changes',
      method: 'x · 값마다 훑기(벌점 +2·ln K)',
      params: {},
      run_id: 'r1',
      caveats: [],
      excluded: {},
      visible_share: 1,
      axis: 'period',
      window: null,
      kind: 'rate',
      per: 1000,
      by: 'symptom',
      by_label: '증상',
      scanned: 2,
      other_values: 0,
      extra_penalty: 1.4,
      periods: [],
      items: [
        { key: '발열', label: '발열', total: 8000, points: 48, direction: 'up',
          last: change('2028-07', 1.4), changes: [change('2028-07', 1.4)], level_now: 10.5,
          dispersion: 1, seasonal: false, note: null, drill },
        { key: '소음', label: '소음', total: 9600, points: 48, direction: 'flat', last: null,
          changes: [], level_now: 10, dispersion: 1, seasonal: false, note: null, drill },
      ],
    }
    metricsApi.analysis.mockResolvedValue(SCAN)
    render(
      <MemoryRouter>
        <AnalysisTab
          metric={metric}
          read={{ filters: {} }}
          initial={{ recipe: 'changes', params: { by: 'symptom' } }}
        />
      </MemoryRouter>,
    )
    const table = await screen.findByRole('region', { name: '값마다 훑기' })
    expect(within(table).getByText('2028-07부터 올라감')).toBeInTheDocument()
    expect(within(table).getByText('1.4배 (1.3 ~ 1.5)')).toBeInTheDocument()
    expect(within(table).getByText('바뀐 곳 없음')).toBeInTheDocument()
    expect(screen.getByText(/2개 중 1개가 마지막 변화에서 올랐습니다/)).toBeInTheDocument()
    expect(metricsApi.analysis.mock.calls[0][1]).toBe('changes/scan')
  })

  it('집단 비교는 줄인 비율로 세우고, 대수가 작은 집단의 그대로 비율을 곁에 둔다', async () => {
    const metric = {
      ...METRIC,
      dims: [
        { name: 'base_model', address: 'ref.model.base', label: '기본 모델', kind: 'object_ref',
          multi: false, grain: null },
        ...METRIC.dims,
      ],
      analyses: [{ recipe: 'groups', label: '집단 비교', ok: true, reason: null }],
    } as unknown as Metric
    const row = (key: string, extra: Record<string, unknown>) => ({
      key, label: key, count: 0, exposure: 0, rate: 5, shrunk: 5, shrunk_low: 4.5,
      shrunk_high: 5.5, shrinkage: 0.1, ratio: 1, p_value: 0.5, q_value: 0.6, flag: null,
      drill: { type_slug: 'svc_case', params: { 'f.ref.model.base.eq': key }, partial: [] },
      ...extra,
    })
    const GROUPS: GroupsResult = {
      ...HEADER,
      recipe: 'groups',
      method: '포아송 이질성 χ² · … v1',
      params: {},
      run_id: 'r1',
      caveats: [],
      excluded: {},
      visible_share: 1,
      dim: 'base_model',
      dim_label: '기본 모델',
      axis: 'period',
      window: null,
      per: 1000,
      pooled: 6.67,
      groups: 2,
      heterogeneity_chi2: 400,
      heterogeneity_df: 1,
      heterogeneity_p: 0.0000001,
      spread: 0.35,
      flagged: 1,
      rows: [
        row('B3', { count: 2400, exposure: 240000, rate: 10, shrunk: 9.9, ratio: 1.48,
          q_value: 0.000001, flag: 'high' }),
        row('B4', { count: 3, exposure: 150, rate: 20, shrunk: 8.1, shrinkage: 0.84,
          ratio: 1.21, q_value: 0.2 }),
      ] as GroupsResult['rows'],
      other_groups: 0,
    }
    metricsApi.analysis.mockResolvedValue(GROUPS)
    render(
      <MemoryRouter>
        <AnalysisTab metric={metric} read={{ filters: { base_model: 'B1' } }} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('B3 — 높음')).toBeInTheDocument()
    expect(screen.getByText(/집단 사이에 우연보다 큰 차이가 있습니다/)).toBeInTheDocument()
    expect(screen.getByText('84%')).toBeInTheDocument() // B4 는 대부분 줄였다
    // 견주는 기준의 거르기는 빼고 보낸다.
    expect(metricsApi.analysis).toHaveBeenCalledWith(
      'cases',
      'groups',
      expect.objectContaining({ dim: 'base_model' }),
      expect.objectContaining({ filters: {} }),
    )
  })
  it('전후 비교는 적용일을 넣어야 묻고, 결론과 「대책 때문」 이 아니라는 주의를 앞에 둔다', async () => {
    const metric = {
      ...METRIC,
      analyses: [{ recipe: 'cutin', label: '전후 비교', ok: true, reason: null }],
    } as unknown as Metric
    const side = (first: string, last: string, rate: number) => ({
      first, last, subgroups: 6, count: 600, exposure: 120000, rate,
      drill: { type_slug: 'svc_case', params: { 'f.received.gte': '2026-01-01' }, partial: [] },
    })
    const CUTIN: CutinResult = {
      ...HEADER,
      recipe: 'cutin',
      method: '적용일 전후 부분군 비 · … v1',
      params: {},
      run_id: 'r1',
      caveats: [
        {
          code: 'association',
          level: 'info',
          message: '이 결과는 「적용일 뒤에 줄었다 · 늘었다」 이지 「대책 때문에」 가 아닙니다.',
          count: null,
        },
      ],
      excluded: {},
      visible_share: 1,
      at: '2026-07-01',
      axis: 'period',
      window: null,
      per: 1000,
      kind: 'rate',
      before: side('2026-01', '2026-06', 5),
      after: side('2026-07', '2026-12', 3),
      open_after: 0,
      ratio: 0.6,
      ratio_low: 0.54,
      ratio_high: 0.67,
      p_value: 0.000001,
      dispersion: 1,
      effect: 0.2,
      decision: 'reduced',
      more_subgroups: null,
      pre_trend: { change_per_period: 0, p_value: 0.9 },
      points: [],
    }
    metricsApi.analysis.mockResolvedValue(CUTIN)
    render(
      <MemoryRouter>
        <AnalysisTab metric={metric} read={{ filters: { base_model: 'S' } }} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('적용일을 넣으면 견줍니다.')).toBeInTheDocument()
    expect(metricsApi.analysis).not.toHaveBeenCalled()
    await userEvent.type(screen.getByLabelText('적용일'), '2026-07-01')
    expect(await screen.findByText('줄었다')).toBeInTheDocument()
    expect(screen.getByText(/뒤가 앞의 0.6배/)).toBeInTheDocument()
    expect(screen.getByText(/대책 때문에/)).toBeInTheDocument()
    expect(metricsApi.analysis).toHaveBeenLastCalledWith(
      'cases',
      'cutin',
      expect.objectContaining({ at: '2026-07-01', effect: '0.2' }),
      expect.objectContaining({ filters: { base_model: 'S' } }),
    )
  })
  it('클레임 예측은 되짚어 보기를 앞에 두고, 평균과 구간 · 비용을 함께 적는다', async () => {
    const metric = {
      ...METRIC,
      analyses: [{ recipe: 'forecast', label: '클레임 예측', ok: true, reason: null }],
    } as unknown as Metric
    const total = (expected: number, low: number, high: number) => ({
      expected, low, high, cost: expected * 1000, cost_low: low * 1000, cost_high: high * 1000,
    })
    const FORECAST: ForecastResult = {
      ...HEADER,
      recipe: 'forecast',
      method: '수명 모형 … v1',
      params: {},
      run_id: 'r1',
      caveats: [
        {
          code: 'already_sold',
          level: 'info',
          message: '이미 판 물량의 예측입니다 — 앞으로 팔 것과 리콜 · 캠페인 같은 일회성 급증은 들어 있지 않습니다.',
          count: null,
        },
      ],
      excluded: {},
      visible_share: 1,
      model: 'weibull',
      beta: 1.5,
      eta: 13,
      p: 1,
      horizon: 6,
      warranty: 24,
      basis: 'records',
      cost: 1000,
      units: 600000,
      cohorts: 30,
      start: '2028-07',
      total: total(1200, 1100, 1300),
      remaining: total(5000, 4700, 5300),
      points: [],
      history: [],
      backtest: {
        start: '2028-01', periods: 6, predicted: 900, low: 840, high: 960, actual: 910,
        within: true, points: [],
      },
    }
    metricsApi.analysis.mockResolvedValue(FORECAST)
    render(
      <MemoryRouter>
        <AnalysisTab metric={metric} read={{ filters: {} }} />
      </MemoryRouter>,
    )
    expect(await screen.findByText(/되짚어 보기 — 2028-01 앞까지만/)).toBeInTheDocument()
    expect(screen.getByText('구간 안')).toBeInTheDocument()
    expect(screen.getByText('1,200건')).toBeInTheDocument()
    expect(screen.getByText('5,000건')).toBeInTheDocument()
    expect(screen.getByText(/리콜/)).toBeInTheDocument()
    expect(metricsApi.analysis).toHaveBeenLastCalledWith(
      'cases',
      'forecast',
      expect.objectContaining({ horizon: '12' }),
      expect.anything(),
    )
  })
  it('커버리지는 덜 다룬 값부터, 빈 칸과 기대 밖 조합을 근거와 함께 적는다', async () => {
    const axis = (name: string, label: string) => ({
      name, address: `properties.ref_${name}`, label, kind: 'object_ref', multi: true, grain: null,
    })
    const metric = {
      ...METRIC,
      dims: [axis('model', '모델'), axis('part', '부품'), axis('mechanism', '메커니즘')],
      analyses: [{ recipe: 'coverage', label: '커버리지', ok: true, reason: null }],
    } as unknown as Metric
    const level = (dim: string, label: string, way: string | null) => ({
      dim, label, type_slug: dim, way, way_label: way ? '이어진 것' : null,
    })
    const COVERAGE: CoverageResult = {
      ...HEADER,
      recipe: 'coverage',
      method: '온톨로지의 길로 편 기대 조합 x 셀이 다룬 조합 v1',
      params: {},
      run_id: 'r1',
      caveats: [],
      excluded: {},
      visible_share: 1,
      levels: [level('model', '모델', null), level('part', '부품', 'out.has_part'),
        level('mechanism', '메커니즘', 'ref.mechanisms')],
      depths: [
        { depth: 0, labels: ['모델'], expected: 2, covered: 2 },
        { depth: 1, labels: ['모델', '부품'], expected: 4, covered: 3 },
        { depth: 2, labels: ['모델', '부품', '메커니즘'], expected: 5, covered: 2 },
      ],
      roots: [
        { key: 'm2', label: 'M2', expected: 3, covered: 0, share: 0 },
        { key: 'm1', label: 'M1', expected: 3, covered: 2, share: 0.67 },
      ],
      expected_leaves: 5,
      covered_leaves: 2,
      gaps: [{ keys: ['m2', 'p2'], labels: ['M2', 'P2'], depth: 1, leaves: 2 }],
      gaps_total: 1,
      extras: [{
        keys: ['m2', 'p3', 'wear'], labels: ['M2', 'P3', '마모'], count: 1,
        drill: { type_slug: 'report', params: { 'f.ref_mech.eq': 'wear' }, partial: [] },
      }],
      extras_total: 1,
      untagged: 0,
    }
    metricsApi.analysis.mockResolvedValue(COVERAGE)
    metricsApi.coverageWays.mockResolvedValue([{ address: 'out.has_part', label: '부품' }])
    render(
      <MemoryRouter>
        <AnalysisTab metric={metric} read={{ filters: {} }} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('M2 / P2')).toBeInTheDocument()
    expect(screen.getByText('2 / 5')).toBeInTheDocument()
    expect(screen.getByText('M2 / P3 / 마모')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /1건 보기/ })).toBeInTheDocument()
    expect(metricsApi.analysis).toHaveBeenLastCalledWith(
      'cases',
      'coverage',
      expect.objectContaining({ levels: 'model,part,mechanism' }),
      expect.anything(),
    )
  })
  it('재발은 서명을 골라야 묻고, 세대 쌍마다 다시 나온 것과 재발률을 적는다', async () => {
    const axis = (name: string, label: string) => ({
      name, address: `properties.ref_${name}`, label, kind: 'object_ref', multi: true, grain: null,
    })
    const metric = {
      ...METRIC,
      dims: [axis('model', '모델'), axis('part', '부품'), axis('mechanism', '메커니즘')],
      analyses: [{ recipe: 'recurrence', label: '재발', ok: true, reason: null }],
    } as unknown as Metric
    const RECURRENCE: RecurrenceResult = {
      ...HEADER,
      recipe: 'recurrence',
      method: '세대마다 서명 집합 x 전작의 서명 집합 v1',
      params: {},
      run_id: 'r1',
      caveats: [],
      excluded: {},
      visible_share: 1,
      generation: 'model',
      generation_label: '모델',
      way: 'ref.predecessor',
      way_label: '전작',
      signature: ['part'],
      signature_labels: ['부품'],
      pairs: [{
        key: 'm2', label: 'M2', predecessor: 'm1', predecessor_label: 'M1',
        predecessor_signatures: 2, signatures: 2, recurring: 1, rate: 0.5, new: 1,
        items: [{
          keys: ['p1'], labels: ['P1'], before: 1, now: 1,
          drill: { type_slug: 'report', params: { 'f.ref_part.eq': 'p1' }, partial: [] },
        }],
      }],
      pairs_total: 1,
      rate: 0.5,
      no_predecessor: 1,
      signatures: [{ keys: ['p1'], labels: ['P1'], pairs: 1, generations: ['M2'] }],
    }
    metricsApi.analysis.mockResolvedValue(RECURRENCE)
    metricsApi.coverageWays.mockResolvedValue([{ address: 'ref.predecessor', label: '전작' }])
    render(
      <MemoryRouter>
        <AnalysisTab metric={metric} read={{ filters: {} }} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('서명 기준을 하나 이상 고릅니다.')).toBeInTheDocument()
    expect(metricsApi.analysis).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('checkbox', { name: '부품' }))
    expect(await screen.findByText('M2 ← M1')).toBeInTheDocument()
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    expect(metricsApi.analysis).toHaveBeenLastCalledWith(
      'cases',
      'recurrence',
      expect.objectContaining({ generation: 'model', signature: 'part' }),
      expect.anything(),
    )
  })
})
