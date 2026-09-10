/**
 * 타입 하나의 인스턴스 목록 — **화면이 정의에서 나온다.**
 *
 * 경로는 `/o/:typeSlug` 하나뿐이다. 타입이 늘어도 라우트는 안 늘어나므로
 * `navigation.ts` 가 정적 화면의 정본이라는 규칙과 `router.test.tsx` 가 그대로 선다.
 */

import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Plus } from 'lucide-react'

import { ontologyApi } from '@/modules/ontology/api'
import type { ObjectType, PropertyDef } from '@/modules/ontology/api'
import { objectApi } from '@/modules/objects/api'
import type { ObjectRow } from '@/modules/objects/api'
import { propertyText } from '@/modules/objects/PropertyFields'
import { ObjectCreateDialog } from '@/modules/objects/ObjectCreateDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Pagination } from '@/shared/components/Pagination'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

/** 「거르지 않음」 을 나타내는 값. **빈 문자열을 쓸 수 없다** — Select 가 빈 값을
 *  「고른 것 없음」 으로 보고 자리표시자로 돌아간다. */
const ALL = '__all__'

/** `list_view.columns` 를 안 정했을 때의 기본. **빈 화면이 되지는 않는다.** */
const FALLBACK_COLUMNS = ['key', 'label', 'updated_at']

interface Column {
  id: string
  label: string
  render: (row: ObjectRow) => string
}

function buildColumns(type: ObjectType, defs: PropertyDef[]): Column[] {
  const wanted = type.list_view?.columns?.length ? type.list_view.columns : FALLBACK_COLUMNS
  const byKey = new Map(defs.map((def) => [def.key, def]))

  return wanted
    .map((id): Column | null => {
      if (id === 'key') return { id, label: '식별자', render: (row) => row.key ?? '—' }
      if (id === 'label') return { id, label: '이름', render: (row) => row.label }
      if (id === 'status') return { id, label: '상태', render: (row) => row.status }
      if (id === 'updated_at')
        return { id, label: '고친 때', render: (row) => shownDateTime(row.updated_at) }
      if (id.startsWith('properties.')) {
        const key = id.slice('properties.'.length)
        const def = byKey.get(key)
        // **정의가 없는 열은 조용히 버린다.** 속성을 지운 뒤 list_view 에 이름이
        // 남아 있으면 빈 열이 서는데, 그 빈 열은 「값이 없다」 로 읽힌다.
        if (!def) return null
        return { id, label: def.label, render: (row) => propertyText(def, row.properties[key]) }
      }
      return null
    })
    .filter((column): column is Column => column !== null)
}

