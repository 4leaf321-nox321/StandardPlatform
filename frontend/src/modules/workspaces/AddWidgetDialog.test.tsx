/**
 * 위젯 추가의 「목록 통계」 — **보고 있던 부서를 싣고 간다.**
 *
 * 타입만 싣고 가던 때는 목록의 「홈 게시」 가 늘 대표 소속에 올렸다 — 시스템 관리자(대표 hq)가
 * /w/sales 에서 추가하면 hq 홈에 섰고, 창의 「여기에 표시됩니다」 는 거짓이었다(2026-10-08).
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const ontologyApi = vi.hoisted(() => ({
  schema: vi.fn(async () => ({
    types: [{ slug: 'part', label: '부품', kind_class: 'record', is_active: true }],
    interfaces: [],
  })),
}))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))
const metricsApi = vi.hoisted(() => ({ list: vi.fn(async () => []), pinHome: vi.fn() }))
vi.mock('@/modules/metrics/api', () => ({ metricsApi }))

function Landed() {
  const location = useLocation()
  return <p data-testid="landed">{`${location.pathname}${location.search}`}</p>
}

describe('위젯 추가 · 목록 통계', () => {
  it('목록으로 갈 때 이 부서를 home= 으로 싣는다', async () => {
    const { AddWidgetDialog } = await import('@/modules/workspaces/AddWidgetDialog')
    render(
      <MemoryRouter initialEntries={['/w/sales']}>
        <Routes>
          <Route
            path="/w/:slug"
            element={<AddWidgetDialog workspace="sales" onClose={vi.fn()} />}
          />
          <Route path="/o/:typeSlug" element={<Landed />} />
        </Routes>
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('combobox'))
    await userEvent.click(await screen.findByRole('button', { name: /부품/ }))
    await userEvent.click(screen.getByRole('button', { name: '목록으로 가기' }))
    expect(await screen.findByTestId('landed')).toHaveTextContent('/o/part?group=1&home=sales')
  })
})
