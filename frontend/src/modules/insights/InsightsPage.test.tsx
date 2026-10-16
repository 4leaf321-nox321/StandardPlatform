/**
 * 눈여겨볼 것(ADR 0032) — 점수 높은 것부터, 줄마다 그 분석을 여는 링크, 그만 보기 · 지금 훑기는
 * 시스템 관리자만, 비었으면 왜 비었는지 말한다. 지표 상세의 탭에서는 그 지표의 훑기를 끄고 켠다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Insight } from '@/modules/insights/api'
import type { Metric } from '@/modules/metrics/api'

const insightsApi = vi.hoisted(() => ({
  list: vi.fn(),
  dismiss: vi.fn(),
  scan: vi.fn(),
  setMetric: vi.fn(),
}))
vi.mock('@/modules/insights/api', () => ({ insightsApi }))
const auth = vi.hoisted(() => ({ user: { is_system_admin: true, memberships: [] } }))
vi.mock('@/shared/auth/AuthContext', () => ({ useAuth: () => auth }))

function insight(extra: Partial<Insight> = {}): Insight {
  return {
    id: 'i1',
    metric_slug: 'svc_rate_uiw',
    metric_label: '보증 중 인입률',
    kind: 'high',
    kind_label: '다른 집단보다 높음',
    recipe: 'groups',
    recipe_label: '집단 비교',
    title: '불량 대분류 제품결함 · 기본 모델 F5 폴드: 전체의 1.92배 — 1,587건',
    detail: {},
    score: 2.1,
    first_seen_at: '2026-10-16T00:00:00Z',
    last_seen_at: '2026-10-16T00:00:00Z',
    past: false,
    dismissed: false,
    link: '/metrics/svc_rate_uiw?tab=analysis&recipe=groups&dim=base_model',
    ...extra,
  }
}

async function openPage() {
  const { default: InsightsPage } = await import('@/modules/insights/InsightsPage')
  render(
    <MemoryRouter>
      <InsightsPage />
    </MemoryRouter>,
  )
}

describe('눈여겨볼 것', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.user.is_system_admin = true
  })

  it('결론과 그 분석을 여는 링크를 보이고, 관리자는 그만 볼 수 있다', async () => {
    insightsApi.list.mockResolvedValue({
      items: [insight({ detail: { ratio_bound: 1.8456 }, also: ['판매월 불량 코드별'] })],
      total: 1,
      scanned_at: null,
    })
    insightsApi.dismiss.mockResolvedValue(insight({ dismissed: true }))
    await openPage()
    expect(await screen.findByText(/F5 폴드: 전체의 1.92배/)).toBeInTheDocument()
    expect(screen.getByText(/적게 잡아 1.85배/)).toBeInTheDocument() // 점수가 쓴 비
    expect(screen.getByText(/같은 줄: 판매월 불량 코드별/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '분석 열기' })).toHaveAttribute(
      'href',
      '/metrics/svc_rate_uiw?tab=analysis&recipe=groups&dim=base_model',
    )
    await userEvent.click(screen.getByRole('button', { name: '그만 보기' }))
    await waitFor(() => expect(insightsApi.dismiss).toHaveBeenCalledWith('i1', true))
    expect(insightsApi.list).toHaveBeenCalledTimes(2) // 다시 읽는다
  })

  it('관리자가 아니면 그만 보기 · 지금 훑기가 없다', async () => {
    auth.user.is_system_admin = false
    insightsApi.list.mockResolvedValue({ items: [insight()], total: 1, scanned_at: null })
    await openPage()
    expect(await screen.findByText(/F5 폴드/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '그만 보기' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '지금 훑기' })).not.toBeInTheDocument()
  })

  it('비었으면 왜 비었을 수 있는지 말하고, 지난 것도 보기를 고르면 다시 묻는다', async () => {
    insightsApi.list.mockResolvedValue({ items: [], total: 0, scanned_at: null })
    await openPage()
    expect(await screen.findByText('눈여겨볼 것이 없습니다')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: '지난 것도' }))
    await waitFor(() =>
      expect(insightsApi.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ include_past: true }),
      ),
    )
  })

  it('지표 상세의 탭에서 관리자는 그 지표의 훑기를 끈다', async () => {
    insightsApi.list.mockResolvedValue({ items: [], total: 0, scanned_at: null })
    insightsApi.setMetric.mockResolvedValue({ slug: 'm1', off: true })
    const onChanged = vi.fn()
    const { MetricInsightsTab } = await import('@/modules/insights/MetricInsightsTab')
    render(
      <MemoryRouter>
        <MetricInsightsTab
          metric={{ slug: 'm1', insights_off: false, insights_scanned_at: null } as Metric}
          onChanged={onChanged}
        />
      </MemoryRouter>,
    )
    expect(await screen.findByText('이 지표에서 찾은 것이 없습니다.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '훑기 끄기' }))
    await waitFor(() => expect(insightsApi.setMetric).toHaveBeenCalledWith('m1', true))
    expect(onChanged).toHaveBeenCalled()
  })
})
