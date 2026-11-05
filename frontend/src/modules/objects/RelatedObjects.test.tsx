/**
 * 관련 객체의 유효 기간(ADR 0039) — **지난 · 예정 관계를 지금 관계로 읽히게 두지 않는다.**
 *
 * - 줄마다 기간을 적고(끝날은 제외라 「전까지」), 지난 · 예정 관계는 배지로 말한다.
 * - 「지난 · 예정 관계 포함」 · 기준일은 상세가 들고 있다 — 고르면 그것을 알린다.
 * - 유효 기간을 받는 종류가 없는 설치에는 그 고르개가 없다(바꿔도 아무것도 안 바뀐다).
 * - 수정 창은 **바꾼 칸만** 보낸다 — 비운 기간은 `null`.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RelatedObject, RelationProposal } from '@/modules/objects/api'
import type { RelationType } from '@/modules/ontology/api'
import { ApiError } from '@/shared/api/client'

const objectApi = vi.hoisted(() => ({
  updateRelation: vi.fn(),
  removeRelation: vi.fn(),
  relationProposals: vi.fn(),
  acceptProposal: vi.fn(),
  rejectProposal: vi.fn(),
  withdrawProposal: vi.fn(),
}))
vi.mock('@/modules/objects/api', () => ({ objectApi }))
vi.mock('@/modules/objects/RelationAddDialog', () => ({ RelationAddDialog: () => null }))

function row(id: string, label: string, extra: Partial<RelatedObject> = {}): RelatedObject {
  return {
    relation_id: id,
    relation: 'bom_of',
    label: '구성',
    outgoing: true,
    object_id: `o-${id}`,
    object_label: label,
    object_key: null,
    object_type_slug: 'part',
    object_type_label: '부품',
    properties: {},
    evidence_note: '',
    created_at: '2026-01-01T00:00:00Z',
    stored_as: 'relation',
    period_state: 'current',
    ...extra,
  }
}

const PERIODIC = {
  slug: 'bom_of',
  label: '구성',
  effectivity: true,
  is_active: true,
} as unknown as RelationType
const PLAIN = { slug: 'uses', label: '씀', effectivity: false, is_active: true } as RelationType

async function mount(related: RelatedObject[], kinds: RelationType[], extra = {}) {
  const { RelatedObjects } = await import('@/modules/objects/RelatedObjects')
  const props = {
    typeSlug: 'part',
    objectId: 'me',
    objectLabel: '제품',
    objectTypeSlug: 'part',
    related,
    relationTypes: kinds,
    canEdit: true,
    onChanged: vi.fn(),
    asOf: '',
    onAsOf: vi.fn(),
    allPeriods: false,
    onAllPeriods: vi.fn(),
    ...extra,
  }
  render(
    <MemoryRouter>
      <RelatedObjects {...props} />
    </MemoryRouter>,
  )
  // 열린 제안(ADR 0042)을 따로 읽는다 — 그 응답이 시험 밖에서 그려지지 않게 기다린다.
  await act(async () => {})
  return props
}

describe('관련 객체의 유효 기간', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    objectApi.relationProposals.mockResolvedValue([])
  })

  it('줄마다 기간을 적고 지난 · 예정 관계는 배지로 말한다', async () => {
    await mount(
      [
        row('1', 'A1', { valid_to: '2026-03-01', period_state: 'past' }),
        row('2', 'A2', { valid_from: '2026-03-01' }),
        row('3', 'A3', { valid_from: '2099-01-01', period_state: 'future' }),
      ],
      [PERIODIC],
      { allPeriods: true },
    )
    expect(screen.getByText('유효 기간: 처음부터 2026-03-01 전까지')).toBeInTheDocument()
    expect(screen.getByText('유효 기간: 2026-03-01 부터 끝없이')).toBeInTheDocument()
    expect(screen.getByText('지난 관계')).toBeInTheDocument()
    expect(screen.getByText('예정 관계')).toBeInTheDocument()
    // 지금 유효한 줄에는 배지를 안 단다 — 그것이 기본이다.
    expect(screen.queryByText('유효')).not.toBeInTheDocument()
  })

  it('「지난 · 예정 관계 포함」 · 기준일을 고르면 상세에 알린다', async () => {
    const props = await mount([row('2', 'A2', { valid_from: '2026-03-01' })], [PERIODIC])
    await userEvent.click(screen.getByLabelText('지난 · 예정 관계 포함'))
    expect(props.onAllPeriods).toHaveBeenCalledWith(true)
    fireEvent.change(screen.getByLabelText('기준일'), { target: { value: '2026-02-15' } })
    expect(props.onAsOf).toHaveBeenCalledWith('2026-02-15')
  })

  it('유효 기간을 받는 종류가 없으면 그 고르개가 없다', async () => {
    await mount([row('1', 'B', { relation: 'uses' })], [PLAIN])
    expect(screen.queryByLabelText('지난 · 예정 관계 포함')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('기준일')).not.toBeInTheDocument()
  })

  it('오늘 유효한 것이 없으면 그 까닭과 포함하는 길을 말한다', async () => {
    await mount([], [PERIODIC])
    expect(screen.getByText(/오늘 유효한 관계가 없습니다/)).toBeInTheDocument()
  })

  it('수정 창은 바꾼 칸만 보낸다 — 비운 끝날은 null', async () => {
    objectApi.updateRelation.mockResolvedValue({})
    const props = await mount(
      [row('1', 'A1', { valid_from: '2026-01-01', valid_to: '2026-03-01' })],
      [PERIODIC],
    )
    await userEvent.click(screen.getByRole('button', { name: 'A1 관계 수정' }))
    const to = await screen.findByLabelText('끝날 (제외)')
    expect(to).toHaveValue('2026-03-01')
    fireEvent.change(to, { target: { value: '' } })
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(objectApi.updateRelation).toHaveBeenCalledWith('part', 'me', '1', { valid_to: null }),
    )
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled())
  })

  it('꺼진 종류의 관계는 수정 창에 기간 칸이 없다', async () => {
    await mount([row('1', 'B', { relation: 'uses' })], [PLAIN])
    await userEvent.click(screen.getByRole('button', { name: 'B 관계 수정' }))
    expect(await screen.findByLabelText('근거')).toBeInTheDocument()
    expect(screen.queryByLabelText('끝날 (제외)')).not.toBeInTheDocument()
  })
})

function proposal(id: string, extra: Partial<RelationProposal> = {}): RelationProposal {
  return {
    id,
    relation: 'supplied_by',
    relation_label: '공급받음',
    label: '공급받음',
    outgoing: true,
    status: 'proposed',
    src_object_id: 'me',
    src_label: '제품',
    src_key: null,
    src_type_slug: 'part',
    src_type_label: '부품',
    dst_object_id: `v-${id}`,
    dst_label: 'ACME',
    dst_key: 'V-1',
    dst_type_slug: 'vendor',
    dst_type_label: '공급사',
    properties: {},
    valid_from: null,
    valid_to: null,
    evidence_note: '공급 계약서 3쪽',
    confidence: 0.8,
    origin: 'mcp',
    origin_ref: '대화 #12',
    proposed_by_label: 'member',
    created_at: '2026-10-30T00:00:00Z',
    decided_by_label: '',
    decided_at: null,
    decision_note: '',
    relation_id: null,
    can_decide: false,
    can_withdraw: false,
    ...extra,
  }
}

const SUPPLY = { slug: 'supplied_by', label: '공급받음', is_active: true } as RelationType

/**
 * 제안된 관계(ADR 0042) — **흐리게**, 「제안」 배지와 근거 · 확신도 · 출처로. 확정 · 거절은 출발
 * 객체를 수정할 수 있는 사람(`can_decide`)에게만, 철회는 제안한 사람에게만. 수정할 수 없는 사람의
 * 단추는 「연결」 대신 「제안으로 추가」.
 */
