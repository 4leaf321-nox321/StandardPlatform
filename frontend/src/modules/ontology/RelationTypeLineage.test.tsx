/**
 * 관계 종류의 「계보 관계」(ADR 0041) — 어느 끝이 원천인가(없음 · 출발 · 도착)를 고르고, **바꾼
 * 때만** `lineage_source` 를 보낸다(켜거나 끝을 바꾸면 그 종류의 관계 전부가 다시 고정된다).
 *
 * - 고르는 줄에 원천 · 파생물이 무엇이 되는지(끝의 타입 이름)를 적는다.
 * - 방향이 없는 종류는 고르지 못하고 까닭을 적는다. 계보였던 종류의 방향을 끄면 해제된다고 말하고
 *   빈 글자를 보낸다(서버는 방향 없는 계보를 거절한다, ONTOLOGY-0097).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ObjectType, RelationType } from '@/modules/ontology/api'

const ontologyApi = vi.hoisted(() => ({
  createRelationType: vi.fn(),
  updateRelationType: vi.fn(),
  removeRelationType: vi.fn(),
}))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

const TYPES = [
  { slug: 'drawing', label: '도면' },
  { slug: 'mesh', label: '메시' },
] as ObjectType[]

function madeFrom(extra: Partial<RelationType> = {}): RelationType {
  return {
    id: 'r1',
    slug: 'made_from',
    label: '만든 원천',
    inverse_label: '만든 것',
    description: '',
    directed: true,
    transitive: false,
    acyclic: true,
    cardinality: 'many_to_many',
    effectivity: false,
    quantity_key: '',
    lineage_source: '',
    properties: [],
    src_type_slugs: ['mesh'],
    dst_type_slugs: ['drawing'],
    sort_order: 0,
    is_active: true,
    ...extra,
  } as RelationType
}

async function open(relation: RelationType | null) {
  const { RelationTypeEditDialog } = await import('@/modules/ontology/RelationTypeEditDialog')
  render(
    <RelationTypeEditDialog
      relation={relation}
      types={TYPES}
      onClose={vi.fn()}
      onChanged={vi.fn()}
    />,
  )
}

describe('관계 종류의 계보 관계', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ontologyApi.updateRelationType.mockResolvedValue({})
    ontologyApi.createRelationType.mockResolvedValue({})
  })

  it('원천 끝을 고르면 그 끝을 보내고, 고르는 줄에 원천 · 파생물의 타입을 적는다', async () => {
    await open(madeFrom())
    const field = screen.getByRole('combobox', { name: '계보 관계' })
    expect(field).toHaveTextContent('없음')
    await userEvent.click(field)
    // 「메시 → 도면」 관계에서 도착(도면)이 원천이면 메시가 파생물이다.
    expect(
      await screen.findByRole('option', { name: '출발이 원천 (원천 메시 → 파생물 도면)' }),
    ).toBeInTheDocument()
    await userEvent.click(
      screen.getByRole('option', { name: '도착이 원천 (원천 도면 → 파생물 메시)' }),
    )
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(ontologyApi.updateRelationType).toHaveBeenCalledWith(
        'made_from',
        expect.objectContaining({ lineage_source: 'dst' }),
      ),
    )
  })

  it('바꾸지 않으면 lineage_source 를 보내지 않는다(다시 고정이 안 일어나게)', async () => {
    await open(madeFrom({ lineage_source: 'dst' }))
    expect(screen.getByRole('combobox', { name: '계보 관계' })).toHaveTextContent('도착이 원천')
    await userEvent.type(screen.getByLabelText('설명'), '해석의 원천')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(ontologyApi.updateRelationType).toHaveBeenCalled())
    expect(ontologyApi.updateRelationType.mock.calls[0][1]).not.toHaveProperty('lineage_source')
  })

  it('없음으로 바꾸면 빈 글자를 보낸다(계보 해제)', async () => {
    await open(madeFrom({ lineage_source: 'src' }))
    await userEvent.click(screen.getByRole('combobox', { name: '계보 관계' }))
    await userEvent.click(await screen.findByRole('option', { name: /없음/ }))
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(ontologyApi.updateRelationType).toHaveBeenCalledWith(
        'made_from',
        expect.objectContaining({ lineage_source: '' }),
      ),
    )
  })

  it('방향이 없는 종류는 고르지 못하고 까닭을 적는다', async () => {
    await open(madeFrom({ directed: false }))
    expect(screen.getByRole('combobox', { name: '계보 관계' })).toBeDisabled()
    expect(screen.getByText(/방향이 없는 관계는 계보 관계가 될 수 없습니다/)).toBeInTheDocument()
  })

  it('계보였던 종류의 방향을 끄면 해제된다고 말하고, 빈 글자를 보낸다', async () => {
    await open(madeFrom({ lineage_source: 'dst' }))
    expect(screen.queryByText(/방향을 끄면 계보 관계도 해제됩니다/)).toBeNull()
    await userEvent.click(screen.getByRole('checkbox', { name: /방향이 있다/ }))
    expect(screen.getByRole('combobox', { name: '계보 관계' })).toBeDisabled()
    expect(screen.getByText(/방향을 끄면 계보 관계도 해제됩니다/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(ontologyApi.updateRelationType).toHaveBeenCalledWith(
        'made_from',
        expect.objectContaining({ directed: false, lineage_source: '' }),
      ),
    )
  })

  it('새 종류에 고르면 만들 때 싣고, 안 고르면 싣지 않는다', async () => {
    await open(null)
    await userEvent.type(screen.getByLabelText('slug'), 'made_from')
    await userEvent.type(screen.getByLabelText('이름 (→ 방향)'), '만든 원천')
    await userEvent.click(screen.getByRole('button', { name: '생성' }))
    await waitFor(() => expect(ontologyApi.createRelationType).toHaveBeenCalledTimes(1))
    expect(ontologyApi.createRelationType.mock.calls[0][0]).not.toHaveProperty('lineage_source')
  })

  it('새 종류에서 원천 끝을 고르면 lineage_source 가 함께 나간다', async () => {
    await open(null)
    await userEvent.type(screen.getByLabelText('slug'), 'made_from')
    await userEvent.type(screen.getByLabelText('이름 (→ 방향)'), '만든 원천')
    await userEvent.click(screen.getByRole('combobox', { name: '계보 관계' }))
    // 끝을 안 골랐으면 「아무 타입」 이다.
    await userEvent.click(
      await screen.findByRole('option', {
        name: '출발이 원천 (원천 아무 타입 → 파생물 아무 타입)',
      }),
    )
    await userEvent.click(screen.getByRole('button', { name: '생성' }))
    await waitFor(() =>
      expect(ontologyApi.createRelationType).toHaveBeenCalledWith(
        expect.objectContaining({ slug: 'made_from', lineage_source: 'src' }),
      ),
    )
  })
})
