/**
 * 축 객체의 「기록 요약」 — **이 타입을 기준으로 가진 지표를 찾아 스스로 선다**(ADR 0022).
 */

import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { ProfileResult } from '@/modules/metrics/analysis/types'

const metricsApi = vi.hoisted(() => ({ list: vi.fn(), analysis: vi.fn() }))
vi.mock('@/modules/metrics/api', () => ({ metricsApi }))
vi.mock('@/shared/charts/LazyPlot', () => ({ LazyPlot: () => <div>그림</div> }))

const metric = (slug: string, target: string | null) => ({
  slug,
  label: '분석 보고 수',
  dims: [
    {
      name: 'mechanism',
      address: 'properties.ref_mech',
      label: '메커니즘',
      kind: 'object_ref',
      multi: true,
      grain: null,
      target,
    },
  ],
  analyses: [{ recipe: 'profile', label: '한 장 요약', ok: true, reason: null }],
})

describe('기록 요약', () => {
  it('이 타입을 가리키는 기준이 있는 지표로 이 객체의 요약을 묻는다', async () => {
    metricsApi.list.mockResolvedValue([metric('other', 'part'), metric('reports', 'mech')])
    metricsApi.analysis.mockResolvedValue({
      caveats: [],
      dim: 'mechanism',
      dim_label: '메커니즘',
      value: 'm-fatigue',
      value_label: '피로',
      count: 2,
      total: 4,
      share: 0.5,
      drill: { type_slug: 'report', params: {}, partial: [] },
      points: [],
      related: [
        {
          dim: 'part',
          label: '부품',
          values: [
            {
              key: 'p2',
              label: 'P2',
              count: 1,
              share: 0.5,
              lift: 2,
              drill: { type_slug: 'report', params: {}, partial: [] },
            },
          ],
          others: 0,
        },
      ],
    } as unknown as ProfileResult)
    const { AxisProfile } = await import('@/modules/metrics/AxisProfile')
    render(
      <MemoryRouter>
        <AxisProfile typeSlug="mech" objectId="m-fatigue" />
      </MemoryRouter>,
    )
    expect(await screen.findByText('기록 요약 — 분석 보고 수')).toBeInTheDocument()
    expect(await screen.findByText('함께 나온 부품')).toBeInTheDocument()
    expect(screen.getByText(/2배/)).toBeInTheDocument()
    expect(metricsApi.analysis).toHaveBeenCalledWith('reports', 'profile', {
      dim: 'mechanism',
      value: 'm-fatigue',
    })
  })

  it('그런 지표가 없으면 아무것도 그리지 않는다', async () => {
    metricsApi.list.mockResolvedValue([metric('other', 'part')])
    const { AxisProfile } = await import('@/modules/metrics/AxisProfile')
    const { container } = render(
      <MemoryRouter>
        <AxisProfile typeSlug="mech" objectId="m-fatigue" />
      </MemoryRouter>,
    )
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(container).toBeEmptyDOMElement()
  })
})
