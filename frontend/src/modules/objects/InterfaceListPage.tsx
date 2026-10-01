/**
 * 인터페이스 목록 — **구현 타입 전부의 객체를 한 목록으로**(ADR 0006).
 *
 * 「설비」 를 열면 시험장비 · 계측기 · 생산설비가 한 목록에 선다. 구현 타입은 공통 속성을 같은 키 ·
 * 같은 모양으로 가지므로 조건 · 검색 · 통계가 그대로 걸린다. **줄마다 제 타입을 말하고**, 누르면
 * 그 타입의 상세로 간다.
 *
 * **읽기만 한다.** 만들기 · 일괄 입력 · 트리 · 저장된 뷰 · 내보내기는 타입의 일이다 — 어느 타입에
 * 넣을지 모르는 채로 받으면 짐작이 된다. 라우트는 늘리지 않는다(`/o/:typeSlug` 가 둘을 받는다).
 */

import { Suspense, lazy, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { BarChart3 } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import type { Condition, LinkedField, ObjectQuery, ObjectRow } from '@/modules/objects/api'
import { ConditionBar } from '@/modules/objects/ConditionBar'
import { propertyText } from '@/modules/objects/PropertyFields'
import { DEFAULT_SUMMARY } from '@/modules/objects/summarySettings'
import type { SummarySettings } from '@/modules/objects/summarySettings'
import { conditionsFromParams, withConditions } from '@/modules/objects/urlConditions'
import type { ObjectInterface, ObjectType, PropertyDef } from '@/modules/ontology/api'
import { commonProperties, defaultInterfaceColumns } from '@/modules/ontology/interfaces'
import type { InterfaceWithProperties } from '@/modules/ontology/interfaces'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Pagination } from '@/shared/components/Pagination'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { TypeIcon } from '@/shared/components/TypeIcon'
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

/** 통계는 누를 때 받는다 — 차트 라이브러리를 목록 덩어리에 싣지 않는다(타입 목록과 같다). */
const SummaryPanel = lazy(() =>
  import('@/modules/objects/SummaryPanel').then((mod) => ({ default: mod.SummaryPanel })),
)

const ALL = '__all__'
const NO_LINKED: LinkedField[] = []
const YEAR_OPTIONS = Array.from({ length: 11 }, (_, index) => new Date().getFullYear() - index)

interface Column {
  id: string
  label: string
  render: (row: ObjectRow) => string
}

/** 열 — `list_view.columns` 가 정했으면 그대로, 아니면 `defaultInterfaceColumns`. */
function columnsOf(
  iface: ObjectInterface,
  defs: PropertyDef[],
  typeLabel: (slug: string) => string,
): Column[] {
  const wanted = iface.list_view?.columns?.length
    ? iface.list_view.columns
    : defaultInterfaceColumns(defs)
  const byKey = new Map(defs.map((def) => [def.key, def]))
  return wanted
    .map((id): Column | null => {
      if (id === 'type') return { id, label: '타입', render: (row) => typeLabel(row.type_slug) }
      if (id === 'label') return { id, label: '이름', render: (row) => row.label }
      if (id === 'key') return { id, label: '식별자', render: (row) => row.key ?? '—' }
      if (id === 'status') return { id, label: '상태', render: (row) => row.status }
      if (id === 'updated_at')
        return { id, label: '수정한 때', render: (row) => shownDateTime(row.updated_at) }
      if (id.startsWith('properties.')) {
        const def = byKey.get(id.slice('properties.'.length))
        // 정의가 없는 열은 버린다 — 빈 열은 「값이 없다」 로 읽힌다(타입 목록과 같다).
        if (!def) return null
        return {
          id,
          label: def.label,
          render: (row) => propertyText(def, row.properties[def.key], row.ref_labels),
        }
      }
      return null
    })
    .filter((column): column is Column => column !== null)
}

