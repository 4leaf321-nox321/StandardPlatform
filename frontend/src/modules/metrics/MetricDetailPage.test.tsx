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
}))
vi.mock('@/modules/metrics/api', () => ({ metricsApi }))
vi.mock('@/shared/charts', () => ({ Chart: () => <div>차트</div> }))
vi.mock('@/shared/charts/LazyPlot', () => ({ LazyPlot: () => <div>히트맵</div> }))

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

async function mount() {
  metricsApi.get.mockResolvedValue(METRIC)
  metricsApi.dims.mockResolvedValue({
    name: 'symptom',
    label: '증상',
    kind: 'enum',
    values: [{ value: '소음', label: '소음', count: 2 }],
    truncated: false,
  })
  metricsApi.values.mockResolvedValue(TABLE)
  metricsApi.cohort.mockResolvedValue(COHORT)
  const { default: MetricDetailPage } = await import('@/modules/metrics/MetricDetailPage')
  render(
    <MemoryRouter initialEntries={['/metrics/cases_monthly']}>
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
})
