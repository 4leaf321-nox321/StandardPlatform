/**
 * ② 수명 · B수명 — 판매월 코호트의 경과별 인입으로 맞춘 와이블과 B1 · B5 · B10.
 *
 * **이르지 않는 B수명은 값이 없다.** 판매의 몇 %만 결국 고장 나는 제품이면 B10 은 오지 않는다 —
 * 외삽한 수를 지어 그리지 않고, 「결국 고장 나는 것들 중」 의 수명만 그 이름으로 보인다.
 */

import { useMemo, useState } from 'react'

import type { Metric, ReadOptions } from '@/modules/metrics/api'
import { metricsApi } from '@/modules/metrics/api'
import {
  AnalysisMeta,
  CaveatList,
  DrillLink,
  Stat,
  interval,
  percent,
} from '@/modules/metrics/analysis/common'
import type { BLife, LifeFit, LifeResult, LifeStatus } from '@/modules/metrics/analysis/types'
import { GRAIN_LABELS, shownNumber } from '@/modules/metrics/metricDrill'
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

export const STATUS_LABELS: Record<LifeStatus, string> = {
  observed: '관측 안',
  extrapolated: '외삽 — 관측 밖으로 늘려 읽은 값',
  unreachable: '이르지 않음',
  uncertain: '불확실 — 결국 고장 나는 비율의 구간이 걸친다',
  none: '맞추지 않음',
}

const MODEL_LABELS: Record<string, string> = {
  weibull: '표준 와이블',
  defective: '결함 와이블(결국 고장 나는 비율 p)',
}

function shape(beta: number): string {
  if (beta < 0.95) return '초기 고장 쪽'
  if (beta <= 1.05) return '우발 고장 쪽'
  return '마모 쪽'
}

/** B수명 한 줄의 말 — 이르지 않으면 왜 없는지를 말한다. */
export function lifeText(life: BLife, fit: LifeFit | undefined, unit: string): string {
  const name = `B${Math.round(life.q * 100)}`
  if (life.status === 'unreachable') {
    const share = fit?.p !== null && fit?.p !== undefined ? percent(fit.p) : '일부'
    return `${name}: 판매의 ${share} 만 결국 고장 나 ${percent(life.q, 0)} 에 이르지 않습니다.`
  }
  if (life.age === null) return `${name}: 값이 없습니다.`
  return `${name}: ${shownNumber(life.age, 1)}${unit} (${STATUS_LABELS[life.status]})`
}

