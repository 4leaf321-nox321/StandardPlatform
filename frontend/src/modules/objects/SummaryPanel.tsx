/**
 * 묶어 보기 — 목록 위에서 **「그래서 몇 건인데」** 에 답한다.
 *
 * 그리는 일은 `shared/charts` 가 한다. 이 화면은 **무엇을 그릴지만** 정한다 —
 * 축 색·범례 높이·빈 데이터 처리를 여기서 또 정하면 다른 화면의 그림과 어긋나고,
 * 그때 같은 값이 화면마다 다른 색으로 선다.
 *
 * ## 기본은 막대다
 *
 * 사람은 각도보다 길이를 훨씬 잘 읽는다. 원그래프는 조각이 서넛일 때 비율을
 * 보여 주는 데만 쓴다 — 여덟 조각짜리 원에서는 순위를 읽을 수 없다.
 *
 * ## 막대를 누르면 그 줄만 걸러진다
 *
 * 「B 등급이 12건」 다음의 물음은 언제나 「그 12건이 뭔데」 다. 거기서 거르기 칸으로
 * 돌아가 값을 다시 치게 하면, 그 한 번이 사람을 엑셀로 돌려보낸다.
 */

import { useEffect, useMemo, useState } from 'react'
import { BarChart3, ChartLine, ChartPie, Loader2, X } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import type { ObjectQuery, Summary } from '@/modules/objects/api'
import { Chart } from '@/shared/charts'
import type { ChartKind } from '@/shared/charts'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { cn } from '@/shared/lib/utils'

/** 그림 모양. **막대가 기본이다** — 길이는 눈이 바로 비교하지만 각도는 못 한다. */
const KINDS: { value: ChartKind; label: string; Icon: typeof BarChart3 }[] = [
  { value: 'bar', label: '막대', Icon: BarChart3 },
  { value: 'line', label: '꺾은선', Icon: ChartLine },
  { value: 'pie', label: '원', Icon: ChartPie },
]

/** 그림 안에서 쓰는 키. 사람이 고른 칸 이름과 안 겹치게 둔다. */
const VALUE = '__value__'
const KEY = '__key__'

const METRICS = [
  { value: 'count', label: '건수' },
  { value: 'sum', label: '합계' },
  { value: 'avg', label: '평균' },
  { value: 'min', label: '최솟값' },
  { value: 'max', label: '최댓값' },
]

/** 무엇을 어떻게 그릴지. **뷰에 담기는 것과 같은 모양이다** — 저장했다가 그대로 돌려받는다. */
export interface SummarySettings {
  groupBy: string
  metric: string
  metricField: string | null
  chart: ChartKind
}

export const DEFAULT_SUMMARY: SummarySettings = {
  groupBy: 'status',
  metric: 'count',
  metricField: null,
  chart: 'bar',
}

interface Props {
  typeSlug: string
  /** 목록이 지금 쓰는 거르기 그대로. */
  query: ObjectQuery
  /**
   * 지금 설정. **화면이 들고 있다** — 뷰를 불러오면 그 뷰의 축으로 열려야 하고,
   * 저장할 때는 지금 축이 함께 담겨야 한다. 이 패널이 혼자 들고 있으면 둘 다 못 한다.
   */
  settings: SummarySettings
  onSettings: (next: SummarySettings) => void
  /** 막대를 눌렀을 때 — 걸 수 있는 축이면 부른다. */
  onPick: (field: string, key: string | null) => void
  onClose: () => void
}

/** 이 축으로 묶은 값을 목록 거르기에 그대로 걸 수 있나. */
function filterable(field: string): boolean {
  return field === 'status' || field.startsWith('properties.')
}

