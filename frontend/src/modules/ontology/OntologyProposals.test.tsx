/**
 * 정의 제안(ADR 0096) — **계획 전부를 읽고, 처리는 검토함에서.**
 *
 * 지키는 것: 제안마다 요약 · 상태 · 누가 · 어느 AI 도구 · 근거를 보이는가, 계획(바뀌는 것 ·
 * 경고)을 가져오기 화면과 같은 모양으로 보이는가, 「다시 셈」 이 달라졌는지 알리는가, 「철회」 가
 * 확인을 거치는가, 상태를 바꾸면 그 상태로 다시 읽는가, 처리는 검토함으로 가는가.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import OntologyProposalsPage from '@/modules/ontology/OntologyProposalsPage'

const ontologyApi = vi.hoisted(() => ({
  proposals: vi.fn(),
  replanProposal: vi.fn(),
  withdrawProposal: vi.fn(),
}))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

function proposal(over: Record<string, unknown> = {}) {
  return {
    id: 'p1',
    status: 'proposed',
    status_label: '제안',
    summary: '타입 새로 1 · 속성 새로 1',
    source: '',
    note: '공급 계약서 3건에서 협력사 · 국가가 반복',
    origin_ref: '계약서-2026.docx',
    tool: 'ontology_propose',
    token_name: 'bot',
    proposed_by: '김구성',
    created_at: '2026-11-25T01:00:00Z',
    planned_at: '2026-11-25T01:00:00Z',
    decided_by: '',
    decided_at: null,
    decision_note: '',
    snapshot_id: null,
    plan: {
      applied: false,
      changes: [
        { kind: 'type', slug: 'vendor', action: 'create', fields: [] },
        { kind: 'property', slug: 'vendor.country', action: 'create', fields: [] },
      ],
      warnings: ['source 없이 새 정의(vendor)를 생성합니다'],
      errors: [],
      snapshot_id: null,
    },
    fingerprint: 'abc',
    created: false,
    replanned: null,
    review_link: '/review?kind=definition_proposal',
    ...over,
  }
}

function open() {
  render(
    <MemoryRouter>
      <OntologyProposalsPage />
    </MemoryRouter>,
  )
}

describe('정의 제안', () => {
  beforeEach(() => vi.clearAllMocks())

  it('제안마다 근거 · 누가 · 계획을 보이고 처리는 검토함으로 간다', async () => {
    ontologyApi.proposals.mockResolvedValue([proposal()])
    open()
    expect(await screen.findByText('타입 새로 1 · 속성 새로 1')).toBeInTheDocument()
    expect(ontologyApi.proposals).toHaveBeenCalledWith('proposed')
    expect(screen.getByText(/김구성 · AI 도구 ontology_propose/)).toBeInTheDocument()
    expect(screen.getByText('공급 계약서 3건에서 협력사 · 국가가 반복')).toBeInTheDocument()
    expect(screen.getByText('계약서-2026.docx')).toBeInTheDocument()
    expect(screen.getByText('vendor.country')).toBeInTheDocument()
    expect(screen.getByText(/source 없이 새 정의/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /검토함에서 처리/ })).toHaveAttribute(
      'href',
      '/review?kind=definition_proposal',
    )
  })

  it('다시 셈은 계획이 달라졌는지 알리고, 철회는 확인을 거친다', async () => {
    ontologyApi.proposals.mockResolvedValue([proposal()])
    ontologyApi.replanProposal.mockResolvedValue(
      proposal({ summary: '속성 새로 1', replanned: true }),
    )
    ontologyApi.withdrawProposal.mockResolvedValue(proposal({ status: 'withdrawn' }))
    open()
    await userEvent.click(await screen.findByRole('button', { name: /다시 셈/ }))
    expect(await screen.findByText(/지금은 「속성 새로 1」 입니다/)).toBeInTheDocument()
    expect(ontologyApi.replanProposal).toHaveBeenCalledWith('p1')

    await userEvent.click(screen.getByRole('button', { name: '철회' }))
    expect(ontologyApi.withdrawProposal).not.toHaveBeenCalled()
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: '철회' }))
    await waitFor(() => expect(ontologyApi.withdrawProposal).toHaveBeenCalledWith('p1'))
  })

  it('상태를 바꾸면 그 상태로 다시 읽고, 적용한 것은 되돌릴 자리를 알린다', async () => {
    ontologyApi.proposals.mockResolvedValueOnce([]).mockResolvedValueOnce([
      proposal({
        status: 'accepted',
        status_label: '적용',
        decided_by: '관리자',
        decided_at: '2026-11-25T02:00:00Z',
        snapshot_id: 's1',
      }),
    ])
    open()
    expect(await screen.findByText('정의 제안이 없습니다')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('상태'), 'accepted')
    expect(await screen.findByText(/가져오기 › 정의 이력/)).toBeInTheDocument()
    expect(ontologyApi.proposals).toHaveBeenLastCalledWith('accepted')
    expect(screen.queryByRole('button', { name: /다시 셈/ })).toBeNull()
  })
})
