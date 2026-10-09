/**
 * 채울 곳이 지키는 것 — **어디를 채우면 무엇이 좋아지는지 말하고, 어림이면 어림이라고 적는다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const fillApi = vi.hoisted(() => ({ report: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ fillApi }))

describe('채울 곳', () => {
  it('줄마다 쓰는 곳과 좋아지는 것을 말하고 빈 것만 거른 목록으로 간다', async () => {
    fillApi.report.mockResolvedValue({
      types: [],
      notes: ['큰 타입은 id 로 고른 표본에서 세어 늘렸습니다(어림) — 시장 서비스(약 200만 건).'],
      priorities: [
        {
          kind: 'field',
          type_slug: 'svc_case',
          type_label: '시장 서비스',
          target: 'parts',
          target_label: '교체 부품',
          missing: 68986,
          total: 135892,
          score: 57.1,
          weight: 7,
          uses: ['지표 「교체 부품」'],
          gain: '지표 「교체 부품」가 이 칸을 씁니다.',
          link: '/o/svc_case?f.parts.empty=',
          estimated: true,
        },
        {
          kind: 'empty_type',
          type_slug: 'vendor',
          type_label: '공급사',
          target: '',
          target_label: '객체',
          missing: 0,
          total: 0,
          score: 6,
          weight: 2,
          uses: [],
          gain: '정의만 있고 객체가 없습니다.',
          link: '/o/vendor',
          estimated: false,
        },
      ],
    })
    const { FillPriorities } = await import('@/modules/objects/FillPriorities')
    render(
      <MemoryRouter>
        <FillPriorities />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('교체 부품')).toBeInTheDocument())
    expect(screen.getByText('지표 「교체 부품」')).toBeInTheDocument()
    expect(screen.getByText('지표 「교체 부품」가 이 칸을 씁니다.')).toBeInTheDocument()
    // 어림이면 「약」 — 표본에서 센 수를 정확한 수처럼 적지 않는다.
    const amount = screen.getByRole('link', { name: /약 68,986건/ })
    expect(amount).toHaveAttribute('href', '/o/svc_case?f.parts.empty=')
    expect(screen.getByRole('link', { name: '객체 없음' })).toHaveAttribute('href', '/o/vendor')
    expect(screen.getByText(/표본에서 세어 늘렸습니다/)).toBeInTheDocument()
  })

  it('채울 곳이 없으면 이유를 말한다', async () => {
    fillApi.report.mockResolvedValue({ types: [], notes: [], priorities: [] })
    const { FillPriorities } = await import('@/modules/objects/FillPriorities')
    render(
      <MemoryRouter>
        <FillPriorities />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText(/채울 곳이 없습니다/)).toBeInTheDocument())
  })
})
