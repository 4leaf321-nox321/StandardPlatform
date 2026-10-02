/**
 * 가리키는 기록 — **줄 대신 수로, 누르면 그 칸으로 걸러진 목록**(ADR 0011).
 */

import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { LogCounts } from './LogCounts'

describe('가리키는 기록', () => {
  it('타입 · 칸마다 수를 보이고 그 칸으로 걸러진 목록으로 잇는다', () => {
    render(
      <MemoryRouter>
        <LogCounts
          objectId="m1"
          counts={[
            {
              type_slug: 'case',
              type_label: '시장 서비스',
              key: 'model',
              label: '모델',
              inverse_label: '시장 서비스',
              count: 100000,
            },
          ]}
        />
      </MemoryRouter>,
    )
    const link = screen.getByRole('link', { name: /시장 서비스/ })
    expect(link).toHaveTextContent('100,000건')
    expect(link).toHaveAttribute('href', '/o/case?f.model.eq=m1')
  })

  it('가리키는 기록이 없으면 자리도 없다', () => {
    const { container } = render(
      <MemoryRouter>
        <LogCounts objectId="m1" counts={[]} />
      </MemoryRouter>,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
