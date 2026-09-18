/**
 * 검색 — **타입을 모르는 사람이 서는 자리.**
 *
 * 목록은 타입마다 따로다. 그런데 무엇을 찾을 때 그것이 어느 타입인지 아는 경우는
 * 드물다 — 알았다면 이미 그 목록에 가 있었을 것이다.
 *
 * ## 주소가 곧 상태다
 *
 * `?q=` 와 `?type=` 이 주소에 남는다. 찾은 화면을 그대로 복사해 보낼 수 있고,
 * 뒤로가기가 이전 검색으로 돌아간다. 헤더의 칸이 결과를 드롭다운에 떨구지 않고
 * 이 화면으로 넘기는 이유가 그것이다.
 *
 * ## 왜 걸렸는지 함께 보여 준다
 *
 * 「Ansys」 를 쳤는데 이름에 그 글자가 없는 줄이 나오면 사람은 그것을 오류로 읽는다.
 * 별칭으로 걸렸다면 그 별칭을 적는다 — 그러면 같은 결과가 답이 된다.
 */

import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Loader2, Search } from 'lucide-react'

import { searchApi } from '@/modules/search/api'
import type { SearchResult } from '@/modules/search/api'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Pagination } from '@/shared/components/Pagination'
import { TypeIcon } from '@/shared/components/TypeIcon'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { cn } from '@/shared/lib/utils'

const MATCHED: Record<string, string> = {
  label: '이름',
  key: '식별자',
  alias: '별칭',
}

export default function SearchPage() {
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const type = params.get('type')
  const offset = Number(params.get('offset') ?? 0)

  // 칸에 친 글자는 따로 든다 — 한 글자 칠 때마다 주소를 바꾸면 뒤로가기가
  // 글자 수만큼 쌓여 이전 검색으로 못 돌아간다.
  const [typed, setTyped] = useState(q)
  const [data, setData] = useState<SearchResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => setTyped(q), [q])

  useEffect(() => {
    if (!q) {
      setData(null)
      return
    }
    let cancelled = false
    setLoading(true)
    searchApi
      .find(q, { type, offset })
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
  }, [q, type, offset])

  function go(next: { q?: string; type?: string | null; offset?: number }) {
    const copy = new URLSearchParams()
    const word = next.q ?? q
    if (word) copy.set('q', word)
    const narrowed = next.type === undefined ? type : next.type
    if (narrowed) copy.set('type', narrowed)
    if (next.offset) copy.set('offset', String(next.offset))
    setParams(copy)
  }

  const tooShort = Boolean(q) && data !== null && q.length < data.min_query

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="검색"
        description="타입을 가리지 않고 이름·식별자·별칭으로 찾습니다. 볼 수 있는 것만 나옵니다."
      />

      <form
        className="mb-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          go({ q: typed, type: null, offset: 0 })
        }}
      >
        <div className="relative flex-1">
          <Search className="text-muted-foreground absolute top-1/2 left-2 size-4 -translate-y-1/2" />
          <Input
            autoFocus
            className="pl-8"
            value={typed}
            placeholder="이름·식별자·별칭"
            aria-label="찾을 말"
            onChange={(event) => setTyped(event.target.value)}
          />
        </div>
        <Button type="submit" disabled={loading}>
          {loading ? <Loader2 className="size-4 animate-spin" /> : '검색'}
        </Button>
      </form>

      <ErrorNotice error={error} />

      {data && data.types.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          {/* **타입마다 몇 건인지가 곧 좁히는 단추다.** 상한에 걸렸을 때 나머지가
              어디 있는지 이것만이 말해 준다. */}
          <Button
            variant={type ? 'outline' : 'secondary'}
            size="sm"
            onClick={() => go({ type: null, offset: 0 })}
          >
            전체 {data.total}
          </Button>
          {data.types.map((one) => (
            <Button
              key={one.type_slug}
              variant={type === one.type_slug ? 'secondary' : 'outline'}
              size="sm"
              onClick={() => go({ type: one.type_slug, offset: 0 })}
            >
              <TypeIcon name={one.icon} />
              {one.type_label} {one.count}
            </Button>
          ))}
        </div>
      )}

      {!q ? (
        <EmptyState
          title="무엇을 찾으시나요"
          hint="이름 일부, 식별자, 또는 다른 시스템에서 사용하던 코드를 입력해 보세요. 타입은 선택하지 않아도 됩니다."
        />
      ) : tooShort ? (
        <EmptyState
          title={`${data?.min_query ?? 2}글자 이상 쳐 주세요`}
          hint="한 글자로는 거의 모든 것이 걸려 검색이 아니라 목록이 됩니다."
        />
      ) : data && data.items.length === 0 ? (
        <EmptyState
          title="맞는 것이 없습니다"
          hint="다른 말로 찾아 보세요. 남의 부서 것은 여기 안 보입니다 — 그 부서 사람에게 물어야 합니다."
        />
      ) : (
        <ul className="divide-y rounded-md border">
          {(data?.items ?? []).map((one) => (
            <li key={`${one.type_slug}:${one.id}`}>
              <Link
                to={`/o/${one.type_slug}/${one.id}`}
                className="hover:bg-muted/50 flex items-center gap-3 px-3 py-2"
              >
                <TypeIcon name={one.icon} className="shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{one.label}</span>
                  <span className="text-muted-foreground block truncate text-xs">
                    {one.type_label}
                    {one.key && <span className="ml-2 font-mono">{one.key}</span>}
                  </span>
                </span>
                {/* 이름에 없는 말로 걸린 줄은 **왜 걸렸는지**를 오른쪽에 적는다. */}
                {one.matched !== 'label' && (
                  <span
                    className={cn(
                      'text-muted-foreground shrink-0 text-xs',
                      one.matched === 'alias' && 'text-amber-700 dark:text-amber-400',
                    )}
                  >
                    {MATCHED[one.matched] ?? one.matched}
                    {one.matched_text && `: ${one.matched_text}`}
                  </span>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}

      {/* **쪽 넘기기는 좁혔을 때만.** 섞어 볼 때는 타입마다 따로 세고 투영 타입은
          첫 쪽에만 얹으므로, 여기에 쪽을 붙이면 「몇 건 중 몇 건」 이 거짓말이 된다 —
          대신 좁히라고 말한다. */}
      {data && data.items.length > 0 && type && (
        <Pagination
          total={data.total}
          limit={data.limit}
          offset={offset}
          onChange={(next) => go({ offset: next })}
        />
      )}
      {data && !type && data.total > data.items.length && (
        <p className="text-muted-foreground mt-3 text-sm">
          {data.total.toLocaleString()}건 중 {data.items.length}건을 보고 있습니다. 위에서 타입을
          골라 좁히면 나머지까지 쪽을 넘겨 볼 수 있습니다.
        </p>
      )}
    </div>
  )
}
