/**
 * 문서 속성(ADR 0053) — 보기 · 판 · 고치기 · 충돌.
 *
 * - 마크다운으로 그리고(표 · 안쪽 링크), 글 속 HTML 은 그리지 않는다.
 * - 토큰(MCP · AI)으로 쓴 판은 머리에 그 토큰 이름이 선다 — 사람이 검토할 근거.
 * - 저장할 때 보고 고친 판을 보내고, 그사이 바뀌었으면 덮지 않고 충돌 상자가 선다 —
 *   「내 글로 덮어쓰기」 는 지금 판을 보고 고친 것으로 다시 보낸다.
 * - 판 이력에서 옛 판을 열어 복원한다(작성 중으로 열리고 저장해야 반영).
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Attachment } from '@/modules/files/api'
import type { ObjectDocument } from '@/modules/objects/documents'
import type { PropertyDef } from '@/modules/ontology/api'
import { ApiError } from '@/shared/api/client'

const documentApi = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  revisions: vi.fn(),
  revision: vi.fn(),
  mentions: vi.fn(),
}))
vi.mock('@/modules/objects/documents', () => ({ documentApi }))
const attachmentApi = vi.hoisted(() => ({
  list: vi.fn(),
  upload: vi.fn(),
  download: vi.fn(),
  remove: vi.fn(),
}))
vi.mock('@/modules/files/api', () => ({
  attachmentApi,
  attachmentPaths: {
    thumbnail: (id: string) => `/attachments/${id}/thumbnail`,
    content: (id: string) => `/attachments/${id}/content`,
  },
}))
// 첨부 상한은 서버에 묻는다 — 시험에서는 기본값으로(묻는 요청이 시험 밖으로 나가지 않게).
vi.mock('@/modules/files/limit', async (original) => ({
  ...(await original<typeof import('@/modules/files/limit')>()),
  useAttachmentLimit: () => 500 * 1024 * 1024,
}))
vi.mock('@/shared/hooks/useBlobUrl', () => ({
  useBlobUrl: (path: string | null) => ({ url: path ? `blob:${path}` : null, error: null }),
}))

const DEF = { key: 'body', label: '본문', data_type: 'document' } as PropertyDef
const PHOTO = '0f0e0d0c-0b0a-4908-8706-050403020100'

function photo(id: string, extra: Partial<Attachment> = {}): Attachment {
  return {
    id,
    owner_table: 'objects',
    owner_id: 'o1',
    owner_field: 'body',
    original_name: '하우징.png',
    content_type: 'image/png',
    size_bytes: 10,
    sha256: '',
    created_at: '2026-11-06T01:00:00Z',
    is_image: true,
    width: 800,
    height: 600,
    ...extra,
  }
}

function doc(extra: Partial<ObjectDocument> = {}): ObjectDocument {
  return {
    key: 'body',
    label: '본문',
    body: '# 힌지 내구\n\n## 시험 조건\n\n| 항목 | 값 |\n|---|---|\n| 회수 | 20만 |\n\n## 결과\n\n[N12 힌지](/o/part/p1) <script>alert(1)</script>',
    revision: 3,
    chars: 120,
    updated_at: '2026-11-06T01:00:00Z',
    updated_by_name: '관리자',
    updated_via: '백서 백필 봇',
    can_edit: true,
    ...extra,
  }
}

async function mount() {
  const { DocumentPanel } = await import('@/modules/objects/DocumentPanel')
  const onSaved = vi.fn()
  render(
    <MemoryRouter>
      <DocumentPanel typeSlug="paper" objectId="o1" def={DEF} onSaved={onSaved} />
    </MemoryRouter>,
  )
  return onSaved
}

describe('문서 속성', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    attachmentApi.list.mockResolvedValue([])
  })

  it('마크다운으로 그리고 토큰으로 쓴 판을 머리에 적는다', async () => {
    documentApi.get.mockResolvedValue(doc())
    await mount()
    const table = await screen.findByRole('table')
    expect(within(table).getByText('20만')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'N12 힌지' })).toHaveAttribute('href', '/o/part/p1')
    // 글 속 HTML 은 그리지 않는다.
    expect(document.querySelector('script')).toBeNull()
    expect(screen.getByText(/판 3/)).toBeInTheDocument()
    expect(screen.getByText('토큰 「백서 백필 봇」')).toBeInTheDocument()
    // 제목이 셋이면 목차가 선다.
    expect(await screen.findByRole('navigation', { name: '본문 목차' })).toHaveTextContent(
      '시험 조건',
    )
  })

  it('비었으면 작성하는 길을 말한다', async () => {
    documentApi.get.mockResolvedValue(doc({ body: '', revision: 0, chars: 0, updated_via: '' }))
    await mount()
    expect(await screen.findByText(/아직 작성하지 않았습니다/)).toHaveTextContent('document_write')
    expect(screen.getByRole('button', { name: /작성/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /판 이력/ })).not.toBeInTheDocument()
  })

  it('보고 고친 판을 보내고, 그사이 바뀌었으면 덮지 않고 고르게 한다', async () => {
    documentApi.get.mockResolvedValue(doc({ body: '처음', revision: 3 }))
    const onSaved = await mount()
    await userEvent.click(await screen.findByRole('button', { name: /수정/ }))
    const box = screen.getByRole('textbox', { name: '본문 본문' })
    await userEvent.clear(box)
    await userEvent.type(box, '내가 고친 글')
    await userEvent.type(screen.getByRole('textbox', { name: '수정 사유' }), '오타')

    // 그사이 AI 가 판 4 를 냈다.
    documentApi.put.mockRejectedValueOnce(
      new ApiError(409, {
        error: { code: 'APP-OBJECTS-0149', message: '먼저 수정했습니다', details: { revision: 4 } },
      }),
    )
    documentApi.get.mockResolvedValueOnce(
      doc({ body: 'AI 가 고친 글', revision: 4, updated_via: '백서 백필 봇' }),
    )
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    expect(documentApi.put).toHaveBeenLastCalledWith(
      'paper',
      'o1',
      'body',
      '내가 고친 글',
      3,
      '오타',
    )
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('AI 가 고친 글')
    expect(alert).toHaveTextContent('내가 고친 글')

    // 「내 글로 덮어쓰기」 — 지금 판(4)을 보고 고친 것으로 다시 보낸다.
    documentApi.put.mockResolvedValueOnce(doc({ body: '내가 고친 글', revision: 5 }))
    await userEvent.click(within(alert).getByRole('button', { name: /내 글로 덮어쓰기/ }))
    await waitFor(() =>
      expect(documentApi.put).toHaveBeenLastCalledWith(
        'paper',
        'o1',
        'body',
        '내가 고친 글',
        4,
        '오타',
      ),
    )
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
  })

  it('판 이력에서 옛 판을 열어 복원하면 작성 중으로 열린다', async () => {
    documentApi.get.mockResolvedValue(doc({ body: '지금 글', revision: 2 }))
    documentApi.revisions.mockResolvedValue([
      {
        revision: 2,
        chars: 4,
        created_at: '2026-11-06T01:00:00Z',
        created_by_name: '관리자',
        via: '백서 백필 봇',
        note: 'v2',
      },
      {
        revision: 1,
        chars: 4,
        created_at: '2026-11-05T01:00:00Z',
        created_by_name: '관리자',
        via: '',
        note: '처음',
      },
    ])
    documentApi.revision.mockResolvedValue({
      revision: 1,
      chars: 4,
      created_at: '2026-11-05T01:00:00Z',
      created_by_name: '관리자',
      via: '',
      note: '처음',
      body: '옛 글',
    })
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: /판 이력/ }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(await within(dialog).findByRole('button', { name: /판 1/ }))
    await userEvent.click(await within(dialog).findByRole('button', { name: '이 판으로 복원' }))

    expect(screen.getByRole('textbox', { name: '본문 본문' })).toHaveValue('옛 글')
    expect(screen.getByRole('textbox', { name: '수정 사유' })).toHaveValue('판 1 으로 복원')
    expect(documentApi.put).not.toHaveBeenCalled()
  })

  it('그림 표시는 그 문서의 그림으로 그리고, 없는 그림 · 바깥 그림은 캡션과 까닭을 적는다', async () => {
    documentApi.get.mockResolvedValue(
      doc({
        body: `# 결과\n\n![하우징 단면](photo:${PHOTO}?w=60)\n\n![](photo:11111111-2222-4333-8444-555555555555)\n\n![바깥](https://example.com/a.png)`,
      }),
    )
    attachmentApi.list.mockResolvedValue([photo(PHOTO)])
    await mount()
    const image = await screen.findByRole('img', { name: '하우징 단면' })
    // 본문 너비의 40% 를 넘으면 원본을 받는다(미리보기는 320px 라 흐리다).
    expect(image).toHaveAttribute('src', `blob:/attachments/${PHOTO}/content`)
    expect(attachmentApi.list).toHaveBeenCalledWith('objects', 'o1', 'body')
    expect(screen.getByText('그림 — 삭제됐거나 이 문서의 그림이 아닙니다')).toBeInTheDocument()
    expect(screen.getByText('그림: 바깥')).toBeInTheDocument()
    expect(document.querySelector('img[src^="https:"]')).toBeNull()
  })

  it('붙여 넣은 그림은 그 문서 자리에 업로드하고 커서 자리에 표시를 넣는다', async () => {
    const made = '22222222-3333-4444-8555-666666666666'
    documentApi.get.mockResolvedValue(doc({ body: 'A\nB' }))
    attachmentApi.list.mockResolvedValue([photo(PHOTO)])
    attachmentApi.upload.mockResolvedValue(photo(made, { original_name: '캡처.png' }))
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: /수정/ }))
    const editor = screen.getByRole('textbox', { name: '본문 본문' }) as HTMLTextAreaElement
    editor.setSelectionRange(1, 1)
    const shot = new File(['x'], '캡처.png', { type: 'image/png' })
    fireEvent.paste(editor, { clipboardData: { files: [shot], getData: () => '' } })

    await waitFor(() => expect(editor).toHaveValue(`A\n\n![](photo:${made})\n\nB`))
    expect(attachmentApi.upload).toHaveBeenCalledWith(
      expect.objectContaining({
        ownerTable: 'objects',
        ownerId: 'o1',
        ownerField: 'body',
        file: shot,
      }),
    )
    // 본문이 안 쓰는 그림(AI 가 올리기만 한 것)은 「본문에 없음」 — 다시 넣을 수 있다.
    expect(await screen.findByText('본문에 없음')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '본문에 추가' }))
    expect(editor.value).toContain(`![](photo:${PHOTO})`)
  })

  it('그림 삭제는 작성 중인 본문이 몇 곳에서 쓰는지 말하고 첨부를 삭제한다', async () => {
    documentApi.get.mockResolvedValue(
      doc({ body: `![](photo:${PHOTO}?w=50)\n\n글\n\n![다시](photo:${PHOTO})` }),
    )
    attachmentApi.list.mockResolvedValue([photo(PHOTO)])
    attachmentApi.remove.mockResolvedValue(undefined)
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: /수정/ }))
    await userEvent.click(await screen.findByRole('button', { name: '삭제' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('본문의 2곳이 이 그림을 씁니다')
    await userEvent.click(within(dialog).getByRole('button', { name: '삭제' }))
    await waitFor(() => expect(attachmentApi.remove).toHaveBeenCalledWith(PHOTO))
  })
})