describe('관련 객체의 제안된 관계', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('열린 제안이 흐리게 「제안」 과 근거 · 확신도 · 출처로 보인다', async () => {
    objectApi.relationProposals.mockResolvedValue([proposal('p1', { can_withdraw: true })])
    await mount([], [SUPPLY], { canEdit: false })
    const line = await screen.findByTestId('proposal-row')
    expect(objectApi.relationProposals).toHaveBeenCalledWith('part', 'me')
    expect(line).toHaveTextContent('ACME')
    expect(line).toHaveTextContent('제안')
    expect(line).toHaveTextContent('근거: 공급 계약서 3쪽')
    expect(line).toHaveTextContent('확신도 0.80 · 출처: AI(MCP) 「대화 #12」 · member')
    // 흐리게 — 확정 전의 제안은 관계가 아니다.
    expect(screen.getByText('ACME').closest('div')).toHaveClass('opacity-60')
    // 관련 객체가 없어도 「이어진 것이 없습니다」 가 아니다 — 제안이 있다.
    expect(screen.queryByText('이어진 것이 없습니다')).not.toBeInTheDocument()
    expect(screen.getByText('· 제안 1')).toBeInTheDocument()
    // 확정할 수 없는 사람에게는 확정 · 거절이 없고, 제안한 사람에게는 철회가 있다.
    expect(screen.queryByRole('button', { name: 'ACME 제안 확정' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'ACME 제안 거절' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'ACME 제안 철회' })).toBeInTheDocument()
  })

  it('수정할 수 없으면 단추가 「제안으로 추가」 이고, 원 표 타입이면 그것도 없다', async () => {
    await mount([], [SUPPLY], { canEdit: false })
    expect(await screen.findByRole('button', { name: '제안으로 추가' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '연결' })).not.toBeInTheDocument()
    cleanup()
    objectApi.relationProposals.mockClear()
    await mount([], [SUPPLY], { canEdit: false, proposals: false })
    expect(screen.queryByRole('button', { name: '제안으로 추가' })).not.toBeInTheDocument()
    expect(objectApi.relationProposals).not.toHaveBeenCalled()
  })

  it('확정할 수 있으면 확정 · 거절이 서고, 확정하면 제안 목록과 상세를 다시 읽는다', async () => {
    objectApi.relationProposals.mockResolvedValue([proposal('p1', { can_decide: true })])
    objectApi.acceptProposal.mockResolvedValue(proposal('p1', { status: 'accepted' }))
    const props = await mount([], [SUPPLY])
    await userEvent.click(await screen.findByRole('button', { name: 'ACME 제안 확정' }))
    await waitFor(() => expect(objectApi.acceptProposal).toHaveBeenCalledWith('p1'))
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled())
    await waitFor(() => expect(objectApi.relationProposals).toHaveBeenCalledTimes(2))
  })

  it('확정이 서버에서 막히면 그 말을 보이고 제안은 남는다', async () => {
    objectApi.relationProposals.mockResolvedValue([proposal('p1', { can_decide: true })])
    objectApi.acceptProposal.mockRejectedValue(
      new ApiError(409, {
        error: {
          code: 'APP-OBJECTS-0143',
          message: '확정하지 못했습니다 — 공급받음은 하나만 맺을 수 있습니다.',
        },
      }),
    )
    const props = await mount([], [SUPPLY])
    await userEvent.click(await screen.findByRole('button', { name: 'ACME 제안 확정' }))
    expect(await screen.findByText(/하나만 맺을 수 있습니다/)).toBeInTheDocument()
    expect(screen.getByText('APP-OBJECTS-0143')).toBeInTheDocument()
    expect(props.onChanged).not.toHaveBeenCalled()
    expect(screen.getByTestId('proposal-row')).toBeInTheDocument()
  })

  it('거절은 까닭을 받는 창으로 한다', async () => {
    objectApi.relationProposals.mockResolvedValue([proposal('p1', { can_decide: true })])
    objectApi.rejectProposal.mockResolvedValue(proposal('p1', { status: 'rejected' }))
    const props = await mount([], [SUPPLY])
    await userEvent.click(await screen.findByRole('button', { name: 'ACME 제안 거절' }))
    await userEvent.type(await screen.findByLabelText('까닭'), '단종된 공급사')
    await userEvent.click(screen.getByRole('button', { name: '거절' }))
    await waitFor(() =>
      expect(objectApi.rejectProposal).toHaveBeenCalledWith('p1', '단종된 공급사'),
    )
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled())
  })

  it('철회는 무엇이 사라지는지 말하고 한다', async () => {
    objectApi.relationProposals.mockResolvedValue([proposal('p1', { can_withdraw: true })])
    objectApi.withdrawProposal.mockResolvedValue(undefined)
    await mount([], [SUPPLY], { canEdit: false })
    await userEvent.click(await screen.findByRole('button', { name: 'ACME 제안 철회' }))
    expect(await screen.findByText(/제안이 삭제됩니다/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '철회' }))
    await waitFor(() => expect(objectApi.withdrawProposal).toHaveBeenCalledWith('p1'))
    await waitFor(() => expect(objectApi.relationProposals).toHaveBeenCalledTimes(2))
  })
})

