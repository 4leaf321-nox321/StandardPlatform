/**
 * 부서 홈의 **지표** 위젯 — 최근 기간의 추이와 마지막 값, 앞 기간과의 차이. 메뉴는 뷰 위젯과
 * 같다(자리는 뷰와 한 줄에서 센다). 그리고 「위젯 추가」 창에서 지표를 골라 바로 올린다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const metricsApi = vi.hoisted(() => ({
  series: vi.fn(),
  pinHome: vi.fn(),
  unpinHome: vi.fn(),
  list: vi.fn(),
}))
vi.mock('@/modules/metrics/api', async (original) => ({
  ...(await original<typeof import('@/modules/metrics/api')>()),
  metricsApi,
}))
const ontologyApi = vi.hoisted(() => ({ schema: vi.fn() }))
vi.mock('@/modules/ontology/api', async (original) => ({
  ...(await original<typeof import('@/modules/ontology/api')>()),
  ontologyApi,
}))
vi.mock('@/shared/charts', () => ({
  Chart: ({ title }: { title?: string }) => <div data-testid="chart">{title}</div>,
}))

function point(period: string, value: number, closed = true) {
  return {
    period,
    label: period.slice(0, 7),
    count: value,
    value,
    ratio: null,
    prev: null,
    yoy: null,
    closed,
    drill: { conditions: [] },
  }
}

const WIDGET = {
  kind: 'metric' as const,
  type_label: '판매',
  icon: 'Box',
  workspace_slug: 'cae',
  workspace_name: '해석팀',
  metric: {
    id: 'h1',
    metric_slug: 'sales_units',
    metric_label: '판매 대수',
    split: null,
    home_order: 1,
  },
}

describe('홈의 지표 위젯', () => {
  beforeEach(() => vi.clearAllMocks())

  it('마지막 값과 앞 기간과의 차이를 적고, 아직 닫히지 않은 기간은 그렇다고 말한다', async () => {
    metricsApi.series.mockResolvedValue({
      lines: [
        {
          key: null,
          label: '합계',
          points: [point('2026-08-01', 120), point('2026-09-01', 150), point('2026-10-01', 90, false)],
        },
      ],
      lines_truncated: false,
    })
    const { MetricHomeWidget } = await import('@/modules/metrics/MetricHomeWidget')
    render(
      <MemoryRouter>
        <MetricHomeWidget widget={WIDGET} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('90')).toBeInTheDocument()
    expect(screen.getByText(/앞 기간보다 -60/)).toHaveTextContent('아직 닫히지 않음')
    expect(screen.getByRole('link', { name: '판매 대수' })).toHaveAttribute(
      'href',
      '/metrics/sales_units',
    )
    expect(metricsApi.series).toHaveBeenCalledWith('sales_units', { split: undefined })
  })

  it('분모가 있는 지표는 비율만 그린다 — 분모가 아직 없는 기간에 건수를 섞지 않는다', async () => {
    // 1000대당 불량률 — 이번 달 판매가 아직 안 들어와 비율이 없다. 예전에는 그 자리에 건수(300)가
    // 서고 「앞 기간보다 +297.5」 라고 적혔다.
    metricsApi.series.mockResolvedValue({
      measure: 'count',
      denominator: { metric: 'sales', label: '판매', missing: 1 },
      lines: [
        {
          key: null,
          label: '합계',
          points: [
            { ...point('2026-08-01', 240), ratio: 2.4 },
            { ...point('2026-09-01', 250), ratio: 2.5 },
            { ...point('2026-10-01', 300, false), ratio: null },
          ],
        },
      ],
      lines_truncated: false,
    })
    const { MetricHomeWidget } = await import('@/modules/metrics/MetricHomeWidget')
    render(
      <MemoryRouter>
        <MetricHomeWidget widget={WIDGET} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('—')).toBeInTheDocument()
    expect(screen.queryByText('300')).not.toBeInTheDocument()
    expect(screen.queryByText(/앞 기간보다/)).not.toBeInTheDocument()
  })

  it('홈에서 앞으로 옮기고 내린다 — 관리자 메뉴', async () => {
    metricsApi.series.mockResolvedValue({ lines: [], lines_truncated: false })
    metricsApi.pinHome.mockResolvedValue({})
    metricsApi.unpinHome.mockResolvedValue(undefined)
    const changed = vi.fn()
    const { MetricHomeWidget } = await import('@/modules/metrics/MetricHomeWidget')
    render(
      <MemoryRouter>
        <MetricHomeWidget widget={WIDGET} canEdit index={1} total={2} onChanged={changed} />
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '위젯 메뉴' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /앞으로/ }))
    await waitFor(() =>
      expect(metricsApi.pinHome).toHaveBeenCalledWith('sales_units', {
        workspace_slug: 'cae',
        split: null,
        position: 0,
      }),
    )
    await userEvent.click(screen.getByRole('button', { name: '위젯 메뉴' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /홈 게시 해제/ }))
    await waitFor(() => expect(metricsApi.unpinHome).toHaveBeenCalledWith('sales_units', 'cae'))
    expect(changed).toHaveBeenCalled()
  })

  it('「위젯 추가」 에서 지표와 나눌 기준을 골라 바로 올린다', async () => {
    ontologyApi.schema.mockResolvedValue({ types: [], relation_types: [], groups: [] })
    metricsApi.list.mockResolvedValue([
      {
        slug: 'sales_units',
        label: '판매 대수',
        description: '',
        source_type_label: '판매',
        dims: [{ name: 'base_model', label: '기본 모델' }],
      },
    ])
    metricsApi.pinHome.mockResolvedValue({})
    const added = vi.fn()
    const { AddWidgetDialog } = await import('@/modules/workspaces/AddWidgetDialog')
    render(
      <MemoryRouter>
        <AddWidgetDialog workspace="cae" onClose={() => {}} onAdded={added} />
      </MemoryRouter>,
    )
    await userEvent.click(screen.getByRole('tab', { name: '지표' }))
    await userEvent.click(await screen.findByRole('combobox'))
    await userEvent.click(await screen.findByText('판매 대수'))
    await userEvent.selectOptions(screen.getByLabelText('선을 나눌 기준'), 'base_model')
    await userEvent.click(screen.getByRole('button', { name: '홈에 올리기' }))
    await waitFor(() =>
      expect(metricsApi.pinHome).toHaveBeenCalledWith('sales_units', {
        workspace_slug: 'cae',
        split: 'base_model',
      }),
    )
    expect(added).toHaveBeenCalled()
  })
})
