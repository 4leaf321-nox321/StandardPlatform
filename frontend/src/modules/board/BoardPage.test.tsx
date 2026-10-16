/**
 * 게시판(ADR 0033) — 목록은 고정 · 댓글 수 · 첨부 수를 보이고 검색 · 내 글로 거른다. 글 화면은
 * 작성자 · 시스템 관리자에게만 수정 · 삭제 · 첨부 업로드를, 시스템 관리자에게만 고정을 보인다.
 * 새 글은 고른 파일을 글이 생긴 뒤 업로드하고, 못 올린 파일은 글 화면이 이름을 붙여 알린다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Post, PostSummary } from '@/modules/board/api'

const boardApi = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
  pin: vi.fn(),
  comment: vi.fn(),
  updateComment: vi.fn(),
  removeComment: vi.fn(),
}))
vi.mock('@/modules/board/api', () => ({ boardApi, POST_TABLE: 'board_posts' }))
const attachmentApi = vi.hoisted(() => ({ upload: vi.fn() }))
vi.mock('@/modules/files/api', () => ({ attachmentApi }))
// 첨부 패널은 따로 시험한다(AttachmentList.test) — 여기서는 무엇을 넘기는지만.
vi.mock('@/modules/files/AttachmentList', () => ({
  AttachmentList: (props: { ownerTable: string; ownerId: string; canEdit?: boolean }) => (
    <div data-testid="attachments">
      {props.ownerTable}:{props.ownerId}:{props.canEdit ? '업로드 가능' : '보기만'}
    </div>
  ),
}))
const auth = vi.hoisted(() => ({ user: { is_system_admin: false, memberships: [] } }))
vi.mock('@/shared/auth/AuthContext', () => ({ useAuth: () => auth }))

function summary(extra: Partial<PostSummary> = {}): PostSummary {
  return {
    id: 'p1',
    title: '빌드 서버 느림',
    author_id: 'u1',
    author_name: '김작성',
    pinned: false,
    created_at: '2026-10-16T01:00:00Z',
    edited_at: null,
    comment_count: 0,
    attachment_count: 0,
    ...extra,
  }
}

function post(extra: Partial<Post> = {}): Post {
  return { ...summary(), body: '어제부터 느립니다', can_edit: false, comments: [], ...extra }
}

function at(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/board" element={<BoardPageLazy />} />
        <Route path="/board/new" element={<EditLazy />} />
        <Route path="/board/:postId" element={<PostLazy />} />
      </Routes>
    </MemoryRouter>,
  )
}

let BoardPageLazy: () => React.JSX.Element
let PostLazy: () => React.JSX.Element
let EditLazy: () => React.JSX.Element | null

describe('게시판', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    auth.user.is_system_admin = false
    BoardPageLazy = (await import('@/modules/board/BoardPage')).default
    PostLazy = (await import('@/modules/board/BoardPostPage')).default
    EditLazy = (await import('@/modules/board/BoardEditPage')).default
  })

  it('목록은 고정 · 댓글 수 · 첨부 수를 보이고 검색 · 내 글로 거른다', async () => {
    boardApi.list.mockResolvedValue({
      items: [summary({ pinned: true, comment_count: 3, attachment_count: 2 })],
      total: 1,
      limit: 50,
      offset: 0,
    })
    at('/board')
    const link = await screen.findByRole('link', { name: '빌드 서버 느림' })
    expect(link).toHaveAttribute('href', '/board/p1')
    const row = link.closest('tr') as HTMLElement
    expect(within(row).getByText('고정')).toBeInTheDocument()
    expect(within(row).getByTitle('댓글')).toHaveTextContent('3')
    expect(within(row).getByTitle('첨부')).toHaveTextContent('2')

    await userEvent.type(screen.getByRole('textbox', { name: '검색어' }), '서버')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))
    await waitFor(() =>
      expect(boardApi.list).toHaveBeenLastCalledWith({ q: '서버', mine: false, offset: 0 }),
    )
    await userEvent.click(screen.getByRole('checkbox', { name: '내 글' }))
    await waitFor(() =>
      expect(boardApi.list).toHaveBeenLastCalledWith({ q: '서버', mine: true, offset: 0 }),
    )
  })

  it('거른 목록이 비면 조건을 말한다', async () => {
    boardApi.list.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 })
    at('/board')
    expect(await screen.findByText('아직 글이 없습니다')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: '내 글' }))
    expect(await screen.findByText('조건에 맞는 글이 없습니다')).toBeInTheDocument()
  })

  it('남의 글에는 수정 · 삭제 · 고정이 없고 첨부는 보기만', async () => {
    boardApi.get.mockResolvedValue(post())
    at('/board/p1')
    expect(await screen.findByText('어제부터 느립니다')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '삭제' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '수정' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '고정' })).not.toBeInTheDocument()
    expect(screen.getByTestId('attachments')).toHaveTextContent('board_posts:p1:보기만')
  })

  it('작성자는 수정 · 삭제하고 첨부를 업로드하며, 시스템 관리자는 고정한다', async () => {
    auth.user.is_system_admin = true
    boardApi.get.mockResolvedValue(post({ can_edit: true }))
    boardApi.pin.mockResolvedValue(post({ can_edit: true, pinned: true }))
    at('/board/p1')
    expect(await screen.findByRole('link', { name: '수정' })).toHaveAttribute(
      'href',
      '/board/p1/edit',
    )
    expect(screen.getByTestId('attachments')).toHaveTextContent('업로드 가능')
    await userEvent.click(screen.getByRole('button', { name: '고정' }))
    await waitFor(() => expect(boardApi.pin).toHaveBeenCalledWith('p1', true))
    expect(boardApi.get).toHaveBeenCalledTimes(2) // 다시 읽는다
  })

  it('댓글을 작성하면 다시 읽고, 내 댓글만 수정 · 삭제가 보인다', async () => {
    boardApi.get.mockResolvedValue(
      post({
        comments: [
          {
            id: 'c1',
            body: '저도 그렇습니다',
            author_id: 'u2',
            author_name: '이댓글',
            created_at: '2026-10-16T02:00:00Z',
            edited_at: null,
            can_edit: false,
          },
          {
            id: 'c2',
            body: '확인했습니다',
            author_id: 'u3',
            author_name: '나',
            created_at: '2026-10-16T03:00:00Z',
            edited_at: '2026-10-16T04:00:00Z',
            can_edit: true,
          },
        ],
      }),
    )
    boardApi.comment.mockResolvedValue({})
    at('/board/p1')
    const section = await screen.findByRole('region', { name: '댓글' })
    const items = within(section).getAllByRole('listitem')
    expect(within(items[0]).queryByRole('button', { name: '수정' })).not.toBeInTheDocument()
    expect(within(items[1]).getByRole('button', { name: '수정' })).toBeInTheDocument()
    expect(within(items[1]).getByText(/수정됨/)).toBeInTheDocument()

    await userEvent.type(screen.getByRole('textbox', { name: '새 댓글' }), '오늘은 괜찮네요')
    await userEvent.click(screen.getByRole('button', { name: '댓글 작성' }))
    await waitFor(() => expect(boardApi.comment).toHaveBeenCalledWith('p1', '오늘은 괜찮네요'))
    await waitFor(() => expect(boardApi.get).toHaveBeenCalledTimes(2))
  })

  it('새 글은 고른 파일을 글이 생긴 뒤 업로드하고, 못 올린 파일은 글 화면이 알린다', async () => {
    boardApi.create.mockResolvedValue(post({ id: 'p9', can_edit: true }))
    boardApi.get.mockResolvedValue(post({ id: 'p9', can_edit: true }))
    attachmentApi.upload
      .mockResolvedValueOnce({ id: 'a1' })
      .mockRejectedValueOnce(new Error('너무 큽니다'))
    at('/board/new')
    await userEvent.type(await screen.findByLabelText('제목'), '절차서 공유')
    await userEvent.type(screen.getByLabelText('본문'), '첨부를 보세요')
    await userEvent.upload(screen.getByLabelText('첨부할 파일'), [
      new File(['a'], '절차서.pdf', { type: 'application/pdf' }),
      new File(['b'], '큰파일.zip', { type: 'application/zip' }),
    ])
    expect(screen.getByText('절차서.pdf')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '등록' }))

    await waitFor(() =>
      expect(boardApi.create).toHaveBeenCalledWith({ title: '절차서 공유', body: '첨부를 보세요' }),
    )
    expect(attachmentApi.upload).toHaveBeenCalledTimes(2)
    expect(attachmentApi.upload.mock.calls[0][0]).toMatchObject({
      ownerTable: 'board_posts',
      ownerId: 'p9',
      workspaceSlug: null,
    })
    expect(await screen.findByRole('alert')).toHaveTextContent('큰파일.zip')
    expect(boardApi.get).toHaveBeenCalledWith('p9')
  })
})
