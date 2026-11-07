/**
 * 경보 탭이 지키는 것 — **지금 확인은 적지도 알리지도 않고 「새것」 을 가려 보이며, 확인이
 * 실패한 경보는 그 이유를 숨기지 않는다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { Metric, MetricAlert } from '@/modules/metrics/api'
import { AlertsTab } from '@/modules/metrics/AlertsTab'

const metricsApi = vi.hoisted(() => ({
  alerts: vi.fn(),
  checkAlert: vi.fn(),
  alertEvents: vi.fn(),
  updateAlert: vi.fn(),
  removeAlert: vi.fn(),
}))
vi.mock('@/modules/metrics/api', () => ({ metricsApi }))

const METRIC = { slug: 'cases', label: '인입' } as unknown as Metric

const ALERT: MetricAlert = {
  id: 'a1',
  metric: 'cases',
  metric_label: '인입',
  name: 'S 초기 고장',
  recipe: 'sprt',
  recipe_label: '순차 검정(전작 대비)',
  params: { target: 'S', reference: 'A' },
  is_active: true,
  last_checked_at: '2026-10-04T02:31:00+09:00',
  last_status: 'failed',
  last_error: '[APP-METRICS-0008] 이 지표에 없는 기준입니다: nothing',
  events: 1,
  created_at: '2026-10-01T00:00:00+09:00',
  link: '/metrics/cases?tab=analysis&recipe=sprt&target=S&reference=A',
}

function mount() {
  render(
    <MemoryRouter>
      <AlertsTab metric={METRIC} />
    </MemoryRouter>,
  )
}

describe('경보 탭', () => {
  it('실패 이유를 보이고, 지금 확인은 새것을 가리고, 비활성화하면 다시 읽는다', async () => {
    metricsApi.alerts.mockResolvedValue([ALERT])
    metricsApi.checkAlert.mockResolvedValue({
      run_id: 'r1',
      findings: [
        { key: 'worse:S', title: 'S기본: 전작 A기본 보다 나쁨', detail: {}, new: false },
        { key: 'worse:T', title: 'T기본: 전작 A기본 보다 나쁨', detail: {}, new: true },
      ],
      notes: ['최근 6기간 안에 출시된 모델 2개를 봤습니다'],
    })
    metricsApi.updateAlert.mockResolvedValue({ ...ALERT, is_active: false })
    mount()
    expect(await screen.findByText('S 초기 고장')).toBeInTheDocument()
    expect(screen.getByText('확인 실패')).toBeInTheDocument()
    expect(screen.getByText(/이 지표에 없는 기준입니다/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '분석 열기' })).toHaveAttribute('href', ALERT.link)

    await userEvent.click(screen.getByRole('button', { name: '지금 확인' }))
    await waitFor(() => expect(metricsApi.checkAlert).toHaveBeenCalledWith('cases', 'a1'))
    const checked = await screen.findByLabelText('지금 확인')
    expect(checked).toHaveTextContent('T기본: 전작 A기본 보다 나쁨')
    expect(screen.getAllByText('새것')).toHaveLength(1)
    expect(screen.getByText(/모델 2개를 봤습니다/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '비활성화' }))
    await waitFor(() =>
      expect(metricsApi.updateAlert).toHaveBeenCalledWith('cases', 'a1', { is_active: false }),
    )
    await waitFor(() => expect(metricsApi.alerts).toHaveBeenCalledTimes(2))
  })

  it('발생은 새것부터, 만들 때부터 있던 것은 그렇게 적는다', async () => {
    metricsApi.alerts.mockResolvedValue([{ ...ALERT, last_status: 'ok', last_error: null }])
    metricsApi.alertEvents.mockResolvedValue([
      {
        id: 'e2',
        alert_id: 'a1',
        alert_name: 'S 초기 고장',
        metric: 'cases',
        metric_label: '인입',
        recipe: 'sprt',
        key: 'worse:S',
        title: 'S기본: 전작 A기본 보다 나쁨',
        detail: {},
        baseline: true,
        run_id: 'r1',
        created_at: '2026-10-02T02:31:00+09:00',
        link: '/metrics/cases?tab=analysis&recipe=sprt&target=S',
      },
    ])
    mount()
    await userEvent.click(await screen.findByRole('button', { name: '발생' }))
    const listed = await screen.findByLabelText('발생')
    expect(listed).toHaveTextContent('(만들 때부터 있던 것)')
    expect(screen.getByRole('link', { name: 'S기본: 전작 A기본 보다 나쁨' })).toHaveAttribute(
      'href',
      '/metrics/cases?tab=analysis&recipe=sprt&target=S',
    )
  })

  it('미반영 구간의 발생은 판정 유보로 그 말과 함께 보인다', async () => {
    const gap =
      '2028-06 이후는 미반영 구간입니다(원천 최종 반영 기간 2028-05) — 이 구간의 0건 · 감소는 수집 지연 때문일 수 있으므로, 데이터 소스의 수집 현황을 확인해야 합니다.'
    metricsApi.alerts.mockResolvedValue([{ ...ALERT, last_status: 'ok', last_error: null }])
    metricsApi.alertEvents.mockResolvedValue([
      {
        id: 'e3',
        alert_id: 'a1',
        alert_name: 'S 초기 고장',
        metric: 'cases',
        metric_label: '인입',
        recipe: 'changes',
        key: 'down:2028-06-01',
        title: '2028-06부터 내려감: 30 → 0',
        detail: { data_gap: gap, held: true },
        baseline: false,
        held: true,
        run_id: 'r2',
        created_at: '2028-09-15T02:31:00+09:00',
        link: '/metrics/cases?tab=analysis&recipe=changes',
      },
    ])
    mount()
    await userEvent.click(await screen.findByRole('button', { name: '발생' }))
    const listed = await screen.findByLabelText('발생')
    expect(listed).toHaveTextContent(gap)
    expect(listed).toHaveTextContent('판정 유보')
  })

  it('지금 확인에서 미반영 구간의 새 결론은 새것이 아니라 판정 유보로 가린다', async () => {
    metricsApi.alerts.mockResolvedValue([{ ...ALERT, last_status: 'ok', last_error: null }])
    metricsApi.checkAlert.mockResolvedValue({
      run_id: 'r3',
      findings: [
        { key: 'worse:S', title: 'S기본: 전작 A기본 보다 나쁨', detail: {}, new: false },
        { key: 'worse:T', title: 'T기본: 전작 A기본 보다 나쁨', detail: {}, new: true, held: true },
      ],
      notes: ['2028-06 이후는 미반영 구간입니다(원천 최종 반영 기간 2028-05)'],
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: '지금 확인' }))
    const checked = await screen.findByLabelText('지금 확인')
    expect(checked).toHaveTextContent('판정 유보T기본')
    expect(screen.queryByText('새것')).not.toBeInTheDocument()
  })
})
