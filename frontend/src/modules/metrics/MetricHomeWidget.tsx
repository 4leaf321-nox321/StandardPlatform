/**
 * 부서 홈에 올린 **지표** 하나 — 최근 기간의 추이 그림과 마지막 값.
 *
 * 홈은 훑는 자리라 최근 열두 기간만 그린다. 더 보려면 제목을 눌러 지표 화면으로 간다(기간 ·
 * 기준 · 분석은 거기서). 마지막 값 옆에 앞 기간과의 차이를 적는다 — 홈에서 지표를 보고 하는
 * 물음은 대개 「늘었나 줄었나」 다.
 *
 * 메뉴(앞으로 · 뒤로 · 홈 게시 해제)는 뷰 위젯과 같다 — 자리는 뷰와 한 줄에서 센다.
 */

import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronUp, Loader2, MoreHorizontal, X } from 'lucide-react'

import { metricsApi } from '@/modules/metrics/api'
import type { MetricSeries } from '@/modules/metrics/api'
import { shownNumber } from '@/modules/metrics/metricDrill'
import type { HomeMetric, HomeWidget } from '@/modules/objects/api'
import { Chart } from '@/shared/charts'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { TypeIcon } from '@/shared/components/TypeIcon'
import { Button } from '@/shared/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'

/** 홈에 그리는 기간 수. */
export const HOME_PERIODS = 12

interface Props {
  widget: HomeWidget & { metric: HomeMetric }
  showWorkspace?: boolean
  canEdit?: boolean
  index?: number
  total?: number
  onChanged?: () => void
}

type Point = MetricSeries['lines'][number]['points'][number]

type Shown = 'ratio' | 'value' | 'count'

/** 이 지표가 무엇을 그리나 — **지표마다 한 번** 정한다. 점마다 「있는 것」 을 고르면 분모가
 * 아직 없는 기간에 비율 자리로 건수가 서서(1000대당 2.5 옆에 300) 한 선에 다른 것이 섞였다
 * (2026-10-08). 상세 화면과 같은 규칙: 분모나 조건 비율이면 비율, 집계가 건수가 아니면 그
 * 값, 아니면 건수. */
function shownOf(data: MetricSeries): Shown {
  if (data.denominator || data.measure === 'share') return 'ratio'
  return data.measure === 'count' ? 'count' : 'value'
}

/** 그 기간의 값 — 없으면(분모가 아직 없는 기간 · 값이 없는 평균) null, 그림에서는 빈칸. */
function valueOf(point: Point, shown: Shown): number | null {
  if (shown === 'ratio') return point.ratio
  if (shown === 'value') return point.value
  return point.count
}

