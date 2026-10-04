/**
 * ④ 순차 검정 — 새 모델이 전작보다 나빠졌나. 매 기간 봐도 되게 왈드의 경계로 묻는다.
 *
 * **「아직」 은 「문제없음」 이 아니고, 「나쁘지 않음」 은 「같다」 가 아니다.** 결론의 말을
 * 그대로 적고, 결론이 선 뒤의 기간은 참고로만 그린다.
 */

import { useMemo, useState } from 'react'

import type { Metric, ReadOptions } from '@/modules/metrics/api'
import { analysisQuery, metricsApi } from '@/modules/metrics/api'
import { AlertSave } from '@/modules/metrics/analysis/AlertSave'
import {
  AnalysisMeta,
  CaveatList,
  DrillLink,
  Stat,
  interval,
} from '@/modules/metrics/analysis/common'
import { ByPicker, FocusNote, SprtScanTable } from '@/modules/metrics/analysis/Scan'
import type { Focus } from '@/modules/metrics/analysis/Scan'
import type { SprtDecision, SprtResult, SprtScanResult } from '@/modules/metrics/analysis/types'
import { shownNumber } from '@/modules/metrics/metricDrill'
import { LazyPlot } from '@/shared/charts/LazyPlot'
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

export function decisionText(decision: SprtDecision, rho: number): string {
  if (decision === 'worse') return `나쁨 — 전작보다 ${rho}배 쪽입니다.`
  if (decision === 'not_worse')
    return `나쁘지 않음 — 전작보다 ${rho}배 나쁘지는 않습니다(같다는 뜻은 아닙니다).`
  return '아직 — 결론이 서지 않았습니다(문제없다는 뜻이 아닙니다).'
}

export interface AnalysisViewProps {
  metric: Metric
  read: ReadOptions
  /** 처음 고를 인자 — 경보 알림의 링크가 연 화면(분석 경로의 쿼리 그대로). */
  initial?: Record<string, string>
}

