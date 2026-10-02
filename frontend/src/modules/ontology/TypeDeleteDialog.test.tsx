/**
 * 타입 삭제 창 — **서버의 미리 보기를 그대로 적는다**(ADR 0008).
 *
 * - 막는 것이 있으면 그 말을 적고 단추를 잠근다(예전 창은 살아 있는 수만 보고 「삭제할 수
 *   있습니다」 라고 했다가 서버에 거절당했다).
 * - 지운 객체만 남았으면 그 수와 함께 사라지는 것을 적고, 「영구 삭제」 로 확인을 받아 보낸다.
 * - 아무것도 없으면 그냥 「삭제」.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { DeletePlan } from '@/modules/ontology/api'

const ontologyApi = vi.hoisted(() => ({
  deletePlan: vi.fn(),
  removeType: vi.fn(),
}))
vi.mock('@/modules/ontology/api', async (original) => {
  const real = await original<typeof import('@/modules/ontology/api')>()
  return { ...real, ontologyApi }
})

const TYPE = { slug: 'part', label: '부품' }

function plan(extra: Partial<DeletePlan> = {}): DeletePlan {
  return {
    kind: 'type',
    slug: 'part',
    key: null,
    label: '부품',
    allowed: true,
    blocking: [],
    removes: ['속성 정의 2개'],
    keeps: [],
    warnings: [],
    core_consumers: [],
    purge_deleted: 0,
    ...extra,
  }
}

async function open() {
  const { TypeDeleteDialog } = await import('@/modules/ontology/TypeDeleteDialog')
  const onDone = vi.fn()
  render(<TypeDeleteDialog type={TYPE} onDone={onDone} onClose={vi.fn()} />)
  return onDone
}

beforeEach(() => {
  vi.clearAllMocks()
  ontologyApi.removeType.mockResolvedValue(undefined)
})

describe('타입 삭제 창', () => {
  it('막는 것이 있으면 서버의 말을 적고 단추를 잠근다', async () => {
    ontologyApi.deletePlan.mockResolvedValue(
      plan({
        allowed: false,
        blocking: [{ code: 'X-38', message: '부품에 3개가 들어 있어 지울 수 없습니다.' }],
      }),
    )
    await open()
    await waitFor(() => expect(screen.getByText(/3개가 들어 있어/)).toBeInTheDocument())
    expect(ontologyApi.deletePlan).toHaveBeenCalledWith('type', 'part')
    expect(screen.getByRole('button', { name: '삭제' })).toBeDisabled()
  })

  it('지운 객체만 남았으면 수와 사라지는 것을 적고 영구 삭제로 보낸다', async () => {
    ontologyApi.deletePlan.mockResolvedValue(
      plan({
        purge_deleted: 4,
        removes: ['지운 객체 4개 — 영구 삭제(되돌릴 수 없습니다)', '첨부(파일 목록의 행) 1개'],
        keeps: ['감사 기록 — 누가 언제 무엇을 했는지(이름과 함께)'],
      }),
    )
    const onDone = await open()
    await waitFor(() => expect(screen.getByText(/기록으로 남아 있습니다/)).toBeInTheDocument())
    expect(screen.getByText('지운 객체 4개 — 영구 삭제(되돌릴 수 없습니다)')).toBeInTheDocument()
    expect(screen.getByText('첨부(파일 목록의 행) 1개')).toBeInTheDocument()
    expect(screen.getByText(/감사 기록/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '영구 삭제' }))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(ontologyApi.removeType).toHaveBeenCalledWith('part', true)
  })

  it('남은 것이 없으면 그냥 삭제한다 — 영구 삭제를 보내지 않는다', async () => {
    ontologyApi.deletePlan.mockResolvedValue(plan())
    const onDone = await open()
    await waitFor(() => expect(screen.getByText('속성 정의 2개')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: '삭제' }))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(ontologyApi.removeType).toHaveBeenCalledWith('part', false)
  })
})
