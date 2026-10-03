/**
 * 분석 보기들이 함께 쓰는 것 — 주의 목록 · 방법 줄 · 수 표기.
 *
 * **주의는 숫자보다 먼저, 서버의 말 그대로.** 「외삽한 값」 「열린 기간을 뺐다」 「일부 부서만
 * 보인다」 는 숫자를 어떻게 읽을지를 바꾼다 — 화면이 고쳐 쓰거나 접어 두면 그 판단이 사라진다.
 */

import { Link } from 'react-router-dom'

import type { Drill } from '@/modules/metrics/api'
import type { AnalysisHeader, Caveat } from '@/modules/metrics/analysis/types'
import { drillHref, shownNumber } from '@/modules/metrics/metricDrill'
import { shownDateTime } from '@/shared/lib/datetime'

/** 뺀 기록의 이름 — 서버 `excluded` 의 열쇠. 전작 것은 「전작:」 을 붙인다. */
const EXCLUDED_LABELS: Record<string, string> = {
  missing_denominator: '분모 없음',
  open_denominator: '판매가 덜 들어온 달',
  open_cohort: '닫히지 않은 코호트',
  open_cells: '아직 들어오는 경과',
  inconsistent_denominator: '기록이 대수보다 많음',
  beyond_max_age: '경과 상한 밖',
  beyond_reference: '전작이 안 닿은 경과',
  empty: '값이 빈 기록',
  open: '열린 부분군',
}

export function excludedLabel(key: string): string {
  if (key.startsWith('reference_')) return `전작: ${excludedLabel(key.slice(10))}`
  return EXCLUDED_LABELS[key] ?? key
}

/** 몫 · 비율을 %로 — 소수 자리는 크기에 맞춰. */
export function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toLocaleString('ko-KR', { maximumFractionDigits: digits })}%`
}

export function interval(band: number[] | null | undefined, digits = 2): string {
  if (!band || band.length < 2) return '—'
  return `${shownNumber(band[0], digits)} ~ ${shownNumber(band[1], digits)}`
}

export function CaveatList({ caveats }: { caveats: Caveat[] }) {
  if (caveats.length === 0) return null
  return (
    <ul className="space-y-1" aria-label="주의">
      {caveats.map((one) => (
        <li
          key={one.code}
          className={
            one.level === 'warn'
              ? 'rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100'
              : 'text-muted-foreground text-xs'
          }
        >
          {one.message}
          {one.count !== null && one.count > 0 && ` (${shownNumber(one.count)})`}
        </li>
      ))}
    </ul>
  )
}

/** 방법 · 계산 시각 · 보이는 몫 · 뺀 것 — 같은 물음의 답이 왜 달라졌는지 가를 줄. */
export function AnalysisMeta({ result }: { result: AnalysisHeader }) {
  const parts: string[] = [result.method]
  parts.push(
    result.computed_at ? `계산 시각 ${shownDateTime(result.computed_at)}` : '아직 안 셌습니다',
  )
  if (result.stale) parts.push('오래됨')
  if (result.visible_share !== null && result.visible_share < 0.999)
    parts.push(`보이는 몫 ${percent(result.visible_share, 0)}`)
  const excluded = Object.entries(result.excluded)
  if (excluded.length > 0)
    parts.push(
      `뺀 기록: ${excluded.map(([key, value]) => `${excludedLabel(key)} ${shownNumber(value)}`).join(', ')}`,
    )
  return <p className="text-muted-foreground text-xs">{parts.join(' · ')}</p>
}

export function DrillLink({ drill, count }: { drill: Drill; count: number }) {
  return (
    <Link to={drillHref(drill)} className="whitespace-nowrap hover:underline">
      {shownNumber(count)}건 보기
      {drill.partial.length > 0 && (
        <span title={`조건으로 못 적은 축: ${drill.partial.join(', ')}`}> ≈</span>
      )}
    </Link>
  )
}

/** 한 줄 숫자 묶음 — 이름 위, 값 아래. */
export function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded border px-3 py-2" title={hint}>
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
    </div>
  )
}
