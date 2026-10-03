/**
 * ⑦ 파레토 · 집중도 — 기준 하나의 값별 몫, 누적, ABC, 그리고 몰린 정도를 수 몇 개로.
 */

import { useState } from 'react'

import type { Metric, ReadOptions } from '@/modules/metrics/api'
import { metricsApi } from '@/modules/metrics/api'
import {
  AnalysisMeta,
  CaveatList,
  DrillLink,
  Stat,
  percent,
} from '@/modules/metrics/analysis/common'
import type { ParetoResult } from '@/modules/metrics/analysis/types'
import { shownNumber } from '@/modules/metrics/metricDrill'
import { Chart } from '@/shared/charts'
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

export function ParetoView({ metric, read }: { metric: Metric; read: ReadOptions }) {
  const [dim, setDim] = useState(metric.dims[0]?.name ?? '')
  const [byPeriod, setByPeriod] = useState(false)
  // 두 기간 비교 — 지금 범위(상세 위의 기간)가 앞, 여기 범위가 뒤.
  const [compareFrom, setCompareFrom] = useState('')
  const [compareTo, setCompareTo] = useState('')
  const key = JSON.stringify([dim, byPeriod, compareFrom, compareTo, read])
  const result = useResource<ParetoResult | null>(
    () =>
      dim
        ? metricsApi.analysis<ParetoResult>(
            metric.slug,
            'pareto',
            {
              dim,
              by_period: byPeriod || undefined,
              compare_from: compareFrom || undefined,
              compare_to: compareTo || undefined,
            },
            read,
          )
        : Promise.resolve(null),
    [metric.slug, key],
  )
  const data = result.data
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="pareto-dim">몫을 볼 기준</Label>
          <Select value={dim} onValueChange={setDim}>
            <SelectTrigger id="pareto-dim" className="w-48">
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
        {metric.grain && (
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={byPeriod}
              onChange={(event) => setByPeriod(event.target.checked)}
            />
            기간별 집중도 추이
          </label>
        )}
        {metric.grain && (
          <div className="flex gap-2">
            <div className="space-y-1">
              <Label htmlFor="pareto-compare-from">견줄 기간 시작</Label>
              <Input
                id="pareto-compare-from"
                type="date"
                value={compareFrom}
                onChange={(event) => setCompareFrom(event.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="pareto-compare-to">끝(앞까지)</Label>
              <Input
                id="pareto-compare-to"
                type="date"
                value={compareTo}
                onChange={(event) => setCompareTo(event.target.value)}
              />
            </div>
          </div>
        )}
      </div>
      {result.error && <ErrorNotice error={result.error} />}
      {data && (
        <>
          <CaveatList caveats={data.caveats} />
          <AnalysisMeta result={data} />
          {data.concentration && (
            <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
              <Stat
                label="핵심 소수(A)"
                value={`${data.concentration.vital_few}개 / ${data.concentration.categories}개`}
                hint="누적 80% 에 처음 닿는 값까지"
              />
              <Stat
                label="유효 개수"
                value={shownNumber(data.concentration.effective, 1)}
                hint="1 / HHI — 몇 개가 고르게 나눠 가진 셈인가"
              />
              <Stat label="HHI" value={shownNumber(data.concentration.hhi, 3)} />
              <Stat label="지니" value={shownNumber(data.concentration.gini, 2)} />
              <Stat
                label="상위 1 · 3 · 5"
                value={`${percent(data.concentration.cr1, 0)} · ${percent(data.concentration.cr3, 0)} · ${percent(data.concentration.cr5, 0)}`}
              />
            </div>
          )}
          <Chart
            kind="bar"
            data={data.items.map((one) => ({ name: one.label, 몫: one.share * 100 }))}
            x="name"
            series={[{ key: '몫', label: '몫(%)' }]}
            height={280}
            title={`${data.dim_label}별 몫`}
            emptyText="이 범위에는 셀이 없습니다."
          />
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{data.dim_label}</TableHead>
                <TableHead className="text-right">
                  {data.basis === 'occurrences' ? '나온 횟수' : '건수'}
                </TableHead>
                <TableHead className="text-right">몫</TableHead>
                <TableHead className="text-right">누적</TableHead>
                <TableHead>등급</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((one) => (
                <TableRow key={one.key ?? '__empty__'}>
                  <TableCell>{one.label}</TableCell>
                  <TableCell className="text-right">{shownNumber(one.value)}</TableCell>
                  <TableCell className="text-right">{percent(one.share)}</TableCell>
                  <TableCell className="text-right">{percent(one.cumulative)}</TableCell>
                  <TableCell>{one.cls}</TableCell>
                  <TableCell className="text-right">
                    <DrillLink drill={one.drill} count={one.count} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {data.other_categories > 0 && (
            <p className="text-muted-foreground text-xs">
              그 밖 {shownNumber(data.other_categories)}개 값 — 합 {shownNumber(data.other_value)}
            </p>
          )}
          {data.comparison && (
            <section className="space-y-1" aria-label="두 기간 비교">
              <h3 className="text-sm font-semibold">
                {data.comparison.label_a} vs {data.comparison.label_b} — χ²{' '}
                {shownNumber(data.comparison.chi2, 1)} (자유도 {data.comparison.df}), p ={' '}
                {shownNumber(data.comparison.p_value, 4)}
              </h3>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{data.dim_label}</TableHead>
                    <TableHead className="text-right">앞 몫</TableHead>
                    <TableHead className="text-right">뒤 몫</TableHead>
                    <TableHead className="text-right">수정 잔차</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.comparison.items.map((one) => (
                    <TableRow key={one.key ?? '__empty__'}>
                      <TableCell>{one.label}</TableCell>
                      <TableCell className="text-right">{percent(one.share_a)}</TableCell>
                      <TableCell className="text-right">{percent(one.share_b)}</TableCell>
                      <TableCell className="text-right">{shownNumber(one.residual, 1)}</TableCell>
                      <TableCell className="text-xs">
                        {one.notable ? (one.residual > 0 ? '늘었다' : '줄었다') : ''}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </section>
          )}
          {data.trend.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>기간</TableHead>
                  <TableHead className="text-right">합</TableHead>
                  <TableHead className="text-right">유효 개수</TableHead>
                  <TableHead className="text-right">지니</TableHead>
                  <TableHead className="text-right">1위 몫</TableHead>
                  <TableHead>닫힘</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.trend.map((one) => (
                  <TableRow key={one.period}>
                    <TableCell>{one.label}</TableCell>
                    <TableCell className="text-right">{shownNumber(one.total)}</TableCell>
                    <TableCell className="text-right">{shownNumber(one.effective, 1)}</TableCell>
                    <TableCell className="text-right">{shownNumber(one.gini)}</TableCell>
                    <TableCell className="text-right">{percent(one.top_share)}</TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {one.closed ? '닫힘' : '열림'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </>
      )}
    </div>
  )
}
