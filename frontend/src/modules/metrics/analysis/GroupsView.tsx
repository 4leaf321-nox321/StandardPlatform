/**
 * ⑤ 집단 비교 — SKU(색상 · 용량 · 통신사) · 공장 · 기본 모델마다 비율이 다른가.
 *
 * **순위는 줄인 비율로 세운다.** 대수가 작은 집단의 그대로 비율은 우연으로 크게 흔들린다 — 대수 150
 * 대에 3 건(2%)이 2만 대에 200 건(1%)보다 「나쁘다」 로 읽히지 않게, 전체 쪽으로 줄인 값과 그 구간을
 * 앞에 두고 그대로 비율은 곁에 둔다. 「다르다」 는 여럿을 함께 본 거짓 발견율(q)이 0.05 아래인 것만.
 */

import { useState } from 'react'

import { metricsApi } from '@/modules/metrics/api'
import type { AnalysisViewProps } from '@/modules/metrics/analysis/SprtView'
import {
  AnalysisMeta,
  CaveatList,
  DrillLink,
  interval,
  percent,
} from '@/modules/metrics/analysis/common'
import type { GroupsResult } from '@/modules/metrics/analysis/types'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'

const AUTO = '__auto__'
/** 그림에 싣는 줄 — 집단이 수백이면 점이 겹쳐 못 읽는다(표에는 모두). */
const PLOT_ROWS = 40

