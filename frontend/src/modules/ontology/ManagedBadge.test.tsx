/**
 * 원천이 잠근 정의의 표시와 「잠금 해제」(ADR 0090).
 *
 * - 잠근 원천의 이름이 보인다(「허브 관리」 만으로는 datarelay 인지 허브인지 모른다).
 * - 「잠금 해제」 는 시스템 관리자만 — 확인 창이 「원천이 다시 보내면 다시 잠근다」 를 먼저 말한다.
 * - 누른 것은 표의 줄로 안 올라간다(줄을 누르면 수정 창이 열린다).
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const ontologyApi = vi.hoisted(() => ({ unlock: vi.fn() }))
vi.mock('@/modules/ontology/api', async (original) => {
  const real = await original<typeof import('@/modules/ontology/api')>()
  return { ...real, ontologyApi }
})
const auth = vi.hoisted(() => ({ value: null as null | { user: { is_system_admin: boolean } } }))
vi.mock('@/shared/auth/AuthContext', () => ({ useOptionalAuth: () => auth.value }))

import { ManagedBadge } from '@/modules/ontology/ManagedBadge'

function draw(onUnlocked = vi.fn(), onRow = vi.fn()) {
  render(
    <div onClick={onRow}>
      <ManagedBadge
        kind="types"
        slug="plm_demo_lot"
        label="시험 로트"
        owner="datarelay"
        onUnlocked={onUnlocked}
      />
    </div>,
  )
  return { onUnlocked, onRow }
}

describe('ManagedBadge', () => {
  beforeEach(() => {
    ontologyApi.unlock.mockReset()
    auth.value = null
  })

  it('잠근 원천의 이름을 보이고, 시스템 관리자가 아니면 풀 수 없다', () => {
    auth.value = { user: { is_system_admin: false } }
    draw()
    expect(screen.getByText('datarelay 관리')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '잠금 해제' })).toBeNull()
  })

  it('시스템 관리자는 확인 뒤에 풀고, 누른 것이 줄로 안 올라간다', async () => {
    auth.value = { user: { is_system_admin: true } }
    ontologyApi.unlock.mockResolvedValue({ slug: 'plm_demo_lot', was: 'datarelay' })
    const { onUnlocked, onRow } = draw()
    await userEvent.click(screen.getByRole('button', { name: '잠금 해제' }))
    expect(screen.getByText(/다음 적재가 다시 잠그고 덮어씁니다/)).toBeInTheDocument()
    expect(ontologyApi.unlock).not.toHaveBeenCalled()
    const buttons = screen.getAllByRole('button', { name: '잠금 해제' })
    await userEvent.click(buttons[buttons.length - 1])
    expect(ontologyApi.unlock).toHaveBeenCalledWith('types', 'plm_demo_lot')
    expect(onUnlocked).toHaveBeenCalled()
    expect(onRow).not.toHaveBeenCalled()
  })
})
