/**
 * 커버리지 — **다뤄야 할 축 조합 중 무엇을 다뤘고 무엇이 비었나**(ADR 0022).
 *
 * 축인 기준(다른 타입을 가리키는 참조 · 관계)을 차례로 고르면, 첫 기준의 값에서 온톨로지의 길을
 * 따라 다뤄야 할 조합을 펴고 기록이 다룬 조합과 견준다. 기준 사이의 길이 하나면 저절로, 여럿이면
 * 여기서 고른다. 축 이름(제품 · 부품 …)은 화면이 모른다 — 지표의 기준에서 읽는다.
 */

import { useEffect, useState } from 'react'

import { metricsApi } from '@/modules/metrics/api'
import type { AnalysisViewProps } from '@/modules/metrics/analysis/SprtView'
import {
  AnalysisMeta,
  CaveatList,
  DrillLink,
  Stat,
  percent,
} from '@/modules/metrics/analysis/common'
import type { CoverageResult } from '@/modules/metrics/analysis/types'
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

const NONE = '__none__'
const SLOTS = 4
/** 축인 기준 — 다른 타입을 가리키는 참조 · 관계. */
const AXIS_KINDS = ['object_ref', 'related']

export function CoverageView({ metric, read, initial = {} }: AnalysisViewProps) {
  const choices = metric.dims.filter((one) => AXIS_KINDS.includes(one.kind))
  const given = (initial.levels ?? '').split(',').filter(Boolean)
  const [levels, setLevels] = useState<string[]>(
    given.length >= 2
      ? given
      : choices.slice(0, Math.min(SLOTS, choices.length)).map((one) => one.name),
  )
  const [via, setVia] = useState<string[]>((initial.via ?? '').split(','))
  const chosen = levels.filter((one) => one && one !== NONE)
  const asked = { levels: chosen.join(','), via: via.slice(0, chosen.length - 1).join(',') }
  const key = JSON.stringify([asked, read])
  const result = useResource<CoverageResult | null>(
    () =>
      chosen.length >= 2
        ? metricsApi.analysis<CoverageResult>(metric.slug, 'coverage', asked, read)
        : Promise.resolve(null),
    [metric.slug, key],
  )
  const data = result.data

  function setLevel(index: number, value: string) {
    const next = [...levels]
    next[index] = value
    setLevels(next)
    // 기준이 바뀌면 그 앞뒤의 길은 다시 고른다.
    const ways = [...via]
    if (index > 0) ways[index - 1] = ''
    ways[index] = ''
    setVia(ways)
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        {Array.from({ length: SLOTS }, (_, index) => (
          <div key={index} className="flex items-end gap-3">
            {index > 0 && chosen[index - 1] && chosen[index] && (
              <WayPicker
                slug={metric.slug}
                from={chosen[index - 1]}
                to={chosen[index]}
                value={via[index - 1] ?? ''}
                onChange={(next) => {
                  const ways = [...via]
                  ways[index - 1] = next
                  setVia(ways)
                }}
              />
            )}
            {(index < 2 || chosen.length >= index) && (
              <div className="space-y-1">
                <Label htmlFor={`coverage-level-${index}`}>{index + 1}번 기준</Label>
                <Select
                  value={levels[index] ?? NONE}
                  onValueChange={(next) => setLevel(index, next)}
                >
                  <SelectTrigger id={`coverage-level-${index}`} className="w-40">
                    <SelectValue placeholder="없음" />
                  </SelectTrigger>
                  <SelectContent>
                    {index >= 2 && <SelectItem value={NONE}>없음</SelectItem>}
                    {choices
                      .filter((one) => one.name === levels[index] || !chosen.includes(one.name))
                      .map((one) => (
                        <SelectItem key={one.name} value={one.name}>
                          {one.label}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
        ))}
      </div>
      {choices.length < 2 && (
        <p className="text-muted-foreground text-sm">
          축(다른 타입을 가리키는 참조 · 관계)인 기준이 둘 이상 있어야 합니다.
        </p>
      )}
      {result.error && <ErrorNotice error={result.error} />}
      {data && (
        <>
          <CaveatList caveats={data.caveats} />
          <AnalysisMeta result={data} />
          <p className="text-muted-foreground text-xs">
            길:{' '}
            {data.levels
              .map((one) => (one.way_label ? `→ ${one.way_label} → ${one.label}` : one.label))
              .join(' ')}
          </p>
          <div className="flex flex-wrap gap-2">
            <Stat
              label={`끝 조합(${data.levels.map((one) => one.label).join(' x ')})`}
              value={`${shownNumber(data.covered_leaves)} / ${shownNumber(data.expected_leaves)}`}
              hint="다룬 것 / 다뤄야 할 것"
            />
            {data.depths.slice(1, -1).map((one) => (
              <Stat
                key={one.depth}
                label={one.labels.join(' x ')}
                value={`${shownNumber(one.covered)} / ${shownNumber(one.expected)}`}
              />
            ))}
            {data.untagged > 0 && (
              <Stat label="태그가 모자란 기록" value={shownNumber(data.untagged)} />
            )}
          </div>
          <section className="space-y-1">
            <p className="text-sm font-medium">{data.levels[0].label}마다 — 덜 다룬 것부터</p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{data.levels[0].label}</TableHead>
                  <TableHead className="text-right">다룬 끝 조합</TableHead>
                  <TableHead className="text-right">몫</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.roots.map((one) => (
                  <TableRow key={one.key}>
                    <TableCell>{one.label}</TableCell>
                    <TableCell className="text-right">
                      {shownNumber(one.covered)} / {shownNumber(one.expected)}
                    </TableCell>
                    <TableCell className="text-right">{percent(one.share, 0)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
          <section className="space-y-1">
            <p className="text-sm font-medium">
              빈 칸 {shownNumber(data.gaps_total)}곳 — 바로 위는 다뤘는데 이 조합은 기록이 없다
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>조합</TableHead>
                  <TableHead className="text-right">그 아래 조합</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.gaps.map((one) => (
                  <TableRow key={one.keys.join('/')}>
                    <TableCell>{one.labels.join(' / ')}</TableCell>
                    <TableCell className="text-right">{shownNumber(one.leaves)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
          {data.extras.length > 0 && (
            <section className="space-y-1">
              <p className="text-sm font-medium">
                기대 밖 {shownNumber(data.extras_total)}곳 — 기록은 있는데 온톨로지로는 닿지 않는
                조합(관계가 빠졌거나 태그가 잘못 붙은 곳)
              </p>
              <Table>
                <TableBody>
                  {data.extras.map((one) => (
                    <TableRow key={one.keys.join('/')}>
                      <TableCell>{one.labels.join(' / ')}</TableCell>
                      <TableCell className="text-right">
                        <DrillLink drill={one.drill} count={one.count} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </section>
          )}
        </>
      )}
    </div>
  )
}

/** 두 기준 사이의 길 — 후보가 하나면 저절로(빈 값), 여럿이면 고른다. */
function WayPicker({
  slug,
  from,
  to,
  value,
  onChange,
}: {
  slug: string
  from: string
  to: string
  value: string
  onChange: (next: string) => void
}) {
  const ways = useResource(() => metricsApi.coverageWays(slug, from, to), [slug, from, to])
  const list = ways.data ?? []
  useEffect(() => {
    if (list.length > 1 && !value) onChange(list[0].address)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list.length])
  if (list.length <= 1) {
    return (
      <span className="text-muted-foreground pb-2 text-xs">→ {list[0]?.label ?? '길 없음'} →</span>
    )
  }
  return (
    <div className="space-y-1">
      <Label htmlFor={`coverage-way-${from}-${to}`}>길</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={`coverage-way-${from}-${to}`} className="w-44">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {list.map((one) => (
            <SelectItem key={one.address} value={one.address}>
              {one.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