export function GroupsView({ metric, read, initial = {} }: AnalysisViewProps) {
  // 집단마다 대수를 알아야 견준다 — 분모 짝(on)의 기준만.
  const paired = metric.spec.denominator?.on ?? []
  // 조건 비율은 집단마다 분모(그 집단의 전체 건수)를 스스로 든다 — 어느 기준으로도 견준다.
  const share = metric.spec.measure === 'share'
  const choices = share ? metric.dims : metric.dims.filter((one) => paired.includes(one.name))
  const [dim, setDim] = useState(initial.dim ?? choices[0]?.name ?? '')
  const [axis, setAxis] = useState(initial.axis ?? AUTO)
  const [span, setSpan] = useState(initial.window ?? '3')
  // 견주는 기준의 거르기는 빼고 보낸다 — 값 하나로 거른 기준은 견줄 집단이 없다.
  const filters = Object.fromEntries(
    Object.entries(read.filters ?? {}).filter(([name]) => name !== dim),
  )
  const asked = { dim, axis: axis === AUTO ? undefined : axis, window: span }
  const key = JSON.stringify([dim, axis, span, filters, read.period_from, read.period_to])
  const result = useResource<GroupsResult | null>(
    () =>
      dim
        ? metricsApi.analysis<GroupsResult>(metric.slug, 'groups', asked, { ...read, filters })
        : Promise.resolve(null),
    [metric.slug, key],
  )
  const data = result.data
  const plotted = data ? data.rows.slice(0, PLOT_ROWS) : []
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="groups-dim">견줄 집단</Label>
          <Select value={dim} onValueChange={setDim}>
            <SelectTrigger id="groups-dim" className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {choices.map((one) => (
                <SelectItem key={one.name} value={one.name}>
                  {one.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="groups-axis">부분군</Label>
          <Select value={axis} onValueChange={setAxis}>
            <SelectTrigger id="groups-axis" className="w-48">
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
            <Label htmlFor="groups-window">창(기간 수)</Label>
            <Input
              id="groups-window"
              type="number"
              min={1}
              className="w-24"
              value={span}
              onChange={(event) => setSpan(event.target.value)}
            />
          </div>
        )}
      </div>
      {!dim && (
        <p className="text-muted-foreground text-sm">
          분모(대수)와 짝지은 기준이 없어 견줄 집단이 없습니다.
        </p>
      )}
      {result.error && <ErrorNotice error={result.error} />}
      {data && (
        <>
          <CaveatList caveats={data.caveats} />
          <AnalysisMeta result={data} />
          <p className="text-sm">
            {data.dim_label} {shownNumber(data.groups)}개 — 전체 {shownNumber(data.pooled, 2)}
            {share ? '%' : `건/${shownNumber(data.per)}대`}.{' '}
            {data.heterogeneity_p !== null && data.heterogeneity_p < 0.05
              ? `집단 사이에 우연보다 큰 차이가 있습니다(p ${shownNumber(data.heterogeneity_p, 4)}, 참 비율이 집단마다 약 ±${percent(data.spread, 0)} 흔들림).`
              : '집단 사이 차이가 우연의 흔들림 안입니다.'}{' '}
            {data.flagged > 0 && `전체와 다른 집단 ${data.flagged}개.`}
          </p>
          {plotted.length > 0 && (
            <LazyPlot
              height={Math.max(220, 28 * plotted.length)}
              title={`줄인 비율과 95% 구간 — 점선이 전체${data.rows.length > PLOT_ROWS ? ` · 위 ${PLOT_ROWS}개` : ''}`}
              data={[
                {
                  type: 'scatter',
                  mode: 'markers',
                  name: '줄인 비율',
                  x: plotted.map((one) => one.shrunk),
                  y: plotted.map((one) => one.label),
                  error_x: {
                    type: 'data',
                    symmetric: false,
                    array: plotted.map((one) =>
                      one.shrunk_high !== null && one.shrunk !== null
                        ? one.shrunk_high - one.shrunk
                        : 0,
                    ),
                    arrayminus: plotted.map((one) =>
                      one.shrunk_low !== null && one.shrunk !== null
                        ? one.shrunk - one.shrunk_low
                        : 0,
                    ),
                  },
                  marker: {
                    color: plotted.map((one) =>
                      one.flag === 'high' ? '#dc2626' : one.flag === 'low' ? '#059669' : '#2563eb',
                    ),
                  },
                },
                {
                  type: 'scatter',
                  mode: 'markers',
                  name: '그대로 비율',
                  x: plotted.map((one) => one.rate),
                  y: plotted.map((one) => one.label),
                  marker: { symbol: 'x', color: '#9ca3af' },
                },
              ]}
              layout={{
                yaxis: { autorange: 'reversed' },
                shapes:
                  data.pooled !== null
                    ? [
                        {
                          type: 'line',
                          x0: data.pooled,
                          x1: data.pooled,
                          yref: 'paper',
                          y0: 0,
                          y1: 1,
                          line: { dash: 'dot', color: '#6b7280' },
                        },
                      ]
                    : [],
              }}
            />
          )}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{data.dim_label}</TableHead>
                <TableHead className="text-right">줄인 비율(95% 구간)</TableHead>
                <TableHead className="text-right">전체 대비</TableHead>
                <TableHead className="text-right">그대로 비율</TableHead>
                <TableHead className="text-right">대수</TableHead>
                <TableHead className="text-right">줄인 정도</TableHead>
                <TableHead className="text-right">q</TableHead>
                <TableHead className="text-right">건수</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.rows.map((one) => (
                <TableRow key={one.key ?? '-'}>
                  <TableCell
                    className={
                      one.flag === 'high'
                        ? 'text-destructive font-semibold'
                        : one.flag === 'low'
                          ? 'font-semibold text-emerald-700 dark:text-emerald-400'
                          : undefined
                    }
                  >
                    {one.label}
                    {one.flag === 'high' && ' — 높음'}
                    {one.flag === 'low' && ' — 낮음'}
                  </TableCell>
                  <TableCell className="text-right">
                    {shownNumber(one.shrunk, 2)}
                    {one.shrunk_low !== null &&
                      ` (${interval([one.shrunk_low, one.shrunk_high ?? 0])})`}
                  </TableCell>
                  <TableCell className="text-right">{shownNumber(one.ratio, 2)}배</TableCell>
                  <TableCell className="text-muted-foreground text-right">
                    {shownNumber(one.rate, 2)}
                  </TableCell>
                  <TableCell className="text-right">{shownNumber(one.exposure)}</TableCell>
                  <TableCell className="text-right">{percent(one.shrinkage, 0)}</TableCell>
                  <TableCell className="text-right">{shownNumber(one.q_value, 4)}</TableCell>
                  <TableCell className="text-right">
                    <DrillLink drill={one.drill} count={one.count} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}
    </div>
  )
}
