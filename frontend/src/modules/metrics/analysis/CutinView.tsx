/**
 * 전후 비교 — **대책 적용일 뒤에 만든(판) 것부터 줄었나.**
 *
 * 앞은 거른 범위의 첫 부분군(모델로 거르면 그 모델의 첫 출고)부터 적용일 직전까지, 뒤는 적용일
 * 뒤의 **창이 닫힌** 부분군만이다. 「줄었다」 는 날짜 뒤에 줄었다는 것이지 대책 때문이라는 것이
 * 아니다 — 그 말(주의)과 앞쪽 흐름을 결과보다 앞에 둔다.
 */

import { useState } from 'react'

import { metricsApi } from '@/modules/metrics/api'
import type { AnalysisViewProps } from '@/modules/metrics/analysis/SprtView'
import {
  AnalysisMeta,
  CaveatList,
  DrillLink,
  Stat,
  interval,
  percent,
} from '@/modules/metrics/analysis/common'
import type { CutinResult, CutinSide } from '@/modules/metrics/analysis/types'
import { shownNumber } from '@/modules/metrics/metricDrill'
import { LazyPlot } from '@/shared/charts/LazyPlot'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { useResource } from '@/shared/hooks/useResource'

const AUTO = '__auto__'
const EFFECTS = ['0.1', '0.2', '0.3']
const SIDE_COLORS = {
  before: '#2563eb',
  after: '#059669',
  skipped: '#9ca3af',
  boundary: '#d97706',
}

const DECISIONS: Record<CutinResult['decision'], string> = {
  reduced: '줄었다',
  increased: '늘었다',
  no_difference: '차이 없음',
  too_early: '아직 이르다',
}

