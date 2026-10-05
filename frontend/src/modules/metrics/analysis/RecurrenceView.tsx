/**
 * 재발 — **전작에서 나온 축 조합이 다음 모델에서 다시 나왔나**(ADR 0022).
 *
 * 세대 기준(모델)과 서명 기준(부품 · 메커니즘 …)을 고르면, 세대마다 나온 서명을 온톨로지의 전작과
 * 견준다. 날짜 순서는 보지 않는다 — 「다시 나왔다」 는 두 세대 모두에 그 조합의 기록이 있다는 뜻이다.
 */

import { useState } from 'react'

import { metricsApi } from '@/modules/metrics/api'
import type { AnalysisViewProps } from '@/modules/metrics/analysis/SprtView'
import {
  AnalysisMeta,
  CaveatList,
  DrillLink,
  Stat,
  percent,
} from '@/modules/metrics/analysis/common'
import type { RecurrenceResult } from '@/modules/metrics/analysis/types'
import { shownNumber } from '@/modules/metrics/metricDrill'
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

const AUTO = '__auto__'
const AXIS_KINDS = ['object_ref', 'related']
const MAX_SIGNATURE = 3

export function RecurrenceView({ metric, read, initial = {} }: AnalysisViewProps) {
  const axes = metric.dims.filter((one) => AXIS_KINDS.includes(one.kind))
  const [generation, setGeneration] = useState(initial.generation ?? axes[0]?.name ?? '')
  const [signature, setSignature] = useState<string[]>(
    (initial.signature ?? '').split(',').filter(Boolean),
  )
  const [via, setVia] = useState(initial.via ?? AUTO)
  const ways = useResource(
    () =>
      generation
        ? metricsApi.coverageWays(metric.slug, generation, generation)
        : Promise.resolve([]),
    [metric.slug, generation],
  )
  const chosen = signature.filter((one) => one !== generation)
  const asked = {
    generation,
    signature: chosen.join(','),
    via: via === AUTO ? undefined : via,
  }
  const key = JSON.stringify([asked, read])
  const result = useResource<RecurrenceResult | null>(
    () =>
      generation && chosen.length > 0
        ? metricsApi.analysis<RecurrenceResult>(metric.slug, 'recurrence', asked, read)
        : Promise.resolve(null),
    [metric.slug, key],
  )
  const data = result.data

  function toggle(name: string, on: boolean) {
    setSignature(
      on ? [...signature, name].slice(-MAX_SIGNATURE) : signature.filter((one) => one !== name),
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="recurrence-generation">세대</Label>
          <Select value={generation} onValueChange={setGeneration}>
            <SelectTrigger id="recurrence-generation" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {axes.map((one) => (
                <SelectItem key={one.name} value={one.name}>
                  {one.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {(ways.data?.length ?? 0) > 1 && (
          <div className="space-y-1">
            <Label htmlFor="recurrence-via">전작으로 가는 길</Label>
            <Select value={via} onValueChange={setVia}>
              <SelectTrigger id="recurrence-via" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={AUTO}>정의에 맞게(앞쪽 길)</SelectItem>
                {(ways.data ?? []).map((one) => (
                  <SelectItem key={one.address} value={one.address}>
                    {one.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        <fieldset className="space-y-1">
          <legend className="text-sm">서명(다시 나왔나 볼 조합) — {MAX_SIGNATURE}개까지</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {metric.dims
              .filter((one) => one.name !== generation)
              .map((one) => (
                <label key={one.name} className="flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="size-4"
                    checked={chosen.includes(one.name)}
                    onChange={(event) => toggle(one.name, event.target.checked)}
                  />
                  {one.label}
                </label>
              ))}
          </div>
        </fieldset>
      </div>
      {chosen.length === 0 && (
        <p className="text-muted-foreground text-sm">서명 기준을 하나 이상 고릅니다.</p>
      )}
      {result.error && <ErrorNotice error={result.error} />}
      {data && (
        <>
          <CaveatList caveats={data.caveats} />
          <AnalysisMeta result={data} />
          <div className="flex flex-wrap gap-2">
            <Stat
              label="재발률(모든 쌍)"
              value={percent(data.rate, 0)}
              hint="다시 나온 서명 / 전작 서명"
            />
            <Stat label="세대 쌍" value={shownNumber(data.pairs_total)} />
            <Stat label="전작 없음" value={shownNumber(data.no_predecessor)} />
            <Stat label="전작으로 가는 길" value={data.way_label} />
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{data.generation_label} ← 전작</TableHead>
                <TableHead className="text-right">다시 나옴 / 전작 서명</TableHead>
                <TableHead className="text-right">재발률</TableHead>
                <TableHead className="text-right">새로 나옴</TableHead>
                <TableHead>다시 나온 것({data.signature_labels.join(' x ')})</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.pairs.map((one) => (
                <TableRow key={`${one.key}-${one.predecessor}`}>
                  <TableCell>
                    {one.label} ← {one.predecessor_label}
                  </TableCell>
                  <TableCell className="text-right">
                    {shownNumber(one.recurring)} / {shownNumber(one.predecessor_signatures)}
                  </TableCell>
                  <TableCell className="text-right">{percent(one.rate, 0)}</TableCell>
                  <TableCell className="text-right">{shownNumber(one.new)}</TableCell>
                  <TableCell>
                    <ul className="space-y-0.5 text-xs">
                      {one.items.map((item) => (
                        <li key={item.keys.join('/')}>
                          {item.labels.join(' / ')} — 전작 {shownNumber(item.before)}건 ·{' '}
                          <DrillLink drill={item.drill} count={item.now} />
                        </li>
                      ))}
                    </ul>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {data.signatures.length > 0 && (
            <section className="space-y-1">
              <p className="text-sm font-medium">되풀이된 서명 — 다시 나온 세대 쌍이 많은 것부터</p>
              <ul className="space-y-0.5 text-sm">
                {data.signatures.map((one) => (
                  <li key={one.keys.join('/')}>
                    {one.labels.join(' / ')} —{' '}
                    <span className="text-muted-foreground">
                      {one.pairs}번({one.generations.join(', ')})
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  )
}
