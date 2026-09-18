/**
 * 같이 바뀐 것 복원이 지키는 것 — **계획을 먼저 보이고, 못 되돌리는 행은 이유를 적고,
 * 되돌릴 것이 없으면 단추를 죽인다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const objectApi = vi.hoisted(() => ({ bulkEditUndo: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

const PLAN = {
  applied: false,
  batch_id: null,
  field: 'properties.grade',
  field_label: '등급',
  counts: { change: 2, unchanged: 0, error: 1 },
  fields: [],
  rows: [
    { id: 'a', label: '가', action: 'change', before: 'B', after: 'A', message: '' },
    { id: 'b', label: '나', action: 'change', before: 'B', after: 'A', message: '' },
    {
      id: 'c',
      label: '다',
      action: 'error',
      before: 'C',
      after: 'A',
      message: '그 뒤에 다시 바뀌었습니다 — 지금 값을 덮어쓰지 않습니다.',
    },
  ],
}

async function open() {
  const { BulkUndoDialog } = await import('@/modules/objects/BulkUndoDialog')
  const onApplied = vi.fn()
  render(
    <BulkUndoDialog typeSlug="part" batchId="batch-1" onClose={vi.fn()} onApplied={onApplied} />,
  )
  return onApplied
}

describe('같이 바뀐 것 한 번에 복원', () => {
  beforeEach(() => vi.clearAllMocks())

  it('열자마자 계획을 보이고, 그 뒤에 바뀐 행은 이유와 지금 값을 적는다', async () => {
    objectApi.bulkEditUndo.mockResolvedValue(PLAN)
    await open()
    expect(await screen.findByRole('button', { name: /2건 복원/ })).toBeEnabled()
    expect(objectApi.bulkEditUndo).toHaveBeenCalledWith('part', 'batch-1', false)
    expect(screen.getByText(/「등급」 칸을/)).toBeInTheDocument()
    expect(screen.getByText(/다시 바뀌었습니다.*\(지금: C\)/)).toBeInTheDocument()
  })

  it('클릭하면 적용하고 목록을 다시 읽게 한다', async () => {
    objectApi.bulkEditUndo
      .mockResolvedValueOnce(PLAN)
      .mockResolvedValueOnce({ ...PLAN, applied: true, batch_id: 'batch-2' })
    const onApplied = await open()
    await userEvent.click(await screen.findByRole('button', { name: /2건 복원/ }))
    await waitFor(() => expect(onApplied).toHaveBeenCalled())
    expect(objectApi.bulkEditUndo).toHaveBeenLastCalledWith('part', 'batch-1', true)
    expect(screen.getByText('되돌렸습니다.')).toBeInTheDocument()
  })

  it('되돌릴 것이 없으면 왜 없는지 말하고 단추를 죽인다', async () => {
    objectApi.bulkEditUndo.mockResolvedValue({
      ...PLAN,
      counts: { change: 0, unchanged: 3, error: 0 },
      rows: PLAN.rows.map((one) => ({ ...one, action: 'unchanged', message: '' })),
    })
    await open()
    expect(await screen.findByRole('button', { name: /0건 복원/ })).toBeDisabled()
    expect(screen.getByText(/이미 되돌렸거나/)).toBeInTheDocument()
  })
})
