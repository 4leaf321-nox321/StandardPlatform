/**
 * ④-2 전작 대비 배율(ADR 0082) — 배율은 **구간과 함께**, 「unclear」 는 「같다」 가 아니라 「판단
 * 어려움」 으로 적고, 값마다의 표는 자르지 않는다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Metric } from '@/modules/metrics/api'
import { RatioView, verdictText } from '@/modules/metrics/analysis/RatioView'
import type { RatioResult, RatioRow } from '@/modules/metrics/analysis/types'

const metricsApi = vi.hoisted(() => ({ analysis: vi.fn(), dims: vi.fn() }))
vi.mock('@/modules/metrics/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/metrics/api')>()),
  metricsApi,
}))

const METRIC = {
  slug: 'cases',
  label: '인입',
  dims: [
    { name: 'base_model', label: '기본 모델', kind: 'object_ref', multi: false },
    { name: 'market', label: '시장', kind: 'enum', multi: false },
  ],
  spec: { denominator: { metric: 'sales', on: ['base_model'], time: 'cohort', per: 1 } },
  analyses: [],
} as unknown as Metric

function row(key: string | null, label: string, extra: Partial<RatioRow> = {}): RatioRow {
  return {
    key,
    label,
    observed: 30,
    expected: 10,
    ratio: 3,
    low: 2.1,
    high: 4.3,
    verdict: 'higher',
    crude: 2.4,
    target_units: 1000,
    target_records: 36,
    target_rate: 0.036,
    reference_units: 2000,
    reference_records: 30,
    reference_rate: 0.015,
    new: false,
    ...extra,
  }
}

const RESULT = {
  slug: 'cases',
  recipe: 'ratio',
  caveats: [
    {
      code: 'per_value_intervals',
      level: 'info',
      message: '값 3개의 구간을 따로 냈습니다.',
      count: null,
    },
  ],
  params: {},
  excluded: {},
  visible_share: 1,
  computed_at: '2026-11-17T02:30:00+09:00',
  method: '간접 표준화 배율',
  dim: 'base_model',
  dim_label: '기본 모델',
  target: 'S',
  target_label: 'S기본',
  reference: 'A',
  reference_label: 'A기본',
  level: 0.95,
  dispersion: 1,
  overall: row(null, '전체'),
  by: 'market',
  by_label: '시장',
  items: [
    row('KR', '한국'),
    row('US', '미국', { ratio: 1.02, low: 0.7, high: 1.5, verdict: 'unclear' }),
    row('JP', '일본', { ratio: null, low: null, high: null, verdict: 'unclear', new: true }),
  ],
} as unknown as RatioResult

describe('전작 대비 배율', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    metricsApi.dims.mockResolvedValue({ values: [] })
    metricsApi.analysis.mockResolvedValue(RESULT)
  })

  it('배율을 구간과 함께, 값마다 전부를 늘어놓는다', async () => {
    render(
      <RatioView
        metric={METRIC}
        read={{}}
        initial={{ target: 'S', reference: 'A', by: 'market' }}
      />,
    )
    await waitFor(() => expect(metricsApi.analysis).toHaveBeenCalled())
    const [, recipe, asked] = metricsApi.analysis.mock.calls[0]
    expect(recipe).toBe('ratio')
    expect(asked).toMatchObject({ target: 'S', reference: 'A', by: 'market', dim: 'base_model' })
    expect(await screen.findByText(/S기본 vs A기본: 배율 3배/)).toBeInTheDocument()
    expect(screen.getByText(/95% 구간 2.1 ~ 4.3/)).toBeInTheDocument()
    expect(screen.getByText('한국')).toBeInTheDocument()
    expect(screen.getByText('일본')).toBeInTheDocument()
    expect(screen.getByText('판단 어려움(1 을 품음)')).toBeInTheDocument()
    expect(screen.getByText('전작에 없던 값')).toBeInTheDocument()
    expect(screen.getByText('값 3개의 구간을 따로 냈습니다.')).toBeInTheDocument()
  })

  it('판단 말은 「같다」 로 읽히지 않는다', () => {
    expect(verdictText(row('x', 'x', { verdict: 'unclear' }))).not.toContain('같')
    expect(verdictText(row('x', 'x', { verdict: 'lower' }))).toBe('전작보다 낮음')
  })
})
