/**
 * 「코어」 — **바깥에 무엇을 열었는지 화면이 말한다.**
 *
 * 공개 여부를 창 안에서만 알 수 있으면 열어 둔 것을 잊는다. 그리고 투영 타입(부서 · 계정)은
 * 애초에 고를 수 없어야 한다 — 행이 원 표에 있어 사람 정보가 그대로 나간다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const ontologyApi = vi.hoisted(() => ({ updateType: vi.fn(), propertyUsage: vi.fn() }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

const TYPE = {
  id: 't1',
  slug: 'material',
  label: '재료',
  icon: '',
  description: '',
  sort_order: 0,
  nav_group_id: null,
  nav_group_slug: null,
  kind_class: 'record' as const,
  system_source: '',
  entry_policy: 'open' as const,
  key_policy: 'optional' as const,
  key_scope: 'global' as const,
  temporal_kind: 'evergreen' as const,
  list_view: {},
  form_view: {},
  detail_view: {},
  title_template: '',
  is_active: true,
  object_count: 3,
  core: false,
  properties: [],
}

async function open(overrides: Record<string, unknown> = {}) {
  const { TypeEditDialog } = await import('@/modules/ontology/TypeEditDialog')
  render(
    <TypeEditDialog
      type={{ ...TYPE, ...overrides } as never}
      groups={[]}
      relationTypes={[]}
      onClose={vi.fn()}
      onChanged={vi.fn()}
    />,
  )
}

describe('타입을 바깥에 여는 칸', () => {
  it('켜서 저장하면 core 로 나간다', async () => {
    ontologyApi.updateType.mockResolvedValue({})
    await open()

    await userEvent.click(screen.getByRole('checkbox', { name: /코어/ }))
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(ontologyApi.updateType).toHaveBeenCalledWith(
        'material',
        expect.objectContaining({ core: true }),
      ),
    )
  })

  it('켜는 순간 약속이 된다는 것을 그 자리에 적는다', async () => {
    await open()
    // 고른 뒤에 알게 되면 이미 남의 시스템이 그 이름을 쓰고 있다.
    expect(screen.getByText(/약속이 됩니다/)).toBeInTheDocument()
  })

  it('투영 타입에는 칸이 아예 없다', async () => {
    await open({ kind_class: 'system', system_source: 'workspace', object_count: 0 })
    expect(screen.queryByRole('checkbox', { name: /코어/ })).not.toBeInTheDocument()
  })
})
