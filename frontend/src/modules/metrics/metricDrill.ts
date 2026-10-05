/**
 * 셀에서 목록으로 — **숫자마다 근거로 돌아간다.**
 *
 * 서버가 셀마다 그 수를 이룬 기록의 목록 조건(`drill`)을 준다. 화면은 그것을 그대로 주소에
 * 적는다 — 기간은 버킷 시작일의 범위(`gte` + `lt`), 기준은 같은 주소의 `eq`. 조건으로 못 적은
 * 축(`partial`)이 있으면 목록의 수가 셀의 수보다 클 수 있다고 함께 말한다.
 */

import type { Drill } from '@/modules/metrics/api'

export function drillHref(drill: Drill): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(drill.params)) params.append(key, value)
  const text = params.toString()
  return `/o/${drill.type_slug}${text ? `?${text}` : ''}`
}

/** 수를 사람이 읽게 — 정수는 그대로, 소수는 두 자리까지. 없으면 「—」. */
export function shownNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  if (Number.isInteger(value)) return value.toLocaleString('ko-KR')
  return value.toLocaleString('ko-KR', { maximumFractionDigits: digits })
}

/** 기간 단위의 이름 — 서버의 `GRAIN_LABELS` 와 같다. */
export const GRAIN_LABELS: Record<string, string> = {
  day: '일',
  week: '주',
  month: '월',
  quarter: '분기',
  year: '해',
}

export const MEASURE_LABELS: Record<string, string> = {
  count: '건수',
  share: '조건 비율',
  sum: '합계',
  avg: '평균',
  min: '최솟값',
  max: '최댓값',
}

/** 목록 화면의 신선도 한 마디 — 실패 · 깨짐 · 오래됨 · 아직 안 셈. 정상이면 null. */
export function freshness(metric: {
  broken: string | null
  last_status: string | null
  last_run_at: string | null
  stale: boolean
  is_active: boolean
}): { label: string; tone: 'bad' | 'warn' | 'neutral' } | null {
  if (!metric.is_active) return { label: '사용 안 함', tone: 'neutral' }
  if (metric.broken) return { label: '정의 깨짐', tone: 'bad' }
  if (metric.last_status === 'failed') return { label: '계산 실패', tone: 'bad' }
  if (!metric.last_run_at) return { label: '아직 안 셈', tone: 'neutral' }
  if (metric.stale) return { label: '오래됨', tone: 'warn' }
  return null
}
