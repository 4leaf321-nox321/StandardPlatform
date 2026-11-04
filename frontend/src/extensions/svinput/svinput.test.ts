/**
 * 자주검증 입력 — 화면이 숫자 · 이름을 읽는 작은 규칙들. 서버의 설정이 없으면 타입 slug 를 짐작하지 않는다.
 */
import { describe, expect, it } from 'vitest'

import { verdictText } from './ReportPage'
import { roleOf, type Status } from './api'
import { refText } from './useSetup'

const STATUS: Status = {
  ready: true,
  defs: [],
  roles: [
    {
      key: 'issue',
      type_slug: 'sv_pm_issue',
      type_label: '문제점',
      fields: { report_ref: 'report', status: 'plan_status' },
      missing: [],
    },
  ],
}

describe('svinput', () => {
  it('판정 요약은 통과 · 실패 / 전체 — 판정이 없으면 —', () => {
    expect(verdictText({ stage_result: 0, cause: 0, prevention: 0, figure: 0, passed: 0, failed: 0 })).toBe('—')
    expect(verdictText({ stage_result: 3, cause: 0, prevention: 0, figure: 0, passed: 2, failed: 1 })).toBe(
      '통과 2 · 실패 1 / 3',
    )
  })

  it('참조 칸은 id 하나든 여럿이든 이름으로 — 이름이 없으면 id 그대로(비어 보이지 않게)', () => {
    const labels = { a: '모델 X 포스트모텀', b: 'Deco' }
    expect(refText('a', labels)).toBe('모델 X 포스트모텀')
    expect(refText(['a', 'b'], labels)).toBe('모델 X 포스트모텀 · Deco')
    expect(refText('zzz', labels)).toBe('zzz')
    expect(refText(null, labels)).toBe('')
  })

  it('역할의 타입 · 칸은 서버의 설정에서만 — 없으면 undefined', () => {
    expect(roleOf(STATUS, 'issue')?.type_slug).toBe('sv_pm_issue')
    expect(roleOf(STATUS, 'issue')?.fields.report_ref).toBe('report')
    expect(roleOf(STATUS, 'report')).toBeUndefined()
    expect(roleOf(null, 'issue')).toBeUndefined()
  })
})
