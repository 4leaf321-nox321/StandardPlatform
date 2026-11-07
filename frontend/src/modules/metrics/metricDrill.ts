/**
 * 셀에서 목록으로 — **숫자마다 근거로 돌아간다.**
 *
 * 서버가 셀마다 그 수를 이룬 기록의 목록 조건(`drill`)을 준다. 화면은 그것을 그대로 주소에
 * 적는다 — 기간은 버킷 시작일의 범위(`gte` + `lt`), 기준은 같은 주소의 `eq`. 조건으로 못 적은
 * 축(`partial`)이 있으면 목록의 수가 셀의 수보다 클 수 있다고 함께 말한다.
 *
 * **`year=all` 을 함께 단다.** 셀의 수는 연도와 상관없이 센 것인데 목록 화면은 연도를 쓰는
 * 타입이면 올해가 기본이다 — 안 달면 지난해 셀의 「N건 보기」 가 올해 것만(대개 0건) 연다.
 */

import type { Drill, ReadHeader, SourceFreshness } from '@/modules/metrics/api'
import { shownDateTime } from '@/shared/lib/datetime'

export function drillHref(drill: Drill): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(drill.params)) params.append(key, value)
  if (!params.has('year')) params.set('year', 'all')
  return `/o/${drill.type_slug}?${params.toString()}`
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

/**
 * 기간 시작일(`YYYY-MM-DD`) → 사람이 읽는 기간 — 서버 `axes.period_label` 과 같다(2026 ·
 * 2026-Q3 · 2026-09 · 2026-W37 · 2026-09-11). 셀은 서버가 이름을 붙여 오지만 머리의 날짜(최종
 * 반영 기간)는 날짜만 온다. 주는 ISO 주(월요일 시작, 그 주의 목요일이 든 해).
 */
export function periodLabel(iso: string, grain: string | null | undefined): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (!match) return iso
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  if (grain === 'year') return match[1]
  if (grain === 'quarter') return `${match[1]}-Q${Math.floor((month - 1) / 3) + 1}`
  if (grain === 'month') return `${match[1]}-${match[2]}`
  if (grain === 'week') {
    const thursday = new Date(Date.UTC(year, month - 1, day))
    thursday.setUTCDate(thursday.getUTCDate() - ((thursday.getUTCDay() + 6) % 7) + 3)
    const weekYear = thursday.getUTCFullYear()
    const first = Date.UTC(weekYear, 0, 1)
    const week = Math.floor((thursday.getTime() - first) / (7 * 86_400_000)) + 1
    return `${weekYear}-W${String(week).padStart(2, '0')}`
  }
  return iso.slice(0, 10)
}

/** 다음 기간의 시작일 — 서버 `axes.next_period` 와 같다(단위가 없으면 하루). */
function nextPeriodStart(iso: string, grain: string | null | undefined): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (!match) return iso
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const months: Record<string, number> = { month: 1, quarter: 3, year: 12 }
  const next =
    grain && grain in months
      ? new Date(Date.UTC(year, month - 1 + months[grain], day))
      : new Date(Date.UTC(year, month - 1, day + (grain === 'week' ? 7 : 1)))
  return next.toISOString().slice(0, 10)
}

/**
 * 머리의 「최종 반영 기간」 — 분자 것과, 분모가 다른 지표면 그 것. 원천 최종 반영 기간(원천 타입
 * 전체의 끝 — 미반영 구간을 이것으로 판정한다)이 지표의 것과 다르면 함께 적는다: 좁은 지표는 원천이
 * 살아 있어도 끝이 멈춰 있고, 그때 「최종 반영 기간 2026-03」 만 적으면 멎은 것으로 읽힌다. 시간
 * 칸이 없거나 옛 계산이면 없다.
 */
export function dataThroughParts(header: ReadHeader): string[] {
  const parts: string[] = []
  if (header.data_through)
    parts.push(`최종 반영 기간 ${periodLabel(header.data_through, header.grain)}`)
  if (header.source_through && header.source_through !== header.data_through)
    parts.push(`원천 최종 반영 기간 ${periodLabel(header.source_through, header.grain)}`)
  const den = header.denominator
  const denGrain = den?.grain ?? header.grain
  if (den?.data_through)
    parts.push(`분모 최종 반영 기간 ${periodLabel(den.data_through, denGrain)}`)
  if (den?.source_through && den.source_through !== den.data_through)
    parts.push(`분모 원천 최종 반영 기간 ${periodLabel(den.source_through, denGrain)}`)
  return parts
}