export function SummaryPanel({ typeSlug, query, settings, onSettings, onPick, onClose }: Props) {
  const { groupBy, metric, metricField, chart: kind } = settings
  const patch = (next: Partial<SummarySettings>) => onSettings({ ...settings, ...next })
  const [data, setData] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  // 거르기가 바뀌면 다시 센다. `query` 는 매 렌더 새 객체라 **내용**으로 비교한다 —
  // 안 그러면 이 효과가 끝없이 돈다.
  const signature = JSON.stringify(query)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    objectApi
      .summary(typeSlug, JSON.parse(signature) as ObjectQuery, {
        groupBy,
        metric,
        metricField: metric === 'count' ? null : metricField,
      })
      .then((found) => {
        if (cancelled) return
        setData(found)
        setError(null)
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [typeSlug, signature, groupBy, metric, metricField])

  const numbers = data?.metric_options ?? []
  /** 그림이 받는 행 — 이름과 값, 그리고 눌렀을 때 되찾을 원래 값. */
  const rows = useMemo(
    () =>
      (data?.buckets ?? []).map((one) => ({
        name: one.label,
        [VALUE]: data?.metric === 'count' ? one.count : (one.value ?? 0),
        [KEY]: one.key,
        건수: one.count,
      })),
    [data],
  )
  const canFilter = Boolean(data && filterable(data.group_field))

  return (
    <div className="space-y-3 rounded-md border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <BarChart3 className="text-muted-foreground size-4" />
        <span className="text-sm font-medium">묶어 보기</span>

        <Select value={groupBy} onValueChange={(next) => patch({ groupBy: next })}>
          <SelectTrigger className="w-48" aria-label="묶을 축">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(data?.group_options ?? []).map((one) => (
              <SelectItem key={one.field} value={one.field}>
                {one.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={metric}
          onValueChange={(next) => {
            // 합·평균으로 바꾸면 셀 칸이 필요하다. 하나뿐이면 그것으로 정해 준다 —
            // 고를 것이 하나인 고르개는 묻는 시늉일 뿐이다.
            const field =
              next !== 'count' && !metricField && numbers.length > 0
                ? numbers[0].field
                : metricField
            patch({ metric: next, metricField: field })
          }}
        >
          <SelectTrigger className="w-32" aria-label="세는 방법">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {METRICS.map((one) => (
              <SelectItem
                key={one.value}
                value={one.value}
                disabled={one.value !== 'count' && numbers.length === 0}
              >
                {one.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {metric !== 'count' && (
          <Select value={metricField ?? ''} onValueChange={(next) => patch({ metricField: next })}>
            <SelectTrigger className="w-40" aria-label="셀 칸">
              <SelectValue placeholder="숫자 칸" />
            </SelectTrigger>
            <SelectContent>
              {numbers.map((one) => (
                <SelectItem key={one.field} value={one.field}>
                  {one.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        <div className="flex items-center rounded-md border">
          {KINDS.map((one) => (
            <button
              key={one.value}
              type="button"
              aria-label={one.label}
              aria-pressed={kind === one.value}
              title={one.label}
              className={cn('px-2 py-1.5', kind === one.value ? 'bg-muted' : 'hover:bg-muted/50')}
              onClick={() => patch({ chart: one.value })}
            >
              <one.Icon className="size-4" />
            </button>
          ))}
        </div>

        {loading && <Loader2 className="text-muted-foreground size-4 animate-spin" />}
        <Button variant="ghost" size="icon" aria-label="묶어 보기 닫기" onClick={onClose}>
          <X className="size-4" />
        </Button>
      </div>

      <ErrorNotice error={error} />

      {data && !error && (
        <div className="space-y-1">
          <Chart
            kind={kind}
            data={rows}
            x="name"
            series={[{ key: VALUE, label: data.metric_label }]}
            height={Math.max(220, Math.min(420, rows.length * 28 + 80))}
            title={`${data.group_label}별 ${data.metric_label}`}
            emptyText="셀 것이 없습니다."
            onPick={
              canFilter
                ? (row) => {
                    const key = row[KEY]
                    // 빈 칸은 「값이 없음」 이라 거르기로 옮길 값이 없다.
                    if (typeof key === 'string') onPick(data.group_field, key)
                  }
                : undefined
            }
          />
          <p className="text-muted-foreground pt-1 text-xs">
            {/* **막대의 합과 전체가 다르면 그 차이를 적는다.** 안 적으면 사람은 그것을
                오류로 읽고, 그 뒤로 이 그림을 안 믿는다. */}
            거른 것 전체 {data.total.toLocaleString()}건
            {data.other_groups > 0 && (
              <>
                {' · '}
                <strong>그 밖에</strong> {data.other_groups}종류 {data.other_count}건은 접혔습니다
              </>
            )}
            {data.metric !== 'count' && ' · 숫자로 안 읽히는 값은 셈에서 빠집니다'}
            {canFilter && ' · 막대를 누르면 그것만 걸러집니다'}
          </p>
        </div>
      )}
    </div>
  )
}
