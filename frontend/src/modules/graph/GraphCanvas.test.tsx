/**
 * 캔버스 껍데기가 지키는 것 — **넓게 보기가 실제로 화면을 덮나, 높이가 아래까지 차나.**
 *
 * 그림 자체는 여기서 안 그린다(happy-dom 에는 canvas 가 없다). 보는 것은 그 바깥의
 * 배치다 — 그리고 이 둘은 **눈으로만 확인하던 것**이라 조용히 되돌아갈 수 있었다.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import type { CanvasNode } from '@/modules/graph/GraphCanvas'

vi.mock('@/shared/theme/ThemeProvider', () => ({
  useTheme: () => ({ theme: 'light', toggle: () => {} }),
}))
// 그림 자체는 canvas 를 쓴다 — happy-dom 에 없다. 도구 막대가 그 곁에 있으므로,
// 라이브러리 자리에 빈 것을 끼워 넣어 껍데기까지 그려지게 한다.
vi.mock('react-force-graph-2d', () => ({
  default: () => <div data-testid="force-graph" />,
}))
// happy-dom 에는 배치가 없어 모든 요소가 0×0 이다. 캔버스는 **폭이 있을 때만** 그려지고
// 도구 막대가 그 안에 있으므로, 크기를 아는 척해 줘야 단추까지 선다.
beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
    value: () => ({
      width: 800,
      height: 600,
      top: 120,
      left: 0,
      right: 800,
      bottom: 720,
      x: 0,
      y: 120,
      toJSON: () => ({}),
    }),
    configurable: true,
  })
})

const NODES: CanvasNode[] = [
  { id: 'a', label: '가', color: '#2563eb', radius: 6, shape: 'circle' },
  { id: 'b', label: '나', color: '#dc2626', radius: 6, shape: 'circle' },
]

async function draw(props: Record<string, unknown> = {}) {
  const { GraphCanvas } = await import('@/modules/graph/GraphCanvas')
  const { container } = render(<GraphCanvas nodes={NODES} links={[]} {...props} />)
  return container.firstElementChild as HTMLElement
}

describe('그래프 캔버스', () => {
  it('넓게 보기는 **자리를 실제로 바꾼다**', async () => {
    // `relative` 와 `fixed` 를 함께 두면 Tailwind 가 내보내는 차례(fixed → relative)
    // 때문에 나중 것이 이긴다 — 켜도 제자리에 남고 높이만 사라져 그림이 사라졌다.
    const box = await draw()
    expect(box.className).toContain('relative')
    expect(box.className).not.toContain('fixed')

    // 그림 라이브러리는 늦게 온다(lazy) — 도구 막대가 그 뒤에 선다.
    await userEvent.click(await screen.findByRole('button', { name: '넓게 보기' }))
    expect(box.className).toContain('fixed')
    expect(box.className).not.toContain('relative')

    await userEvent.click(await screen.findByRole('button', { name: '축소' }))
    expect(box.className).toContain('relative')
  })

  it('높이를 재서 화면 아래까지 채운다', async () => {
    const box = await draw()
    // 고정값(640px)이면 큰 화면에서 아래가 빈 채로 남는다.
    expect(box.style.height).toMatch(/^\d+px$/)
    expect(Number.parseInt(box.style.height, 10)).toBeGreaterThanOrEqual(420)
  })

  it('제 높이를 가진 곳은 그것을 덮어쓸 수 있다', async () => {
    // 상세 안의 작은 관계도는 `h-[360px]!` 로 재는 높이를 이긴다.
    const box = await draw({ className: 'h-[360px]!' })
    expect(box.className).toContain('h-[360px]!')
  })
})
