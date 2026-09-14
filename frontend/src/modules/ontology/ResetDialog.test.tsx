/**
 * 초기화 창이 지키는 것 — **무엇이 사라지는지 적고, 적어야 넘어가고, 뭐가 안 돌아오는지 말한다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const ontologyApi = vi.hoisted(() => ({ reset: vi.fn() }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

const PLAN = {
  applied: false,
  total: 27,
  confirm_phrase: '온톨로지 초기화',
  snapshot_id: null,
  items: [
    { table: 'object_types', label: '타입', count: 5 },
    { table: 'objects', label: '객체(지운 것 포함)', count: 22 },
    { table: 'data_sources', label: '데이터 소스', count: 0 },
  ],
}

async function open(plan: object = PLAN) {
  ontologyApi.reset.mockResolvedValue(plan)
  const { ResetDialog } = await import('@/modules/ontology/ResetDialog')
  const onDone = vi.fn()
  render(<ResetDialog onClose={vi.fn()} onDone={onDone} />)
  return onDone
}

describe('온톨로지 초기화', () => {
  it('무엇이 몇 건 사라지는지 적는다 — 0 건은 안 적는다', async () => {
    // 「정말 삭제하시겠습니까」 만 묻는 창은 아무도 안 읽고 예를 누른다.
    await open()
    expect(await screen.findByText('타입')).toBeInTheDocument()
    expect(screen.getByText('22건')).toBeInTheDocument()
    expect(screen.queryByText('데이터 소스')).not.toBeInTheDocument()
  })

  it('되돌릴 수 있는 것과 없는 것을 가른다', async () => {
    // 이 비대칭을 안 적으면 사람은 「되돌리면 되지」 로 읽는다.
    await open()
    expect(await screen.findByText(/객체와 관계는 안 돌아옵니다/)).toBeInTheDocument()
  })

  it('문구를 그대로 적어야 단추가 산다', async () => {
    await open()
    const button = await screen.findByRole('button', { name: /27건 삭제/ })
    expect(button).toBeDisabled()

    await userEvent.type(screen.getByPlaceholderText('온톨로지 초기화'), '초기화')
    expect(button).toBeDisabled()

    await userEvent.clear(screen.getByPlaceholderText('온톨로지 초기화'))
    await userEvent.type(screen.getByPlaceholderText('온톨로지 초기화'), '온톨로지 초기화')
    expect(button).toBeEnabled()

    await userEvent.click(button)
    await waitFor(() =>
      expect(ontologyApi.reset).toHaveBeenLastCalledWith({
        apply: true,
        confirm: '온톨로지 초기화',
      }),
    )
  })

  it('이미 비어 있으면 지울 단추를 안 보인다', async () => {
    await open({ ...PLAN, total: 0, items: [{ table: 'objects', label: '객체', count: 0 }] })
    expect(await screen.findByText('지울 것이 없습니다. 이미 비어 있습니다.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /삭제/ })).not.toBeInTheDocument()
  })
})
