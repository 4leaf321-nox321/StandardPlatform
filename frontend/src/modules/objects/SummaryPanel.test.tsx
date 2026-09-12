/**
 * 묶어 보기가 지키는 것 — **목록과 같은 거르기를 넘기고, 합이 안 맞으면 그 차이를 적고,
 * 막대를 누르면 원래 값으로 거른다.**
 *
 * 그림 자체는 `shared/charts` 의 몫이라 여기서는 **무엇을 넘기나**만 본다. 그래서
 * 차트를 가짜로 바꿔 끼우고, 넘어온 행과 `onPick` 을 들여다본다.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const objectApi = vi.hoisted(() => ({ summary: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

/** 가짜 차트 — 행마다 단추 하나. 누르면 진짜 차트가 하듯 **행 그대로** 넘긴다. */
const charts = vi.hoisted(() => ({ rows: [] as Record<string, unknown>[] }))
vi.mock('@/shared/charts', () => ({
  Chart: ({
    data,
    x,
    onPick,
    title,
  }: {
    data: Record<string, unknown>[]
    x: string
    onPick?: (row: Record<string, unknown>) => void
    title?: string
  }) => {
    charts.rows = data
    return (
      <div aria-label={title}>
        {data.map((row) => (
          <button
            key={String(row[x])}
            type="button"
            disabled={!onPick}
            onClick={() => onPick?.(row)}
          >
            {String(row[x])}
          </button>
        ))}
      </div>
    )
  },
}))

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
  await screen.findByText(/거른 것 전체/)
  return onPick
}

describe('묶어 보기', () => {
  it('목록과 같은 거르기를 넘기고, 빈 값도 한 칸으로 그린다', async () => {
    await panel(BASE)
    expect(objectApi.summary).toHaveBeenCalledWith(
      'part',
      { q: '볼트' },
      { groupBy: 'status', metric: 'count', metricField: null },
    )
    // **빈 값을 숨기면 막대의 합이 전체와 안 맞고, 그 차이는 화면 어디에도 안 적힌다.**
    expect(charts.rows.map((one) => one.name)).toEqual(['A', 'B', '(비어 있음)'])
    expect(screen.getByText(/거른 것 전체 5건/)).toBeInTheDocument()
  })

  it('접힌 그룹이 있으면 몇 종류 몇 건이 빠졌는지 적는다', async () => {
    await panel({ ...BASE, total: 40, other_groups: 7, other_count: 35 })
    expect(screen.getByText(/7종류 35건은 접혔습니다/)).toBeInTheDocument()
  })

  it('막대를 누르면 **원래 값**으로 거른다 — 빈 칸은 거를 값이 없다', async () => {
    const onPick = await panel(BASE)
    await userEvent.click(screen.getByRole('button', { name: 'A' }))
    expect(onPick).toHaveBeenCalledWith('properties.grade', 'A')

    onPick.mockClear()
    await userEvent.click(screen.getByRole('button', { name: '(비어 있음)' }))
    expect(onPick).not.toHaveBeenCalled()
  })

  it('걸 수 없는 축이면 아예 안 누르게 한다', async () => {
    await panel({ ...BASE, group_field: 'workspace', group_label: '소유 부서' })
    expect(screen.getByRole('button', { name: 'A' })).toBeDisabled()
  })

  it('셀 숫자 칸이 없으면 합계·평균을 못 고른다', async () => {
    await panel({ ...BASE, metric_options: [] })
    await userEvent.click(screen.getByRole('combobox', { name: '세는 방법' }))
    expect(await screen.findByRole('option', { name: '합계' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
  })

  it('그림 모양을 막대·꺾은선·원으로 바꾼다', async () => {
    await panel(BASE)
    const pie = screen.getByRole('button', { name: '원' })
    expect(pie).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(pie)
    expect(pie).toHaveAttribute('aria-pressed', 'true')
  })
})
