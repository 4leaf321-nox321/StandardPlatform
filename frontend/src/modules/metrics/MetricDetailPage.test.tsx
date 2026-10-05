/**
 * 지표 상세가 지키는 것 — **셀마다 목록 조건으로 돌아가고, 계산 시각을 숨기지 않는다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { Metric, MetricCohort, MetricTable, ReadHeader } from '@/modules/metrics/api'

const metricsApi = vi.hoisted(() => ({
  get: vi.fn(),
  values: vi.fn(),
  series: vi.fn(),
  cohort: vi.fn(),
  dims: vi.fn(),
  runs: vi.fn(),
  analysis: vi.fn(),
  alerts: vi.fn(),
}))
vi.mock('@/modules/metrics/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/metrics/api')>()),
  metricsApi,
}))
vi.mock('@/shared/charts', () => ({ Chart: () => <div>차트</div> }))
vi.mock('@/shared/charts/LazyPlot', () => ({ LazyPlot: () => <div>히트맵</div> }))

/** 늦게 불러오는 탭을 기다리는 시간 — 시험 전체가 한꺼번에 돌면 기본 1초로는 모자라다. */
const LAZY_WAIT = 10_000

const METRIC: Metric = {
  id: 'm1',
  slug: 'cases_monthly',
  label: '월별 인입',
  description: '',
  source_type_slug: 'svc_case',
  source_type_label: '시장 서비스',
  spec: {
    measure: 'count',
    time: { address: 'properties.received', grain: 'month' },
    cohort: { address: 'properties.sold', grain: 'month' },
    dimensions: [{ name: 'symptom', address: 'properties.symptom' }],
    filters: [],
    denominator: { metric: 'sales', on: [], time: 'cohort', per: 100 },
    settle_days: 30,
  },
  interval_hours: 24,
  is_active: true,
  overlap: false,
  measure_label: '건수',
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
  broken: null,
  current_run_id: 'r1',
  last_run_at: '2026-10-04T02:30:00+09:00',
  last_status: 'ok',
  last_error: null,
  cells: 12,
  stale: false,
  analyses: [],
  created_at: '2026-10-01T00:00:00+09:00',
  updated_at: '2026-10-01T00:00:00+09:00',
}

const HEADER: ReadHeader = {
  slug: 'cases_monthly',
  label: '월별 인입',
  measure: 'count',
  measure_label: '건수',
  grain: 'month',
  cohort_grain: 'month',
  settle_days: 30,
  computed_at: '2026-10-04T02:30:00+09:00',
  watermark: '2026-10-04T02:30:00+09:00',
  stale: false,
  overlap: false,
  unbucketed: 1,
  unbucketed_cohort: 0,
  negative_age: 0,
  truncated: false,
  denominator: {
    metric: 'sales',
    label: '판매 대수',
    measure: 'sum',
    measure_label: '합계',
    time: 'cohort',
    per: 100,
    missing: 0,
    truncated: false,
  },
}

const TABLE: MetricTable = {
  ...HEADER,
  dims: ['symptom'],
  by: ['period'],
  total_count: 3,
  total_value: 3,
  cells: [
    {
      dims: { symptom: '소음' },
      labels: { symptom: '소음' },
      period: '2026-02-01',
      period_label: '2026-02',
      cohort: null,
      cohort_label: null,
      age: null,
      count: 2,
      value_count: 0,
      sum: null,
      min: null,
      max: null,
      avg: null,
      value: 2,
      ratio: 2.5,
      denominator: 80,
      closed: true,
      drill: {
        type_slug: 'svc_case',
        params: {
          'f.symptom.eq': '소음',
          'f.received.gte': '2026-02-01',
          'f.received.lt': '2026-03-01',
        },
        partial: [],
      },
    },
  ],
}

const COHORT: MetricCohort = {
  ...HEADER,
  cumulative: true,
  ages: [0, 1],
  rows: [
    {
      cohort: '2026-01-01',
      label: '2026-01',
      denominator: 100,
      cells: [
        {
          age: 0,
          count: 1,
          value: 1,
          cumulative: 1,
          ratio: 1,
          closed: true,
          drill: { type_slug: 'svc_case', params: { 'f.sold.gte': '2026-01-01' }, partial: [] },
        },
        {
          age: 1,
          count: 1,
          value: 1,
          cumulative: 2,
          ratio: 2,
          closed: true,
          drill: { type_slug: 'svc_case', params: { 'f.sold.gte': '2026-01-01' }, partial: [] },
        },
      ],
    },
  ],
}

