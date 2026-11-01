/**
 * 회의 목록이 지키는 것 — 일자가 새것부터 서고 누르면 그 한 장으로, 빈 목록은 까닭을 말하고, 「새
 * 회의」 는 종류 · 제목 · 일자(· 부서)만 묻고 만든 한 장으로 바로 간다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const jvApi = vi.hoisted(() => ({ meetings: vi.fn(), create: vi.fn() }))
vi.mock('@/extensions/jajuverify/api', async (original) => ({
  ...(await original<typeof import('@/extensions/jajuverify/api')>()),
  jvApi,
}))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({
    user: {
      is_system_admin: false,
      home_workspace_slug: 'qa',
      memberships: [{ slug: 'qa', name: '품질팀', path: '품질팀', role: 'member' }],
    },
  }),
}))

const MEETING = {
  id: 'm1',
  kind: 'review',
  kind_label: '심사회',
  title: '모델 A 1차 심사회',
  held_on: '2026-10-28',
  workspace_slug: 'qa',
  workspace_name: '품질팀',
  author_name: '홍길동',
  created_at: '2026-10-28T00:00:00Z',
  updated_at: '2026-10-28T00:00:00Z',
  row_count: 4,
  done_count: 3,
  revision: 5,
  confirmed_at: null,
  confirmed_by_name: null,
}

async function show() {
  const { MeetingListPage } = await import('@/extensions/jajuverify/MeetingListPage')
  render(
    <MemoryRouter initialEntries={['/ext/jajuverify/meetings/review']}>
      <Routes>
        <Route path="ext/jajuverify/meetings/review" element={<MeetingListPage kind="review" />} />
        <Route path="ext/jajuverify/meetings/review/:meetingId" element={<p>한 장 화면</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('자주검증 회의 목록', () => {
  it('회의가 서고 누르면 그 한 장으로 간다', async () => {
    jvApi.meetings.mockResolvedValue({ items: [MEETING], total: 1, limit: 50, offset: 0 })
    await show()
    const link = await screen.findByRole('link', { name: '모델 A 1차 심사회' })
    expect(link.getAttribute('href')).toBe('/ext/jajuverify/meetings/review/m1')
    expect(jvApi.meetings).toHaveBeenCalledWith('review', { q: '', offset: 0 })
    expect(screen.getByText('품질팀')).toBeInTheDocument()
  })

  it('상태 열은 「확정」 또는 「작성 중 3/4」(작성 완료한 줄 / 줄)', async () => {
    jvApi.meetings.mockResolvedValue({
      items: [
        MEETING,
        {
          ...MEETING,
          id: 'm2',
          title: '확정한 회의',
          confirmed_at: '2026-10-31T01:00:00Z',
          confirmed_by_name: '김부장',
        },
      ],
      total: 2,
      limit: 50,
      offset: 0,
    })
    await show()
    const open = (await screen.findByRole('link', { name: '모델 A 1차 심사회' })).closest('tr')!
    expect(within(open).getByText('작성 중')).toBeInTheDocument()
    expect(within(open).getByText('3/4')).toBeInTheDocument()
    const done = screen.getByRole('link', { name: '확정한 회의' }).closest('tr')!
    expect(within(done).getByText('확정')).toBeInTheDocument()
  })

  it('비어 있으면 까닭을 말한다 — 검색이 좁은지, 아직 없는지', async () => {
    jvApi.meetings.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 })
    await show()
    expect(await screen.findByText('아직 심사회가 없습니다')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('검색'), '하우징{Enter}')
    expect(await screen.findByText(/「하우징」 에 맞는 심사회가 없습니다/)).toBeInTheDocument()
    expect(jvApi.meetings).toHaveBeenLastCalledWith('review', { q: '하우징', offset: 0 })
  })

  it('새 회의는 종류 · 제목 · 일자만 묻고(소속 하나면 부서는 안 묻는다) 만든 한 장으로 간다', async () => {
    jvApi.meetings.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 })
    jvApi.create.mockResolvedValue({ ...MEETING, id: 'm9' })
    await show()
    await userEvent.click(await screen.findByRole('button', { name: /새 심사회/ }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByLabelText('부서')).toBeNull()
    await userEvent.type(within(dialog).getByLabelText('제목'), '모델 B 심사회')
    await userEvent.click(within(dialog).getByRole('button', { name: '생성' }))
    await waitFor(() =>
      expect(jvApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'review', title: '모델 B 심사회', workspace_slug: 'qa' }),
      ),
    )
    expect(await screen.findByText('한 장 화면')).toBeInTheDocument()
  })
})
