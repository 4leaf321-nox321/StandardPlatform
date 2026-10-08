/**
 * 날짜·시각 칸 — **가져오기로 들어온 값을 빈칸으로 보이지 않는다.**
 *
 * 공백 · 오프셋이 든 저장값은 입력 칸이 못 읽어 빈칸으로 보였고, 빈 줄 알고 손대면 지워졌다
 * (2026-10-08). 읽을 수 있으면 맞춰 보이고, 못 읽으면 원값을 적어 두고 그대로 둔다.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { PropertyDef } from '@/modules/ontology/api'
import { PropertyFields } from '@/modules/objects/PropertyFields'

const MEASURED = {
  key: 'measured',
  label: '측정 시각',
  data_type: 'datetime',
  multi: false,
  required: false,
} as unknown as PropertyDef

describe('날짜·시각 칸', () => {
  it('공백이 든 값은 맞춰 보이고, 손대기 전에는 아무것도 안 바꾼다', () => {
    const onChange = vi.fn()
    render(
      <PropertyFields
        defs={[MEASURED]}
        values={{ measured: '2026-10-08 09:30:00' }}
        onChange={onChange}
      />,
    )
    // 브라우저는 0 초를 떼어 보인다(정규화) — 시각이 그대로 서 있는지만 본다.
    const input = document.querySelector('input[type="datetime-local"]') as HTMLInputElement
    expect(input.value).toMatch(/^2026-10-08T09:30/)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('못 읽는 값은 원값을 적어 둔다 — 빈칸으로만 보이지 않게', () => {
    render(
      <PropertyFields
        defs={[MEASURED]}
        values={{ measured: '2026-10-08T09' }}
        onChange={vi.fn()}
      />,
    )
    expect(screen.getByText('2026-10-08T09')).toBeInTheDocument()
    expect(screen.getByText(/읽을 수 없는 모양이라/)).toBeInTheDocument()
  })
})
