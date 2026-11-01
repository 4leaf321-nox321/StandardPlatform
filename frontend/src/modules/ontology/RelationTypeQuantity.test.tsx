/**
 * 관계 종류의 「수량 칸」(ADR 0040) — **숫자이고 값이 하나인 관계 속성만** 후보이고, **바꾼 때만**
 * `quantity_key` 를 보낸다(안 보낸 것과 비운 것은 다른 말이다). 고를 것이 없으면 왜 없는지 말한다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PropertyDef, RelationType } from '@/modules/ontology/api'

const ontologyApi = vi.hoisted(() => ({
  createRelationType: vi.fn(),
  updateRelationType: vi.fn(),
  removeRelationType: vi.fn(),
}))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

function prop(key: string, label: string, extra: Partial<PropertyDef> = {}): PropertyDef {
  return {
    key,
    label,
    data_type: 'number',
    multi: false,
    unit: '',
    ...extra,
  } as PropertyDef
}

function bom(extra: Partial<RelationType> = {}): RelationType {
  return {
    id: 'r1',
    slug: 'bom_of',
    label: '구성',
    inverse_label: '구성품',
    description: '',
    directed: true,
    transitive: true,
    acyclic: true,
    cardinality: 'many_to_many',
    effectivity: false,
    quantity_key: '',
    properties: [
      prop('qty', '수량', { unit: '개' }),
      prop('lots', '로트', { multi: true }),
      prop('note', '비고', { data_type: 'text' }),
    ],
    src_type_slugs: null,
    dst_type_slugs: null,
    sort_order: 0,
    is_active: true,
    ...extra,
  } as RelationType
}

async function open(relation: RelationType | null) {
  const { RelationTypeEditDialog } = await import('@/modules/ontology/RelationTypeEditDialog')
  render(
    <RelationTypeEditDialog relation={relation} types={[]} onClose={vi.fn()} onChanged={vi.fn()} />,
  )
}

describe('관계 종류의 수량 칸', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ontologyApi.updateRelationType.mockResolvedValue({})
    ontologyApi.createRelationType.mockResolvedValue({})
  })

  it('숫자 · 값 하나인 관계 속성만 후보이고, 고르면 그 키를 보낸다', async () => {
    await open(bom())
    const field = screen.getByRole('combobox', { name: '수량 칸' })
    expect(field).toHaveTextContent('없음')
    await userEvent.click(field)
    expect(await screen.findByRole('option', { name: '수량 (개)' })).toBeInTheDocument()
    // 값이 여럿인 숫자 · 숫자가 아닌 속성은 후보가 아니다.
    expect(screen.queryByRole('option', { name: /로트/ })).toBeNull()
    expect(screen.queryByRole('option', { name: /비고/ })).toBeNull()
    await userEvent.click(screen.getByRole('option', { name: '수량 (개)' }))
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(ontologyApi.updateRelationType).toHaveBeenCalledWith(
        'bom_of',
        expect.objectContaining({ quantity_key: 'qty' }),
      ),
    )
  })

  it('바꾸지 않으면 quantity_key 를 보내지 않는다', async () => {
    await open(bom({ quantity_key: 'qty' }))
    expect(screen.getByRole('combobox', { name: '수량 칸' })).toHaveTextContent('수량')
    await userEvent.clear(screen.getByLabelText('설명'))
    await userEvent.type(screen.getByLabelText('설명'), '어셈블리의 구성')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(ontologyApi.updateRelationType).toHaveBeenCalled())
    expect(ontologyApi.updateRelationType.mock.calls[0][1]).not.toHaveProperty('quantity_key')
  })

  it('없음으로 바꾸면 빈 글자를 보낸다(수량 칸 비움)', async () => {
    await open(bom({ quantity_key: 'qty' }))
    await userEvent.click(screen.getByRole('combobox', { name: '수량 칸' }))
    await userEvent.click(await screen.findByRole('option', { name: /없음/ }))
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(ontologyApi.updateRelationType).toHaveBeenCalledWith(
        'bom_of',
        expect.objectContaining({ quantity_key: '' }),
      ),
    )
  })

  it('숫자 관계 속성이 없으면 고르개를 막고 정의 가져오기를 말한다', async () => {
    await open(bom({ properties: [prop('note', '비고', { data_type: 'text' })] }))
    expect(screen.getByRole('combobox', { name: '수량 칸' })).toBeDisabled()
    expect(screen.getByText(/숫자 관계 속성이 없습니다 — 정의 가져오기로/)).toBeInTheDocument()
  })

  it('새로 만드는 창에는 고르개가 없고, 만들 때 quantity_key 를 싣지 않는다', async () => {
    await open(null)
    expect(screen.queryByRole('combobox', { name: '수량 칸' })).toBeNull()
    expect(screen.getByText(/관계 속성은 정의 가져오기로 추가합니다/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('slug'), 'bom_of')
    await userEvent.type(screen.getByLabelText('이름 (→ 방향)'), '구성')
    await userEvent.click(screen.getByRole('button', { name: '생성' }))
    await waitFor(() => expect(ontologyApi.createRelationType).toHaveBeenCalled())
    expect(ontologyApi.createRelationType.mock.calls[0][0]).not.toHaveProperty('quantity_key')
  })
})
