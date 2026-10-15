/**
 * 연계 — 고른 것은 **지금 보이는 연계만** 대상이다. 「시험 연계」 탭에서 고른 채 「전용 검토」 로
 * 옮기면 선택이 비고, 해제가 앞 탭의 연계를 평가 · 이력과 함께 지우지 않는다(2026-10-16).
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { Pair } from '@/extensions/caegroup/api'

const dtApi = vi.hoisted(() => ({
  setupStatus: vi.fn(),
  defs: vi.fn(),
  pairs: vi.fn(),
  bulkUnlink: vi.fn(),
  bulkMove: vi.fn(),
  assessments: vi.fn().mockResolvedValue([]),
  history: vi.fn().mockResolvedValue([]),
}))
vi.mock('@/extensions/caegroup/api', async (original) => ({
  ...(await original<typeof import('@/extensions/caegroup/api')>()),
  dtApi,
}))
vi.mock('@/modules/workspaces/api', () => ({
  workspaceApi: { list: vi.fn().mockResolvedValue([{ slug: 'a', name: 'A 부서' }]) },
}))
vi.mock('@/modules/objects/api', () => ({
  objectApi: { list: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
}))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({
    user: { is_system_admin: true, home_workspace_slug: 'a', memberships: [{ slug: 'a' }] },
  }),
}))

function pair(id: string, kind: Pair['kind'], subject: string): Pair {
  return {
    id,
    kind,
    workspace_id: 'w',
    workspace_name: 'A 부서',
    subject_id: `${id}-s`,
    subject_label: subject,
    agent_id: `${id}-a`,
    agent_label: `${subject} 해석`,
    agent_tools: [],
    agent_dept: null,
    assessed: 0,
    created_at: '2026-10-01T00:00:00Z',
  }
}

describe('연계 — 탭을 옮기면', () => {
  it('앞 탭에서 고른 연계는 대상이 아니고, 해제는 이 탭에서 고른 것만 지운다', async () => {
    let releaseSimOnly: (value: Pair[]) => void = () => undefined
    dtApi.setupStatus.mockResolvedValue({
      ready: true,
      subject_type_slug: 'subject',
      subject_type_label: '시험 항목',
      sim_only_type_slug: 'review',
      sim_only_type_label: '검토 항목',
      agent_type_slug: 'agent',
      agent_type_label: '해석',
    })
    dtApi.defs.mockResolvedValue({
      sector: 'cae',
      sector_label: 'CAE',
      subject_label: '시험 항목',
      agent_label: '해석',
      axes: [],
      pair_kinds: [
        { key: 'test', label: '시험 연계', subject_label: '시험 항목', description: '' },
        { key: 'sim_only', label: '전용 검토', subject_label: '검토 항목', description: '' },
      ],
      accuracy_thresholds: [],
      accuracy_rules: [],
      sw_units: [],
      sw_purposes: [],
    })
    dtApi.pairs.mockImplementation((kind: string) =>
      kind === 'test'
        ? Promise.resolve([pair('t1', 'test', '낙하 시험')])
        : new Promise((resolve) => {
            releaseSimOnly = resolve
          }),
    )
    dtApi.bulkUnlink.mockResolvedValue({ removed: 1 })
    const { default: PairsPage } = await import('@/extensions/caegroup/PairsPage')
    render(
      <MemoryRouter>
        <PairsPage />
      </MemoryRouter>,
    )

    await userEvent.click(await screen.findByRole('checkbox', { name: /낙하 시험 · .* 선택/ }))
    expect(screen.getByText('1건 선택')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: '전용 검토' }))
    await waitFor(() => expect(dtApi.pairs).toHaveBeenCalledWith('sim_only'))
    // 앞 탭의 목록이 아직 보이는 사이에도 고른 것은 없다 — 해제할 것이 없다.
    expect(screen.queryByText(/건 선택/)).not.toBeInTheDocument()

    releaseSimOnly([pair('s1', 'sim_only', '전용 검토 A')])
    await userEvent.click(await screen.findByRole('checkbox', { name: /전용 검토 A · .* 선택/ }))
    expect(screen.getByText('1건 선택')).toBeInTheDocument()
    // 선택 막대의 「해제」 — 줄마다의 해제 단추와 가른다.
    const bar = screen.getByText('1건 선택').parentElement as HTMLElement
    await userEvent.click(within(bar).getByRole('button', { name: /해제/ }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('연계 1건을 해제합니까?')).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: '해제' }))
    await waitFor(() => expect(dtApi.bulkUnlink).toHaveBeenCalledWith(['s1']))
  })
})
