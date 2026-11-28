/**
 * 정의 점검 — **삭제하거나 묶을 후보**(ADR 0092).
 *
 * AI 가 넣은 정의는 쌓이기만 하고 줄지 않는다. 「어디부터 채우나」 는 빈 칸을 채울 곳으로
 * 보이지만, 아무도 채우지 않고 아무 데도 사용하지 않는 속성 · 따로 있는 같은 속성 · 오래 빈
 * 타입은 그 화면에서 보이지 않는다. 여기서 한 번에 본다.
 *
 * **아무것도 변경하지 않는다.** 줄마다 까닭을 적고, 삭제는 줄의 「삭제」 가 여는 미리 보기
 * (서버가 막는 것 · 함께 사라지는 것을 센다)를 거친다. 묶기 · 종류 변경은 각 정의 화면에서 한다.
 */

import { useState } from 'react'
import { RefreshCw } from 'lucide-react'

import { useOntology } from '@/modules/ontology/OntologyLayout'
import { PlanDeleteDialog } from '@/modules/ontology/PlanDeleteDialog'
import { TypeDeleteDialog } from '@/modules/ontology/TypeDeleteDialog'
import { ontologyApi } from '@/modules/ontology/api'
import type { CheckItem } from '@/modules/ontology/api'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'

/** 종류마다 한 줄 — 무엇을 후보로 세었나. */
const KIND_NOTE: Record<CheckItem['kind'], string> = {
  unused_field:
    '객체가 있는데 값이 있는 객체가 없고, 필수 · 지표 · 뷰 · 코어 공개 · 데이터 소스 어디에도 사용하지 않는 속성입니다.',
  interface_candidate:
    '같은 키 · 같은 종류의 속성이 여러 타입에 따로 있습니다. 같은 개념이면 인터페이스로 묶어 한 번에 정의 · 검색합니다.',
  field_mismatch:
    '같은 키인데 타입마다 종류 · 단위가 다릅니다. 함께 세거나 인터페이스로 묶을 수 없습니다 — 같은 뜻이면 종류 변경으로 맞춥니다.',
  unused_option: '객체가 20건 넘는 타입에서 선택한 객체가 없는 선택 값입니다.',
  empty_type: '만든 지 30일이 넘었는데 객체가 하나도 없는 타입입니다.',
  unused_relation: '만든 지 30일이 넘었는데 맺어진 관계가 하나도 없는 관계 종류입니다.',
  undescribed_core:
    '외부에 공개 중인데 설명이 없는 타입입니다. 수신 시스템은 이름만으로 뜻을 짐작합니다.',
}

export default function OntologyCheckPage() {
  const { reload: reloadSchema } = useOntology()
  const found = useResource(() => ontologyApi.check(), [])
  const [deleting, setDeleting] = useState<CheckItem | null>(null)

  function done() {
    setDeleting(null)
    found.reload()
    reloadSchema()
  }

  if (found.error) return <ErrorNotice error={found.error} />
  const data = found.data
  if (!data) return <p className="text-muted-foreground text-sm">점검 중…</p>
  const kinds = Object.keys(data.kinds) as CheckItem['kind'][]

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="text-muted-foreground max-w-3xl text-sm">
          삭제하거나 묶을 <b>후보</b>입니다 — 이 화면은 아무것도 변경하지 않습니다. 줄마다 까닭을
          확인하고, 삭제는 미리 보기에서 막는 것과 함께 사라지는 것을 확인한 뒤 진행합니다.
        </p>
        <Button size="sm" variant="outline" disabled={found.loading} onClick={found.reload}>
          <RefreshCw className="mr-1 size-4" />
          다시 점검
        </Button>
      </div>

      {data.items.length === 0 ? (
        <EmptyState title="정리할 후보가 없습니다" />
      ) : (
        <>
          <div className="flex flex-wrap gap-2" aria-label="종류별 수">
            {kinds
              .filter((kind) => data.counts[kind])
              .map((kind) => (
                <a key={kind} href={`#check-${kind}`}>
                  <Badge variant="outline">
                    {data.kinds[kind]} {data.counts[kind]}
                  </Badge>
                </a>
              ))}
          </div>
          {kinds
            .filter((kind) => data.counts[kind])
            .map((kind) => (
              <section key={kind} id={`check-${kind}`} className="space-y-2">
                <h2 className="text-base font-semibold">
                  {data.kinds[kind]}{' '}
                  <span className="text-muted-foreground font-normal">{data.counts[kind]}</span>
                </h2>
                <p className="text-muted-foreground text-sm">{KIND_NOTE[kind]}</p>
                <ul className="divide-y rounded-md border">
                  {data.items
                    .filter((one) => one.kind === kind)
                    .map((one) => (
                      <Row
                        key={`${one.kind}:${one.slug}:${one.key ?? ''}`}
                        item={one}
                        onDelete={() => setDeleting(one)}
                      />
                    ))}
                </ul>
              </section>
            ))}
        </>
      )}

      {data.notes.length > 0 && (
        <ul className="text-muted-foreground list-disc space-y-1 pl-5 text-xs">
          {data.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}

      {deleting?.action === 'delete_type' && (
        <TypeDeleteDialog
          type={{ slug: deleting.slug, label: deleting.label }}
          onDone={done}
          onClose={() => setDeleting(null)}
        />
      )}
      {(deleting?.action === 'delete_property' || deleting?.action === 'delete_relation') && (
        <PlanDeleteDialog
          kind={deleting.action === 'delete_property' ? 'property' : 'relation_type'}
          slug={deleting.slug}
          propertyKey={deleting.key ?? undefined}
          label={deleting.label}
          onDone={done}
          onClose={() => setDeleting(null)}
        />
      )}
    </div>
  )
}

function Row({ item, onDelete }: { item: CheckItem; onDelete: () => void }) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 p-3">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{item.label}</span>
          {item.estimated && <Badge variant="secondary">어림</Badge>}
          {item.managed_by && <Badge variant="outline">{item.managed_by} 관리</Badge>}
        </div>
        <p className="text-muted-foreground text-sm">{item.reason}</p>
        {item.types.length > 0 && (
          <p className="text-muted-foreground font-mono text-xs break-all">
            {item.types.join(' · ')}
          </p>
        )}
      </div>
      {item.action && (
        <Button size="sm" variant="outline" onClick={onDelete}>
          삭제
        </Button>
      )}
    </li>
  )
}
