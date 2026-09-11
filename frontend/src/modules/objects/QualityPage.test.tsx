/**
 * 품질 화면이 지키는 것 — **종류마다 묶고, 잘린 목록은 잘렸다고 말하고, 줄은 그 객체로 간다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const qualityApi = vi.hoisted(() => ({ report: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ qualityApi }))

describe('데이터 품질', () => {
  it('걸린 것이 없으면 이유를 말한다', async () => {
    qualityApi.report.mockResolvedValue({ findings: [], sample_limit: 50 })
    const { default: QualityPage } = await import('@/modules/objects/QualityPage')
    render(<MemoryRouter><QualityPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('걸린 것이 없습니다')).toBeInTheDocument())
  })

  it('종류·타입으로 묶고, 상한을 넘으면 잘렸다고 적고, 줄은 객체로 간다', async () => {
    qualityApi.report.mockResolvedValue({
      sample_limit: 50,
      findings: [
        {
          kind: 'broken_ref', kind_label: '지워진 것을 가리키는 칸', type_slug: 'part', type_label: '부품', count: 60,
          hits: [{ id: 'bolt', label: '볼트', key: 'P-1', detail: '공급사 → ACME (지워짐)' }],
        },
        {
          kind: 'duplicate', kind_label: '이름이 같은 객체', type_slug: 'vendor', type_label: '공급사', count: 2,
          hits: [
            { id: 'a', label: 'ACME', key: null, detail: '같은 이름 2개' },
            { id: 'b', label: 'acme', key: null, detail: '같은 이름 2개' },
          ],
        },
      ],
    })
    const { default: QualityPage } = await import('@/modules/objects/QualityPage')
    render(<MemoryRouter initialEntries={['/quality#duplicate']}><QualityPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('지워진 것을 가리키는 칸')).toBeInTheDocument())
    expect(screen.getByText(/60개 · 아래는 1개까지/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /볼트/ })).toHaveAttribute('href', '/o/part/bolt')
    expect(screen.getByText('이름이 같은 객체')).toBeInTheDocument()
    expect(screen.getAllByText('같은 이름 2개')).toHaveLength(2)
  })
})
