/**
 * 부서 홈에 올라간 뷰 하나 — **그림이거나, 수 하나거나.**
 *
 * 뷰에 묶어 보기 설정이 있으면 그림을 그리고, 없으면 「N건」 한 줄이다. 둘 다 쓸모가
 * 있다 — 「미승인 12건」 은 축이 없어도 홈에 있어야 하는 숫자다.
 *
 * ## 누르면 그 목록으로 간다
 *
 * 홈의 숫자를 보고 다음에 하는 일은 언제나 「그게 뭔데」 다. 거기서 사이드바를 뒤져
 * 타입을 찾고 조건을 다시 걸게 하면, 그 수고 때문에 아무도 홈을 안 쓰게 된다.
 */

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Loader2 } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import type { HomeWidget as Widget, Summary } from '@/modules/objects/api'
import { Chart } from '@/shared/charts'
import type { ChartKind } from '@/shared/charts'
import { TypeIcon } from '@/shared/components/TypeIcon'

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

export function HomeWidget({ widget }: { widget: Widget }) {
  const { view } = widget
  const grouped = Boolean(view.summary.group_by)
  const [data, setData] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    objectApi
      .summary(
        view.type_slug,
        { q: view.query.q || undefined, conditions: view.query.conditions },
        {
          // 축이 없으면 상태로 묶어 수만 쓴다 — 한 번 더 물을 것 없이 total 이 나온다.
          groupBy: view.summary.group_by || 'status',
          metric: view.summary.group_by ? view.summary.metric : 'count',
          metricField: view.summary.group_by ? view.summary.metric_field : null,
        },
      )
      .then((found) => {
        if (!cancelled) setData(found)
      })
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
    view.type_slug,
    view.id,
    view.summary.group_by,
    view.summary.metric,
    view.summary.metric_field,
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
        <span className="text-muted-foreground shrink-0 text-xs">{widget.type_label}</span>
      </div>

      {loading ? (
        <div className="text-muted-foreground flex h-24 items-center justify-center">
          <Loader2 className="size-4 animate-spin" />
        </div>
      ) : failed ? (
        <p className="text-muted-foreground text-sm">
          지금 셀 수 없습니다. 뷰의 조건이나 축이 정의와 안 맞을 수 있습니다 —{' '}
          <Link to={viewHref(widget)} className="underline">
            목록에서 확인
          </Link>
          하세요.
        </p>
      ) : grouped ? (
        <Chart
          kind={(view.summary.chart as ChartKind) || 'bar'}
          data={(data?.buckets ?? []).map((one) => ({
            name: one.label,
            값: data?.metric === 'count' ? one.count : (one.value ?? 0),
          }))}
          x="name"
          series={[{ key: '값', label: data?.metric_label ?? '건수' }]}
          height={200}
          title={`${view.name} — ${data?.group_label ?? ''}`}
          emptyText="지금은 셀 것이 없습니다."
        />
      ) : (
        <Link to={viewHref(widget)} className="block">
          {/* 축이 없는 뷰는 **수 하나**다. 「미승인 12건」 은 그림이 필요 없다. */}
          <p className="text-3xl font-semibold tabular-nums">
            {(data?.total ?? 0).toLocaleString()}
          </p>
          <p className="text-muted-foreground text-sm">건</p>
        </Link>
      )}
    </section>
  )
}
