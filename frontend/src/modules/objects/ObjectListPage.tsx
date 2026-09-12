/**
 * 타입 하나의 객체 목록 — **화면이 정의에서 나온다.**
 *
 * 경로는 `/o/:typeSlug` 하나뿐이다. 타입이 늘어도 라우트는 안 늘어나므로
 * `navigation.ts` 가 정적 화면의 정본이라는 규칙과 `router.test.tsx` 가 그대로 선다.
 */

import { Suspense, lazy, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { BarChart3, Download, FileUp, Pencil, Plus } from 'lucide-react'

import { ontologyApi } from '@/modules/ontology/api'
import { workspaceApi } from '@/modules/workspaces/api'
import type { ObjectType, PropertyDef } from '@/modules/ontology/api'
import { objectApi } from '@/modules/objects/api'
import type {
  Condition,
  ConditionOp,
  ObjectQuery,
  ObjectRow,
  SavedView,
} from '@/modules/objects/api'
import { DEFAULT_SUMMARY } from '@/modules/objects/summarySettings'
import type { SummarySettings } from '@/modules/objects/summarySettings'
import { ConditionBar } from '@/modules/objects/ConditionBar'
import { ViewPicker } from '@/modules/objects/ViewPicker'
import { propertyText } from '@/modules/objects/PropertyFields'
import { ObjectCreateDialog } from '@/modules/objects/ObjectCreateDialog'
import { BulkEditDialog } from '@/modules/objects/BulkEditDialog'
import { BulkUndoDialog } from '@/modules/objects/BulkUndoDialog'
import { ObjectImportDialog } from '@/modules/objects/ObjectImportDialog'
import { ObjectTree } from '@/modules/objects/ObjectTree'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Pagination } from '@/shared/components/Pagination'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Button } from '@/shared/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
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
import { TypeIcon } from '@/shared/components/TypeIcon'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

/**
 * 통계는 **누를 때 받는다.**
 *
 * 차트 라이브러리가 이 화면 덩어리에 들어가면, 그림을 한 번도 안 여는 사람까지
 * 목록을 볼 때마다 그만큼을 받는다. 목록은 이 플랫폼에서 가장 자주 여는 화면이라
 * 그 값이 가장 비싸게 매겨진다.
 */
const SummaryPanel = lazy(() =>
  import('@/modules/objects/SummaryPanel').then((mod) => ({ default: mod.SummaryPanel })),
)

/** 「거르지 않음」 을 나타내는 값. **빈 문자열을 쓸 수 없다** — Select 가 빈 값을
 *  「고른 것 없음」 으로 보고 자리표시자로 돌아간다. */
const ALL = '__all__'

/** 고를 수 있는 해. 올해에서 뒤로 열 해 — 그보다 옛것은 「전체 연도」 로 본다. */
const YEAR_OPTIONS = Array.from({ length: 11 }, (_, index) => new Date().getFullYear() - index)

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
        return {
          id,
          label: def.label,
          render: (row) => propertyText(def, row.properties[key], row.ref_labels),
        }
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

/** 주소 ↔ 조건. `f.<칸>.<연산>=<값>` — 붙여 넣으면 같은 목록이 선다. */
function conditionsFromParams(params: URLSearchParams): Condition[] {
  const out: Condition[] = []
  for (const [key, value] of params.entries()) {
    if (!key.startsWith('f.')) continue
    const dot = key.lastIndexOf('.')
    if (dot <= 2) continue
    out.push({ field: key.slice(2, dot), op: key.slice(dot + 1) as ConditionOp, value })
  }
  return out
}

