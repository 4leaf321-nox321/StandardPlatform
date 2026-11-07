/**
 * 셀에서 목록으로 — 셀의 수는 연도와 상관없이 센 것이라, 연 목록에도 연도가 걸리면 안 된다.
 */

import { describe, expect, it } from 'vitest'

import type { ReadHeader, SourceFreshness } from '@/modules/metrics/api'
import {
  dataGapNotice,
  dataThroughParts,
  drillHref,
  periodLabel,
  sourceIssueNotice,
} from '@/modules/metrics/metricDrill'
import { shownDateTime } from '@/shared/lib/datetime'

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

describe('periodLabel', () => {
  it('서버의 기간 이름과 같다 — 해 · 분기 · 월 · ISO 주 · 일', () => {
    expect(periodLabel('2026-01-01', 'year')).toBe('2026')
    expect(periodLabel('2026-07-01', 'quarter')).toBe('2026-Q3')
    expect(periodLabel('2026-09-01', 'month')).toBe('2026-09')
    expect(periodLabel('2026-09-07', 'week')).toBe('2026-W37')
    // 해를 넘는 주 — 2026-12-28 주는 2026년 53째 주, 2027-01-04 는 2027년 첫 주.
    expect(periodLabel('2026-12-28', 'week')).toBe('2026-W53')
    expect(periodLabel('2027-01-04', 'week')).toBe('2027-W01')
    expect(periodLabel('2026-09-11', 'day')).toBe('2026-09-11')
    expect(periodLabel('망가짐', 'month')).toBe('망가짐')
  })
})

const HEADER: ReadHeader = {
  slug: 'rate',
  label: '인입률',
  measure: 'count',
  measure_label: '건수',
  grain: 'month',
  cohort_grain: null,
  settle_days: 10,
  computed_at: '2026-11-12T12:00:00+00:00',
  watermark: '2026-11-12T12:00:00+00:00',
  stale: false,
  overlap: false,
  unbucketed: 0,
  unbucketed_cohort: 0,
  negative_age: 0,
  truncated: false,
  denominator: null,
}

const DENOMINATOR = {
  metric: 'units',
  label: '판매 대수',
  measure: 'sum' as const,
  measure_label: '합계',
  time: 'period' as const,
  per: 100,
  missing: 0,
  truncated: false,
  grain: 'month',
}

const TAIL = '으므로, 데이터 소스의 수집 현황을 확인해야 합니다.'

