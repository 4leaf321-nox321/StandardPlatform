/**
 * ⑨ 연관 · 묶음 — 어떤 증상과 부품(원인)이 우연보다 자주 함께 나오나, 원인 모양이 닮은 증상끼리.
 *
 * **향상도는 함께 나옴이지 원인이 아니다.** 짝은 거짓 발견율을 맞춘(BH) 것만 싣고, 묶음은
 * 실루엣이 작으면 참고로만 읽는다. 짝마다 그 기록 목록으로 간다.
 */

import { useMemo, useState } from 'react'

import type { Metric, ReadOptions } from '@/modules/metrics/api'
import { metricsApi } from '@/modules/metrics/api'
import {
  AnalysisMeta,
  CaveatList,
  DrillLink,
  percent,
} from '@/modules/metrics/analysis/common'
import type { AssocResult } from '@/modules/metrics/analysis/types'
import { shownNumber } from '@/modules/metrics/metricDrill'
import { LazyPlot } from '@/shared/charts/LazyPlot'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
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

export function AssocView({ metric, read }: { metric: Metric; read: ReadOptions }) {
  const [rows, setRows] = useState(metric.dims[0]?.name ?? '')
  const [cols, setCols] = useState(metric.dims[1]?.name ?? '')
  // 행 · 열로 고른 기준의 거르기는 빼고 보낸다 — 값 하나로 거른 기준은 짝을 못 만든다.
  const filters = Object.fromEntries(
    Object.entries(read.filters ?? {}).filter(([name]) => name !== rows && name !== cols),
  )
  const ready = Boolean(rows && cols && rows !== cols)
  const key = JSON.stringify([rows, cols, filters, read.period_from, read.period_to])
  const result = useResource<AssocResult | null>(
    () =>
      ready
        ? metricsApi.analysis<AssocResult>(
            metric.slug,
            'assoc',
            { rows, cols },
            { ...read, filters },
          )
        : Promise.resolve(null),
    [metric.slug, key],
  )
  const data = result.data
  const traces = useMemo(() => (data ? mapTraces(data) : []), [data])
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        {(
          [
            ['assoc-rows', '행(증상 등)', rows, setRows],
            ['assoc-cols', '열(부품 · 원인 등)', cols, setCols],
          ] as const
        ).map(([id, text, value, set]) => (
          <div key={id} className="space-y-1">
            <Label htmlFor={id}>{text}</Label>
            <Select value={value} onValueChange={set}>
              <SelectTrigger id={id} className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {metric.dims.map((one) => (
                  <SelectItem key={one.name} value={one.name}>
                    {one.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ))}
      </div>
      {!ready && <p className="text-muted-foreground text-sm">서로 다른 기준 둘을 고릅니다.</p>}
      {result.error && <ErrorNotice error={result.error} />}
      {data && (
        <>
          <CaveatList caveats={data.caveats} />
          <AnalysisMeta result={data} />
          <p className="text-sm">
            {data.pairs.length === 0
              ? `검정한 짝 ${shownNumber(data.tested)}개 중 우연보다 자주 함께 나오는 짝이 없습니다.`
              : `검정한 짝 ${shownNumber(data.tested)}개 중 ${data.pairs.length}개가 우연보다 자주 함께 나옵니다.`}
          </p>
          {data.pairs.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{data.rows_label}</TableHead>
                  <TableHead>{data.cols_label}</TableHead>
                  <TableHead className="text-right">향상도</TableHead>
                  <TableHead className="text-right">기대</TableHead>
                  <TableHead className="text-right">행 안의 몫</TableHead>
                  <TableHead className="text-right">q</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.pairs.map((one) => (
                  <TableRow key={`${one.row.key}|${one.col.key}`}>
                    <TableCell>{one.row.label}</TableCell>
                    <TableCell>{one.col.label}</TableCell>
                    <TableCell className="text-right">{shownNumber(one.lift, 2)}배</TableCell>
                    <TableCell className="text-right">{shownNumber(one.expected, 1)}</TableCell>
                    <TableCell className="text-right">{percent(one.share)}</TableCell>
                    <TableCell className="text-right">{shownNumber(one.q_value, 4)}</TableCell>
                    <TableCell className="text-right">
                      <DrillLink drill={one.drill} count={one.count} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {(data.dispersion ?? []).length > 0 && (
            <section className="space-y-1" aria-label="원인분산도">
              <h3 className="text-sm font-semibold">
                원인분산도 — {data.rows_label}마다 {data.cols_label}이(가) 얼마나 갈렸나
              </h3>
              <p className="text-muted-foreground text-xs">
                유효 원인 수는 원인이 몇 개에 고르게 퍼진 것과 같은가입니다 — 1 이면 하나에 몰림.
                전체로는 {shownNumber(data.overall_effective, 1)}개. 많이 갈린 것부터.
              </p>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{data.rows_label}</TableHead>
                    <TableHead className="text-right">건수</TableHead>
                    <TableHead className="text-right">원인 값</TableHead>
                    <TableHead className="text-right">유효 원인 수</TableHead>
                    <TableHead>가장 많은 {data.cols_label}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(data.dispersion ?? []).map((one) => (
                    <TableRow key={one.row.key}>
                      <TableCell>{one.row.label}</TableCell>
                      <TableCell className="text-right">{shownNumber(one.count)}</TableCell>
                      <TableCell className="text-right">{shownNumber(one.causes)}</TableCell>
                      <TableCell className="text-right">{shownNumber(one.effective, 1)}</TableCell>
                      <TableCell>
                        {one.top.label}{' '}
                        <span className="text-muted-foreground">({percent(one.top_share)})</span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </section>
          )}
          {data.clusters.length > 0 && (
            <section className="space-y-1" aria-label="묶음">
              <h3 className="text-sm font-semibold">
                {data.rows_label} 묶음 — 실루엣 {shownNumber(data.silhouette, 2)}
              </h3>
              <ul className="space-y-1 text-sm">
                {data.clusters.map((one, index) => (
                  <li key={index}>
                    <strong>{one.members.map((member) => member.label).join(' · ')}</strong>
                    {one.top.length > 0 && (
                      <span className="text-muted-foreground">
                        {' '}
                        — 함께 자주 나오는 {data.cols_label}:{' '}
                        {one.top.map((col) => col.label).join(' · ')}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
          {traces.length > 0 && (
            <LazyPlot
              height={420}
              title={`대응 분석 — 가까운 것끼리 함께 자주 나온다(두 축이 ${percent(data.map_explained, 0)} 설명)`}
              data={traces}
              layout={{ xaxis: { zeroline: true }, yaxis: { zeroline: true } }}
            />
          )}
        </>
      )}
    </div>
  )
}

function mapTraces(data: AssocResult): Record<string, unknown>[] {
  if (data.map_rows.length === 0) return []
  return [
    {
      type: 'scatter',
      mode: 'markers+text',
      name: data.rows_label,
      x: data.map_rows.map((one) => one.x),
      y: data.map_rows.map((one) => one.y),
      text: data.map_rows.map((one) => one.label),
      textposition: 'top center',
    },
    {
      type: 'scatter',
      mode: 'markers+text',
      name: data.cols_label,
      x: data.map_cols.map((one) => one.x),
      y: data.map_cols.map((one) => one.y),
      text: data.map_cols.map((one) => one.label),
      textposition: 'bottom center',
      marker: { symbol: 'diamond' },
    },
  ]
}