export function InterfaceListPage({
  iface,
  interfaces,
  types,
}: {
  iface: InterfaceWithProperties
  /** 정의된 인터페이스 전부 — 상위 인터페이스의 공통 속성을 여기서 찾는다. */
  interfaces: InterfaceWithProperties[]
  /** 정의된 타입 전부 — 구현 타입의 이름을 여기서 찾는다. */
  types: ObjectType[]
}) {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  // 주소가 곧 상태다 — 타입 목록과 같은 규칙(`urlConditions`)으로 읽는다.
  const query = params.get('q') ?? ''
  const conditions = useMemo(() => conditionsFromParams(params), [params])
  const chosen = useMemo(() => (params.get('types') ?? '').split('|').filter(Boolean), [params])
  const [offset, setOffset] = useState(0)
  const [grouping, setGrouping] = useState(false)
  // 여러 타입이 섞인 목록에서 처음 궁금한 것은 「어느 타입이 몇 건」 이다.
  const [summary, setSummary] = useState<SummarySettings>({ ...DEFAULT_SUMMARY, groupBy: 'type' })

  const implementers = types.filter((one) => iface.implementers.includes(one.slug))
  /**
   * **올해가 기본이다**(타입 목록과 같다). 구현 타입 중 하나라도 연도를 쓰면 선다 — 서버가
   * 타입마다 제 시간 정책대로 거르고, 상시 타입은 연도와 상관없이 늘 나온다.
   */
  const yearApplies = implementers.some((one) => one.temporal_kind !== 'evergreen')
  const [year, setYear] = useState<number | null>(new Date().getFullYear())

  const defs = useMemo(() => commonProperties(iface, interfaces), [iface, interfaces])
  const columns = useMemo(() => {
    const labels = new Map(types.map((one) => [one.slug, one.label]))
    return columnsOf(iface, defs, (slug) => labels.get(slug) ?? slug)
  }, [iface, defs, types])

  const sync = (next: { q?: string; conditions?: Condition[]; types?: string[] }) => {
    const copy = withConditions(params, {
      q: next.q ?? query,
      conditions: next.conditions ?? conditions,
    })
    const narrow = next.types ?? chosen
    if (narrow.length) copy.set('types', narrow.join('|'))
    else copy.delete('types')
    setParams(copy, { replace: true })
    setOffset(0)
  }

  /** 지금 거른 것 — 목록과 통계가 같은 것을 말한다. */
  const filters: ObjectQuery = {
    q: query || undefined,
    conditions,
    types: chosen,
    year: yearApplies ? year : null,
  }
  const linked = useResource(() => objectApi.fields(iface.slug), [iface.slug])
  const list = useResource(
    // 구현 타입이 없으면 묻지 않는다 — 답은 늘 0건이고, 화면은 그 이유를 따로 말한다.
    () =>
      implementers.length > 0
        ? objectApi.list(iface.slug, { ...filters, offset })
        : Promise.resolve(null),
    // conditions 는 배열이라 참조가 매번 바뀐다 — 내용으로 비교한다.
    [iface.slug, query, JSON.stringify(conditions), chosen.join('|'), year, yearApplies, offset],
  )
  const refLabelsOfList = useMemo(() => {
    const out: Record<string, string> = {}
    for (const row of list.data?.items ?? []) Object.assign(out, row.ref_labels)
    return out
  }, [list.data])
  const narrowed =
    Boolean(query) || conditions.length > 0 || chosen.length > 0 || (yearApplies && year !== null)

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <TypeIcon name={iface.icon} className="size-5" />
            {iface.label}
            <span className="text-muted-foreground rounded border px-1.5 text-xs font-normal">
              인터페이스
            </span>
          </span>
        }
        description={
          iface.description ||
          `구현 타입 ${implementers.length}개의 객체를 한 목록으로 봅니다. 만들고 고치는 것은 각 타입의 목록에서 합니다.`
        }
        actions={
          implementers.length > 0 && (
            <Button
              size="sm"
              variant={grouping ? 'secondary' : 'outline'}
              onClick={() => setGrouping((before) => !before)}
            >
              <BarChart3 className="mr-1 size-4" />
              통계
            </Button>
          )
        }
      />

      {implementers.length === 0 ? (
        /* 「객체가 없다」 와 「담을 타입이 없다」 는 할 일이 다르다. */
        <EmptyState
          title="이 인터페이스를 구현한 타입이 없습니다"
          hint="관리 › 온톨로지 › 타입에서 타입을 열고 「구현 인터페이스」 에 이것을 고르면, 그 타입의 객체가 여기 섭니다."
        />
      ) : (
        <>
          {/* 타입 좁히기 — 아무것도 안 고르면 구현 타입 전부다. 이름을 누르면 그 타입의 목록으로. */}
          <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
            <span className="text-muted-foreground">구현 타입</span>
            {implementers.map((one) => {
              const on = chosen.includes(one.slug)
              return (
                <span key={one.slug} className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    className="size-4"
                    aria-label={`${one.label}만 보기`}
                    checked={on}
                    onChange={() =>
                      sync({
                        types: on
                          ? chosen.filter((slug) => slug !== one.slug)
                          : [...chosen, one.slug],
                      })
                    }
                  />
                  <Link to={`/o/${one.slug}`} className="hover:underline">
                    {one.label}
                  </Link>
                </span>
              )
            })}
            {chosen.length === 0 && <span className="text-muted-foreground text-xs">(전부)</span>}
          </div>

          <div className="mb-4 flex flex-wrap items-end gap-3">
            <Input
              value={query}
              placeholder="이름·식별자로 검색"
              onChange={(event) => sync({ q: event.target.value })}
              className="max-w-xs"
            />
            {yearApplies && (
              <div className="flex items-end gap-2 pb-0.5">
                <Select
                  value={year === null ? ALL : String(year)}
                  onValueChange={(next) => {
                    setYear(next === ALL ? null : Number(next))
                    setOffset(0)
                  }}
                >
                  <SelectTrigger className="h-9 w-32" aria-label="연도">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ALL}>전체 연도</SelectItem>
                    {YEAR_OPTIONS.map((one) => (
                      <SelectItem key={one} value={String(one)}>
                        {one}년
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>

          {/* 조건은 공통 속성에만 — 한 타입에만 있는 칸은 그 타입의 목록에서 건다. */}
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <ConditionBar
              defs={defs}
              conditions={conditions}
              onChange={(next) => sync({ conditions: next })}
              refLabels={refLabelsOfList}
              linked={linked.data ?? NO_LINKED}
            />
          </div>

          {grouping && (
            <div className="mb-4">
              <Suspense
                fallback={
                  <p className="text-muted-foreground rounded-md border p-4 text-sm">
                    통계를 준비하는 중…
                  </p>
                }
              >
                <SummaryPanel
                  typeSlug={iface.slug}
                  query={filters}
                  settings={summary}
                  onSettings={setSummary}
                  onClose={() => setGrouping(false)}
                  // 홈에 거는 위젯은 저장된 뷰로 선다 — 인터페이스 목록에는 뷰가 없다.
                  pinnable={false}
                  onPick={(field, key) => {
                    if (key === null || field === 'status') return
                    // **누른 막대가 곧 필터다** — 타입 막대는 타입 좁히기로.
                    if (field === 'type') {
                      const slug = implementers.find((one) => one.id === key)?.slug
                      if (slug) sync({ types: [slug] })
                      return
                    }
                    const propertyKey = field.startsWith('properties.')
                      ? field.slice('properties.'.length)
                      : field
                    const rest = conditions.filter(
                      (one) => !(one.field === propertyKey && one.op === 'eq'),
                    )
                    sync({ conditions: [...rest, { field: propertyKey, op: 'eq', value: key }] })
                  }}
                />
              </Suspense>
            </div>
          )}

          {list.error && <ErrorNotice error={list.error} />}

          {list.data && list.data.items.length === 0 ? (
            <EmptyState
              title={narrowed ? '필터에 맞는 것이 없습니다' : '아직 아무것도 없습니다'}
              hint={
                narrowed
                  ? yearApplies && year !== null
                    ? `${year}년에 해당하는 것이 없습니다. 「전체 연도」 로 바꾸거나 필터를 줄여 보세요.`
                    : '찾는 말 · 조건 · 타입 좁히기를 줄여 보세요. 다른 부서의 것은 여기 안 보입니다.'
                  : '구현 타입들에 아직 객체가 없습니다. 각 타입의 목록에서 만듭니다.'
              }
              action={
                narrowed ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      sync({ q: '', conditions: [], types: [] })
                      setYear(null)
                    }}
                  >
                    필터 삭제
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
                    /* **줄은 제 타입의 상세로 간다** — 인터페이스에는 상세가 없다. */
                    <TableRow
                      key={row.id}
                      className="hover:bg-muted/50 cursor-pointer"
                      onClick={(event) => {
                        if ((event.target as HTMLElement).closest('a,button,input,label')) return
                        if (window.getSelection()?.toString()) return
                        navigate(`/o/${row.type_slug}/${row.id}`)
                      }}
                    >
                      {columns.map((column, index) => (
                        <TableCell key={column.id}>
                          {index === 0 ? (
                            <Link
                              className="font-medium hover:underline"
                              to={`/o/${row.type_slug}/${row.id}`}
                            >
                              {column.render(row)}
                            </Link>
                          ) : (
                            column.render(row)
                          )}
                        </TableCell>
                      ))}
                      <TableCell className="text-muted-foreground">
                        {row.owner_workspace_name ?? row.owner_workspace_slug ?? '전역'}
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
        </>
      )}
    </div>
  )
}
