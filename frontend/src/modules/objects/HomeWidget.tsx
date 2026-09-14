/**
 * 부서 홈에 올라간 뷰 하나 — **그림이거나, 수 하나거나.**
 *
 * 축이 있으면 그림, 없으면 **수 하나이거나 몇 줄**이다. 셋 다 쓸모가 있다 —
 * 「미승인 12건」 은 수가 낫고, 「최근 들어온 것」 은 이름이 보여야 한다.
 *
 * ## 누르면 그 목록으로 간다
 *
 * 홈의 숫자를 보고 다음에 하는 일은 언제나 「그게 뭔데」 다. 거기서 사이드바를 뒤져
 * 타입을 찾고 조건을 다시 걸게 하면, 그 수고 때문에 아무도 홈을 안 쓰게 된다.
 *
 * ## 내리는 단추도 여기 있다
 *
 * 「이건 좀 치우자」 는 생각은 **홈을 보다가** 온다. 그때 목록 화면으로 가서 그 뷰를
 * 불러온 뒤 메뉴를 열게 하면, 올리는 것만 쉽고 내리는 것은 어려운 화면이 된다 —
 * 그러면 홈은 한번 올라간 것들로 곧 지저분해지고, 아무도 안 보게 된다.
 */

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronUp, Loader2, MoreHorizontal, X } from 'lucide-react'

import { objectApi, viewApi } from '@/modules/objects/api'
import type { HomeWidget as Widget, ObjectRow, Summary } from '@/modules/objects/api'
import { Chart, LazyPlot, colorFor } from '@/shared/charts'
import type { ChartKind } from '@/shared/charts'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { TypeIcon } from '@/shared/components/TypeIcon'
import { Button } from '@/shared/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'

/** 목록으로 세운 위젯이 보여 줄 줄 수. 홈은 훑는 자리라 다섯이면 충분하다 — 더 보려면
 *  제목을 눌러 목록으로 간다. */
const LIST_ROWS = 5

/** 세부 기준 조각의 값. 없는 계열은 0 — 빈 자리는 「없음」 과 0 을 구별 못 하게 만든다. */
function partValue(
  bucket: { parts: { label: string; count: number; value: number | null }[] },
  label: string,
  metric: string | undefined,
): number {
  const part = bucket.parts.find((one) => one.label === label)
  if (!part) return 0
  return metric === 'count' ? part.count : (part.value ?? 0)
}

/** 이 뷰를 그대로 여는 목록 주소 — 조건까지 실어 보낸다. */
export function viewHref(widget: Widget): string {
  const params = new URLSearchParams()
  const query = widget.view.query
  if (query.q) params.set('q', query.q)
  for (const one of query.conditions ?? []) {
    params.append(`f.${one.field}.${one.op}`, one.value)
  }
  params.set('view', widget.view.id)
  return `/o/${widget.view.type_slug}?${params.toString()}`
}

interface Props {
  widget: Widget
  /** 여러 부서를 한 화면에 놓을 때 — **어디 것인지** 적는다. */
  showWorkspace?: boolean
  /** 내리고 옮길 수 있나 — 부서 관리자만. */
  canEdit?: boolean
  /** 몇 번째인지와 전부 몇 개인지 — 끝에서는 그 방향 단추를 안 보인다. */
  index?: number
  total?: number
  /** 내리거나 옮긴 뒤. 홈이 다시 읽는다. */
  onChanged?: () => void
}

