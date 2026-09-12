/**
 * 묶어 보기 — 목록 위에서 **「그래서 몇 건인데」** 에 답한다.
 *
 * ## 차트 라이브러리를 안 쓴다
 *
 * 가로 막대는 `div` 의 너비다. 라이브러리를 들이면 번들이 수백 KB 늘고, 폐쇄망
 * 배포에서 의존성 하나는 곧 「그날 빌드가 안 되는 이유」 가 된다. 축이 여럿인
 * 그림이 필요해지는 날 그때 들인다 — 지금 필요한 것은 **순서대로 늘어선 막대**다.
 *
 * ## 숫자를 막대 밖에 적는다
 *
 * 막대 안에 넣으면 짧은 막대에서 글자가 막대를 넘고, 그러면 제일 작은 값이 제일
 * 읽기 어려워진다. 작은 값이야말로 사람이 확인하러 오는 값이다.
 *
 * ## 막대를 누르면 그 줄만 걸러진다
 *
 * 「B 등급이 12건」 다음의 물음은 언제나 「그 12건이 뭔데」 다. 거기서 거르기 칸으로
 * 돌아가 값을 다시 치게 하면, 그 한 번이 사람을 엑셀로 돌려보낸다.
 */

import { useEffect, useMemo, useState } from 'react'
import { BarChart3, Loader2, X } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import type { ObjectQuery, Summary } from '@/modules/objects/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'

const METRICS = [
  { value: 'count', label: '건수' },
  { value: 'sum', label: '합계' },
  { value: 'avg', label: '평균' },
  { value: 'min', label: '최솟값' },
  { value: 'max', label: '최댓값' },
]

interface Props {
  typeSlug: string
  /** 목록이 지금 쓰는 거르기 그대로. */
  query: ObjectQuery
  /** 막대를 눌렀을 때 — 걸 수 있는 축이면 부른다. */
  onPick: (field: string, key: string | null) => void
  onClose: () => void
}

/** 이 축으로 묶은 값을 목록 거르기에 그대로 걸 수 있나. */
function filterable(field: string): boolean {
  return field === 'status' || field.startsWith('properties.')
}

function shownNumber(value: number): string {
  // 소수는 둘째 자리까지 — 평균이 3.3333333 으로 뜨면 그 자리는 숫자가 아니라 잡음이다.
  return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2)
}

export function SummaryPanel({ typeSlug, query, onPick, onClose }: Props) {
  const [groupBy, setGroupBy] = useState('status')
  const [metric, setMetric] = useState('count')
  const [metricField, setMetricField] = useState<string | null>(null)
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
  const widest = useMemo(() => {
    const values = (data?.buckets ?? []).map((one) =>
      data?.metric === 'count' ? one.count : (one.value ?? 0),
    )
    return Math.max(1, ...values)
  }, [data])

  return (
    <div className="space-y-3 rounded-md border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <BarChart3 className="text-muted-foreground size-4" />
        <span className="text-sm font-medium">묶어 보기</span>

        <Select value={groupBy} onValueChange={setGroupBy}>
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
            setMetric(next)
            // 합·평균으로 바꾸면 셀 칸이 필요하다. 하나뿐이면 그것으로 정해 준다 —
            // 고를 것이 하나인 고르개는 묻는 시늉일 뿐이다.
            if (next !== 'count' && !metricField && numbers.length > 0) {
              setMetricField(numbers[0].field)
            }
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
          <Select value={metricField ?? ''} onValueChange={setMetricField}>
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

        {loading && <Loader2 className="text-muted-foreground size-4 animate-spin" />}
        <Button variant="ghost" size="icon" aria-label="묶어 보기 닫기" onClick={onClose}>
          <X className="size-4" />
        </Button>
      </div>

      <ErrorNotice error={error} />

      {data && !error && (
        <div className="space-y-1">
          {data.buckets.length === 0 ? (
            <p className="text-muted-foreground text-sm">셀 것이 없습니다.</p>
          ) : (
            data.buckets.map((one) => {
              const value = data.metric === 'count' ? one.count : (one.value ?? 0)
              const canFilter = filterable(data.group_field) && one.key !== null
              return (
                <div key={one.key ?? '__empty__'} className="flex items-center gap-2 text-sm">
                  <button
                    type="button"
                    className={
                      canFilter
                        ? 'w-40 shrink-0 truncate text-left hover:underline'
                        : 'text-muted-foreground w-40 shrink-0 truncate text-left'
                    }
                    title={canFilter ? `${one.label}만 보기` : one.label}
                    disabled={!canFilter}
                    onClick={() => onPick(data.group_field, one.key)}
                  >
                    {one.label}
                  </button>
                  <div className="bg-muted h-4 flex-1 overflow-hidden rounded-sm">
                    <div
                      className="bg-primary/70 h-full"
                      style={{ width: `${Math.max(1, (value / widest) * 100)}%` }}
                    />
                  </div>
                  {/* 숫자는 막대 밖에 — 안에 넣으면 제일 작은 값이 제일 읽기 어려워진다. */}
                  <span className="w-24 shrink-0 text-right tabular-nums">
                    {shownNumber(value)}
                    {data.metric !== 'count' && (
                      <span className="text-muted-foreground ml-1 text-xs">({one.count}건)</span>
                    )}
                  </span>
                </div>
              )
            })
          )}
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
          </p>
        </div>
      )}
    </div>
  )
}
