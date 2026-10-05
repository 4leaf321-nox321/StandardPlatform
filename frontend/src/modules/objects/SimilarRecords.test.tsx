/**
 * 비슷한 기록 — **누를 때만 묻고, 왜 비슷한지(겹친 태그)를 함께 적는다**(ADR 0022).
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { Similar } from '@/modules/objects/api'

const objectApi = vi.hoisted(() => ({ similar: vi.fn() }))
vi.mock('@/modules/objects/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/objects/api')>()),
  objectApi,
}))

const tag = (field_label: string, value_label: string, weight: number) => ({
  field: field_label,
  field_label,
  value: value_label,
  value_label,
  weight,
  records: 2,
})

describe('비슷한 기록', () => {
  it('누르면 겹친 태그와 함께 비슷한 순으로 보인다', async () => {
    const found: Similar = {
      type_slug: 'report',
      total: 4,
      fields: ['ref_part', 'ref_model'],
      query: [tag('부품', 'P1', 0.69), tag('모델', 'M1', 0.29)],
      items: [
        {
          id: 'r4',
          key: null,
          label: 'R4',
          status: 'active',
          score: 0.414,
          shared: [tag('부품', 'P1', 0.69), tag('모델', 'M1', 0.29)],
          extra: 0,
        },
      ],
    }
    objectApi.similar.mockResolvedValue(found)
    const { SimilarRecords } = await import('@/modules/objects/SimilarRecords')
    render(
      <MemoryRouter>
        <SimilarRecords typeSlug="report" objectId="r1" />
      </MemoryRouter>,
    )
    expect(objectApi.similar).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '찾기' }))
    const link = await screen.findByRole('link', { name: 'R4' })
    expect(link).toHaveAttribute('href', '/o/report/r4')
    expect(screen.getByText(/41% · 부품: P1, 모델: M1/)).toBeInTheDocument()
    expect(objectApi.similar).toHaveBeenCalledWith('report', 'r1')
  })
})