export function SprtView({ metric, read, initial = {} }: AnalysisViewProps) {
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
  const [rho, setRho] = useState(initial.rho ?? '1.5')
  // 값마다 훑기(증상마다) — 고르면 표로, 표의 「이 값만 보기」 는 그 값으로 거른 단건으로.
  const [by, setBy] = useState(initial.by ?? '')
  const [focus, setFocus] = useState<Focus | null>(null)
  // 모델 기준의 거르기는 target · reference 가 대신한다 — 함께 보내면 서버가 거절한다.
  const base = Object.fromEntries(
    Object.entries(read.filters ?? {}).filter(([name]) => name !== dim),
  )
  const filters = focus ? { ...base, [focus.name]: focus.value } : base
  const scanning = Boolean(by) && !focus
  const ready = Boolean(target && (reference || via))
  const asked = {
    target,
    reference: reference || undefined,
    reference_via: reference ? undefined : via || undefined,
    dim,
    rho,
  }
  const key = JSON.stringify([target, reference, via, rho, filters, scanning])
  const result = useResource<SprtResult | null>(
    () =>
      ready && !scanning
        ? metricsApi.analysis<SprtResult>(metric.slug, 'sprt', asked, { filters })
        : Promise.resolve(null),
    [metric.slug, key],
  )
  const scanKey = JSON.stringify([target, reference, via, rho, base, by, scanning])
  const scan = useResource<SprtScanResult | null>(
    () =>
      ready && scanning
        ? metricsApi.analysis<SprtScanResult>(
            metric.slug,
            'sprt/scan',
            { ...asked, by },
            { filters: base },
          )
        : Promise.resolve(null),
    [metric.slug, scanKey],
  )
  const data = scanning ? null : result.data
  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-4">
        <div className="space-y-1">
          <Label htmlFor="sprt-target">새 모델({dimInfo?.label ?? dim})</Label>
          <SearchablePicker
            id="sprt-target"
            options={options}
            value={target}
            loading={values.loading}
            onChange={setTarget}
            placeholder="고르기"
            searchPlaceholder="이름으로 찾기"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="sprt-reference">전작</Label>
          <SearchablePicker
            id="sprt-reference"
            options={options}
            value={reference}
            loading={values.loading}
            onChange={setReference}
            placeholder="고르기"
            searchPlaceholder="이름으로 찾기"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="sprt-via">또는 전작을 가리키는 칸</Label>
          <Input
            id="sprt-via"
            placeholder="예: predecessor"
            value={via}
            disabled={Boolean(reference)}
            onChange={(event) => setVia(event.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="sprt-rho">「나쁨」 의 비</Label>
          <Input
            id="sprt-rho"
            type="number"
            min={1.05}
            step={0.05}
            value={rho}
            onChange={(event) => setRho(event.target.value)}
          />
        </div>
      </div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <ByPicker
          id="sprt-by"
          metric={metric}
          value={by}
          exclude={[dim, ...Object.keys(base)]}
          onChange={(next) => {
            setBy(next)
            setFocus(null)
          }}
        />
        <AlertSave
          metric={metric}
          recipe="sprt"
          params={analysisQuery(scanning ? { ...asked, by } : asked, { filters })}
          disabled={!ready}
        />
      </div>
      {focus && <FocusNote focus={focus} onBack={() => setFocus(null)} />}
      {!ready && (
        <p className="text-muted-foreground text-sm">새 모델과 전작(또는 그 칸)을 고릅니다.</p>
      )}
      {!scanning && result.error && <ErrorNotice error={result.error} />}
      {scanning && scan.error && <ErrorNotice error={scan.error} />}
      {scanning && scan.data && (
        <>
          <CaveatList caveats={scan.data.caveats} />
          <AnalysisMeta result={scan.data} />
          <SprtScanTable data={scan.data} onFocus={setFocus} />
        </>
      )}
      {data && (
        <>
          <p
            className={
              data.decision === 'worse'
                ? 'text-destructive font-semibold'
                : data.decision === 'not_worse'
                  ? 'font-semibold text-emerald-700 dark:text-emerald-400'
                  : 'font-semibold'
            }
          >
            {data.target_label} vs {data.reference_label}: {decisionText(data.decision, data.rho)}
            {data.decided_at && ` (${data.decided_at} 에 결론)`}
          </p>
          <CaveatList caveats={data.caveats} />
          <AnalysisMeta result={data} />
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Stat label="관측 건수" value={shownNumber(data.observed)} />
            <Stat
              label="기대 건수(전작 비율로)"
              value={shownNumber(data.expected, 1)}
              hint="새 모델의 코호트 x 경과마다 대수 x 전작의 그 경과 비율"
            />
            <Stat
              label="표준화 비(관측 / 기대)"
              value={shownNumber(data.smr, 2)}
              hint={`95% 구간 ${interval([data.smr_low ?? 0, data.smr_high ?? 0])}`}
            />
            <Stat
              label="결론까지"
              value={
                data.decision !== 'continue'
                  ? '결론 남'
                  : `같다면 약 ${shownNumber(data.periods_to_not_worse, 0)}기간`
              }
              hint={
                data.decision === 'continue'
                  ? `기대 건수 ${shownNumber(data.to_not_worse, 1)} 더(같다면) · ${shownNumber(data.to_worse, 1)} 더(${data.rho}배라면)`
                  : undefined
              }
            />
          </div>
          {data.looks.length > 0 && (
            <LazyPlot
              height={300}
              title="로그 우도비 — 위 경계를 넘으면 「나쁨」, 아래를 넘으면 「나쁘지 않음」"
              data={[
                {
                  type: 'scatter',
                  mode: 'lines+markers',
                  name: '로그 우도비',
                  x: data.looks.map((one) => one.label),
                  y: data.looks.map((one) => one.llr),
                  marker: {
                    color: data.looks.map((one) => (one.after_decision ? '#9ca3af' : '#2563eb')),
                  },
                },
                {
                  type: 'scatter',
                  mode: 'lines',
                  name: '「나쁨」 경계',
                  x: data.looks.map((one) => one.label),
                  y: data.looks.map(() => data.upper),
                  line: { dash: 'dot', color: '#dc2626' },
                },
                {
                  type: 'scatter',
                  mode: 'lines',
                  name: '「나쁘지 않음」 경계',
                  x: data.looks.map((one) => one.label),
                  y: data.looks.map(() => data.lower),
                  line: { dash: 'dot', color: '#059669' },
                },
              ]}
            />
          )}
          {data.cohort_rows.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>새 모델 코호트</TableHead>
                  <TableHead className="text-right">대수</TableHead>
                  <TableHead className="text-right">기대</TableHead>
                  <TableHead className="text-right">관측</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.cohort_rows.map((one) => (
                  <TableRow key={one.cohort}>
                    <TableCell>{one.label}</TableCell>
                    <TableCell className="text-right">{shownNumber(one.units)}</TableCell>
                    <TableCell className="text-right">{shownNumber(one.expected, 2)}</TableCell>
                    <TableCell className="text-right">
                      <DrillLink drill={one.drill} count={one.observed} />
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
