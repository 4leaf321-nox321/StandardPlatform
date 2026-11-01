/**
 * 관계 종류의 「유효 기간을 받는다」(ADR 0039) — 켜고 끄는 값이 그대로 나가고, 기간이 든 관계가
 * 있어 끌 수 없으면 **서버의 말이 그 창에 뜬다**(조용히 안 꺼지면 사람은 꺼진 줄 안다).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RelationType } from '@/modules/ontology/api'
import { ApiError } from '@/shared/api/client'

const ontologyApi = vi.hoisted(() => ({
  createRelationType: vi.fn(),
  updateRelationType: vi.fn(),
  removeRelationType: vi.fn(),
}))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

const BOM = {
  id: 'r1',
  slug: 'bom_of',
  label: '구성',
  inverse_label: '구성품',
  description: '',
  directed: true,
  transitive: true,
  acyclic: true,
  cardinality: 'many_to_many',
  effectivity: true,
  src_type_slugs: null,
  dst_type_slugs: null,
  sort_order: 0,
  is_active: true,
} as RelationType

async function open(relation: RelationType | null, onChanged = vi.fn()) {
  const { RelationTypeEditDialog } = await import('@/modules/ontology/RelationTypeEditDialog')
  render(
    <RelationTypeEditDialog
      relation={relation}
      types={[]}
      onClose={vi.fn()}
      onChanged={onChanged}
    />,
  )
  return onChanged
}

describe('관계 종류의 유효 기간', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('새 종류에 켜면 effectivity 가 참으로 나간다', async () => {
    ontologyApi.createRelationType.mockResolvedValue({})
    await open(null)
    await userEvent.type(screen.getByLabelText('slug'), 'bom_of')
    await userEvent.type(screen.getByLabelText('이름 (→ 방향)'), '구성')
    const box = screen.getByRole('checkbox', { name: /유효 기간을 받는다/ })
    expect(box).not.toBeChecked()
    await userEvent.click(box)
    await userEvent.click(screen.getByRole('button', { name: '생성' }))
    await waitFor(() =>
      expect(ontologyApi.createRelationType).toHaveBeenCalledWith(
        expect.objectContaining({ slug: 'bom_of', effectivity: true }),
      ),
    )
  })

  it('기간이 든 관계가 있어 끌 수 없으면 서버의 말을 그 자리에 보인다', async () => {
    ontologyApi.updateRelationType.mockRejectedValue(
      new ApiError(409, {
        error: {
          code: 'APP-ONTOLOGY-0095',
          message: '구성의 유효 기간을 끌 수 없습니다 — 기간이 든 선이 3개 있습니다.',
          details: { dated_relations: 3 },
        },
      }),
    )
    const onChanged = await open(BOM)
    const box = screen.getByRole('checkbox', { name: /유효 기간을 받는다/ })
    expect(box).toBeChecked()
    expect(screen.getByText(/기간이 든 관계가 있으면 끌 수 없습니다/)).toBeInTheDocument()
    await userEvent.click(box)
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(ontologyApi.updateRelationType).toHaveBeenCalledWith(
        'bom_of',
        expect.objectContaining({ effectivity: false }),
      ),
    )
    expect(await screen.findByText(/기간이 든 선이 3개 있습니다/)).toBeInTheDocument()
    expect(onChanged).not.toHaveBeenCalled()
  })
})
