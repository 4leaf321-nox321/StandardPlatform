/**
 * 속성 정의 목록 — **타입의 속성과 인터페이스의 공통 속성이 같은 편집기를 쓴다**(ADR 0006).
 *
 * 두 벌로 두면 위젯이 갈리고, 갈린 것은 한쪽만 고쳐진다. 타입 쪽에서는 공통 속성 줄에
 * 「공통 · 인터페이스」 배지가 붙는다 — 그 줄의 모양은 인터페이스에서 고친다는 표시다.
 */

import { useState } from 'react'
import { Plus } from 'lucide-react'

import { PropertyBulkDialog } from '@/modules/ontology/PropertyBulkDialog'
import { DATA_TYPE_LABELS, PropertyEditDialog } from '@/modules/ontology/PropertyEditDialog'
import type { PropertyOwner } from '@/modules/ontology/PropertyEditDialog'
import type { ObjectInterface, ObjectType, PropertyDef } from '@/modules/ontology/api'
import { Button } from '@/shared/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'

export function PropertyEditor({
  owner,
  properties,
  types,
  interfaces = [],
  onChanged,
  readOnly = false,
}: {
  owner: PropertyOwner
  properties: PropertyDef[]
  /** 「객체 참조」 가 가리킬 수 있는 타입들. */
  types: ObjectType[]
  /** 「객체 참조」 가 가리킬 수 있는 인터페이스 — 그것을 구현한 타입의 객체를 고른다. */
  interfaces?: ObjectInterface[]
  onChanged: () => void
  /** 허브가 내려준 정의 — 속성을 보이기만 한다. */
  readOnly?: boolean
}) {
  const [editing, setEditing] = useState<PropertyDef | null>(null)
  const [creating, setCreating] = useState(false)
  const [bulk, setBulk] = useState(false)
  const isInterface = owner.kind === 'interface'
  const noun = isInterface ? '공통 속성' : '속성'

  return (
    <section className="space-y-3 rounded-md border p-4">
      {properties.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          {isInterface ? (
            <>
              아직 공통 속성이 없습니다. 하나 정의하면{' '}
              <b>구현 타입 전부에 같은 키의 속성이 생깁니다.</b>
            </>
          ) : (
            <>
              아직 속성이 없습니다. 하나 정의하면{' '}
              <b>{owner.row.label} 생성·상세 화면의 폼에 칸이 생깁니다.</b>
            </>
          )}
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>이름</TableHead>
              <TableHead>키</TableHead>
              <TableHead>종류</TableHead>
              <TableHead>단위</TableHead>
              <TableHead>필수</TableHead>
              <TableHead>여러 값</TableHead>
              <TableHead className="text-right">순서</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {properties.map((def) => (
              <TableRow
                key={def.key}
                className={readOnly ? undefined : 'cursor-pointer'}
                onClick={() => !readOnly && setEditing(def)}
              >
                <TableCell className="font-medium">
                  {def.label}
                  {def.interface_slug && (
                    <span
                      className="ml-2 rounded border border-sky-500/50 bg-sky-500/10 px-1.5 text-xs"
                      title="모양(종류 · 선택할 값 · 규칙)은 이 인터페이스에서 수정합니다"
                    >
                      공통 · {def.interface_slug}
                    </span>
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs">{def.key}</TableCell>
                <TableCell>{DATA_TYPE_LABELS[def.data_type]}</TableCell>
                <TableCell className="text-muted-foreground">{def.unit || '—'}</TableCell>
                <TableCell>{def.required ? '예' : '—'}</TableCell>
                <TableCell>{def.multi ? '예' : '—'}</TableCell>
                <TableCell className="text-right tabular-nums">{def.sort_order}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <div className="flex items-center justify-between gap-3 border-t pt-3">
        <p className="text-muted-foreground text-xs">
          {readOnly
            ? '허브가 내려준 정의라 여기서 속성을 수정하지 않습니다 — 허브에서 수정한 뒤 받습니다.'
            : `${properties.length > 0 ? '행을 클릭하면 이름·단위·안내·필수·여러 값을 수정하거나 삭제합니다. ' : ''}키와 종류는 만들 때만 정합니다.`}
        </p>
        {!readOnly && (
          <div className="flex gap-2">
            {/* 타입을 세울 때 속성은 대여섯 개씩 함께 온다 — 창을 열 번 여닫게 하지 않는다. */}
            {owner.kind === 'type' && (
              <Button size="sm" variant="outline" onClick={() => setBulk(true)}>
                <Plus className="mr-1 size-4" />
                여러 속성 추가
              </Button>
            )}
            <Button size="sm" onClick={() => setCreating(true)}>
              <Plus className="mr-1 size-4" />
              {noun} 추가
            </Button>
          </div>
        )}
      </div>

      {bulk && owner.kind === 'type' && (
        <PropertyBulkDialog
          type={{ ...owner.row, properties }}
          onClose={() => setBulk(false)}
          onChanged={onChanged}
        />
      )}

      {(creating || editing) && (
        <PropertyEditDialog
          owner={owner}
          property={editing}
          types={types}
          interfaces={interfaces}
          onClose={() => {
            setCreating(false)
            setEditing(null)
          }}
          onChanged={onChanged}
        />
      )}
    </section>
  )
}
