/**
 * 묶어 보기가 지키는 것 — **합이 전체와 맞고, 안 맞으면 그 차이를 적고, 막대는 거르기가 된다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const objectApi = vi.hoisted(() => ({ summary: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

const BASE = {
  group_field: 'properties.grade',
  group_label: '등급',
  metric: 'count',
  metric_field: null,
  metric_label: '건수',
  total: 5,
  buckets: [
    { key: 'A', label: 'A', count: 2, value: null },
    { key: 'B', label: 'B', count: 1, value: null },
    { key: null, label: '(비어 있음)', count: 2, value: null },
  ],
  other_groups: 0,
  other_count: 0,
  group_options: [
    { field: 'status', label: '상태', kind: 'fixed' },
    { field: 'properties.grade', label: '등급', kind: 'enum' },
  ],
  metric_options: [{ field: 'properties.weight', label: '무게', kind: 'number' }],
}

async function panel(data: object) {
  objectApi.summary.mockResolvedValue(data)
  const { SummaryPanel } = await import('@/modules/objects/SummaryPanel')
  const onPick = vi.fn()
  render(<SummaryPanel typeSlug="part" query={{ q: '볼트' }} onPick={onPick} onClose={vi.fn()} />)
  await waitFor(() => expect(screen.getByText('(비어 있음)')).toBeInTheDocument())
  return onPick
}

describe('묶어 보기', () => {
  it('빈 값도 한 칸으로 보여 주고, 목록과 같은 거르기를 넘긴다', async () => {
    await panel(BASE)
    // **빈 값을 숨기면 막대의 합이 전체와 안 맞고, 그 차이는 화면 어디에도 안 적힌다.**
    expect(screen.getByText('(비어 있음)')).toBeInTheDocument()
    expect(screen.getByText('거른 것 전체 5건')).toBeInTheDocument()
    expect(objectApi.summary).toHaveBeenCalledWith(
      'part',
      { q: '볼트' },
      { groupBy: 'status', metric: 'count', metricField: null },
    )
  })

  it('접힌 그룹이 있으면 몇 종류 몇 건이 빠졌는지 적는다', async () => {
    await panel({ ...BASE, total: 40, other_groups: 7, other_count: 35 })
    expect(screen.getByText(/7종류 35건은 접혔습니다/)).toBeInTheDocument()
  })

  it('막대를 누르면 그 값으로 거른다 — 빈 칸은 못 누른다', async () => {
    const onPick = await panel(BASE)
    await userEvent.click(screen.getByRole('button', { name: 'A' }))
    expect(onPick).toHaveBeenCalledWith('properties.grade', 'A')
    expect(screen.getByRole('button', { name: '(비어 있음)' })).toBeDisabled()
  })

  it('셀 숫자 칸이 없으면 합계·평균을 못 고른다', async () => {
    await panel({ ...BASE, metric_options: [] })
    await userEvent.click(screen.getByRole('combobox', { name: '세는 방법' }))
    await waitFor(() =>
      expect(screen.getByRole('option', { name: '합계' })).toHaveAttribute('aria-disabled', 'true'),
    )
  })
})
