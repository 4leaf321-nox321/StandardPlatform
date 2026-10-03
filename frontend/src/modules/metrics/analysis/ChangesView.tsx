/**
 * ⑩ 계절 · 변화점 — 계절을 빼고 보면 수준이 언제 바뀌었나. 새 수준이 짧으면 잠정.
 */

import { useState } from 'react'

import type { Metric, ReadOptions } from '@/modules/metrics/api'
import { metricsApi } from '@/modules/metrics/api'
import { AnalysisMeta, CaveatList, interval } from '@/modules/metrics/analysis/common'
import type { ChangesResult } from '@/modules/metrics/analysis/types'
import { shownNumber } from '@/modules/metrics/metricDrill'
import { Chart } from '@/shared/charts'
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

export function ChangesView({ metric, read }: { metric: Metric; read: ReadOptions }) {
  const [axis, setAxis] = useState(AUTO)
  const [span, setSpan] = useState('3')
  const key = JSON.stringify([axis, span, read])
  const result = useResource<ChangesResult>(
    () =>
      metricsApi.analysis<ChangesResult>(
        metric.slug,
        'changes',
        { axis: axis === AUTO ? undefined : axis, window: span },
        read,
      ),
    [metric.slug, key],
  )
  const data = result.data
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="changes-axis">부분군</Label>
          <Select value={axis} onValueChange={setAxis}>
            <SelectTrigger id="changes-axis" className="w-48">
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
            <Label htmlFor="changes-window">창(기간 수)</Label>
            <Input
              id="changes-window"
              type="number"
              min={1}
              className="w-24"
              value={span}
              onChange={(event) => setSpan(event.target.value)}
            />
          </div>
        )}
      </div>
      {result.error && <ErrorNotice error={result.error} />}
      {data && (
        <>
          <CaveatList caveats={data.caveats} />
          <AnalysisMeta result={data} />
          <p className="text-sm">
            {data.changes.length === 0
              ? '계절을 빼고 보면 수준이 바뀐 곳이 없습니다.'
              : data.changes
                  .map(
                    (one) =>
                      `${one.label} 부터 ${shownNumber(one.ratio, 2)}배${one.provisional ? '(잠정)' : ''}`,
                  )
                  .join(' · ')}
          </p>
          <LazyPlot
            height={300}
            data={[
              {
                type: 'scatter',
                mode: 'markers',
                name: data.kind === 'rate' ? '비율' : '건수',
                x: data.points.map((one) => one.label),
                y: data.points.map((one) => one.rate),
                marker: {
                  color: data.points.map((one) => (one.closed ? '#2563eb' : '#9ca3af')),
                },
              },
              {
                type: 'scatter',
                mode: 'lines',
                name: '계절을 뺀 값',
                x: data.points.map((one) => one.label),
                y: data.points.map((one) => one.adjusted),
                line: { dash: 'dot' },
              },
              {
                type: 'scatter',
                mode: 'lines',
                name: '수준',
                x: data.points.map((one) => one.label),
                y: data.points.map((one) => one.level),
                line: { shape: 'hv', width: 3 },
              },
            ]}
          />
          {data.changes.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>바뀐 때</TableHead>
                  <TableHead className="text-right">앞 수준</TableHead>
                  <TableHead className="text-right">뒤 수준</TableHead>
                  <TableHead className="text-right">비(구간)</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.changes.map((one) => (
                  <TableRow key={one.at}>
                    <TableCell>{one.label}</TableCell>
                    <TableCell className="text-right">{shownNumber(one.before, 3)}</TableCell>
                    <TableCell className="text-right">{shownNumber(one.after, 3)}</TableCell>
                    <TableCell className="text-right">
                      {shownNumber(one.ratio, 2)} ({interval(one.ratio_ci)})
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {one.provisional ? '잠정 — 더 보고 판단' : ''}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {data.seasonal.length > 0 && (
            <Chart
              kind="bar"
              data={data.seasonal.map((one) => ({ name: one.label, 지수: one.index }))}
              x="name"
              series={[{ key: '지수', label: '계절 지수(1 = 평소)' }]}
              height={220}
              title="계절 지수"
            />
          )}
          <p className="text-muted-foreground text-xs">
            과분산 {shownNumber(data.dispersion, 2)} · 벌점 {shownNumber(data.penalty, 1)} · 최소
            구간 {data.min_segment}점
          </p>
        </>
      )}
    </div>
  )
}
