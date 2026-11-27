/**
 * 계획 표 — **줄이 많아도 한 번에 다 그리지 않는다.** 5만 줄을 표 한 장에 그리면 브라우저가
 * 멈췄다(2026-10-09). 앞(서버가 오류 줄을 앞세운다)만 그리고 나머지는 수와 CSV 로.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ImportPlan } from '@/modules/objects/api'
import {
  ImportPlanTable,
  planIsClean,
  RENDER_MAX,
  SHOW_UNCHANGED_BELOW,
} from '@/modules/objects/ImportPlanTable'

function plan(count: number, omitted = 0): ImportPlan {
  return {
    applied: false,
    errors: [],
    counts: { create: count + omitted, update: 0, unchanged: 0, unlink: 0, error: 0 },
    rows: Array.from({ length: count }, (_, at) => ({
      row: at + 1,
      action: 'create' as const,
      label: `줄 ${at + 1}`,
      key: null,
      object_id: null,
      changes: [],
      message: '',
    })),
    ...(omitted ? { rows_omitted: omitted } : {}),
  } as unknown as ImportPlan
}

describe('계획 표', () => {
  it('상한까지만 그리고 나머지 수를 말한다 — 서버가 뺀 줄도 함께 센다', async () => {
    const download = vi.fn()
    render(<ImportPlanTable plan={plan(RENDER_MAX + 20, 1000)} onDownloadRows={download} />)
    expect(screen.getAllByRole('row')).toHaveLength(1 + RENDER_MAX) // 머리 한 줄 + 그린 줄
    expect(screen.getByText(/나머지 1,020줄/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '모든 줄 받기(CSV)' }))
    expect(download).toHaveBeenCalled()
  })

  it('다 그린 계획에는 그 말이 없다', () => {
    render(<ImportPlanTable plan={plan(3)} />)
    expect(screen.queryByText(/나머지/)).not.toBeInTheDocument()
  })

  it('행과 무관한 오류 칸이 없는 계획(문서 올리기 0.4.107)도 그린다', () => {
    const bare = { ...plan(2) } as Partial<ImportPlan>
    delete bare.errors
    render(<ImportPlanTable plan={bare as ImportPlan} />)
    expect(screen.getByText('줄 1')).toBeInTheDocument()
    expect(planIsClean(bare as ImportPlan)).toBe(true)
  })

  it('경고는 줄 아래에 적고, 「그대로」 여도 경고가 있으면 뺄 수 없다', () => {
    const many = plan(SHOW_UNCHANGED_BELOW + 5)
    many.rows = many.rows.map((one, at) => ({ ...one, action: 'unchanged' as const, row: at + 1 }))
    many.rows[3] = { ...many.rows[3], warnings: ['지금 본문 10자가 빈 본문이 됩니다'] }
    many.counts = { ...many.counts, create: 0, unchanged: many.rows.length }
    many.warned = 1
    render(<ImportPlanTable plan={many} />)
    expect(screen.getByText('지금 본문 10자가 빈 본문이 됩니다')).toBeInTheDocument()
    expect(screen.getByText('확인할 것 1')).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(2) // 머리 + 경고 줄 하나
  })
})
