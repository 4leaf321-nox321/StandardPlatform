/**
 * 여럿 고쳐 놓고 지키는 것 — **계획 먼저, 그리고 계획이 지난 것이 되면 버린다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const objectApi = vi.hoisted(() => ({ bulkEdit: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))
vi.mock('@/modules/objects/PropertyFields', () => ({
  PropertyFields: () => <div data-testid="property-input" />,
}))

const PLAN = {
  applied: false,
  field: 'status',
  field_label: '상태',
  counts: { change: 2, unchanged: 1, error: 1 },
  fields: [
    { field: 'status', label: '상태' },
    { field: 'properties.grade', label: '등급' },
  ],
  rows: [
    { id: 'a', label: '가', action: 'change', before: '사용', after: '안 씀', message: '' },
    { id: 'b', label: '나', action: 'unchanged', before: '안 씀', after: '안 씀', message: '' },
    {
      id: 'c',
      label: '다',
      action: 'error',
      before: '',
      after: '',
      message: '이 부서의 것은 고칠 수 없습니다.',
    },
  ],
}

async function open() {
  objectApi.bulkEdit.mockResolvedValue(PLAN)
  const { BulkEditDialog } = await import('@/modules/objects/BulkEditDialog')
  const onApplied = vi.fn()
  render(
    <BulkEditDialog
      typeSlug="part"
      ids={['a', 'b', 'c']}
      defs={[]}
      workspaces={[]}
      onClose={vi.fn()}
      onApplied={onApplied}
    />,
  )
  return onApplied
}

describe('여럿 골라 한 칸 바꾸기', () => {
  it('계획을 먼저 보고, 바꿀 것이 있어야 적용할 수 있다', async () => {
    await open()
    // 계획 전에는 적용을 못 누른다 — 무엇이 바뀌는지 모른 채 누르는 일이 없어야 한다.
    expect(screen.getByRole('button', { name: '바꾸기' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await waitFor(() =>
      expect(objectApi.bulkEdit).toHaveBeenCalledWith('part', {
        ids: ['a', 'b', 'c'],
        field: 'status',
        value: 'active',
        apply: false,
      }),
    )
    expect(await screen.findByRole('button', { name: '2건 바꾸기' })).toBeEnabled()
  })

  it('못 고치는 줄은 이유가 붙는다 — 조용히 빠지지 않는다', async () => {
    await open()
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    expect(await screen.findByText('이 부서의 것은 고칠 수 없습니다.')).toBeInTheDocument()
    expect(screen.getByText('사용 → 안 씀')).toBeInTheDocument()
  })

  it('칸을 바꾸면 계획을 버린다 — 지난 계획을 보고 적용하지 않게', async () => {
    await open()
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await screen.findByRole('button', { name: '2건 바꾸기' })

    // 첫 고르개가 「바꿀 칸」 이다.
    await userEvent.click(screen.getAllByRole('combobox')[0])
    await userEvent.click(await screen.findByRole('option', { name: '등급' }))

    // 계획이 사라지고 적용은 다시 잠긴다 — 상태를 본 계획으로 등급을 바꿀 수는 없다.
    await waitFor(() => expect(screen.getByRole('button', { name: '바꾸기' })).toBeDisabled())
    expect(screen.queryByText('사용 → 안 씀')).not.toBeInTheDocument()
  })

  it('적용한 뒤에는 그 자리에서 되돌릴 수 있다', async () => {
    objectApi.bulkEdit
      .mockResolvedValueOnce(PLAN)
      .mockResolvedValueOnce({ ...PLAN, applied: true, batch_id: 'batch-9' })
    const { BulkEditDialog } = await import('@/modules/objects/BulkEditDialog')
    const onUndo = vi.fn()
    render(
      <BulkEditDialog
        typeSlug="part"
        ids={['a', 'b', 'c']}
        defs={[]}
        workspaces={[]}
        onClose={vi.fn()}
        onApplied={vi.fn()}
        onUndo={onUndo}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await userEvent.click(await screen.findByRole('button', { name: '2건 바꾸기' }))
    await userEvent.click(await screen.findByRole('button', { name: /되돌리기/ }))
    expect(onUndo).toHaveBeenCalledWith('batch-9')
  })
})
