/**
 * 한 장 요약의 본문 — 분석 탭과 축 객체의 상세 화면이 함께 쓴다(ADR 0022).
 *
 * 그 값의 기록 수 · 몫, 기간 추이, 다른 기준마다 함께 나온 값(향상도 — 전체에서보다 몇 배 자주).
 * 함께 나옴은 원인이 아니다(주의가 앞에 선다).
 */

import {
  AnalysisMeta,
  CaveatList,
  DrillLink,
  Stat,
  percent,
} from '@/modules/metrics/analysis/common'
import type { ProfileResult } from '@/modules/metrics/analysis/types'
import { shownNumber } from '@/modules/metrics/metricDrill'
import { LazyPlot } from '@/shared/charts/LazyPlot'

export function ProfileSummary({
  data,
  compact = false,
}: {
  data: ProfileResult
  compact?: boolean
}) {
  return (
    <div className="space-y-3">
      <CaveatList caveats={data.caveats} />
      {!compact && <AnalysisMeta result={data} />}
      <div className="flex flex-wrap items-end gap-2">
        <Stat
          label={`${data.dim_label} 「${data.value_label}」`}
          value={`${shownNumber(data.count)}건`}
          hint={`전체 ${shownNumber(data.total)}건 중 ${percent(data.share, 1)}`}
        />
        <DrillLink drill={data.drill} count={data.count} />
      </div>
      {data.points.length > 1 && (
        <LazyPlot
          height={compact ? 180 : 240}
          title="기간마다 건수"
          data={[
            {
              type: 'bar',
              name: data.value_label,
              x: data.points.map((one) => one.period),
              y: data.points.map((one) => one.count),
              marker: {
                color: data.points.map((one) => (one.closed ? '#2563eb' : '#93c5fd')),
              },
            },
          ]}
        />
      )}
      <div className="grid gap-3 md:grid-cols-2">
        {data.related.map((group) => (
          <section key={group.dim} className="space-y-1">
            <p className="text-sm font-medium">함께 나온 {group.label}</p>
            {group.values.length === 0 ? (
              <p className="text-muted-foreground text-xs">없음</p>
            ) : (
              <ul className="space-y-0.5 text-sm">
                {group.values.map((one) => (
                  <li key={one.key ?? '-'} className="flex items-baseline justify-between gap-2">
                    <span>
                      {one.label}{' '}
                      <span className="text-muted-foreground text-xs">
                        {percent(one.share, 0)}
                        {one.lift !== null && ` · ${shownNumber(one.lift, 1)}배`}
                      </span>
                    </span>
                    <DrillLink drill={one.drill} count={one.count} />
                  </li>
                ))}
                {group.others > 0 && (
                  <li className="text-muted-foreground text-xs">그 밖 {group.others}개</li>
                )}
              </ul>
            )}
          </section>
        ))}
      </div>
    </div>
  )
}
