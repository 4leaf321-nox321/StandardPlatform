/**
 * 묶음의 삭제 표식 — 「작업」 화면이 무엇이 사라지는지 보인다(ADR 0090).
 *
 * 예전에는 객체 · 관계 단계만 그려, 삭제 표식만 담은 걷어내기 묶음은 빈 표였다. 「삭제」 는
 * 「사용 중지」 와 따로 세고, 복원할 수 없다고 말한다.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { TombstonePlanView, graveSummary, tombstonesOf } from '@/modules/jobs/TombstonePlanView'

const RESULT = {
  ok: true,
  objects: [],
  relations: [],
  tombstones: {
    rows: [
      { row: 1, action: 'delete', label: 'M-0001', message: '원천이 지웁니다' },
      { row: 2, action: 'deprecate', label: 'M-0002', message: '사용 중지로 둡니다' },
      { row: 3, action: 'unlink', label: 'M-0001 -derived-> M-0003', message: '끊겼습니다' },
      { row: 4, action: 'unchanged', label: 'M-0009', message: '이미 없습니다' },
    ],
    counts: { create: 0, update: 0, unchanged: 1, unlink: 1, error: 0, delete: 1, deprecate: 1 },
  },
}

describe('TombstonePlanView', () => {
  it('지움과 사용 중지를 따로 세고, 지움은 되살리는 길이 없다고 말한다', () => {
    const plan = tombstonesOf(RESULT)
    expect(plan).not.toBeNull()
    render(<TombstonePlanView plan={plan!} />)
    expect(screen.getByText(/삭제 1 · 사용 중지 1 · 관계 해제 1/)).toBeInTheDocument()
    expect(screen.getByText(/복원할 수/)).toBeInTheDocument()
    expect(screen.getByText('M-0001')).toBeInTheDocument()
    // 「그대로」 는 표에서 뺀다.
    expect(screen.queryByText('M-0009')).toBeNull()
  })

  it('작업 목록의 요약에 붙일 한 줄 — 무덤이 없으면 빈 글', () => {
    expect(graveSummary({ tombstones_delete: 2, tombstones_unlink: 3, objects_create: 1 })).toBe(
      '삭제 2 · 관계 해제 3',
    )
    expect(graveSummary({ objects_create: 1 })).toBe('')
    expect(tombstonesOf({ objects: [] })).toBeNull()
  })
})