export function HomeWidget({
  widget,
  showWorkspace = false,
  canEdit = false,
  index = 0,
  total = 1,
  onChanged,
}: Props) {
  const { view } = widget
  const [busy, setBusy] = useState(false)
  const [failedEdit, setFailedEdit] = useState<Error | null>(null)

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
  const grouped = Boolean(view.summary.group_by)
  /** 기준이 없는 뷰의 두 모양 — 수 하나이거나 몇 줄이거나. */
  const asList = !grouped && view.summary.chart === 'list'
  const [data, setData] = useState<Summary | null>(null)
  const [rows, setRows] = useState<ObjectRow[]>([])
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setFailed(false)
    // 목록으로 세울 것은 **세는 대신 몇 줄을 읽는다** — 「최근 들어온 것」 은 수가
    // 아니라 이름이 보여야 한다.
    const asked = asList
      ? objectApi
          .list(view.type_slug, {
            q: view.query.q || undefined,
            conditions: view.query.conditions,
            limit: LIST_ROWS,
          })
          .then((page) => {
            if (!cancelled) setRows(page.items)
          })
      : objectApi
          .summary(
            view.type_slug,
            { q: view.query.q || undefined, conditions: view.query.conditions },
            {
              // 축이 없으면 상태로 묶어 수만 쓴다 — 한 번 더 물을 것 없이 total 이 나온다.
              groupBy: view.summary.group_by || 'status',
              splitBy: view.summary.group_by ? view.summary.split_by || null : null,
              metric: view.summary.group_by ? view.summary.metric : 'count',
              metricField: view.summary.group_by ? view.summary.metric_field : null,
              order: view.summary.order || 'desc',
            },
          )
          .then((found) => {
            if (!cancelled) setData(found)
          })
    asked
      .catch(() => {
        // **한 위젯이 깨져도 홈은 선다.** 정의가 바뀌어 축이 사라질 수 있고, 그때
        // 홈 전체가 안 뜨면 고칠 화면으로 가는 길까지 막힌다.
        if (!cancelled) setFailed(true)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [
    asList,
    view.type_slug,
    view.id,
    view.summary.group_by,
    view.summary.split_by,
    view.summary.metric,
    view.summary.metric_field,
    view.summary.order,
    view.query.q,
    view.query.conditions,
  ])

  return (
    <section className="rounded-md border p-4">
      <div className="mb-2 flex items-center gap-2">
        <TypeIcon name={widget.icon} className="shrink-0" />
        <Link
          to={viewHref(widget)}
          className="min-w-0 flex-1 truncate text-sm font-medium hover:underline"
        >
          {view.name}
        </Link>
        <span className="text-muted-foreground shrink-0 text-xs">
          {showWorkspace ? `${widget.workspace_name} · ${widget.type_label}` : widget.type_label}
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
                <DropdownMenuItem
                  disabled={busy}
                  onSelect={() =>
                    act(() => viewApi.update(view.type_slug, view.id, { home_position: index - 1 }))
                  }
                >
                  <ChevronUp className="mr-1 size-3.5" />
                  앞으로
                </DropdownMenuItem>
              )}
              {index < total - 1 && (
                <DropdownMenuItem
                  disabled={busy}
                  onSelect={() =>
                    act(() => viewApi.update(view.type_slug, view.id, { home_position: index + 1 }))
                  }
                >
                  <ChevronDown className="mr-1 size-3.5" />
                  뒤로
                </DropdownMenuItem>
              )}
              {/* **뷰는 안 지운다.** 홈에서만 내린다 — 필터까지 잃을 이유가 없고,
                  나중에 다시 올릴 수도 있다. */}
              <DropdownMenuItem
                disabled={busy}
                onSelect={() =>
                  act(() => viewApi.update(view.type_slug, view.id, { on_home: false }))
                }
              >
                <X className="mr-1 size-3.5" />
                홈에서 내리기
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
      ) : failed ? (
        <p className="text-muted-foreground text-sm">
          지금 셀 수 없습니다. 뷰의 조건이나 기준이 정의와 안 맞을 수 있습니다 —{' '}
          <Link to={viewHref(widget)} className="underline">
            목록에서 확인
          </Link>
          하세요.
        </p>
      ) : grouped && view.summary.chart === 'heatmap' && (data?.splits.length ?? 0) > 0 ? (
        <LazyPlot
          height={220}
          title={`${data?.group_label} × ${data?.split_label}`}
          data={[
            {
              type: 'heatmap',
              z: (data?.buckets ?? []).map((one) =>
                (data?.splits ?? []).map((label) => partValue(one, label, data?.metric)),
              ),
              x: data?.splits ?? [],
              y: (data?.buckets ?? []).map((one) => one.label),
              colorscale: 'Blues',
            },
          ]}
        />
      ) : grouped ? (
        <Chart
          kind={(view.summary.chart as ChartKind) || 'bar'}
          data={(data?.buckets ?? []).map((one) => {
            const row: Record<string, unknown> = {
              name: one.label,
              값: data?.metric === 'count' ? one.count : (one.value ?? 0),
            }
            // **없는 계열은 0 으로 채운다** — 빠뜨리면 쌓은 막대에서 그 자리만 비고,
            // 사람은 데이터가 없는 것과 0 을 구별 못 한다.
            for (const label of data?.splits ?? []) {
              row[label] = partValue(one, label, data?.metric)
            }
            return row
          })}
          x="name"
          series={
            (data?.splits ?? []).length > 0
              ? (data?.splits ?? []).map((label) => ({
                  key: label,
                  label,
                  color: colorFor(label),
                }))
              : [{ key: '값', label: data?.metric_label ?? '건수' }]
          }
          stacked={view.summary.stacked}
          height={200}
          title={`${view.name} — ${data?.group_label ?? ''}`}
          emptyText="지금은 셀 것이 없습니다."
        />
      ) : asList ? (
        /* 「최근 들어온 것」 은 수가 아니라 이름이 보여야 한다. */
        rows.length === 0 ? (
          <p className="text-muted-foreground text-sm">지금은 해당하는 것이 없습니다.</p>
        ) : (
          <ul className="divide-y text-sm">
            {rows.map((one) => (
              <li key={one.id}>
                <Link
                  to={`/o/${view.type_slug}/${one.id}`}
                  className="hover:bg-muted/50 -mx-1 flex items-center gap-2 rounded px-1 py-1.5"
                >
                  <span className="min-w-0 flex-1 truncate">{one.label}</span>
                  {one.key && (
                    <span className="text-muted-foreground shrink-0 font-mono text-xs">
                      {one.key}
                    </span>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        )
      ) : (
        <Link to={viewHref(widget)} className="block">
          {/* 기준이 없는 뷰는 **수 하나**다. 「미승인 12건」 은 그림이 필요 없다. */}
          <p className="text-3xl font-semibold tabular-nums">
            {(data?.total ?? 0).toLocaleString()}
          </p>
          <p className="text-muted-foreground text-sm">건</p>
        </Link>
      )}
    </section>
  )
}