export default function ObjectListPage() {
  const { typeSlug = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  // **주소가 곧 상태다.** 따로 들고 있으면 사이드바에서 다른 타입으로 갈 때(같은 화면이
  // 재사용된다) 옛 조건이 남아 「없는 칸」 으로 422 가 난다. 주소에서 매번 읽는다.
  const query = params.get('q') ?? ''
  const conditions = useMemo(() => conditionsFromParams(params), [params])
  const activeView = params.get('view')
  /** 검색어·조건을 바꾸면 주소도 같이 — 그리고 걸려 있던 뷰는 풀린다(손댄 순간 그 뷰가 아니다). */
  const sync = (next: { q?: string; conditions?: Condition[]; view?: string | null }) => {
    const q = next.q ?? query
    const list = next.conditions ?? conditions
    const view = next.view === undefined ? null : next.view
    const copy = new URLSearchParams(params)
    // 지우면서 돌면 건너뛰는 키가 생긴다 — 먼저 복사해 둔다.
    for (const key of Array.from(copy.keys())) if (key.startsWith('f.')) copy.delete(key)
    copy.delete('q')
    copy.delete('view')
    if (q) copy.set('q', q)
    for (const one of list) copy.append(`f.${one.field}.${one.op}`, one.value)
    if (view) copy.set('view', view)
    setParams(copy, { replace: true })
  }
  const setQuery = (q: string) => sync({ q })
  const setConditions = (list: Condition[]) => sync({ conditions: list })
  const applyView = (view: SavedView) => {
    sync({ q: view.query.q, conditions: view.query.conditions, view: view.id })
    // 뷰에 축이 담겨 있으면 그림까지 그대로 연다 — 조건만 돌려주고 축을 다시 고르게
    // 하면, 그 수고 때문에 사람은 뷰를 안 쓰게 된다.
    if (view.summary?.group_by) {
      setSummary({
        groupBy: view.summary.group_by,
        splitBy: view.summary.split_by || '',
        metric: view.summary.metric || 'count',
        metricField: view.summary.metric_field,
        chart: (view.summary.chart as SummarySettings['chart']) || 'bar',
        x: '',
        y: '',
        stacked: Boolean(view.summary.stacked),
        order: view.summary.order || 'desc',
      })
      setGrouping(true)
    }
  }
  const [under, setUnder] = useState<string | null>(null)
  const [deep, setDeep] = useState(true)
  /**
   * **올해가 기본이다.** 연도를 쓰는 축에서 전체를 먼저 보여 주면 몇 해치가
   * 섞여 뜨고, 사람은 그것을 지금 쓰는 것으로 읽는다. 「전체 보기」 는 옵트인이다.
   */
  const [year, setYear] = useState<number | null>(new Date().getFullYear())
  const [offset, setOffset] = useState(0)
  const [creating, setCreating] = useState(false)
  /** 통계를 펼쳐 두었나. **기본은 접힘** — 목록을 보러 온 사람에게
   *  막대를 먼저 들이밀면 목록이 한 화면 아래로 밀린다. */
  /** 홈의 「위젯 추가」 가 `?group=1` 로 보낸다 — 도착하자마자 통계가 펼쳐져
   *  있어야, 여기까지 온 사람이 무엇을 하러 왔는지 화면이 이어서 말한다. */
  const [grouping, setGrouping] = useState(params.get('group') === '1')
  /** 통계 설정 — **여기가 들고 있다.** 뷰를 불러오면 그 뷰의 기준으로 열려야 하고,
   *  저장할 때는 지금 축이 함께 담겨야 한다. 패널이 혼자 들면 둘 다 못 한다. */
  const [summary, setSummary] = useState<SummarySettings>(DEFAULT_SUMMARY)
  /** 고른 줄 — **쪽을 넘기면 푼다.** 안 보이는 것을 고른 채로 두면 「10건 골랐다」 는
   *  말과 눈에 보이는 것이 어긋나고, 그때 사람은 무엇을 바꾸는지 모른다. */
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [bulkEditing, setBulkEditing] = useState(false)
  /** 되돌릴 묶음 — 여럿 고치기를 적용한 직후 「되돌리기」 로 연다. */
  const [undoBatch, setUndoBatch] = useState<string | null>(null)
  // 소유 부서를 바꿀 때 고를 것 — **내가 관리하는 부서만** 서버가 받아 준다. 목록은
  // 내 소속을 주고, 못 고르는 것은 서버가 행마다 이유를 적는다.
  const myWorkspaces = useResource(() => workspaceApi.list(), [])
  const [importing, setImporting] = useState(false)
  /** 내보내기 실패 — 새 탭이 아니라 이 화면에 떠야 한다. 조용히 실패하면 아무 일도 안 일어난 것처럼 보인다. */
  const [exportError, setExportError] = useState<Error | null>(null)
  const download = (run: () => Promise<void>) => {
    setExportError(null)
    run().catch((caught: unknown) =>
      setExportError(caught instanceof Error ? caught : new Error('알 수 없는 오류')),
    )
  }

  const schema = useResource(() => ontologyApi.schema(), [])
  const foundType = schema.data?.types.find((row) => row.slug === typeSlug)
  /** 연도가 뜻을 갖는 축인가. `evergreen` 이면 토글을 안 그린다 — **없는 것을
   *  있는 척하지 않는다.** */
  const yearApplies = Boolean(foundType && foundType.temporal_kind !== 'evergreen')

  // 목록이 달라지면 고른 것을 푼다(쪽 넘김·거르기·타입 바꾸기).
  useEffect(() => {
    setPicked(new Set())
  }, [typeSlug, query, JSON.stringify(conditions), under, deep, year, offset])

  const list = useResource(
    () =>
      objectApi.list(typeSlug, {
        q: query || undefined,
        conditions,
        under,
        deep,
        year: yearApplies ? year : null,
        offset,
      }),
    // conditions 는 배열이라 참조가 매번 바뀐다 — 내용으로 비교한다.
    [typeSlug, query, JSON.stringify(conditions), under, deep, year, offset],
  )

  /** 내보내기에 그대로 넘기는 거르기 — 쪽(offset)만 뺀다. 파일은 전부다. */
  const exportQuery: ObjectQuery = {
    q: query || undefined,
    conditions,
    under,
    deep,
    year: yearApplies ? year : null,
  }

  /** 참조 조건의 값을 이름으로 보여 주려고 — 목록에 실린 것들의 ref_labels 를 모은다. */
  const refLabelsOfList = useMemo(() => {
    const out: Record<string, string> = {}
    for (const row of list.data?.items ?? []) Object.assign(out, row.ref_labels)
    return out
  }, [list.data])

  const type = foundType
  /** 원 표를 비추는 타입 — 행이 없다. 만들기·파일·조건·뷰가 없고, 찾기만 있다. */
  const isSystem = type?.kind_class === 'system'
  /** 지금 목록이 좁혀져 있나. 빈 목록의 이유가 이것으로 갈린다. */
  const narrowed =
    Boolean(query) || conditions.length > 0 || Boolean(under) || (yearApplies && year !== null)
  /** 트리가 정의된 타입인가. 안 정했으면 왼쪽을 안 그린다. */
  const hasTree = Boolean(type?.list_view?.tree?.relation)
  const columns = useMemo(
    () =>
      type
        ? isSystem
          ? buildColumns({ ...type, list_view: { columns: ['key', 'label'] } }, [])
          : buildColumns(type, type.properties)
        : [],
    [type, isSystem],
  )

  if (schema.error) return <ErrorNotice error={schema.error} />
  if (schema.data && !type) {
    return (
      <EmptyState
        title="없는 타입입니다"
        hint={
          <>
            <code>{typeSlug}</code> 이라는 타입이 정의돼 있지 않습니다. 온톨로지 관리에서 만들거나
            주소를 확인하세요.
          </>
        }
      />
    )
  }

  return (
    <div className={hasTree ? 'flex min-h-full gap-6' : 'mx-auto max-w-5xl'}>
      {hasTree && type && (
        <ObjectTree
          typeSlug={typeSlug}
          selected={under}
          onSelect={(id) => {
            setUnder(id)
            setOffset(0)
          }}
          reloadKey={list.data?.total ?? 0}
        />
      )}

      <div className="min-w-0 flex-1">
        <PageHeader
          title={
            type ? (
              <span className="flex items-center gap-2">
                {/* 사이드바에서 누른 그림이 여기에도 선다 — 같은 것을 보고 있다는
                    확인이 없으면, 타입이 여럿일 때 「내가 뭘 열었지」 를 묻게 된다. */}
                <TypeIcon name={type.icon} className="size-5" />
                {type.label}
              </span>
            ) : (
              '…'
            )
          }
          description={type?.description || undefined}
          actions={
            type &&
            !isSystem && (
              <div className="flex gap-2">
                {/* 내보내기는 **지금 거른 목록 그대로** — 화면과 파일이 같은 것을 말한다. */}
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button size="sm" variant="outline">
                      <Download className="mr-1 size-4" />
                      내보내기
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem
                      onSelect={() =>
                        download(() => objectApi.export(typeSlug, 'csv', exportQuery))
                      }
                    >
                      객체 CSV (거른 {list.data?.total ?? 0}건)
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() =>
                        download(() => objectApi.export(typeSlug, 'json', exportQuery))
                      }
                    >
                      객체 JSON
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() => download(() => objectApi.exportRelations(typeSlug, 'csv'))}
                    >
                      관계 CSV (이 타입에서 출발하는 것 전부)
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                {type.kind_class !== 'system' && (
                  <>
                    <Button
                      size="sm"
                      variant={grouping ? 'secondary' : 'outline'}
                      onClick={() => setGrouping((before) => !before)}
                    >
                      <BarChart3 className="mr-1 size-4" />
                      통계
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => setImporting(true)}>
                      <FileUp className="mr-1 size-4" />
                      파일로 넣기
                    </Button>
                    <Button size="sm" onClick={() => setCreating(true)}>
                      <Plus className="mr-1 size-4" />
                      만들기
                    </Button>
                  </>
                )}
              </div>
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
            // 빠른 거르기는 「같음」 조건 하나다 — 아래 조건 줄과 같은 것을 가리킨다.
            const quick = conditions.find((one) => one.field === key && one.op === 'eq')
            return (
              <PropertyFilter
                key={key}
                def={def}
                value={quick?.value ?? ''}
                onChange={(next) => {
                  const rest = conditions.filter((one) => !(one.field === key && one.op === 'eq'))
                  // **빈 값은 「거르지 않음」 이다.** 빈 문자열로 걸면 값이 빈
                  // 행만 나오는데, 그것은 아무도 뜻한 적 없는 결과다.
                  setConditions(next ? [...rest, { field: key, op: 'eq', value: next }] : rest)
                  setOffset(0)
                }}
              />
            )
          })}
          {/* **올해 기본 + 전체 보기.** 연도를 쓰는 축에서 전체를 먼저 보여 주면
            몇 해치가 섞여 뜨고, 사람은 그것을 지금 쓰는 것으로 읽는다. */}
          {yearApplies && (
            <div className="flex items-end gap-2 pb-0.5">
              <Select
                value={year === null ? ALL : String(year)}
                onValueChange={(next) => {
                  setYear(next === ALL ? null : Number(next))
                  setOffset(0)
                }}
              >
                <SelectTrigger className="h-9 w-32">
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

          {/* **기본은 포함이다.** 끄면 직계만 본다 — 상위 노드를 눌렀을 때 목록이
            비면 그 빈 목록은 「없다」 로 읽힌다. */}
          {under && (
            <label className="text-muted-foreground flex items-center gap-1.5 pb-1.5 text-sm">
              <input
                type="checkbox"
                className="size-4"
                checked={deep}
                onChange={(event) => {
                  setDeep(event.target.checked)
                  setOffset(0)
                }}
              />
              아래 것까지 포함
            </label>
          )}
        </div>

        {exportError && <ErrorNotice error={exportError} className="mb-4" />}

        {isSystem && (
          <p className="text-muted-foreground mb-4 text-sm">
            이 목록은 다른 표(원 표)를 비춥니다 — 여기서 만들거나 고치지 않고, 그 표의 화면에서
            합니다. 객체는 이것을 속성이나 관계로 가리킬 수 있습니다.
          </p>
        )}

        {/* 조건 줄 — 칸 안 OR, 칸끼리 AND. 주소에 남고, 뷰로 저장된다. */}
        {type && !isSystem && (
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <ViewPicker
              typeSlug={typeSlug}
              current={{ q: query, conditions, status: null }}
              // 통계를 펼쳐 둔 채 저장하면 **기준까지 담긴다.** 조건과 기준은 같은
              // 물음의 두 쪽이다 — 「영남 공급사를 등급별로」.
              summary={
                grouping
                  ? {
                      group_by: summary.groupBy,
                      split_by: summary.splitBy,
                      metric: summary.metric,
                      metric_field: summary.metricField,
                      chart: summary.chart,
                      stacked: summary.stacked,
                      order: summary.order,
                    }
                  : null
              }
              activeId={activeView}
              onApply={(view) => {
                applyView(view)
                setOffset(0)
              }}
              onClear={() => {
                sync({ q: '', conditions: [], view: null })
                setOffset(0)
              }}
            />
            <ConditionBar
              defs={type.properties}
              conditions={conditions}
              onChange={(next) => {
                setConditions(next)
                setOffset(0)
              }}
              refLabels={refLabelsOfList}
            />
          </div>
        )}

        {grouping && type && !isSystem && (
          <div className="mb-4">
            <Suspense
              fallback={
                <p className="text-muted-foreground rounded-md border p-4 text-sm">
                  통계를 준비하는 중…
                </p>
              }
            >
              <SummaryPanel
                typeSlug={typeSlug}
                query={exportQuery}
                settings={summary}
                onSettings={setSummary}
                onClose={() => setGrouping(false)}
                onPick={(field, key) => {
                  // **누른 막대가 곧 거르기다.** 여기서 거르기 칸으로 돌아가 값을 다시
                  // 치게 하면, 그 한 번이 사람을 엑셀로 돌려보낸다.
                  if (key === null) return
                  if (field === 'status') return
                  const propertyKey = field.slice('properties.'.length)
                  const rest = conditions.filter(
                    (one) => !(one.field === propertyKey && one.op === 'eq'),
                  )
                  setConditions([...rest, { field: propertyKey, op: 'eq', value: key }])
                  setOffset(0)
                }}
              />
            </Suspense>
          </div>
        )}

        {/* **고른 것이 있으면 무엇을 할 수 있는지 그 자리에 뜬다.** 위쪽 도구 막대에
            숨겨 두면 고른 사람은 그것을 찾지 못하고, 결국 한 건씩 연다. */}
        {picked.size > 0 && !isSystem && (
          <div className="bg-muted/50 mb-4 flex flex-wrap items-center gap-3 rounded-md border p-3">
            <span className="text-sm">
              <strong>{picked.size}건</strong> 골랐습니다
            </span>
            <Button size="sm" onClick={() => setBulkEditing(true)}>
              <Pencil className="mr-1 size-4" />한 칸 바꾸기
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setPicked(new Set())}>
              고른 것 풀기
            </Button>
          </div>
        )}

        {list.error && <ErrorNotice error={list.error} />}

        {list.data && list.data.items.length === 0 ? (
          <EmptyState
            title={narrowed ? '거르기에 맞는 것이 없습니다' : '아직 아무것도 없습니다'}
            hint={
              /* **비어 있는 이유를 말한다.** 데이터가 없는 것인지, 거르기가 좁은
               것인지, 권한 때문인지는 해야 할 일이 전혀 다르다. */
              narrowed
                ? under
                  ? '고른 가지 아래에 맞는 것이 없습니다. 왼쪽에서 「전체 보기」 를 누르거나 거르기를 줄여 보세요.'
                  : yearApplies && year !== null
                    ? `${year}년에 해당하는 것이 없습니다. 「전체 연도」 로 바꿔 보세요.`
                    : '찾는 말이나 거르기를 줄여 보세요. 다른 부서의 것은 여기 안 보입니다.'
                : isSystem
                  ? '비추는 원 표가 비어 있습니다.'
                  : '「만들기」 로 첫 항목을 넣거나, 다른 부서의 것이라면 그 부서 사람에게 물어보세요.'
            }
            action={
              narrowed ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    sync({ q: '', conditions: [], view: null })
                    setUnder(null)
                    setYear(null)
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
                  {/* 고르기는 **고칠 수 있는 타입에서만** — 투영 타입은 여기서 안 고친다. */}
                  {!isSystem && (
                    <TableHead className="w-8">
                      <input
                        type="checkbox"
                        aria-label="이 쪽 전부 고르기"
                        className="size-4"
                        checked={
                          (list.data?.items.length ?? 0) > 0 &&
                          picked.size === (list.data?.items.length ?? 0)
                        }
                        onChange={(event) =>
                          setPicked(
                            event.target.checked
                              ? new Set((list.data?.items ?? []).map((one) => one.id))
                              : new Set(),
                          )
                        }
                      />
                    </TableHead>
                  )}
                  {columns.map((column) => (
                    <TableHead key={column.id}>{column.label}</TableHead>
                  ))}
                  {!isSystem && <TableHead>부서</TableHead>}
                  <TableHead>상태</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(list.data?.items ?? []).map((row) => (
                  /* **행 어디를 눌러도 들어간다.** 이름 글자만 링크로 두면 사람은 그
                     좁은 과녁을 매번 겨눠야 하고, 옆 칸을 눌렀을 때 아무 일도 안
                     일어나는 것을 고장으로 읽는다. 링크는 그대로 둔다 — 가운데 클릭으로
                     새 탭을 열고 키보드로 옮겨 다니는 길이 거기 있다. */
                  <TableRow
                    key={row.id}
                    className="hover:bg-muted/50 cursor-pointer"
                    onClick={(event) => {
                      // 안에 있는 링크·단추·체크를 눌렀으면 그쪽이 한다 — 두 번 가지 않는다.
                      if ((event.target as HTMLElement).closest('a,button,input,label')) return
                      // 글자를 끌어 고른 것은 열려는 것이 아니다.
                      if (window.getSelection()?.toString()) return
                      navigate(`/o/${typeSlug}/${row.id}`)
                    }}
                  >
                    {!isSystem && (
                      <TableCell className="w-8">
                        <input
                          type="checkbox"
                          aria-label={`${row.label} 고르기`}
                          className="size-4"
                          checked={picked.has(row.id)}
                          onChange={(event) =>
                            setPicked((before) => {
                              const next = new Set(before)
                              if (event.target.checked) next.add(row.id)
                              else next.delete(row.id)
                              return next
                            })
                          }
                        />
                      </TableCell>
                    )}
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
                    {!isSystem && (
                      <TableCell className="text-muted-foreground">
                        {/* **NULL 은 전역이다.** 빈 칸으로 두면 「부서가 없다」 로 읽힌다. */}
                        {row.owner_workspace_slug ?? '전역'}
                      </TableCell>
                    )}
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
      </div>

      {type && bulkEditing && (
        <BulkEditDialog
          typeSlug={typeSlug}
          ids={[...picked]}
          defs={type.properties}
          workspaces={(myWorkspaces.data ?? []).map((one) => ({ slug: one.slug, name: one.name }))}
          onClose={() => setBulkEditing(false)}
          onApplied={() => {
            list.reload()
            setPicked(new Set())
          }}
          onUndo={(batchId) => {
            setBulkEditing(false)
            setUndoBatch(batchId)
          }}
        />
      )}

      {undoBatch && (
        <BulkUndoDialog
          typeSlug={typeSlug}
          batchId={undoBatch}
          onClose={() => setUndoBatch(null)}
          onApplied={() => list.reload()}
        />
      )}

      {type && importing && (
        <ObjectImportDialog
          type={type}
          onClose={() => setImporting(false)}
          onApplied={() => list.reload()}
        />
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