describe('최종 반영 기간 · 미반영 구간', () => {
  it('머리에 분자와 분모의 최종 반영 기간을 적고, 옛 계산(칸 없음)은 아무것도 안 적는다', () => {
    expect(dataThroughParts(HEADER)).toEqual([])
    expect(
      dataThroughParts({
        ...HEADER,
        data_through: '2026-09-01',
        source_through: '2026-09-01',
        denominator: { ...DENOMINATOR, data_through: '2026-01-01', grain: 'year' },
      }),
    ).toEqual(['최종 반영 기간 2026-09', '분모 최종 반영 기간 2026'])
  })

  it('원천 최종 반영 기간이 지표의 것과 다르면 함께 적는다 — 좁은 지표는 원천이 살아 있어도 끝이 멈춘다', () => {
    expect(
      dataThroughParts({
        ...HEADER,
        data_through: '2026-03-01',
        source_through: '2026-10-01',
        denominator: { ...DENOMINATOR, data_through: '2026-08-01', source_through: '2026-10-01' },
      }),
    ).toEqual([
      '최종 반영 기간 2026-03',
      '원천 최종 반영 기간 2026-10',
      '분모 최종 반영 기간 2026-08',
      '분모 원천 최종 반영 기간 2026-10',
    ])
  })

  it('미반영 구간이 아니면 경고가 없고, 원천 최종 반영 기간의 다음 기간부터 말한다', () => {
    const stopped = { ...HEADER, data_through: '2026-03-01', source_through: '2026-09-01' }
    expect(dataGapNotice({ ...stopped, data_gap: false, own_gap: false })).toBeNull()
    expect(dataGapNotice({ ...stopped, data_gap: true, own_gap: true })).toBe(
      `2026-10 이후는 미반영 구간입니다(원천 최종 반영 기간 2026-09) — 이 구간의 0건 · 감소는 수집 지연 때문일 수 있${TAIL}`,
    )
    // 머무는 기간 지표는 값이 0 이 아니다 — 새 기록이 빠진 것이다.
    expect(
      dataGapNotice({
        ...stopped,
        data_gap: true,
        own_gap: true,
        stay: { periods: 12, periods_from: null, periods_from_label: null },
      }),
    ).toBe(
      `2026-10 이후는 미반영 구간입니다(원천 최종 반영 기간 2026-09) — 이 구간의 값에는 새 기록이 빠졌을 수 있${TAIL}`,
    )
  })

  it('다음 기간은 단위를 따른다 — 분기 · ISO 주 · 일이 해를 넘어가도', () => {
    const gap = { ...HEADER, data_gap: true, own_gap: true }
    expect(dataGapNotice({ ...gap, grain: 'quarter', source_through: '2026-10-01' })).toMatch(
      /^2027-Q1 이후는 미반영 구간입니다\(원천 최종 반영 기간 2026-Q4\)/,
    )
    // 2026 은 53주까지 있다(1월 1일이 목요일).
    expect(dataGapNotice({ ...gap, grain: 'week', source_through: '2026-12-28' })).toMatch(
      /^2027-W01 이후는 미반영 구간입니다\(원천 최종 반영 기간 2026-W53\)/,
    )
    expect(dataGapNotice({ ...gap, grain: 'day', source_through: '2026-12-31' })).toMatch(
      /^2027-01-01 이후는 미반영 구간입니다\(원천 최종 반영 기간 2026-12-31\)/,
    )
  })

  it('분모 쪽이 비었으면 분모를 말하고, 둘 다 비면 두 문장이다', () => {
    const paired = { ...DENOMINATOR, source_through: '2026-08-01', data_gap: true }
    const onlyDen = {
      ...HEADER,
      source_through: '2026-10-01',
      data_gap: true,
      own_gap: false,
      denominator: paired,
    }
    expect(dataGapNotice(onlyDen)).toBe(
      `분모 「판매 대수」 의 2026-09 이후는 미반영 구간입니다(원천 최종 반영 기간 2026-08) — 이 구간의 비율은 분모가 빠졌거나 덜 반영된 값일 수 있${TAIL}`,
    )
    // 기간으로 짝짓지 않은 분모는 분모 합이 덜 반영된 것이다.
    expect(dataGapNotice({ ...onlyDen, denominator: { ...paired, time: null } })).toBe(
      `분모 「판매 대수」 의 2026-09 이후는 미반영 구간입니다(원천 최종 반영 기간 2026-08) — 분모 합이 덜 반영되었을 수 있${TAIL}`,
    )
    expect(dataGapNotice({ ...onlyDen, source_through: '2026-07-01', own_gap: true })).toBe(
      `2026-08 이후는 미반영 구간입니다(원천 최종 반영 기간 2026-07) — 이 구간의 0건 · 감소는 수집 지연 때문일 수 있습니다. 분모 「판매 대수」 의 2026-09 이후는 미반영 구간입니다(원천 최종 반영 기간 2026-08) — 이 구간의 비율은 분모가 빠졌거나 덜 반영된 값일 수 있${TAIL}`,
    )
  })
})

describe('수집 이상', () => {
  const source = (extra: Partial<SourceFreshness>): SourceFreshness => ({
    slug: 's',
    name: '가 ERP',
    kind: 'odata',
    type_slug: 'svc_case',
    interval_minutes: 60,
    last_success_at: '2026-11-01T03:00:00Z',
    last_run_at: '2026-11-07T03:00:00Z',
    failing: false,
    stale: false,
    active: true,
    catching_up: false,
    ...extra,
  })

  it('수집 실패 · 수집 지연 · 적재 진행 중인 원천을 한 줄로, 비활성과 정상인 것은 말하지 않는다', () => {
    const last = shownDateTime('2026-11-01T03:00:00Z')
    expect(sourceIssueNotice({ ...HEADER, freshness: [source({})] })).toBeNull()
    expect(
      sourceIssueNotice({
        ...HEADER,
        freshness: [
          source({ failing: true }),
          source({ name: '나 끔', failing: true, active: false }),
          source({ name: '다 코어', catching_up: true }),
        ],
        denominator: {
          ...DENOMINATOR,
          freshness: [source({ name: '판매 SAP', stale: true, last_success_at: null })],
        },
      }),
    ).toBe(
      `원천 「가 ERP」 수집 실패(최종 수집 시각 ${last}) · 원천 「다 코어」 적재 진행 중(최종 수집 시각 ${last}) · 분모 「판매 대수」 의 원천 「판매 SAP」 수집 지연(최종 수집 시각 없음) — 해당 원천의 새 기록이 집계에 빠졌을 수 있습니다.`,
    )
  })
})
