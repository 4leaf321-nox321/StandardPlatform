/**
 * 인터페이스 — **여러 타입이 따르는 공통 모양**(ADR 0006).
 *
 * 그룹마다 「설비」 를 따로 정의하면 같은 개념이 키도 종류도 다르게 갈린다. 인터페이스에 공통
 * 속성을 한 번 정하고 타입이 그것을 **구현**하면, 구현 타입 전부가 같은 키 · 같은 모양의 속성을
 * 갖는다. 구현은 타입 창의 「구현 인터페이스」 에서 고른다.
 *
 * 행을 누르면 인터페이스 자체(이름 · 상위 인터페이스)를, 「공통 속성」 을 누르면 그 모양을
 * 고친다 — 타입 화면과 같은 갈림이다.
 */

import { useState } from 'react'
import { ChevronDown, Plus, Settings2 } from 'lucide-react'

import { InterfaceEditDialog } from '@/modules/ontology/InterfaceEditDialog'
import { useOntology } from '@/modules/ontology/OntologyLayout'
import { PropertyEditor } from '@/modules/ontology/PropertyEditor'
import { EmptyState } from '@/shared/components/EmptyState'
import { TypeIcon } from '@/shared/components/TypeIcon'
import { Button } from '@/shared/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { cn } from '@/shared/lib/utils'

export default function OntologyInterfacesPage() {
  const { schema, reload } = useOntology()
  const [editing, setEditing] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [openProperties, setOpenProperties] = useState<string | null>(null)

  const interfaces = schema?.interfaces ?? []
  const types = schema?.types ?? []
  const target = interfaces.find((row) => row.slug === editing) ?? null
  const propertyTarget = interfaces.find((row) => row.slug === openProperties) ?? null
  const labelOf = (slug: string) =>
    interfaces.find((one) => one.slug === slug)?.label ??
    types.find((one) => one.slug === slug)?.label ??
    slug

  return (
    <div className="space-y-4">
      <p className="text-muted-foreground text-sm">
        여러 타입이 따르는 <b>공통 모양</b>입니다. 공통 속성을 한 번 정하면 구현 타입 전부가{' '}
        <b>같은 키 · 같은 모양</b>의 속성을 갖습니다 — 그룹마다 따로 정의해 갈라지는 것을 막고, 그
        타입들을 한 번에 묻게 합니다.
      </p>

      <div className="flex justify-end">
        <Button size="sm" onClick={() => setCreating(true)}>
          <Plus className="mr-1 size-4" />
          인터페이스 생성
        </Button>
      </div>

      {interfaces.length === 0 ? (
        <EmptyState
          title="인터페이스가 없습니다"
          hint="여러 그룹이 같은 개념(설비 · 부품 …)을 따로 정의하고 있다면, 그 공통 속성을 담은 인터페이스를 하나 만들고 각 타입이 구현하게 하세요."
        />
      ) : (
        <>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>이름</TableHead>
                  <TableHead>slug</TableHead>
                  <TableHead>상위 인터페이스</TableHead>
                  <TableHead>구현 타입</TableHead>
                  <TableHead className="text-right">객체</TableHead>
                  <TableHead className="text-right">공통 속성</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {interfaces.map((row) => (
                  <TableRow
                    key={row.slug}
                    className={row.managed_by ? undefined : 'cursor-pointer'}
                    // 허브가 내려준 것은 여기서 안 고친다 — 허브에서 고친 뒤 받는다.
                    onClick={() => !row.managed_by && setEditing(row.slug)}
                  >
                    <TableCell className="font-medium">
                      <TypeIcon name={row.icon} className="mr-2 inline align-text-bottom" />
                      {row.label}
                      {row.managed_by && (
                        <span className="ml-2 rounded border px-1.5 text-xs">허브 관리</span>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{row.slug}</TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {row.extends_slugs.length > 0
                        ? row.extends_slugs.map(labelOf).join(' · ')
                        : '—'}
                    </TableCell>
                    <TableCell className="text-sm">
                      {/* **비어 있으면 말한다** — 아무도 안 따르는 인터페이스는 목록에서도 빈다. */}
                      {row.implementers.length > 0 ? (
                        row.implementers.map(labelOf).join(' · ')
                      ) : (
                        <span className="text-muted-foreground">아직 없음</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{row.object_count}</TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        aria-expanded={row.slug === openProperties}
                        onClick={(event) => {
                          event.stopPropagation()
                          setOpenProperties(row.slug === openProperties ? null : row.slug)
                        }}
                      >
                        <Settings2 className="mr-1 size-3.5" />
                        공통 속성 {row.properties.length}
                        <ChevronDown
                          className={cn(
                            'ml-1 size-3.5 transition-transform',
                            row.slug === openProperties && 'rotate-180',
                          )}
                        />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <p className="text-muted-foreground text-xs">
            <b>행</b>을 클릭하면 이름 · 상위 인터페이스를 수정하거나 삭제합니다.{' '}
            <b>「공통 속성」 단추</b>를 클릭하면 그 모양이 아래에 열립니다 — 여기서 고치면 구현
            타입들의 속성도 함께 바뀝니다.
          </p>
        </>
      )}

      {propertyTarget && (
        <section className="space-y-3">
          <h2 className="text-base font-semibold">{propertyTarget.label} 의 공통 속성</h2>
          <PropertyEditor
            owner={{ kind: 'interface', row: propertyTarget }}
            properties={propertyTarget.properties}
            types={types}
            interfaces={interfaces}
            onChanged={reload}
            readOnly={Boolean(propertyTarget.managed_by)}
          />
        </section>
      )}

      {(creating || target) && (
        <InterfaceEditDialog
          iface={target}
          interfaces={interfaces}
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
