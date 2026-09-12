/**
 * 홈 위젯이 지키는 것 — **축이 있으면 그림, 없으면 수 하나, 깨져도 홈은 선다.**
 */

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const objectApi = vi.hoisted(() => ({ summary: vi.fn() }))
const viewApi = vi.hoisted(() => ({ update: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi, viewApi }))
vi.mock('@/shared/charts', () => ({
  Chart: ({ title }: { title?: string }) => <div data-testid="chart">{title}</div>,
}))

const SUMMARY = {
  group_field: 'properties.grade',
  group_label: '등급',
  split_field: '',
  split_label: '',
  splits: [],
  other_splits: 0,
  metric: 'count',
  metric_label: '건수',
  total: 7,
  buckets: [{ key: 'A', label: 'A', count: 7, value: null, parts: [] }],
  other_groups: 0,
  other_count: 0,
  group_options: [],
  metric_options: [],
}

function widget(overrides: Record<string, unknown> = {}) {
  return {
    type_label: '부품',
    icon: 'Box',
    view: {
      id: 'v1',
      type_slug: 'part',
      name: '등급별',
      query: {
        q: '',
        status: null,
        conditions: [{ field: 'grade', op: 'eq' as const, value: 'A' }],
      },
      owner_user_id: 'u1',
      owner_label: '나',
      workspace_slug: 'cae',
      summary: {
        group_by: 'properties.grade',
        split_by: '',
        metric: 'count',
        metric_field: null,
        chart: 'bar',
        stacked: false,
      },
      home_order: 0,
      can_edit: true,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      ...overrides,
    },
  }
}

async function show(one: ReturnType<typeof widget>, props: Record<string, unknown> = {}) {
  const { HomeWidget } = await import('@/modules/objects/HomeWidget')
  const onChanged = vi.fn()
  render(
    <MemoryRouter>
      <HomeWidget widget={one} onChanged={onChanged} {...props} />
    </MemoryRouter>,
  )
  return onChanged
}

describe('홈 위젯', () => {
  it('축이 있으면 그림을 그리고, 제목은 그 뷰로 간다', async () => {
    objectApi.summary.mockResolvedValue(SUMMARY)
    await show(widget())
    await waitFor(() => expect(screen.getByTestId('chart')).toBeInTheDocument())
    // 홈의 숫자를 보고 다음에 하는 일은 언제나 「그게 뭔데」 다 — 조건까지 실어 보낸다.
    expect(screen.getByRole('link', { name: '등급별' })).toHaveAttribute(
      'href',
      '/o/part?f.grade.eq=A&view=v1',
    )
  })

  it('축이 없는 뷰는 수 하나다 — 「미승인 12건」 은 그림이 필요 없다', async () => {
    objectApi.summary.mockResolvedValue({ ...SUMMARY, total: 12 })
    await show(
      widget({ summary: { group_by: '', metric: 'count', metric_field: null, chart: 'bar' } }),
    )
    await waitFor(() => expect(screen.getByText('12')).toBeInTheDocument())
    expect(screen.queryByTestId('chart')).not.toBeInTheDocument()
  })

  it('한 위젯이 깨져도 홈은 선다 — 정의가 바뀌어 축이 사라질 수 있다', async () => {
    objectApi.summary.mockRejectedValue(new Error('422'))
    await show(widget())
    await waitFor(() => expect(screen.getByText(/지금 셀 수 없습니다/)).toBeInTheDocument())
    expect(screen.getByRole('link', { name: '목록에서 확인' })).toBeInTheDocument()
  })
})

describe('홈에서 내리기', () => {
  it('부서 관리자에게만 메뉴가 보인다', async () => {
    objectApi.summary.mockResolvedValue(SUMMARY)
    await show(widget())
    expect(screen.queryByRole('button', { name: '위젯 메뉴' })).not.toBeInTheDocument()

    cleanup()
    await show(widget(), { canEdit: true })
    expect(screen.getByRole('button', { name: '위젯 메뉴' })).toBeInTheDocument()
  })

  it('내려도 **뷰는 안 지운다** — 거르기까지 잃을 이유가 없다', async () => {
    objectApi.summary.mockResolvedValue(SUMMARY)
    viewApi.update.mockResolvedValue({})
    const onChanged = await show(widget(), { canEdit: true })

    await userEvent.click(screen.getByRole('button', { name: '위젯 메뉴' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /홈에서 내리기/ }))

    await waitFor(() =>
      expect(viewApi.update).toHaveBeenCalledWith('part', 'v1', { on_home: false }),
    )
    expect(onChanged).toHaveBeenCalled()
  })

  it('끝에서는 그 방향 단추를 안 보인다', async () => {
    objectApi.summary.mockResolvedValue(SUMMARY)
    await show(widget(), { canEdit: true, index: 0, total: 2 })
    await userEvent.click(screen.getByRole('button', { name: '위젯 메뉴' }))
    expect(await screen.findByRole('menuitem', { name: /뒤로/ })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: /앞으로/ })).not.toBeInTheDocument()
  })
})