/**
 * 상세에서는 **이 객체의 참조 칸이 가리키는 묶음을 접는다** — 그 값은 기본 정보에 링크로 있다.
 * 저쪽 칸이 이 객체를 가리키는 줄(역방향)과 관계로 이은 줄은 그대로 선다.
 */
describe('이 객체의 칸이 가리키는 것', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    objectApi.relationProposals.mockResolvedValue([])
  })

  const related = [
    row('f1', '대한민국', {
      relation: 'ref:country',
      label: '국가',
      stored_as: 'field',
      object_type_slug: 'country',
      object_type_label: '국가',
    }),
    row('f2', 'SM-N120K', {
      relation: 'ref:base_model',
      label: '판매 모델',
      outgoing: false,
      stored_as: 'field',
      field_key: 'base_model',
      object_type_slug: 'sku',
      object_type_label: '판매 모델',
    }),
    row('r1', '힌지 어셈블리', { relation: 'uses', label: '씀' }),
  ]

  it('접어 두고 몇 건인지 말하며, 펼치면 보인다', async () => {
    await mount(related, [PLAIN], { foldOwnFields: true })
    expect(screen.queryByRole('link', { name: '대한민국' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'SM-N120K' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '힌지 어셈블리' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /이 객체의 칸이 가리키는 것 1건/ }))
    expect(screen.getByRole('link', { name: '대한민국' })).toBeInTheDocument()
  })

  it('접지 않으면(기본) 예전처럼 다 보인다', async () => {
    await mount(related, [PLAIN])
    expect(screen.getByRole('link', { name: '대한민국' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /이 객체의 칸이 가리키는 것/ })).toBeNull()
  })
})
