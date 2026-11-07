/**
 * 셀에서 목록으로 — 셀의 수는 연도와 상관없이 센 것이라, 연 목록에도 연도가 걸리면 안 된다.
 */

import { describe, expect, it } from 'vitest'

import { drillHref } from '@/modules/metrics/metricDrill'

describe('drillHref', () => {
  it('목록 조건 그대로에 year=all 을 단다 — 목록의 기본(올해)이 셀의 수를 줄이지 않게', () => {
    expect(
      drillHref({
        type_slug: 'svc_case',
        params: { 'f.symptom.eq': '소음', 'f.received.gte': '2025-02-01' },
        partial: [],
      }),
    ).toBe('/o/svc_case?f.symptom.eq=%EC%86%8C%EC%9D%8C&f.received.gte=2025-02-01&year=all')
    // 조건이 없어도 — 전체 기록의 수다.
    expect(drillHref({ type_slug: 'svc_case', params: {}, partial: [] })).toBe(
      '/o/svc_case?year=all',
    )
  })

  it('조건이 연도를 이미 정했으면 그것을 둔다', () => {
    expect(drillHref({ type_slug: 'svc_case', params: { year: '2025' }, partial: [] })).toBe(
      '/o/svc_case?year=2025',
    )
  })
})
