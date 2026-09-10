/**
 * 「관련 객체」 — **한 객체에서 사방을 본다.**
 *
 * 트리가 관계 **한 종류**로 구조를 훑는 도구라면, 여기는 이 객체에 걸린 것을
 * 종류별로 모아 보여 준다. 둘은 경쟁이 아니라 짝이다 — 트리로는 「이 부품을
 * 공급하는 곳」 이 안 보이고, 여기서는 「이 어셈블리 전체」 가 안 보인다.
 *
 * **양방향을 함께 보여 준다.** 「이것이 가리키는 것」 만 보이면 그 부품을 쓰는
 * 어셈블리를 못 찾고, 그러면 지워도 되는지 알 수 없다.
 */

import { useState } from 'react'
import { ArrowLeft, ArrowRight, Link2, Plus, X } from 'lucide-react'
import { Link } from 'react-router-dom'

import type { RelationType } from '@/modules/ontology/api'
import { objectApi } from '@/modules/objects/api'
import type { RelatedObject } from '@/modules/objects/api'
import { RelationAddDialog } from '@/modules/objects/RelationAddDialog'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'

interface Props {
  typeSlug: string
  objectId: string
  objectLabel: string
  objectTypeSlug: string
  related: RelatedObject[]
  relationTypes: RelationType[]
  canEdit: boolean
  onChanged: () => void
}

export function RelatedObjects({
  typeSlug,
  objectId,
  objectLabel,
  objectTypeSlug,
  related,
  relationTypes,
  canEdit,
  onChanged,
}: Props) {
  const [adding, setAdding] = useState(false)
  const [cutting, setCutting] = useState<RelatedObject | null>(null)
  const [error, setError] = useState<Error | null>(null)

  /** 관계 종류별로 묶는다 — **줄이 스물이면 종류별로 안 묶은 목록은 못 읽는다.** */
  const groups = new Map<string, RelatedObject[]>()
  for (const row of related) {
    const key = `${row.relation}:${row.outgoing ? 'out' : 'in'}`
    groups.set(key, [...(groups.get(key) ?? []), row])
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <Link2 className="size-4" />
          관련 객체
          {related.length > 0 && (
            <span className="text-muted-foreground text-sm font-normal tabular-nums">
              {related.length}
            </span>
          )}
        </h2>
        {canEdit && (
          <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
            <Plus className="mr-1 size-4" />
            잇기
          </Button>
        )}
      </div>

      {error && <ErrorNotice error={error} />}

      {related.length === 0 ? (
        <EmptyState
          title="이어진 것이 없습니다"
          hint={
            relationTypes.length === 0
              ? '관계 종류가 아직 정의되지 않았습니다 — 관리 → 온톨로지 → 관계 종류에서 먼저 만드세요.'
              : '「잇기」 로 다른 객체와 이어 보세요. 남의 부서 것은 여기 안 보입니다.'
          }
        />
      ) : (
        <div className="space-y-3">
          {[...groups.entries()].map(([key, rows]) => (
            <div key={key} className="rounded-md border">
              <div className="text-muted-foreground flex items-center gap-1.5 border-b px-3 py-1.5 text-xs">
                {/* **방향을 보여 준다.** 어느 쪽으로 읽는지가 말의 뜻을 바꾼다. */}
                {rows[0].outgoing ? (
                  <ArrowRight className="size-3.5" />
                ) : (
                  <ArrowLeft className="size-3.5" />
                )}
                {rows[0].label}
                <span className="tabular-nums">({rows.length})</span>
              </div>
              <ul className="divide-y">
                {rows.map((row) => (
                  <li key={row.relation_id} className="flex items-center gap-3 px-3 py-2">
                    <div className="min-w-0 flex-1">
                      <Link
                        className="text-sm font-medium hover:underline"
                        to={`/o/${row.object_type_slug}/${row.object_id}`}
                      >
                        {row.object_label}
                      </Link>
                      <span className="text-muted-foreground ml-2 text-xs">
                        {row.object_type_label}
                        {row.object_key && ` · ${row.object_key}`}
                      </span>
                      {/* **왜 이렇게 이었는가.** 근거 없는 연결은 시간이 지나면
                          아무도 못 믿는다 — 확인하려면 처음부터 다시 조사해야 한다. */}
                      {row.evidence_note && (
                        <p className="text-muted-foreground mt-0.5 text-xs">
                          {row.evidence_note}
                        </p>
                      )}
                    </div>
                    {canEdit && (
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`${row.object_label} 끊기`}
                        onClick={() => setCutting(row)}
                      >
                        <X className="size-4" />
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {adding && (
        <RelationAddDialog
          typeSlug={typeSlug}
          objectId={objectId}
          objectLabel={objectLabel}
          objectTypeSlug={objectTypeSlug}
          relationTypes={relationTypes}
          onClose={() => setAdding(false)}
          onAdded={() => {
            setAdding(false)
            onChanged()
          }}
        />
      )}

      {cutting && (
        <ConfirmDialog
          open
          destructive
          title={`${cutting.object_label} 와(과)의 관계를 끊습니다`}
          description={
            <>
              「{cutting.label}」 연결만 사라집니다. <b>양쪽 객체는 그대로 남습니다.</b>{' '}
              끊은 기록은 변경 이력에 남습니다.
            </>
          }
          confirmLabel="끊기"
          onConfirm={async () => {
            setError(null)
            try {
              await objectApi.removeRelation(typeSlug, objectId, cutting.relation_id)
              onChanged()
            } catch (caught) {
              setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
              throw caught
            }
          }}
          onClose={() => setCutting(null)}
        />
      )}
    </section>
  )
}
