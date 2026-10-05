/**
 * 클레임 예측 — **이미 판 물량에서 앞으로 몇 건(얼마)이 더 들어오나.**
 *
 * 수명 분석과 같은 맞춤으로 판매월마다 아직 안 본 경과를 더한다. 평균만 보이지 않는다 — 구간을
 * 띠로 그리고, 「6개월 전에 예측했다면 맞혔나」(되짚어 보기)를 앞에 둔다. 앞으로 팔 것 · 리콜은
 * 들어 있지 않다(주의).
 */

import { useState } from 'react'

import { metricsApi } from '@/modules/metrics/api'
import type { AnalysisViewProps } from '@/modules/metrics/analysis/SprtView'
import { AnalysisMeta, CaveatList, Stat } from '@/modules/metrics/analysis/common'
import type { ForecastResult, ForecastTotal } from '@/modules/metrics/analysis/types'
import { shownNumber } from '@/modules/metrics/metricDrill'
import { LazyPlot } from '@/shared/charts/LazyPlot'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import { useResource } from '@/shared/hooks/useResource'

export function ForecastView({ metric, read, initial = {} }: AnalysisViewProps) {
  const [horizon, setHorizon] = useState(initial.horizon ?? '12')
  const [warranty, setWarranty] = useState(initial.warranty ?? '')
  const [cost, setCost] = useState(initial.cost ?? '')
  const asked = {
    horizon,
    warranty: warranty || undefined,
    cost: cost || undefined,
  }
  const key = JSON.stringify([asked, read])
  const result = useResource<ForecastResult>(
    () => metricsApi.analysis<ForecastResult>(metric.slug, 'forecast', asked, read),
    [metric.slug, key],
  )
  const data = result.data
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="forecast-horizon">앞으로(기간 수)</Label>
          <Input
            id="forecast-horizon"
            type="number"
            min={1}
            className="w-24"
            value={horizon}
            onChange={(event) => setHorizon(event.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="forecast-warranty">보증 기간(선택)</Label>
          <Input
            id="forecast-warranty"
            type="number"
            min={1}
            className="w-24"
            placeholder="없음"
            value={warranty}
            onChange={(event) => setWarranty(event.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="forecast-cost">건당 비용(선택)</Label>
          <Input
            id="forecast-cost"
            type="number"
            min={0}
            className="w-32"
            placeholder="없음"
            value={cost}
            onChange={(event) => setCost(event.target.value)}
          />
        </div>
      </div>
      {result.error && <ErrorNotice error={result.error} />}
      {data && (
        <>
          <CaveatList caveats={data.caveats} />
          <AnalysisMeta result={data} />
          {data.backtest && (
            <p className="text-sm">
              되짚어 보기 — {data.backtest.start} 앞까지만 보고 맞췄다면 그 뒤{' '}
              {data.backtest.periods}
              기간은 {shownNumber(data.backtest.predicted, 0)}건(
              {shownNumber(data.backtest.low, 0)} ~ {shownNumber(data.backtest.high, 0)})이라고 했을
              것이고, 실제는 {shownNumber(data.backtest.actual, 0)}건 —{' '}
              <b>{data.backtest.within ? '구간 안' : '구간 밖'}</b>.
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <TotalStat label={`앞으로 ${data.horizon}기간(${data.start}부터)`} total={data.total} />
            {data.remaining && (
              <TotalStat label={`보증(${data.warranty}기간) 끝까지`} total={data.remaining} />
            )}
            <Stat
              label="맞춘 모형"
              value={data.model === 'defective' ? '결함 와이블' : '와이블'}
              hint={`형상 ${shownNumber(data.beta, 2)} · 척도 ${shownNumber(data.eta, 1)}기간${
                data.model === 'defective' ? ` · 결국 고장 나는 비율 ${shownNumber(data.p, 3)}` : ''
              }`}
            />
            <Stat
              label="판매 대수"
              value={shownNumber(data.units)}
              hint={`코호트 ${data.cohorts}개`}
            />
          </div>
          <LazyPlot
            height={320}
            title="기간마다 건수 — 실제(앞), 예측과 95% 구간(뒤)"
            data={[
              {
                type: 'scatter',
                mode: 'lines+markers',
                name: '실제',
                x: data.history.map((one) => one.period),
                y: data.history.map((one) => one.actual),
                line: { color: '#6b7280' },
              },
              {
                type: 'scatter',
                mode: 'lines',
                name: '95% 아래',
                x: data.points.map((one) => one.period),
                y: data.points.map((one) => one.low),
                line: { width: 0 },
                showlegend: false,
              },
              {
                type: 'scatter',
                mode: 'lines',
                name: '95% 구간',
                x: data.points.map((one) => one.period),
                y: data.points.map((one) => one.high),
                fill: 'tonexty',
                fillcolor: 'rgba(37, 99, 235, 0.15)',
                line: { width: 0 },
              },
              {
                type: 'scatter',
                mode: 'lines+markers',
                name: '예측',
                x: data.points.map((one) => one.period),
                y: data.points.map((one) => one.expected),
                line: { color: '#2563eb' },
              },
            ]}
          />
        </>
      )}
    </div>
  )
}

function TotalStat({ label, total }: { label: string; total: ForecastTotal }) {
  const money =
    total.cost !== null
      ? ` · 비용 ${shownNumber(total.cost, 0)}(${shownNumber(total.cost_low, 0)} ~ ${shownNumber(total.cost_high, 0)})`
      : ''
  return (
    <Stat
      label={label}
      value={`${shownNumber(total.expected, 0)}건`}
      hint={`95% 구간 ${shownNumber(total.low, 0)} ~ ${shownNumber(total.high, 0)}${money}`}
    />
  )
}
