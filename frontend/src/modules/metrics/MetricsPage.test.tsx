/**
 * 지표 목록이 지키는 것 — **신선도를 숨기지 않고, 다시 세는 일은 작업으로 기다린다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Metric } from '@/modules/metrics/api'

const metricsApi = vi.hoisted(() => ({
  list: vi.fn(),
  recompute: vi.fn(),
  remove: vi.fn(),
}))
const jobsApi = vi.hoisted(() => ({ waitFor: vi.fn() }))
const auth = vi.hoisted(() => ({ admin: true }))
vi.mock('@/modules/metrics/api', () => ({ metricsApi }))
vi.mock('@/modules/jobs/api', () => ({ jobsApi }))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { is_system_admin: auth.admin } }),
}))
vi.mock('@/modules/metrics/MetricDefinitionDialog', () => ({
  MetricDefinitionDialog: () => <div>정의 대화상자</div>,
}))

function metric(over: Partial<Metric> = {}): Metric {
  return {
    id: 'm1',
    slug: 'cases_monthly',
    label: '월별 인입',
    description: '',
    source_type_slug: 'svc_case',
    source_type_label: '시장 서비스',
    spec: { measure: 'count', dimensions: [], filters: [], settle_days: 30 },
    interval_hours: 24,
    is_active: true,
    overlap: false,
    measure_label: '건수',
    grain: 'month',
    cohort_grain: null,
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
    cells: 120,
    stale: false,
    analyses: [],
    created_at: '2026-10-01T00:00:00+09:00',
    updated_at: '2026-10-01T00:00:00+09:00',
    ...over,
  }
}

async function mount() {
  const { default: MetricsPage } = await import('@/modules/metrics/MetricsPage')
  render(
    <MemoryRouter>
      <MetricsPage />
    </MemoryRouter>,
  )
}

describe('지표 목록', () => {
  beforeEach(() => vi.clearAllMocks())

  it('신선도 · 겹침 · 깨짐을 배지로 말하고 상세로 간다', async () => {
    metricsApi.list.mockResolvedValue([
      metric(),
      metric({
        id: 'm2',
        slug: 'failed',
        label: '실패한 것',
        last_status: 'failed',
        last_error: '없는 칸',
      }),
      metric({
        id: 'm3',
        slug: 'never',
        label: '안 센 것',
        last_run_at: null,
        last_status: null,
        stale: true,
      }),
      metric({ id: 'm4', slug: 'old', label: '낡은 것', stale: true, overlap: true }),
      metric({ id: 'm5', slug: 'broken', label: '깨진 것', broken: '칸이 지워졌습니다' }),
    ])
    await mount()
    await waitFor(() => expect(screen.getByText('월별 인입')).toBeInTheDocument())
    expect(screen.getByRole('link', { name: '월별 인입' })).toHaveAttribute(
      'href',
      '/metrics/cases_monthly',
    )
    expect(screen.getByText('계산 실패')).toBeInTheDocument()
    expect(screen.getByText('없는 칸')).toBeInTheDocument()
    expect(screen.getByText('아직 안 셈')).toBeInTheDocument()
    expect(screen.getByText('오래됨')).toBeInTheDocument()
    expect(screen.getByText('겹침')).toBeInTheDocument()
    expect(screen.getByText('정의 깨짐')).toBeInTheDocument()
    expect(screen.getByText('칸이 지워졌습니다')).toBeInTheDocument()
    expect(screen.getAllByText(/증상 · 셀 120 · 계산 시각/).length).toBeGreaterThan(0)
  })

  it('시스템 관리자는 지금 다시 세고, 끝날 때까지 작업을 본다', async () => {
    metricsApi.list.mockResolvedValue([metric()])
    metricsApi.recompute.mockResolvedValue({ id: 'j1', status: 'queued' })
    jobsApi.waitFor.mockResolvedValue({ id: 'j1', status: 'done', error: null })
    await mount()
    await waitFor(() => expect(screen.getByText('월별 인입')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '새 지표' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '지금 다시 계산' }))
    await waitFor(() => expect(jobsApi.waitFor).toHaveBeenCalledWith('j1'))
    expect(metricsApi.recompute).toHaveBeenCalledWith('cases_monthly')
    expect(metricsApi.list).toHaveBeenCalledTimes(2)
  })

  it('관리자가 아니면 읽기만', async () => {
    auth.admin = false
    metricsApi.list.mockResolvedValue([metric()])
    await mount()
    await waitFor(() => expect(screen.getByText('월별 인입')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: '새 지표' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '지금 다시 계산' })).not.toBeInTheDocument()
    auth.admin = true
  })
})
