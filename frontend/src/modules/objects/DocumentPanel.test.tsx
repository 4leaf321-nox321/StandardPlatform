/**
 * 문서 속성(ADR 0053) — 보기 · 판 · 고치기 · 충돌.
 *
 * - 마크다운으로 그리고(표 · 안쪽 링크), 글 속 HTML 은 그리지 않는다.
 * - 토큰(MCP · AI)으로 쓴 판은 머리에 그 토큰 이름이 선다 — 사람이 검토할 근거.
 * - 저장할 때 보고 고친 판을 보내고, 그사이 바뀌었으면 덮지 않고 충돌 상자가 선다 —
 *   「내 글로 덮어쓰기」 는 지금 판을 보고 고친 것으로 다시 보낸다.
 * - 판 이력에서 옛 판을 열어 복원한다(작성 중으로 열리고 저장해야 반영).
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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

const DEF = { key: 'body', label: '본문', data_type: 'document' } as PropertyDef

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
  beforeEach(() => vi.clearAllMocks())

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
})
