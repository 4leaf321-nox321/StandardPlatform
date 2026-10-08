/**
 * 속성 고치기 창 — **이 창이 안 다루는 칸도 그대로 실어 보낸다.** 서버의 PATCH 는 통째 교체라,
 * 빼면 그 칸이 비워진다. 묶음(`section` — 폼 · 상세의 섹션)이 그렇게 이름 한 글자만 고쳐도
 * 사라졌다(2026-10-08).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { ObjectType, PropertyDef } from '@/modules/ontology/api'

const ontologyApi = vi.hoisted(() => ({
  updateProperty: vi.fn(),
  propertyUsage: vi.fn(),
  createProperty: vi.fn(),
  removeProperty: vi.fn(),
}))
vi.mock('@/modules/ontology/api', async (original) => {
  const real = await original<typeof import('@/modules/ontology/api')>()
  return { ...real, ontologyApi }
})

const WEIGHT = {
  id: 'p1',
  owner_kind: 'type',
  owner_id: 't1',
  key: 'w',
  label: '무게',
  data_type: 'text',
  unit: '',
  help: '',
  required: false,
  multi: false,
  enum_options: null,
  ref_type_slug: null,
  min_value: null,
  max_value: null,
  decimals: null,
  pattern: null,
  default_value: null,
  unique: false,
  section: '치수',
  sort_order: 0,
} as unknown as PropertyDef

const TYPE = { id: 't1', slug: 'part', label: '부품' } as unknown as ObjectType

describe('속성 고치기 창', () => {
  it('고치면 묶음(section)을 그대로 실어 보낸다', async () => {
    ontologyApi.updateProperty.mockResolvedValue(WEIGHT)
    ontologyApi.propertyUsage.mockResolvedValue({ count: 0, core_consumers: [] })
    const { PropertyEditDialog } = await import('@/modules/ontology/PropertyEditDialog')
    render(
      <MemoryRouter>
        <PropertyEditDialog
          owner={{ kind: 'type', row: TYPE }}
          property={WEIGHT}
          types={[TYPE]}
          onClose={vi.fn()}
          onChanged={vi.fn()}
        />
      </MemoryRouter>,
    )
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(ontologyApi.updateProperty).toHaveBeenCalled())
    const [, , body] = ontologyApi.updateProperty.mock.calls[0]
    expect(body).toMatchObject({ key: 'w', label: '무게', section: '치수' })
  })

  it('여러 값 칸의 기본값은 목록으로 보이고 목록으로 보낸다', async () => {
    ontologyApi.updateProperty.mockReset()
    ontologyApi.updateProperty.mockResolvedValue(WEIGHT)
    const multi = {
      ...WEIGHT,
      data_type: 'enum',
      multi: true,
      enum_options: ['A', 'B', 'C'],
      default_value: ['A', 'B'],
    } as unknown as PropertyDef
    const { PropertyEditDialog } = await import('@/modules/ontology/PropertyEditDialog')
    render(
      <MemoryRouter>
        <PropertyEditDialog
          owner={{ kind: 'type', row: TYPE }}
          property={multi}
          types={[TYPE]}
          onClose={vi.fn()}
          onChanged={vi.fn()}
        />
      </MemoryRouter>,
    )
    expect(screen.getByLabelText('기본값')).toHaveValue('A, B')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(ontologyApi.updateProperty).toHaveBeenCalled())
    const [, , body] = ontologyApi.updateProperty.mock.calls[0]
    expect(body.default_value).toEqual(['A', 'B'])
  })
})