export function LifeView({ metric, read }: { metric: Metric; read: ReadOptions }) {
  const [model, setModel] = useState('auto')
  const [maxAge, setMaxAge] = useState('')
  // 방문 차례 기준이 있으면 시리얼마다 첫 방문만 셀 수 있다 — 기록 수가 곧 고장 난 대수.
  const visitNumber = metric.dims.find((one) => one.address === 'visit.number')
  const [basis, setBasis] = useState(visitNumber ? 'first_visits' : 'records')
  const filters = Object.fromEntries(
    Object.entries(read.filters ?? {}).filter(
      ([name]) => !(basis === 'first_visits' && name === visitNumber?.name),
    ),
  )
  const key = JSON.stringify([model, maxAge, basis, filters])
  const result = useResource<LifeResult>(
    () =>
      metricsApi.analysis<LifeResult>(
        metric.slug,
        'life',
        { model, max_age: maxAge || undefined, basis },
        { filters },
      ),
    [metric.slug, key],
  )
  const data = result.data
  const unit = !data ? '' : data.time_unit === 'month' ? '개월' : (GRAIN_LABELS[data.time_unit] ?? '')
  const chosen = data?.fits.find((one) => one.model === data.chosen)
  const traces = useMemo(() => (data ? lifeTraces(data) : []), [data])
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="life-model">모형</Label>
          <Select value={model} onValueChange={setModel}>
            <SelectTrigger id="life-model" className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">둘을 맞춰 고르기</SelectItem>
              <SelectItem value="weibull">표준 와이블만</SelectItem>
              <SelectItem value="defective">결함 와이블만</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {visitNumber && (
          <div className="space-y-1">
            <Label htmlFor="life-basis">무엇을 고장으로 세나</Label>
            <Select value={basis} onValueChange={setBasis}>
              <SelectTrigger id="life-basis" className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="first_visits">시리얼마다 첫 방문</SelectItem>
                <SelectItem value="records">기록 전부</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
        <div className="space-y-1">
          <Label htmlFor="life-max-age">경과 몇 개까지</Label>
          <Input
            id="life-max-age"
            type="number"
            min={1}
            className="w-32"
            placeholder="전부"
            value={maxAge}
            onChange={(event) => setMaxAge(event.target.value)}
          />
        </div>
      </div>
      {result.error && <ErrorNotice error={result.error} />}
      {data && (
        <>
          <CaveatList caveats={data.caveats} />
          <AnalysisMeta result={data} />
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Stat label="쓴 코호트" value={`${shownNumber(data.cohorts_used)}개`} />
            <Stat label="판매 대수" value={shownNumber(data.units)} />
            <Stat label="관측 안의 건수" value={shownNumber(data.failures)} />
            <Stat
              label="관측한 누적 고장률"
              value={percent(data.reached, 2)}
              hint={`경과 ${data.max_age}${unit}까지`}
            />
          </div>
          <ul className="space-y-1 text-sm" aria-label="B수명">
            {data.b_lives.map((one) => (
              <li key={one.q}>
                <strong>{lifeText(one, chosen, unit)}</strong>
                {one.ci && one.status !== 'unreachable' && (
                  <span className="text-muted-foreground"> 구간 {interval(one.ci, 1)}</span>
                )}
                {one.status === 'unreachable' && one.conditional_age !== null && (
                  <span className="text-muted-foreground">
                    {' '}
                    — 결국 고장 나는 것들 중 {percent(one.q, 0)} 는{' '}
                    {shownNumber(one.conditional_age, 1)}
                    {unit}
                  </span>
                )}
                {one.observed_age !== null && (
                  <span className="text-muted-foreground">
                    {' '}
                    · 관측 곡선은 {shownNumber(one.observed_age, 1)}
                    {unit}에 닿음
                  </span>
                )}
              </li>
            ))}
          </ul>
          {traces.length > 0 && (
            <LazyPlot
              height={340}
              title="누적 고장률 — 관측(구간)과 모형"
              data={traces}
              layout={{
                xaxis: { title: { text: `경과(${unit})` } },
                yaxis: { title: { text: '누적 고장률(%)' }, rangemode: 'tozero' },
              }}
            />
          )}
          {data.fits.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>모형</TableHead>
                  <TableHead className="text-right">형상 β</TableHead>
                  <TableHead className="text-right">척도 η({unit})</TableHead>
                  <TableHead className="text-right">결국 고장 나는 비율</TableHead>
                  <TableHead className="text-right">AIC</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.fits.map((one) => (
                  <TableRow key={one.model}>
                    <TableCell>
                      {MODEL_LABELS[one.model]}
                      {one.model === data.chosen && <strong> · 고름</strong>}
                    </TableCell>
                    <TableCell className="text-right" title={interval(one.beta_ci)}>
                      {shownNumber(one.beta)} ({shape(one.beta)})
                    </TableCell>
                    <TableCell className="text-right" title={interval(one.eta_ci, 1)}>
                      {shownNumber(one.eta, 1)} · {shownNumber(one.eta_days, 0)}일
                    </TableCell>
                    <TableCell className="text-right" title={interval(one.p_ci, 4)}>
                      {one.p !== null ? percent(one.p, 2) : '모두(100%)'}
                    </TableCell>
                    <TableCell className="text-right">{shownNumber(one.aic, 1)}</TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {one.identifiable ? '' : '구간 없음'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {data.lrt_p_value !== null && (
            <p className="text-muted-foreground text-xs">
              결함 모형 대 표준 모형 우도비 검정 p = {shownNumber(data.lrt_p_value, 4)}
              {data.gof_p_value !== null &&
                ` · 적합도 χ² p = ${shownNumber(data.gof_p_value, 3)}, 가장 큰 상대 차 ${percent(data.max_rel_dev)}`}
            </p>
          )}
          {data.cohort_rows.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>코호트</TableHead>
                  <TableHead className="text-right">대수</TableHead>
                  <TableHead className="text-right">관측 끝(경과)</TableHead>
                  <TableHead className="text-right">건수</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.cohort_rows.map((one) => (
                  <TableRow key={one.cohort}>
                    <TableCell>{one.label}</TableCell>
                    <TableCell className="text-right">{shownNumber(one.units)}</TableCell>
                    <TableCell className="text-right">{one.horizon}</TableCell>
                    <TableCell className="text-right">
                      <DrillLink drill={one.drill} count={one.failures} />
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

/** 관측 곡선은 경과 a 를 a + 0.5 에(판매일이 달 안에 고르다고 본 자리), 모형은 연속 곡선. */
function lifeTraces(data: LifeResult): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = []
  if (data.points.length > 0) {
    out.push({
      type: 'scatter',
      mode: 'markers',
      name: '관측(보험계리식)',
      x: data.points.map((one) => one.age + 0.5),
      y: data.points.map((one) => one.observed * 100),
      error_y: {
        type: 'data',
        symmetric: false,
        array: data.points.map((one) =>
          one.observed_high !== null ? (one.observed_high - one.observed) * 100 : 0,
        ),
        arrayminus: data.points.map((one) =>
          one.observed_low !== null ? (one.observed - one.observed_low) * 100 : 0,
        ),
      },
    })
  }
  for (const [key, name] of [
    ['standard', '표준 와이블'],
    ['defective', '결함 와이블'],
  ] as const) {
    const rows = data.curve.filter((one) => one[key] !== null)
    if (rows.length === 0) continue
    out.push({
      type: 'scatter',
      mode: 'lines',
      name,
      x: rows.map((one) => one.age),
      y: rows.map((one) => (one[key] ?? 0) * 100),
      // 고른 모형은 실선, 견준 것은 점선.
      line: { dash: (key === 'standard' ? 'weibull' : key) === data.chosen ? 'solid' : 'dot' },
    })
  }
  return out
}