/** 속성 하나를 거르는 칸. 고를 것이 정해진 속성은 고르게 한다. */
function PropertyFilter({
  def,
  value,
  onChange,
}: {
  def: PropertyDef
  value: string
  onChange: (next: string) => void
}) {
  return (
    <div className="space-y-1.5">
      <span className="text-muted-foreground block text-xs">{def.label}</span>
      {def.data_type === 'enum' ? (
        <Select value={value || ALL} onValueChange={(next) => onChange(next === ALL ? '' : next)}>
          <SelectTrigger className="h-9 w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>전체</SelectItem>
            {(def.enum_options ?? []).map((option) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <Input
          value={value}
          className="h-9 w-36"
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </div>
  )
}

export default function ObjectListPage() {
  const { typeSlug = '' } = useParams()
  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [offset, setOffset] = useState(0)
  const [creating, setCreating] = useState(false)

  const schema = useResource(() => ontologyApi.schema(), [])
  const list = useResource(
    () => objectApi.list(typeSlug, { q: query || undefined, properties: filters, offset }),
    // filters 는 객체라 참조가 매번 바뀐다 — 내용으로 비교한다.
    [typeSlug, query, JSON.stringify(filters), offset],
  )

  const type = schema.data?.types.find((row) => row.slug === typeSlug)
  /** 지금 목록이 좁혀져 있나. 빈 목록의 이유가 이것으로 갈린다. */
  const narrowed = Boolean(query) || Object.keys(filters).length > 0
  const columns = useMemo(
    () => (type ? buildColumns(type, type.properties) : []),
    [type],
  )

  if (schema.error) return <ErrorNotice error={schema.error} />
  if (schema.data && !type) {
    return (
      <EmptyState
        title="없는 타입입니다"
        hint={
          <>
            <code>{typeSlug}</code> 이라는 타입이 정의돼 있지 않습니다. 온톨로지 관리에서
            만들거나 주소를 확인하세요.
          </>
        }
      />
    )
  }

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title={type?.label ?? '…'}
        description={type?.description || undefined}
        actions={
          type &&
          type.kind_class !== 'system' && (
            <Button size="sm" onClick={() => setCreating(true)}>
              <Plus className="mr-1 size-4" />
              만들기
            </Button>
          )
        }
      />

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Input
          value={query}
          placeholder="이름·식별자로 찾기"
          onChange={(event) => {
            setQuery(event.target.value)
            setOffset(0)
          }}
          className="max-w-xs"
        />

        {/* **거르기도 정의에서 나온다.** `list_view.filters` 가 가리키는 속성만
            선다 — 전부 세우면 속성이 스물인 타입에서 그 줄이 화면을 덮는다. */}
        {(type?.list_view?.filters ?? []).map((id) => {
          if (!id.startsWith('properties.')) return null
          const key = id.slice('properties.'.length)
          const def = type?.properties.find((one) => one.key === key)
          if (!def) return null
          return (
            <PropertyFilter
              key={key}
              def={def}
              value={filters[key] ?? ''}
              onChange={(next) => {
                setFilters((current) => {
                  const copy = { ...current }
                  // **빈 값은 「거르지 않음」 이다.** 빈 문자열로 걸면 값이 빈
                  // 행만 나오는데, 그것은 아무도 뜻한 적 없는 결과다.
                  if (next) copy[key] = next
                  else delete copy[key]
                  return copy
                })
                setOffset(0)
              }}
            />
          )
        })}
      </div>

      {list.error && <ErrorNotice error={list.error} />}

      {list.data && list.data.items.length === 0 ? (
        <EmptyState
          title={narrowed ? '거르기에 맞는 것이 없습니다' : '아직 아무것도 없습니다'}
          hint={
            /* **비어 있는 이유를 말한다.** 데이터가 없는 것인지, 거르기가 좁은
               것인지, 권한 때문인지는 해야 할 일이 전혀 다르다. */
            narrowed
              ? '찾는 말이나 거르기를 줄여 보세요. 다른 부서의 것은 여기 안 보입니다.'
              : '「만들기」 로 첫 항목을 넣거나, 다른 부서의 것이라면 그 부서 사람에게 물어보세요.'
          }
          action={
            narrowed ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setQuery('')
                  setFilters({})
                  setOffset(0)
                }}
              >
                거르기 지우기
              </Button>
            ) : undefined
          }
        />
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                {columns.map((column) => (
                  <TableHead key={column.id}>{column.label}</TableHead>
                ))}
                <TableHead>부서</TableHead>
                <TableHead>상태</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(list.data?.items ?? []).map((row) => (
                <TableRow key={row.id}>
                  {columns.map((column, index) => (
                    <TableCell key={column.id}>
                      {index === 0 ? (
                        <Link
                          className="font-medium hover:underline"
                          to={`/o/${typeSlug}/${row.id}`}
                        >
                          {column.render(row)}
                        </Link>
                      ) : (
                        column.render(row)
                      )}
                    </TableCell>
                  ))}
                  <TableCell className="text-muted-foreground">
                    {/* **NULL 은 전역이다.** 빈 칸으로 두면 「부서가 없다」 로 읽힌다. */}
                    {row.owner_workspace_slug ?? '전역'}
                  </TableCell>
                  <TableCell>
                    <StatusBadge kind="object" value={row.status} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {list.data && (
        <div className="mt-4">
          <Pagination
            total={list.data.total}
            limit={list.data.limit}
            offset={list.data.offset}
            onChange={setOffset}
          />
        </div>
      )}

      {type && creating && (
        <ObjectCreateDialog
          type={type}
          defs={type.properties}
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false)
            list.reload()
          }}
        />
      )}
    </div>
  )
}