async function mount(
  path = '/metrics/cases_monthly',
  metric: Metric = METRIC,
  table: MetricTable = TABLE,
) {
  metricsApi.get.mockResolvedValue(metric)
  metricsApi.dims.mockResolvedValue({
    name: 'symptom',
    label: '증상',
    kind: 'enum',
    values: [{ value: '소음', label: '소음', count: 2 }],
    truncated: false,
  })
  metricsApi.values.mockResolvedValue(table)
  metricsApi.cohort.mockResolvedValue(COHORT)
  const { default: MetricDetailPage } = await import('@/modules/metrics/MetricDetailPage')
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/metrics/:slug" element={<MetricDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('지표 상세', () => {
  it('표의 셀이 목록 조건으로 돌아가고 머리가 계산 시각을 말한다', async () => {
    await mount()
    await waitFor(() => expect(screen.getByText('월별 인입')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText(/계산 시각/)).toBeInTheDocument())
    expect(screen.getByText(/날짜가 없거나 못 읽은 기록 1건/)).toBeInTheDocument()
    expect(metricsApi.values).toHaveBeenCalledWith(
      'cases_monthly',
      expect.objectContaining({ dims: ['symptom'], by: [] }),
    )
    const link = screen.getByRole('link', { name: /2건 보기/ })
    expect(link).toHaveAttribute(
      'href',
      '/o/svc_case?f.symptom.eq=%EC%86%8C%EC%9D%8C&f.received.gte=2026-02-01&f.received.lt=2026-03-01',
    )
    // 분모가 있으니 비율 열이 선다.
    expect(screen.getByText('2.5')).toBeInTheDocument()
  })

  it('머무는 기간이 있는 지표는 머리가 「최근 N기간의 합」 이라고 말한다', async () => {
    await mount('/metrics/cases_monthly', METRIC, {
      ...TABLE,
      stay: {
        periods: 24,
        periods_from: 'ref.country.warranty_months',
        periods_from_label: '국가 › 보증 기간',
      },
    })
    await waitFor(() =>
      expect(
        screen.getByText(/기간마다 최근 국가 › 보증 기간만큼\(최대 24기간\)의 합/),
      ).toBeInTheDocument(),
    )
  })

  it('조건 비율은 조건 건수를 그 목록으로 잇고 비율(%) 열을 세운다', async () => {
    // 분모 지표 없이 — 같은 기록 전체가 분모다. 「N건 보기」 는 셀의 전부, 조건 건수는 조건까지.
    const share: Metric = {
      ...METRIC,
      measure_label: '조건 비율',
      spec: {
        ...METRIC.spec,
        measure: 'share',
        share_when: [{ field: 'handling', op: 'eq', value: 'NTF' }],
        denominator: null,
      },
    }
    const cell = TABLE.cells[0]
    await mount('/metrics/cases_monthly', share, {
      ...TABLE,
      measure: 'share',
      measure_label: '조건 비율',
      cells: [
        {
          ...cell,
          count: 8,
          value: 2,
          ratio: 25,
          value_drill: {
            ...cell.drill,
            params: { ...cell.drill.params, 'f.handling.eq': 'NTF' },
          },
        },
      ],
    })
    await waitFor(() => expect(screen.getByText('비율(%)')).toBeInTheDocument())
    expect(screen.getByText('조건 건수')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /8건 보기/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /2건 보기/ }).getAttribute('href')).toContain(
      'f.handling.eq=NTF',
    )
    expect(screen.getByText('25')).toBeInTheDocument()
  })

  it('코호트 탭은 누적을 켜고 끄며 다시 묻는다', async () => {
    await mount()
    await waitFor(() => expect(screen.getByText('월별 인입')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('tab', { name: '코호트' }))
    await waitFor(() =>
      expect(metricsApi.cohort).toHaveBeenCalledWith(
        'cases_monthly',
        expect.objectContaining({ cumulative: true }),
      ),
    )
    await waitFor(() => expect(screen.getByText('히트맵')).toBeInTheDocument())
    expect(screen.getByText('2026-01')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: /누적/ }))
    await waitFor(() =>
      expect(metricsApi.cohort).toHaveBeenCalledWith(
        'cases_monthly',
        expect.objectContaining({ cumulative: false }),
      ),
    )
  })

  it('계산 기록은 실패한 계산의 이유를 보여 준다 — 옛 값이 왜 그대로인지', async () => {
    metricsApi.runs.mockResolvedValue([
      {
        id: 'r2',
        job_id: 'j2',
        status: 'failed',
        watermark: null,
        started_at: '2026-10-04T02:30:00+09:00',
        finished_at: '2026-10-04T02:30:01+09:00',
        rows: 0,
        cells: 0,
        error: '[APP-METRICS-0003] 이 타입에 없는 칸입니다: nope',
        stats: {},
      },
      {
        id: 'r1',
        job_id: 'j1',
        status: 'ok',
        watermark: '2026-10-03T02:30:00+09:00',
        started_at: '2026-10-03T02:30:00+09:00',
        finished_at: '2026-10-03T02:30:20+09:00',
        rows: 2000000,
        cells: 5800,
        error: null,
        stats: { unbucketed: 12 },
      },
    ])
    await mount()
    await waitFor(() => expect(screen.getByText('월별 인입')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('tab', { name: '계산 기록' }))
    await waitFor(() => expect(metricsApi.runs).toHaveBeenCalledWith('cases_monthly'))
    expect(await screen.findByText('실패')).toBeInTheDocument()
    expect(screen.getByText(/이 타입에 없는 칸입니다/)).toBeInTheDocument()
    expect(screen.getByText('성공')).toBeInTheDocument()
    expect(screen.getByText('20초')).toBeInTheDocument()
    expect(screen.getByText('2,000,000')).toBeInTheDocument()
    expect(screen.getByText('5,800')).toBeInTheDocument()
  })

  it('경보 알림의 링크는 그 분석을 그 거르기 · 인자로 연다', async () => {
    metricsApi.values.mockClear()
    metricsApi.analysis.mockResolvedValue({
      ...HEADER,
      recipe: 'control',
      method: '라니 u-관리도 v1',
      params: {},
      run_id: 'r1',
      caveats: [],
      excluded: {},
      visible_share: 1,
      axis: 'period',
      window: null,
      kind: 'u',
      per: 100,
      split: null,
      split_label: null,
      baseline_to: null,
      rules: [],
      charts: [],
      other_groups: 0,
    })
    await mount(
      '/metrics/cases_monthly?tab=analysis&recipe=control&axis=period&window=3&d.symptom=%EC%86%8C%EC%9D%8C',
      {
        ...METRIC,
        analyses: [
          { recipe: 'pareto', label: '파레토 · 집중도', ok: true, reason: null },
          { recipe: 'control', label: '관리도', ok: true, reason: null },
        ],
      },
    )
    // 분석 탭은 늦게 불러온다(그림 도구가 무겁다) — 시험 전체가 한꺼번에 돌 때는 1초를 넘긴다.
    await waitFor(
      () =>
        expect(metricsApi.analysis).toHaveBeenCalledWith(
          'cases_monthly',
          'control',
          { axis: 'period', window: '3', split: undefined, baseline_to: undefined },
          expect.objectContaining({ filters: { symptom: '소음' } }),
        ),
      { timeout: LAZY_WAIT },
    )
    expect(metricsApi.values).not.toHaveBeenCalled()
    expect(screen.getByRole('tab', { name: '분석' })).toHaveAttribute('aria-selected', 'true')
  })

  it('경보 탭의 「분석 열기」 는 같은 화면 안에서도 그 인자로 다시 연다', async () => {
    metricsApi.analysis.mockClear()
    metricsApi.analysis.mockResolvedValue({
      ...HEADER,
      recipe: 'changes',
      method: '변화점 v1',
      params: {},
      run_id: 'r1',
      caveats: [],
      excluded: {},
      visible_share: 1,
      axis: 'cohort',
      window: 6,
      kind: 'rate',
      per: 100,
      season_length: 12,
      seasonal: [],
      seasonal_p_value: null,
      dispersion: 1,
      penalty: null,
      min_segment: 3,
      changes: [],
      segments: [],
      points: [],
    })
    metricsApi.alerts.mockResolvedValue([
      {
        id: 'a1',
        metric: 'cases_monthly',
        metric_label: '월별 인입',
        name: '수준 변화',
        recipe: 'changes',
        recipe_label: '계절 · 변화점',
        params: { axis: 'cohort', window: '6' },
        is_active: true,
        last_checked_at: null,
        last_status: 'ok',
        last_error: null,
        events: 0,
        created_at: '2026-10-04T00:00:00+09:00',
        link: '/metrics/cases_monthly?tab=analysis&recipe=changes&axis=cohort&window=6',
      },
    ])
    await mount('/metrics/cases_monthly?tab=alerts', {
      ...METRIC,
      analyses: [{ recipe: 'changes', label: '계절 · 변화점', ok: true, reason: null }],
    })
    await userEvent.click(await screen.findByRole('link', { name: '분석 열기' }))
    await waitFor(
      () =>
        expect(metricsApi.analysis).toHaveBeenCalledWith(
          'cases_monthly',
          'changes',
          { axis: 'cohort', window: '6' },
          expect.anything(),
        ),
      { timeout: LAZY_WAIT },
    )
    expect(screen.getByRole('tab', { name: '분석' })).toHaveAttribute('aria-selected', 'true')
  })

  it('경보 탭은 이 지표에 건 내 경보를 보인다', async () => {
    metricsApi.alerts.mockResolvedValue([])
    await mount('/metrics/cases_monthly?tab=alerts')
    await waitFor(() => expect(metricsApi.alerts).toHaveBeenCalledWith('cases_monthly'))
    expect(await screen.findByText('이 지표에 건 내 경보가 없습니다.')).toBeInTheDocument()
  })
})
