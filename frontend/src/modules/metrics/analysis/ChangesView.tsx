/**
 * ⑩ 계절 · 변화점 — 계절을 빼고 보면 수준이 언제 바뀌었나. 새 수준이 짧으면 잠정.
 */

import { useState } from 'react'

import { analysisQuery, metricsApi } from '@/modules/metrics/api'
import { AlertSave } from '@/modules/metrics/analysis/AlertSave'
import type { AnalysisViewProps } from '@/modules/metrics/analysis/SprtView'
import { AnalysisMeta, CaveatList, interval } from '@/modules/metrics/analysis/common'
import { ByPicker, ChangesScanTable, FocusNote } from '@/modules/metrics/analysis/Scan'
import type { Focus } from '@/modules/metrics/analysis/Scan'
import type { ChangesResult, ChangesScanResult } from '@/modules/metrics/analysis/types'
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

export function ChangesView({ metric, read, initial = {} }: AnalysisViewProps) {
  const [axis, setAxis] = useState(initial.axis ?? AUTO)
  const [span, setSpan] = useState(initial.window ?? '3')
  // 값마다 훑기(증상마다) — 고르면 표로, 표의 「이 값만 보기」 는 그 값으로 거른 단건으로.
  const [by, setBy] = useState(initial.by ?? '')
  const [focus, setFocus] = useState<Focus | null>(null)
  const scanning = Boolean(by) && !focus
  const asked = { axis: axis === AUTO ? undefined : axis, window: span }
  const single = focus
    ? { ...read, filters: { ...read.filters, [focus.name]: focus.value } }
    : read
  const key = JSON.stringify([axis, span, single, scanning])
  const result = useResource<ChangesResult | null>(
    () =>
      scanning
        ? Promise.resolve(null)
        : metricsApi.analysis<ChangesResult>(metric.slug, 'changes', asked, single),
    [metric.slug, key],
  )
  const scanKey = JSON.stringify([axis, span, read, by, scanning])
  const scan = useResource<ChangesScanResult | null>(
    () =>
      scanning
        ? metricsApi.analysis<ChangesScanResult>(
            metric.slug,
            'changes/scan',
            { ...asked, by },
            read,
          )
        : Promise.resolve(null),
    [metric.slug, scanKey],
  )
  const data = scanning ? null : result.data
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
        <ByPicker
          id="changes-by"
          metric={metric}
          value={by}
          exclude={Object.keys(read.filters ?? {})}
          onChange={(next) => {
            setBy(next)
            setFocus(null)
          }}
        />
        <div className="ml-auto">
          <AlertSave
            metric={metric}
            recipe="changes"
            params={analysisQuery(scanning ? { ...asked, by } : asked, single)}
          />
        </div>
      </div>
      {focus && <FocusNote focus={focus} onBack={() => setFocus(null)} />}
      {!scanning && result.error && <ErrorNotice error={result.error} />}
      {scanning && scan.error && <ErrorNotice error={scan.error} />}
      {scanning && scan.data && (
        <>
          <CaveatList caveats={scan.data.caveats} />
          <AnalysisMeta result={scan.data} />
          <ChangesScanTable
            data={scan.data}
            onFocus={setFocus}
            share={metric.spec.measure === 'share'}
          />
        </>
      )}
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
