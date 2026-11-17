/**
 * ④-2 전작 대비 배율(ADR 0082) — 새 모델의 판매 대수당 건수가 전작의 몇 배인가, 점추정과 95%
 * 구간. 기준(시장 · 증상)을 고르면 그 값마다 **전부**(자르지 않는다 — 시장 전수표).
 *
 * **「unclear」 는 「같다」 가 아니다.** 구간이 1 을 품는다는 뜻이다. 값마다 따로 낸 구간이라 많이
 * 늘어놓으면 몇 개는 우연히 1 을 벗어난다 — 서버의 주의를 그대로 보인다.
 */

import { useMemo, useState } from 'react'

import { metricsApi } from '@/modules/metrics/api'
import { AnalysisMeta, CaveatList, Stat, interval } from '@/modules/metrics/analysis/common'
import { ByPicker } from '@/modules/metrics/analysis/Scan'
import type { AnalysisViewProps } from '@/modules/metrics/analysis/SprtView'
import type { RatioResult, RatioRow } from '@/modules/metrics/analysis/types'
import { shownNumber } from '@/modules/metrics/metricDrill'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
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

export function verdictText(row: RatioRow): string {
  if (row.new) return '전작에 없던 값'
  if (row.verdict === 'higher') return '전작보다 높음'
  if (row.verdict === 'lower') return '전작보다 낮음'
  return '판단 어려움(1 을 품음)'
}

function verdictClass(row: RatioRow): string {
  if (row.verdict === 'higher' || row.new) return 'text-destructive font-medium'
  if (row.verdict === 'lower') return 'font-medium text-emerald-700 dark:text-emerald-400'
  return 'text-muted-foreground'
}

export function RatioView({ metric, read, initial = {} }: AnalysisViewProps) {
  const dim = metric.spec.denominator?.on[0] ?? ''
  const dimInfo = metric.dims.find((one) => one.name === dim)
  const values = useResource(
    () => (dim ? metricsApi.dims(metric.slug, dim) : Promise.resolve(null)),
    [metric.slug, dim],
  )
  const options = useMemo(
    () =>
      (values.data?.values ?? [])
        .filter((one) => one.value !== null)
        .map((one) => ({
          value: one.value ?? '',
          label: one.label,
          hint: `${shownNumber(one.count)}건`,
        })),
    [values.data],
  )
  const [target, setTarget] = useState(initial.target ?? '')
  const [reference, setReference] = useState(initial.reference ?? '')
  const [via, setVia] = useState(initial.reference_via ?? '')
  const [by, setBy] = useState(initial.by ?? '')
  // 모델 기준의 거르기는 target · reference 가 대신한다 — 함께 보내면 서버가 거절한다.
  const filters = Object.fromEntries(
    Object.entries(read.filters ?? {}).filter(([name]) => name !== dim && name !== by),
  )
  const ready = Boolean(target && (reference || via))
  const asked = {
    target,
    reference: reference || undefined,
    reference_via: reference ? undefined : via || undefined,
    dim,
    by: by || undefined,
  }
  const key = JSON.stringify([target, reference, via, by, filters])
  const result = useResource<RatioResult | null>(
    () =>
      ready
        ? metricsApi.analysis<RatioResult>(metric.slug, 'ratio', asked, { filters })
        : Promise.resolve(null),
    [metric.slug, key],
  )
  const data = result.data
  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-3">
        <div className="space-y-1">
          <Label htmlFor="ratio-target">새 모델({dimInfo?.label ?? dim})</Label>
          <SearchablePicker
            id="ratio-target"
            options={options}
            value={target}
            loading={values.loading}
            onChange={setTarget}
            placeholder="고르기"
            searchPlaceholder="이름으로 찾기"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="ratio-reference">전작</Label>
          <SearchablePicker
            id="ratio-reference"
            options={options}
            value={reference}
            loading={values.loading}
            onChange={setReference}
            placeholder="고르기"
            searchPlaceholder="이름으로 찾기"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="ratio-via">또는 전작을 가리키는 칸</Label>
          <Input
            id="ratio-via"
            placeholder="예: predecessor"
            value={via}
            disabled={Boolean(reference)}
            onChange={(event) => setVia(event.target.value)}
          />
        </div>
      </div>
      <ByPicker
        id="ratio-by"
        metric={metric}
        value={by}
        exclude={[dim, ...Object.keys(filters)]}
        onChange={setBy}
      />
      {!ready && (
        <p className="text-muted-foreground text-sm">새 모델과 전작(또는 그 칸)을 고릅니다.</p>
      )}
      {result.error && <ErrorNotice error={result.error} />}
      {data && (
        <>
          <p className={verdictClass(data.overall)}>
            {data.target_label} vs {data.reference_label}: 배율 {shownNumber(data.overall.ratio, 2)}
            배 (95% 구간 {interval([data.overall.low ?? 0, data.overall.high ?? 0])}) —{' '}
            {verdictText(data.overall)}
          </p>
          <CaveatList caveats={data.caveats} />
          <AnalysisMeta result={data} />
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Stat
              label="배율(경과를 맞춤)"
              value={shownNumber(data.overall.ratio, 2)}
              hint="새 모델 건수 / 전작의 경과별 비율로 낸 기대 건수"
            />
            <Stat
              label="단순 배율"
              value={shownNumber(data.overall.crude, 2)}
              hint="(새 모델 건수 / 대수) ÷ (전작 건수 / 대수) — 경과를 안 맞춤"
            />
            <Stat
              label="관측 · 기대 건수"
              value={`${shownNumber(data.overall.observed)} · ${shownNumber(data.overall.expected, 1)}`}
            />
            <Stat label="과분산 φ" value={shownNumber(data.dispersion, 2)} />
          </div>
          {data.items.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{data.by_label ?? data.by}</TableHead>
                  <TableHead className="text-right">새 모델 건수 / 대수</TableHead>
                  <TableHead className="text-right">전작 건수 / 대수</TableHead>
                  <TableHead className="text-right">배율</TableHead>
                  <TableHead className="text-right">95% 구간</TableHead>
                  <TableHead>판단</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((one) => (
                  <TableRow key={one.key ?? '(empty)'}>
                    <TableCell>{one.label}</TableCell>
                    <TableCell className="text-right">
                      {shownNumber(one.target_records)} / {shownNumber(one.target_units)}
                    </TableCell>
                    <TableCell className="text-right">
                      {shownNumber(one.reference_records)} / {shownNumber(one.reference_units)}
                    </TableCell>
                    <TableCell className="text-right">{shownNumber(one.ratio, 2)}</TableCell>
                    <TableCell className="text-right">
                      {one.low === null || one.high === null ? '—' : interval([one.low, one.high])}
                    </TableCell>
                    <TableCell className={verdictClass(one)}>{verdictText(one)}</TableCell>
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
