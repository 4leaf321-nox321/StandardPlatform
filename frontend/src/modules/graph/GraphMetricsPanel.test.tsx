/**
 * 그래프 지표 — **언제 센 값인지 함께 적고**, 센 것이 없으면 아무것도 안 그린다(ADR 0067).
 */

import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { CalcObject } from '@/modules/graph/calc'

const graphCalcApi = vi.hoisted(() => ({ object: vi.fn() }))
vi.mock('@/modules/graph/calc', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/graph/calc')>()),
  graphCalcApi,
}))

const run = { id: 3, computed_at: '2026-11-11T05:12:00+09:00', engine: 'cpu', stats: {} }

describe('그래프 지표', () => {
  it('기대는 수 · 다리 · 무리 · 비슷한 것을 순위와 함께 보인다', async () => {
    const found: CalcObject = {
      run,
      scores: [
        { metric: 'bridge', relation: '', value: 0.37, rank: 2, of: 312418 },
        { metric: 'depends_top', relation: 'bom', value: 4856, rank: 1, of: 279076 },
      ],
      cluster: {
        cluster: 14,
        rank: 3,
        size: 9168,
        label: '시장 EU',
        representative_id: 'm1',
        types: { model: 2000 },
      },
      similar: [
        {
          id: 'md2',
          label: 'MD-01601',
          key: 'MD-01601',
          type_slug: 'model',
          relation: 'bom',
          rank: 1,
          score: 0.516,
        },
      ],
    }
    graphCalcApi.object.mockResolvedValue({
      ...found,
      run: { ...run, stats: { whole_relations: ['bom'] } },
    })
    const { GraphMetricsPanel } = await import('@/modules/graph/GraphMetricsPanel')
    render(
      <MemoryRouter>
        <GraphMetricsPanel objectId="p1" relationLabels={{ bom: '구성' }} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('기대는 수 · 구성')).toBeInTheDocument()
    expect(screen.getByText('4,856')).toBeInTheDocument()
    expect(screen.getByText(/279,076개 중 1위 · 상위 1% 안/)).toBeInTheDocument()
    expect(screen.getByText(/가장 큰 다리 대비 37%/)).toBeInTheDocument()
    expect(screen.getByText(/무리 14 「시장 EU」/)).toBeInTheDocument()
    expect(screen.getByText(/구성\(으\)로 지음/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'MD-01601' })).toHaveAttribute('href', '/o/model/md2')
    expect(screen.getByText(/기준 · 밤에 센 값\(CPU\)/)).toBeInTheDocument()
    expect(graphCalcApi.object).toHaveBeenCalledWith('p1')
  })

  it('판이 없거나 센 값이 없으면 아무것도 안 그린다', async () => {
    const { GraphMetricsPanel } = await import('@/modules/graph/GraphMetricsPanel')
    graphCalcApi.object.mockResolvedValueOnce({ run: null, scores: [], cluster: null, similar: [] })
    const first = render(
      <MemoryRouter>
        <GraphMetricsPanel objectId="a" />
      </MemoryRouter>,
    )
    graphCalcApi.object.mockResolvedValueOnce({ run, scores: [], cluster: null, similar: [] })
    const second = render(
      <MemoryRouter>
        <GraphMetricsPanel objectId="b" />
      </MemoryRouter>,
    )
    // 볼 수 없는 설치(403)도 조용히 — 상세의 다른 칸을 막지 않는다.
    graphCalcApi.object.mockRejectedValueOnce(new Error('forbidden'))
    const third = render(
      <MemoryRouter>
        <GraphMetricsPanel objectId="c" />
      </MemoryRouter>,
    )
    await vi.waitFor(() => expect(graphCalcApi.object).toHaveBeenCalledWith('c'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(first.container).toBeEmptyDOMElement()
    expect(second.container).toBeEmptyDOMElement()
    expect(third.container).toBeEmptyDOMElement()
  })
})
