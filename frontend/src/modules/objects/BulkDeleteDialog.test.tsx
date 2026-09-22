/**
 * 여럿 골라 지우기에서 지키는 것 — **계획 먼저, 걸린 줄은 이유가 붙고, 방식을 바꾸면
 * 지난 계획은 버린다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const objectApi = vi.hoisted(() => ({ bulkDelete: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

const PLAN = {
  applied: false,
  mode: 'block',
  counts: { delete: 2, error: 1 },
  rows: [
    { id: 'a', label: '가', action: 'delete', before: '', after: '', message: '' },
    { id: 'b', label: '나', action: 'delete', before: '', after: '', message: '' },
    {
      id: 'c',
      label: '다',
      action: 'error',
      before: '',
      after: '',
      message: '가리키는 것이 3개 있습니다 — 「참조를 비우고 삭제」 를 선택하거나 먼저 해제하세요.',
    },
  ],
}

async function open() {
  objectApi.bulkDelete.mockResolvedValue(PLAN)
  const { BulkDeleteDialog } = await import('@/modules/objects/BulkDeleteDialog')
  const onApplied = vi.fn()
  render(
    <BulkDeleteDialog
      typeSlug="part"
      typeLabel="부품"
      ids={['a', 'b', 'c']}
      onClose={vi.fn()}
      onApplied={onApplied}
    />,
  )
  return onApplied
}

describe('여럿 골라 삭제', () => {
  it('계획을 먼저 보고, 지울 것이 있어야 삭제할 수 있다', async () => {
    const onApplied = await open()
    // 계획 전에는 못 누른다 — 무엇이 지워지는지 모른 채 누르는 일이 없어야 한다.
    expect(screen.getByRole('button', { name: '삭제' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await waitFor(() =>
      expect(objectApi.bulkDelete).toHaveBeenCalledWith('part', {
        ids: ['a', 'b', 'c'],
        mode: 'block',
        apply: false,
      }),
    )
    expect(await screen.findByRole('button', { name: '2건 삭제' })).toBeEnabled()
    expect(onApplied).not.toHaveBeenCalled()
  })

  it('가리키는 것이 있어 안 지워지는 줄은 이유가 붙는다', async () => {
    await open()
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    expect(await screen.findByText(/가리키는 것이 3개 있습니다/)).toBeInTheDocument()
  })

  it('방식을 바꾸면 계획을 버린다 — 지난 계획을 보고 지우지 않게', async () => {
    await open()
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await screen.findByRole('button', { name: '2건 삭제' })

    await userEvent.click(screen.getByRole('radio', { name: /참조를 비우고 삭제/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: '삭제' })).toBeDisabled())
    expect(screen.queryByText(/가리키는 것이 3개 있습니다/)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await waitFor(() =>
      expect(objectApi.bulkDelete).toHaveBeenLastCalledWith('part', {
        ids: ['a', 'b', 'c'],
        mode: 'detach',
        apply: false,
      }),
    )
  })

  it('적용하면 목록에 알린다', async () => {
    objectApi.bulkDelete
      .mockResolvedValueOnce(PLAN)
      .mockResolvedValueOnce({ ...PLAN, applied: true })
    const { BulkDeleteDialog } = await import('@/modules/objects/BulkDeleteDialog')
    const onApplied = vi.fn()
    render(
      <BulkDeleteDialog
        typeSlug="part"
        typeLabel="부품"
        ids={['a', 'b', 'c']}
        onClose={vi.fn()}
        onApplied={onApplied}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await userEvent.click(await screen.findByRole('button', { name: '2건 삭제' }))
    await waitFor(() => expect(onApplied).toHaveBeenCalled())
    expect(await screen.findByText('지웠습니다.')).toBeInTheDocument()
  })
})
