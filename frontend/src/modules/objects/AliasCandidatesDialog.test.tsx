/**
 * 별칭 후보 창이 지키는 것 — **짐작(이것 아닐까)은 사람이 고르고, 추가하기 전에 무엇이 바뀌는지
 * 먼저 보이고, 막는 것이 있으면 추가하지 않는다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const aliasCandidateApi = vi.hoisted(() => ({ list: vi.fn(), attach: vi.fn(), decide: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ aliasCandidateApi }))
const searchApi = vi.hoisted(() => ({ find: vi.fn() }))
vi.mock('@/modules/search/api', () => ({ searchApi }))

const candidate = {
  id: 'c1',
  text: 'Ansys Fluet',
  scope: 'tool',
  scope_label: '툴',
  scope_kind: 'type',
  hits: 4,
  people: 2,
  vias: ['resolve', 'search'],
  status: 'pending',
  object_id: null,
  object_label: null,
  object_type_slug: null,
  first_at: '2026-10-08T00:00:00Z',
  last_at: '2026-10-08T01:00:00Z',
  decided_at: null,
  suggestions: [
    {
      id: 'o1',
      type_slug: 'tool',
      type_label: '툴',
      label: 'Ansys Fluent',
      key: null,
      matched: 'label',
      matched_text: '',
      score: 0.7,
      scored_by: '',
    },
  ],
  suggest_note: '',
  suggested_at: null as string | null,
}

function plan(apply: boolean, extra: Record<string, unknown> = {}) {
  return {
    applied: apply,
    candidate: { ...candidate, status: apply ? 'attached' : 'pending' },
    object: { id: 'o1', type_slug: 'tool', type_label: '툴', label: 'Ansys Fluent', key: null },
    value: 'Ansys Fluet',
    aliases_before: [],
    aliases_after: ['Ansys Fluet'],
    warnings: [],
    blocking: [],
    closed: apply ? 1 : 0,
    ...extra,
  }
}

describe('별칭 후보', () => {
  it('고른 객체의 계획을 먼저 보이고, 확인하면 별칭으로 추가한다', async () => {
    aliasCandidateApi.list.mockResolvedValue({ items: [candidate], total: 1, limit: 50, offset: 0 })
    aliasCandidateApi.attach.mockImplementation((_id: string, _o: string, apply: boolean) =>
      Promise.resolve(plan(apply)),
    )
    const onDone = vi.fn()
    const { AliasCandidatesDialog } = await import('@/modules/objects/AliasCandidatesDialog')
    render(<AliasCandidatesDialog onClose={() => {}} onDone={onDone} />)
    await waitFor(() => expect(screen.getByText('Ansys Fluet')).toBeInTheDocument())
    expect(screen.getByText(/툴 · 4번 · 2명 · 이름 풀이 · 통합 검색/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /Ansys Fluent/ }))
    await waitFor(() => expect(aliasCandidateApi.attach).toHaveBeenCalledWith('c1', 'o1', false))
    expect(screen.getByText(/별칭: 없음 → Ansys Fluet/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '별칭 추가' }))
    await waitFor(() => expect(aliasCandidateApi.attach).toHaveBeenCalledWith('c1', 'o1', true))
    expect(screen.getByText(/Ansys Fluent의 별칭으로 추가했습니다/)).toBeInTheDocument()
    expect(onDone).toHaveBeenCalled()
  })

  it('미리 셈한 「이것 아닐까」 면 언제 셈했는지 보인다(ADR 0074)', async () => {
    aliasCandidateApi.list.mockResolvedValue({
      items: [{ ...candidate, suggested_at: '2026-11-16T16:05:00Z' }],
      total: 1,
      limit: 50,
      offset: 0,
    })
    const { AliasCandidatesDialog } = await import('@/modules/objects/AliasCandidatesDialog')
    render(<AliasCandidatesDialog onClose={() => {}} onDone={() => {}} />)
    await waitFor(() => expect(screen.getByText('Ansys Fluet')).toBeInTheDocument())
    expect(screen.getByText(/\(.*2026.*계산\)/)).toBeInTheDocument()
  })

  it('뜻으로 찾은 「이것 아닐까」 는 그렇다고 적는다(ADR 0076)', async () => {
    aliasCandidateApi.list.mockResolvedValue({
      items: [
        {
          ...candidate,
          text: '액정 깨짐',
          suggestions: [
            {
              id: 'o3',
              type_slug: 'defect',
              type_label: '불량 유형',
              label: '화면 파손',
              key: null,
              matched: 'meaning',
              matched_text: '',
              score: 0.91,
              scored_by: 'rerank',
            },
          ],
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    })
    const { AliasCandidatesDialog } = await import('@/modules/objects/AliasCandidatesDialog')
    render(<AliasCandidatesDialog onClose={() => {}} onDone={() => {}} />)
    await waitFor(() => expect(screen.getByText('액정 깨짐')).toBeInTheDocument())
    const hit = screen.getByRole('button', { name: /화면 파손/ })
    expect(hit).toHaveTextContent('불량 유형 · 뜻으로 찾음')
    expect(hit).toHaveAttribute('title', expect.stringContaining('뜻이 가까운'))
  })

  it('다른 객체가 쓰는 별칭이면 막는 까닭을 보이고 추가 단추를 끈다', async () => {
    aliasCandidateApi.list.mockResolvedValue({ items: [candidate], total: 1, limit: 50, offset: 0 })
    aliasCandidateApi.attach.mockResolvedValue(
      plan(false, { blocking: ['「Ansys Fluet」 은(는) 이미 Radioss의 별칭입니다'] }),
    )
    const { AliasCandidatesDialog } = await import('@/modules/objects/AliasCandidatesDialog')
    render(<AliasCandidatesDialog onClose={() => {}} onDone={() => {}} />)
    await waitFor(() => expect(screen.getByText('Ansys Fluet')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: /Ansys Fluent/ }))
    await waitFor(() => expect(screen.getByText(/이미 Radioss의 별칭/)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '별칭 추가' })).toBeDisabled()
  })

  it('짐작에 없으면 검색해서 고르고, 별칭이 아닌 말은 무시한다', async () => {
    aliasCandidateApi.list.mockResolvedValue({
      items: [{ ...candidate, suggestions: [] }],
      total: 1,
      limit: 50,
      offset: 0,
    })
    searchApi.find.mockResolvedValue({
      items: [{ id: 'o2', type_slug: 'tool', type_label: '툴', label: 'Fluent 2024' }],
    })
    aliasCandidateApi.attach.mockResolvedValue(plan(false))
    aliasCandidateApi.decide.mockResolvedValue({ done: 1, refused: [] })
    const { AliasCandidatesDialog } = await import('@/modules/objects/AliasCandidatesDialog')
    render(<AliasCandidatesDialog onClose={() => {}} onDone={() => {}} />)
    await waitFor(() => expect(screen.getByText('Ansys Fluet')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: '다른 객체 선택' }))
    const box = screen.getByRole('textbox', { name: '객체 검색' })
    await userEvent.clear(box)
    await userEvent.type(box, 'Fluent')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))
    // 그 말을 찾은 자리(타입)로 좁혀 찾는다.
    await waitFor(() => expect(searchApi.find).toHaveBeenCalledWith('Fluent', { type: 'tool' }))
    await userEvent.click(await screen.findByRole('button', { name: /Fluent 2024/ }))
    await waitFor(() => expect(aliasCandidateApi.attach).toHaveBeenCalledWith('c1', 'o2', false))

    await userEvent.click(screen.getByRole('button', { name: '무시' }))
    await waitFor(() => expect(aliasCandidateApi.decide).toHaveBeenCalledWith(['c1'], 'ignore'))
    expect(screen.getByText(/무시했습니다/)).toBeInTheDocument()
  })
})
