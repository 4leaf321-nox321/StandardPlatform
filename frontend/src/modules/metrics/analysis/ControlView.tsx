/**
 * ③ 관리도 — 부분군의 비율이 평소의 흔들림 안에 있나. 신호는 「조사할 곳」 이지 원인이 아니다.
 */

import { useState } from 'react'

import { analysisQuery, metricsApi } from '@/modules/metrics/api'
import { AlertSave } from '@/modules/metrics/analysis/AlertSave'
import type { AnalysisViewProps } from '@/modules/metrics/analysis/SprtView'
import { AnalysisMeta, CaveatList, DrillLink } from '@/modules/metrics/analysis/common'
import type { ControlChart, ControlResult } from '@/modules/metrics/analysis/types'
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

const NONE = '__none__'
const AUTO = '__auto__'

export function ControlView({ metric, read, initial = {} }: AnalysisViewProps) {
  const [axis, setAxis] = useState(initial.axis ?? AUTO)
  const [span, setSpan] = useState(initial.window ?? '3')
  const [split, setSplit] = useState(initial.split ?? NONE)
  const [baselineTo, setBaselineTo] = useState(initial.baseline_to ?? '')
  const paired = metric.spec.denominator?.on ?? []
  const asked = {
    axis: axis === AUTO ? undefined : axis,
    window: span,
    split: split === NONE ? undefined : split,
    baseline_to: baselineTo || undefined,
  }
  const key = JSON.stringify([axis, span, split, baselineTo, read])
  const result = useResource<ControlResult>(
    () => metricsApi.analysis<ControlResult>(metric.slug, 'control', asked, read),
    [metric.slug, key],
  )
  const data = result.data
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="control-axis">부분군</Label>
          <Select value={axis} onValueChange={setAxis}>
            <SelectTrigger id="control-axis" className="w-48">
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
            <Label htmlFor="control-window">창(기간 수)</Label>
            <Input
              id="control-window"
              type="number"
              min={1}
              className="w-24"
              value={span}
              onChange={(event) => setSpan(event.target.value)}
            />
          </div>
        )}
        <div className="space-y-1">
          <Label htmlFor="control-split">나누기</Label>
          <Select value={split} onValueChange={setSplit}>
            <SelectTrigger id="control-split" className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>나누지 않음</SelectItem>
              {metric.dims.map((one) => (
                <SelectItem key={one.name} value={one.name}>
                  {one.label}
                  {paired.length > 0 && !paired.includes(one.name) && ' (분모 짝 없음)'}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="control-baseline">한계를 이 날 앞으로만</Label>
          <Input
            id="control-baseline"
            type="date"
            value={baselineTo}
            onChange={(event) => setBaselineTo(event.target.value)}
          />
        </div>
        <div className="ml-auto">
          <AlertSave metric={metric} recipe="control" params={analysisQuery(asked, read)} />
        </div>
      </div>
      {result.error && <ErrorNotice error={result.error} />}
      {data && (
        <>
          <CaveatList caveats={data.caveats} />
          <AnalysisMeta result={data} />
          <p className="text-muted-foreground text-xs">
            {data.kind === 'u' ? `대수 ${shownNumber(data.per)}대당 비율` : '건수'} · 규칙:{' '}
            {data.rules.map((one) => `${one.number}) ${one.label}`).join(' · ')}
          </p>
          {data.charts.map((chart) => (
            <ChartBlock key={chart.key ?? '__all__'} chart={chart} result={data} />
          ))}
        </>
      )}
    </div>
  )
}

function ChartBlock({ chart, result }: { chart: ControlChart; result: ControlResult }) {
  const labels = chart.points.map((one) => one.label)
  const flagged = chart.points.filter((one) => one.signals.length > 0)
  const rules = new Map(result.rules.map((one) => [one.number, one.label]))
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">
        {chart.label} — 중심 {shownNumber(chart.center, 3)}
        {chart.sigma_z !== null && chart.sigma_z > 1 && ` · 한계 ${shownNumber(chart.sigma_z, 1)}배(라니)`}
        {` · 신호 ${chart.signals}점`}
      </h3>
      <LazyPlot
        height={280}
        data={[
          {
            type: 'scatter',
            mode: 'lines',
            name: '위 한계',
            x: labels,
            y: chart.points.map((one) => one.ucl),
            line: { shape: 'hv', dash: 'dot' },
          },
          {
            type: 'scatter',
            mode: 'lines',
            name: '아래 한계',
            x: labels,
            y: chart.points.map((one) => one.lcl),
            line: { shape: 'hv', dash: 'dot' },
          },
          {
            type: 'scatter',
            mode: 'lines+markers',
            name: result.kind === 'u' ? '비율' : '건수',
            x: labels,
            y: chart.points.map((one) => one.rate),
            marker: {
              color: chart.points.map((one) =>
                !one.closed ? '#9ca3af' : one.signals.length > 0 ? '#dc2626' : '#2563eb',
              ),
            },
          },
        ]}
        layout={{
          shapes:
            chart.center !== null
              ? [
                  {
                    type: 'line',
                    xref: 'paper',
                    x0: 0,
                    x1: 1,
                    y0: chart.center,
                    y1: chart.center,
                    line: { color: '#6b7280', width: 1 },
                  },
                ]
              : [],
        }}
      />
      {flagged.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{result.axis === 'cohort' ? '코호트' : '기간'}</TableHead>
              <TableHead className="text-right">{result.kind === 'u' ? '비율' : '건수'}</TableHead>
              <TableHead>걸린 규칙</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {flagged.map((one) => (
              <TableRow key={one.when}>
                <TableCell>{one.label}</TableCell>
                <TableCell className="text-right">{shownNumber(one.rate, 3)}</TableCell>
                <TableCell className="text-xs">
                  {one.signals.map((number) => rules.get(number) ?? number).join(' · ')}
                </TableCell>
                <TableCell className="text-right">
                  <DrillLink drill={one.drill} count={one.count} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  )
}
