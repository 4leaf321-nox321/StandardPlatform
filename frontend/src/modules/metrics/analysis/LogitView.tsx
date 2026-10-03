/**
 * ⑥ 재방문 위험 요인 — 어떤 조건에서 정한 일수 안에 다시 들어오나.
 *
 * **오즈비는 함께 나옴이지 원인이 아니다.** 기준 수준 대비 몇 배인지와 그 구간, 요인 하나를
 * 뺐을 때 설명이 얼마나 줄어드는지(LR 검정)를 함께 보인다. 불안정한 값은 오즈비를 적지 않는다.
 */

import { useState } from 'react'

import type { Metric, ReadOptions } from '@/modules/metrics/api'
import { metricsApi } from '@/modules/metrics/api'
import {
  AnalysisMeta,
  CaveatList,
  Stat,
  interval,
  percent,
} from '@/modules/metrics/analysis/common'
import type { LogitResult } from '@/modules/metrics/analysis/types'
import { shownNumber } from '@/modules/metrics/metricDrill'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'

const MAX_FACTORS = 4

export function LogitView({ metric, read }: { metric: Metric; read: ReadOptions }) {
  // 요인 후보 — 재방문 자신과 여러 값 기준은 안 된다(건수가 부푼다).
  const candidates = metric.dims.filter(
    (one) => one.address !== 'visit.repeat' && !one.multi,
  )
  const [factors, setFactors] = useState<string[]>(() =>
    candidates.slice(0, 1).map((one) => one.name),
  )
  const [minCount, setMinCount] = useState('30')
  // 요인으로 고른 기준의 거르기는 빼고 보낸다 — 거른 기준은 값이 하나라 요인이 못 된다.
  const filters = Object.fromEntries(
    Object.entries(read.filters ?? {}).filter(([name]) => !factors.includes(name)),
  )
  const key = JSON.stringify([factors, minCount, filters, read.period_from, read.period_to])
  const result = useResource<LogitResult | null>(
    () =>
      factors.length > 0
        ? metricsApi.analysis<LogitResult>(
            metric.slug,
            'logit',
            { factors: factors.join(','), min_count: minCount },
            { ...read, filters },
          )
        : Promise.resolve(null),
    [metric.slug, key],
  )
  const data = result.data
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <span className="text-sm">요인 (넷까지 — 값이 적은 기준)</span>
          <div className="flex flex-wrap gap-1" role="group" aria-label="요인 고르기">
            {candidates.map((one) => {
              const on = factors.includes(one.name)
              return (
                <Button
                  key={one.name}
                  size="sm"
                  variant={on ? 'default' : 'outline'}
                  aria-pressed={on}
                  disabled={!on && factors.length >= MAX_FACTORS}
                  onClick={() =>
                    setFactors(
                      on ? factors.filter((name) => name !== one.name) : [...factors, one.name],
                    )
                  }
                >
                  {one.label}
                </Button>
              )
            })}
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="logit-min-count">이보다 적은 값은 「그 밖」</Label>
          <Input
            id="logit-min-count"
            type="number"
            min={1}
            className="w-28"
            value={minCount}
            onChange={(event) => setMinCount(event.target.value)}
          />
        </div>
      </div>
      {factors.length === 0 && (
        <p className="text-muted-foreground text-sm">요인을 하나 이상 고릅니다.</p>
      )}
      {result.error && <ErrorNotice error={result.error} />}
      {data && (
        <>
          <CaveatList caveats={data.caveats} />
          <AnalysisMeta result={data} />
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Stat label="창이 닫힌 기록" value={shownNumber(data.records)} />
            <Stat
              label={`${data.within_days}일 안 재방문`}
              value={`${shownNumber(data.yes)} (${percent(data.rate)})`}
            />
            <Stat
              label="기준 조건의 재방문 확률"
              value={percent(data.baseline_rate)}
              hint="모든 요인이 기준 수준일 때"
            />
            <Stat
              label="AUC"
              value={shownNumber(data.auc, 2)}
              hint="예측이 재방문 기록과 아닌 기록을 가르는 정도 — 0.5 면 못 가름"
            />
          </div>
          {data.factors.map((factor) => (
            <section key={factor.name} className="space-y-1">
              <h3 className="text-sm font-semibold">
                {factor.label} — LR 검정 χ² {shownNumber(factor.chi2, 1)} (자유도 {factor.df}),
                p = {shownNumber(factor.p_value, 4)}
              </h3>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>값</TableHead>
                    <TableHead className="text-right">기록</TableHead>
                    <TableHead className="text-right">재방문율</TableHead>
                    <TableHead className="text-right">오즈비</TableHead>
                    <TableHead className="text-right">95% 구간</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {factor.levels.map((level) => (
                    <TableRow key={level.key ?? '__empty__'}>
                      <TableCell>
                        {level.label}
                        {level.reference && <span className="text-muted-foreground"> (기준)</span>}
                        {level.pooled > 0 && (
                          <span className="text-muted-foreground"> — 값 {level.pooled}개</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right">{shownNumber(level.count)}</TableCell>
                      <TableCell className="text-right">{percent(level.rate)}</TableCell>
                      <TableCell className="text-right">
                        {level.unstable ? '불안정' : shownNumber(level.odds_ratio, 2)}
                      </TableCell>
                      <TableCell className="text-right">
                        {level.reference ? '—' : interval(level.ci)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </section>
          ))}
        </>
      )}
    </div>
  )
}
