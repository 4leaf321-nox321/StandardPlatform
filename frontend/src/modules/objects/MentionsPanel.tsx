/**
 * 이 객체를 언급한 문서(ADR 0053) — 문서 본문의 링크 `[이름](/o/<타입>/<id>)` 가 가리킨 것을 모은다.
 * 백서 · 위키 페이지가 온톨로지와 이어지는 자리다. 없으면 안 선다. 볼 수 있는 문서만.
 */

import { Link } from 'react-router-dom'
import { BookOpen } from 'lucide-react'

import { documentApi } from '@/modules/objects/documents'
import { useResource } from '@/shared/hooks/useResource'

export function MentionsPanel({
  typeSlug,
  objectId,
  reloadKey,
}: {
  typeSlug: string
  objectId: string
  /** 상세가 다시 읽힐 때 함께 — 문서를 고치면 언급이 바뀐다. */
  reloadKey?: unknown
}) {
  const list = useResource(
    () => documentApi.mentions(typeSlug, objectId),
    [typeSlug, objectId, reloadKey],
  )
  const rows = list.data ?? []
  if (rows.length === 0) return null
  return (
    <section className="space-y-2" aria-label="이 객체를 언급한 문서">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <BookOpen className="size-4" />이 객체를 언급한 문서
        <span className="text-muted-foreground font-normal tabular-nums">{rows.length}</span>
      </h2>
      <ul className="divide-y rounded-md border text-sm">
        {rows.map((one) => (
          <li key={`${one.object_id}:${one.key}`}>
            <Link
              to={`/o/${one.type_slug}/${one.object_id}`}
              className="hover:bg-muted/50 block px-3 py-2"
            >
              <span className="block truncate">{one.label}</span>
              <span className="text-muted-foreground block truncate text-xs">
                {one.type_label} · {one.property_label}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}