/** 미반영 구간의 머리 — 서버 `common._gap_head` 와 같다. 「이후」 는 그 기간을 넣는다. */
function gapHead(through: string, grain: string | null | undefined): string {
  const after = periodLabel(nextPeriodStart(through, grain), grain)
  return `${after} 이후는 미반영 구간입니다(원천 최종 반영 기간 ${periodLabel(through, grain)})`
}

/**
 * 미반영 구간의 경고 — 서버 분석의 주의 `data_gap` 과 같은 말. 닫힌 기간의 경계는 계산 시각에서
 * 나와 원천이 멎어도 앞으로 가므로, 원천 최종 반영 기간 뒤의 0건은 아직 안 들어온 것일 수 있다.
 * 분자 쪽(`own_gap`) · 분모 쪽(`denominator.data_gap`)이 둘 다 비면 두 문장이다. 머무는 기간
 * 지표는 값이 0 이 아니라 새 기록이 빠진 것이고, 기간으로 짝짓지 않은 분모는 분모 합이 덜 반영된
 * 것이다.
 */
export function dataGapNotice(header: ReadHeader): string | null {
  if (!header.data_gap) return null
  const den = header.denominator
  // 쪽마다 「…수 있」 으로 끝난다 — 서버 `common._gap_message` 와 같이 잇는다.
  const sides: string[] = []
  // 옛 서버(`own_gap` 없음)는 분모 쪽이 아니면 분자다.
  const own = header.own_gap ?? !den?.data_gap
  if (own && header.source_through)
    sides.push(
      `${gapHead(header.source_through, header.grain)} — ${
        header.stay
          ? '이 구간의 값에는 새 기록이 빠졌을 수 있'
          : '이 구간의 0건 · 감소는 수집 지연 때문일 수 있'
      }`,
    )
  if (den?.data_gap && den.source_through)
    sides.push(
      `분모 「${den.label}」 의 ${gapHead(den.source_through, den.grain ?? header.grain)} — ${
        den.time
          ? '이 구간의 비율은 분모가 빠졌거나 덜 반영된 값일 수 있'
          : '분모 합이 덜 반영되었을 수 있'
      }`,
    )
  const last = sides.pop()
  if (last === undefined) return null
  return [
    ...sides.map((one) => `${one}습니다.`),
    `${last}으므로, 데이터 소스의 수집 현황을 확인해야 합니다.`,
  ].join(' ')
}

/**
 * 원천 하나의 수집 이상 — 수집 실패 · 수집 지연 · 적재 진행 중. 비활성 소스는 일부러 멈춘 것이라
 * 말하지 않는다.
 */
function sourceProblem(one: SourceFreshness): string | null {
  if (!one.active) return null
  const state = one.failing
    ? '수집 실패'
    : one.stale
      ? '수집 지연'
      : one.catching_up
        ? '적재 진행 중'
        : null
  if (state === null) return null
  const last = one.last_success_at ? shownDateTime(one.last_success_at) : '없음'
  return `원천 「${one.name}」 ${state}(최종 수집 시각 ${last})`
}

/**
 * 원천 하나만 멎은 때의 한 줄(수집 이상) — 서버 분석의 주의 `source_issue` 와 같은 말(시각만 이
 * 화면의 시각으로). 원천이 여럿이면 하나가 멎어도 다른 것이 들어와 원천 최종 반영 기간이 새것이라
 * 미반영 구간이 안 선다. 분모 지표의 원천도 본다.
 */
export function sourceIssueNotice(header: ReadHeader): string | null {
  const parts = (header.freshness ?? [])
    .map(sourceProblem)
    .filter((one): one is string => one !== null)
  const den = header.denominator
  for (const one of den?.freshness ?? []) {
    const found = sourceProblem(one)
    if (found) parts.push(`분모 「${den?.label}」 의 ${found}`)
  }
  if (parts.length === 0) return null
  return `${parts.join(' · ')} — 해당 원천의 새 기록이 집계에 빠졌을 수 있습니다.`
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
  if (!metric.is_active) return { label: '비활성', tone: 'neutral' }
  if (metric.broken) return { label: '정의 깨짐', tone: 'bad' }
  if (metric.last_status === 'failed') return { label: '계산 실패', tone: 'bad' }
  if (!metric.last_run_at) return { label: '아직 안 셈', tone: 'neutral' }
  if (metric.stale) return { label: '오래됨', tone: 'warn' }
  return null
}