export function CutinView({ metric, read, initial = {} }: AnalysisViewProps) {
  const [at, setAt] = useState(initial.at ?? '')
  const [axis, setAxis] = useState(initial.axis ?? AUTO)
  const [span, setSpan] = useState(initial.window ?? '3')
  const [skip, setSkip] = useState(initial.skip_first ?? '0')
  const [effect, setEffect] = useState(initial.effect ?? '0.2')
  const share = metric.spec.measure === 'share'
  const filtered = Object.keys(read.filters ?? {}).length > 0
  const asked = {
    at,
    axis: axis === AUTO ? undefined : axis,
    window: span,
    skip_first: skip,
    effect,
  }
  const key = JSON.stringify([asked, read])
  const result = useResource<CutinResult | null>(
    () =>
      at
        ? metricsApi.analysis<CutinResult>(metric.slug, 'cutin', asked, read)
        : Promise.resolve(null),
    [metric.slug, key],
  )
  const data = result.data
  const unit = share ? '%' : data && data.kind === 'rate' ? `건/${shownNumber(data.per)}대` : '건'
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="cutin-at">적용일</Label>
          <Input
            id="cutin-at"
            type="date"
            className="w-44"
            value={at}
            onChange={(event) => setAt(event.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="cutin-axis">부분군</Label>
          <Select value={axis} onValueChange={setAxis}>
            <SelectTrigger id="cutin-axis" className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={AUTO}>정의에 맞게</SelectItem>
              {metric.grain && <SelectItem value="period">접수 기간마다</SelectItem>}
              {metric.cohort_grain && metric.grain && (
                <SelectItem value="cohort">코호트마다(출고 뒤 창)</SelectItem>
              )}
            </SelectContent>
          </Select>
        </div>
        {metric.cohort_grain && (
          <div className="space-y-1">
            <Label htmlFor="cutin-window">창(기간 수)</Label>
            <Input
              id="cutin-window"
              type="number"
              min={1}
              className="w-24"
              value={span}
              onChange={(event) => setSpan(event.target.value)}
            />
          </div>
        )}
        <div className="space-y-1">
          <Label htmlFor="cutin-skip">처음 빼기(출시 초기)</Label>
          <Input
            id="cutin-skip"
            type="number"
            min={0}
            className="w-24"
            value={skip}
            onChange={(event) => setSkip(event.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="cutin-effect">의미 있는 차이</Label>
          <Select value={effect} onValueChange={setEffect}>
            <SelectTrigger id="cutin-effect" className="w-28">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {EFFECTS.map((one) => (
                <SelectItem key={one} value={one}>
                  {percent(Number(one), 0)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      {!filtered && (
        <p className="text-muted-foreground text-xs">
          대책은 모델마다 들어갑니다 — 위의 거르기에서 그 모델을 고르면 앞쪽이 그 모델의 첫
          출고부터입니다.
        </p>
      )}
      {!at && <p className="text-muted-foreground text-sm">적용일을 넣으면 견줍니다.</p>}
      {result.error && <ErrorNotice error={result.error} />}
      {data && (
        <>
          <CaveatList caveats={data.caveats} />
          <AnalysisMeta result={data} />
          <p className="text-sm">
            <b>{DECISIONS[data.decision]}</b>
            {data.ratio !== null &&
              ` — 뒤가 앞의 ${shownNumber(data.ratio, 2)}배(${
                // 앞쪽 건수가 아주 적으면 위 끝이 없다(서버가 null) — 「—」 로만 보이면 구간이 아예
                // 없는 줄 안다(2026-10-08).
                data.ratio_low !== null && data.ratio_high === null
                  ? `${shownNumber(data.ratio_low, 2)} ~ 상한 없음`
                  : interval(
                      data.ratio_low !== null && data.ratio_high !== null
                        ? [data.ratio_low, data.ratio_high]
                        : null,
                    )
              }).`}
            {data.decision === 'no_difference' &&
              ` ${percent(data.effect, 0)} 넘게 달라지지는 않았습니다.`}
            {data.decision === 'too_early' &&
              (data.more_subgroups !== null
                ? ` ${percent(data.effect, 0)} 차이를 가리려면 뒤의 닫힌 부분군이 약 ${data.more_subgroups}개 더 필요합니다.`
                : ` 앞쪽 건수가 적어 ${percent(data.effect, 0)} 차이는 가릴 수 없습니다.`)}
          </p>
          <div className="flex flex-wrap gap-2">
            <SideStat label="앞" side={data.before} unit={unit} />
            <SideStat label="뒤" side={data.after} unit={unit} />
            <Stat
              label="흔들림(φ)"
              value={shownNumber(data.dispersion, 2)}
              hint="부분군끼리의 흔들림 / 우연의 흔들림 — 1 보다 크면 구간을 그만큼 넓혔다"
            />
          </div>
          {data.points.length > 0 && (
            <LazyPlot
              height={300}
              title={`부분군 비율(${unit}) — 세로선이 적용일, 가로선이 앞 · 뒤 평균`}
              data={[
                {
                  type: 'scatter',
                  mode: 'markers',
                  name: '부분군',
                  x: data.points.map((one) => one.when),
                  y: data.points.map((one) => one.rate),
                  text: data.points.map((one) => one.label),
                  marker: {
                    color: data.points.map((one) => SIDE_COLORS[one.side]),
                    symbol: data.points.map((one) => (one.closed ? 'circle' : 'circle-open')),
                  },
                },
              ]}
              layout={{
                shapes: [
                  {
                    type: 'line',
                    x0: data.at,
                    x1: data.at,
                    yref: 'paper',
                    y0: 0,
                    y1: 1,
                    line: { dash: 'dot', color: '#6b7280' },
                  },
                  ...meanLine(data, 'before'),
                  ...meanLine(data, 'after'),
                ],
              }}
            />
          )}
          {data.pre_trend.change_per_period !== null && (
            <p className="text-muted-foreground text-xs">
              적용 전 흐름: 부분군마다 {percent(data.pre_trend.change_per_period, 1)}
              {data.pre_trend.p_value !== null && ` (p ${shownNumber(data.pre_trend.p_value, 3)})`}
            </p>
          )}
        </>
      )}
    </div>
  )
}

function SideStat({ label, side, unit }: { label: string; side: CutinSide; unit: string }) {
  if (side.subgroups === 0) return <Stat label={label} value="—" hint="닫힌 부분군이 없다" />
  return (
    <div className="space-y-1">
      <Stat
        label={`${label} ${side.first} ~ ${side.last} (${side.subgroups}개)`}
        value={`${shownNumber(side.rate, 2)} ${unit}`}
      />
      {side.drill && (
        <div className="text-xs">
          <DrillLink drill={side.drill} count={side.count} />
        </div>
      )}
    </div>
  )
}

/** 한쪽의 평균 — 그쪽 첫 점부터 끝 점까지 가로선. */
function meanLine(data: CutinResult, which: 'before' | 'after') {
  const side = data[which]
  const points = data.points.filter((one) => one.side === which && one.closed)
  if (side.rate === null || points.length === 0) return []
  return [
    {
      type: 'line' as const,
      x0: points[0].when,
      x1: points[points.length - 1].when,
      y0: side.rate,
      y1: side.rate,
      line: { color: SIDE_COLORS[which] },
    },
  ]
}
