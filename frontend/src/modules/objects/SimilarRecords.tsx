/**
 * 비슷한 기록(ADR 0022) — 이 기록의 축 태그(참조 칸)와 많이 겹치는 같은 타입의 기록.
 *
 * 드문 태그가 겹칠수록 위에 선다(무게 ln(N / 그 태그를 가진 기록 수)). 「왜 비슷한가」 를 겹친 태그로
 * 함께 적는다. 기록이 수백만인 타입에서도 공짜는 아니라 누를 때만 묻는다.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'

import { objectApi } from '@/modules/objects/api'
import type { Similar } from '@/modules/objects/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'

export function SimilarRecords({ typeSlug, objectId }: { typeSlug: string; objectId: string }) {
  const [data, setData] = useState<Similar | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  async function load() {
    setBusy(true)
    setError(null)
    try {
      setData(await objectApi.similar(typeSlug, objectId))
    } catch (caught) {
      setError(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-2 rounded-md border p-4" aria-label="비슷한 기록">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">비슷한 기록</h2>
        <Button size="sm" variant="outline" disabled={busy} onClick={load}>
          {data ? '다시 찾기' : '찾기'}
        </Button>
      </div>
      <p className="text-muted-foreground text-xs">
        축 태그(참조 칸)가 많이 겹치는 기록 — 드문 태그가 겹칠수록 위에 섭니다.
      </p>
      <ErrorNotice error={error} />
      {data && data.items.length === 0 && (
        <p className="text-muted-foreground text-sm">
          {data.query.length === 0
            ? '이 기록에는 축 태그가 없습니다.'
            : '태그가 겹치는 다른 기록이 없습니다.'}
        </p>
      )}
      {data && data.items.length > 0 && (
        <ul className="space-y-2">
          {data.items.map((one) => (
            <li key={one.id} className="text-sm">
              <Link to={`/o/${typeSlug}/${one.id}`} className="font-medium hover:underline">
                {one.label}
              </Link>{' '}
              <span className="text-muted-foreground text-xs">
                {Math.round(one.score * 100)}% ·{' '}
                {one.shared
                  .map((tag) => `${tag.field_label}: ${tag.value_label ?? '(보이지 않음)'}`)
                  .join(', ')}
                {one.extra > 0 && ` · 그 밖 ${one.extra}`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
