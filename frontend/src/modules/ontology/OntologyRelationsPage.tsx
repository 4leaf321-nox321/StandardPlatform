/**
 * 관계 종류 — **엮는 법을 정하는 자리.**
 *
 * 타입이 「무엇이 있나」 를 정하고, 여기가 「그것들이 어떻게 엮이나」 를 정한다.
 * 관계를 실제로 맺는 것은 객체 상세(2-b)에서 한다.
 */

import { useState } from 'react'
import { ArrowLeftRight, ArrowRight, Plus } from 'lucide-react'

import { useOntology } from '@/modules/ontology/OntologyLayout'
import {
  CARDINALITY_LABELS,
  RelationTypeEditDialog,
} from '@/modules/ontology/RelationTypeEditDialog'
import type { RelationType } from '@/modules/ontology/api'
import { EmptyState } from '@/shared/components/EmptyState'
import { Button } from '@/shared/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'

export default function OntologyRelationsPage() {
  const { schema, reload } = useOntology()
  const [editing, setEditing] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const relations = schema?.relation_types ?? []
  const types = schema?.types ?? []
  const target = relations.find((row) => row.slug === editing) ?? null

  /** 「부품 → 공급사」 처럼 무엇과 무엇을 잇는지. 비어 있으면 제약이 없다는 뜻이다. */
  function endpoints(row: RelationType): string {
    const label = (slugs: string[] | null) =>
      slugs && slugs.length > 0
        ? slugs.map((s) => types.find((t) => t.slug === s)?.label ?? s).join('·')
        : '아무 타입'
    return `${label(row.src_type_slugs)} → ${label(row.dst_type_slugs)}`
  }

  return (
    <div className="space-y-4">
      <p className="text-muted-foreground text-sm">
        객체들이 <b>어떻게 엮이는지</b>를 정합니다. 여기서 정한 의미(방향·재귀·개수 제약·
        허용 타입)를 화면과 MCP 가 함께 읽습니다 — <b>코드에 숨겨 두면 둘 다 그 뜻을 알 수
        없습니다.</b>
      </p>

      <div className="flex justify-end">
        <Button size="sm" onClick={() => setCreating(true)}>
          <Plus className="mr-1 size-4" />
          관계 종류 만들기
        </Button>
      </div>

      {relations.length === 0 ? (
        <EmptyState
          title="관계 종류가 없습니다"
          hint="「속함」(트리를 만드는 관계) 하나부터 만들어 보세요. 재귀로 펼치는 관계라야 목록 왼쪽에 트리가 섭니다."
        />
      ) : (
        <>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>이름</TableHead>
                  <TableHead>slug</TableHead>
                  <TableHead>잇는 것</TableHead>
                  <TableHead>개수</TableHead>
                  <TableHead>성질</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {relations.map((row) => (
                  <TableRow
                    key={row.slug}
                    className="cursor-pointer"
                    onClick={() => setEditing(row.slug)}
                  >
                    <TableCell className="font-medium">
                      <span className="flex items-center gap-1.5">
                        {row.label}
                        {row.directed ? (
                          <ArrowRight className="text-muted-foreground size-3.5" />
                        ) : (
                          <ArrowLeftRight className="text-muted-foreground size-3.5" />
                        )}
                        <span className="text-muted-foreground">
                          {row.inverse_label || '(역방향 이름 없음)'}
                        </span>
                      </span>
                      {!row.is_active && (
                        <span className="text-muted-foreground ml-2 text-xs">사용 안 함</span>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{row.slug}</TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {endpoints(row)}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {CARDINALITY_LABELS[row.cardinality]}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {/* **트리가 이것으로 갈린다.** 성질을 안 보여 주면 「왜 이건
                          트리가 안 되지」 를 물을 자리가 없다. */}
                      {[row.transitive && '재귀', row.acyclic && '순환 금지']
                        .filter(Boolean)
                        .join(' · ') || '—'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <p className="text-muted-foreground text-xs">
            행을 누르면 고치거나 지웁니다. <b>「재귀」 인 관계라야</b> 목록 왼쪽에 트리를
            세울 수 있습니다.
          </p>
        </>
      )}

      {(creating || target) && (
        <RelationTypeEditDialog
          relation={target}
          types={types}
          onClose={() => {
            setCreating(false)
            setEditing(null)
          }}
          onChanged={reload}
        />
      )}
    </div>
  )
}