export function MetricHomeWidget({
  widget,
  showWorkspace = false,
  canEdit = false,
  index = 0,
  total = 1,
  onChanged,
}: Props) {
  const { metric } = widget
  const [data, setData] = useState<MetricSeries | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failedEdit, setFailedEdit] = useState<Error | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setFailed(false)
    metricsApi
      .series(metric.metric_slug, { split: metric.split ?? undefined })
      .then((found) => {
        if (!cancelled) setData(found)
      })
      .catch(() => {
        // **한 위젯이 깨져도 홈은 선다** — 지표의 정의가 바뀌어 기준이 사라질 수 있다.
        if (!cancelled) setFailed(true)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [metric.metric_slug, metric.split])

  const shown = data ? shownOf(data) : 'count'
  const rows = useMemo(() => {
    const byPeriod = new Map<string, Record<string, unknown>>()
    for (const line of data?.lines ?? []) {
      for (const point of line.points) {
        const row = byPeriod.get(point.period) ?? { period: point.period, name: point.label }
        row[line.label] = valueOf(point, shown)
        byPeriod.set(point.period, row)
      }
    }
    return [...byPeriod.values()]
      .sort((a, b) => String(a.period).localeCompare(String(b.period)))
      .slice(-HOME_PERIODS)
  }, [data, shown])

  // 한 줄일 때만 마지막 값과 앞 기간의 차이를 적는다 — 여러 줄이면 어느 줄의 것인지 모호하다.
  const single = data && data.lines.length === 1 ? data.lines[0] : null
  const last = single?.points.at(-1) ?? null
  const before = single && single.points.length > 1 ? single.points.at(-2) : null
  const lastValue = last ? valueOf(last, shown) : null
  const beforeValue = before ? valueOf(before, shown) : null
  const change = lastValue !== null && beforeValue !== null ? lastValue - beforeValue : null

  function act(run: () => Promise<unknown>) {
    setBusy(true)
    setFailedEdit(null)
    run()
      .then(() => onChanged?.())
      .catch((caught: unknown) =>
        setFailedEdit(caught instanceof Error ? caught : new Error('알 수 없는 오류')),
      )
      .finally(() => setBusy(false))
  }
  const place = (position: number) =>
    act(() =>
      metricsApi.pinHome(metric.metric_slug, {
        workspace_slug: widget.workspace_slug,
        split: metric.split,
        position,
      }),
    )

  return (
    <section className="rounded-md border p-4">
      <div className="mb-2 flex items-center gap-2">
        <TypeIcon name={widget.icon} className="shrink-0" />
        <Link
          to={`/metrics/${metric.metric_slug}`}
          className="min-w-0 flex-1 truncate text-sm font-medium hover:underline"
        >
          {metric.metric_label}
        </Link>
        <span className="text-muted-foreground shrink-0 text-xs">
          {showWorkspace ? `${widget.workspace_name} · 지표` : '지표'}
        </span>
        {canEdit && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="size-7" aria-label="위젯 메뉴">
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {index > 0 && (
                <DropdownMenuItem disabled={busy} onSelect={() => place(index - 1)}>
                  <ChevronUp className="mr-1 size-3.5" />
                  앞으로
                </DropdownMenuItem>
              )}
              {index < total - 1 && (
                <DropdownMenuItem disabled={busy} onSelect={() => place(index + 1)}>
                  <ChevronDown className="mr-1 size-3.5" />
                  뒤로
                </DropdownMenuItem>
              )}
              {/* 지표는 안 지운다 — 홈에서만 내린다. */}
              <DropdownMenuItem
                disabled={busy}
                onSelect={() =>
                  act(() => metricsApi.unpinHome(metric.metric_slug, widget.workspace_slug))
                }
              >
                <X className="mr-1 size-3.5" />홈 게시 해제
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      <ErrorNotice error={failedEdit} className="mb-2" />

      {loading ? (
        <div className="text-muted-foreground flex h-24 items-center justify-center">
          <Loader2 className="size-4 animate-spin" />
        </div>
      ) : failed || !data ? (
        <p className="text-muted-foreground text-sm">
          지금 읽을 수 없습니다. 지표의 정의가 바뀌었을 수 있습니다 —{' '}
          <Link to={`/metrics/${metric.metric_slug}`} className="underline">
            지표 화면에서 확인
          </Link>
          하세요.
        </p>
      ) : (
        <>
          {last && (
            <p className="mb-1 flex items-baseline gap-2">
              <span className="text-2xl font-semibold tabular-nums">
                {lastValue === null ? '—' : shownNumber(lastValue)}
              </span>
              <span className="text-muted-foreground text-xs">
                {last.label}
                {change !== null &&
                  ` · 앞 기간보다 ${change >= 0 ? '+' : ''}${shownNumber(change)}`}
                {!last.closed && ' · 아직 닫히지 않음'}
              </span>
            </p>
          )}
          <Chart
            kind="line"
            data={rows}
            x="name"
            series={(data.lines ?? []).map((line) => ({ key: line.label }))}
            height={180}
            title={`${metric.metric_label} — 최근 ${HOME_PERIODS}기간`}
            emptyText="아직 센 값이 없습니다."
          />
        </>
      )}
    </section>
  )
}
