/**
 * 객체 생성의 소유 부서 — **내가 관리자인 부서로 보낸다.**
 *
 * 대표 소속을 고정으로 보내던 때, B 의 관리자인데 대표 소속 A 에서는 멤버인 사람은 늘 403 을
 * 봤다(서버의 `require_manager`, 2026-10-08).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ObjectType } from '@/modules/ontology/api'

const objectApi = vi.hoisted(() => ({ create: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({
    user: {
      home_workspace_slug: 'hq',
      is_system_admin: false,
      memberships: [
        { slug: 'hq', name: '본사', path: '본사', role: 'member' },
        { slug: 'lab', name: '시험팀', path: '본사 / 시험팀', role: 'manager' },
      ],
    },
  }),
}))

const TYPE = { slug: 'part', label: '부품', key_policy: 'none' } as unknown as ObjectType

describe('객체 생성', () => {
  it('대표 소속에서 멤버뿐이면 관리하는 부서의 것으로 만든다', async () => {
    objectApi.create.mockResolvedValue({ id: 'o1' })
    const { ObjectCreateDialog } = await import('@/modules/objects/ObjectCreateDialog')
    const onCreated = vi.fn()
    render(<ObjectCreateDialog type={TYPE} defs={[]} onClose={vi.fn()} onCreated={onCreated} />)
    expect(screen.getByRole('combobox', { name: '소유 부서' })).toHaveTextContent('시험팀')
    await userEvent.type(screen.getByLabelText(/이름/), '볼트')
    await userEvent.click(screen.getByRole('button', { name: '생성' }))
    await waitFor(() =>
      expect(objectApi.create).toHaveBeenCalledWith(
        'part',
        expect.objectContaining({ label: '볼트', workspace_slug: 'lab' }),
      ),
    )
    expect(onCreated).toHaveBeenCalled()
  })
})
