/**
 * 목록 화면의 모양(`list_view`)을 정한다.
 *
 * **없으면 모든 목록이 똑같아지고, 똑같으면 아무도 안 쓴다.** 속성을 아무리
 * 정의해도 목록에는 안 나오고 기본형(식별자·이름·고친 때)으로 떨어진다 —
 * 그러면 「정의가 곧 화면」 이라는 말이 반만 참이 된다.
 *
 * ## 설명을 글로 적지 않고 보여 준다
 *
 * 네 칸이 화면의 어디에 꽂히는지는 말로 하면 안 읽힌다. 그래서 **지금 설정대로
 * 그려지는 목록을 위에 붙인다** — 열을 담으면 그 자리에서 표 머리가 바뀌고,
 * 필터를 켜면 위에 칸이 선다. 이 저장소가 「빈 목록은 이유를 말한다」 로 푸는
 * 것과 같은 방식이다: **화면이 스스로를 설명하게 한다.**
 */

import { ArrowDown, ArrowUp, Plus, X } from 'lucide-react'

import { ROLLUP_FNS } from '@/modules/ontology/api'
import type {
  ListView,
  PropertyDef,
  RelationType,
  RollupFn,
  RollupSpec,
} from '@/modules/ontology/api'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'

/** 속성이 아닌, 어느 타입에나 있는 칸들. */
const BUILT_IN: { id: string; label: string }[] = [
  { id: 'key', label: '식별자' },
  { id: 'label', label: '이름' },
  { id: 'status', label: '상태' },
  { id: 'updated_at', label: '고친 때' },
]

/**
 * 목록에 세울 수 없는 속성 종류.
 *
 * `file` 은 값이 아니라 첨부다 — `properties` 에 아무것도 없어서 빈 열이 서고,
 * 빈 열은 「값이 없다」 로 읽힌다. `text_long` 은 여러 줄이라 표를 무너뜨린다 —
 * 목록에서는 잘려 보이느니 **안 보이는 편이 낫다**(상세에 있다).
 */
const NOT_A_GOOD_COLUMN = new Set(['file', 'text_long'])

/** 「트리 없음」. 빈 문자열을 쓸 수 없다 — Select 가 그것을 「고른 것 없음」 으로 본다. */
const NO_TREE = '__none__'

/** 열을 안 골랐을 때 목록이 떨어지는 기본. `ObjectListPage` 와 같아야 한다. */
const FALLBACK_PREVIEW = ['key', 'label', 'updated_at']

/** 필터·검색에 쓸 수 있는 종류. 문자열 비교만 하므로 글과 선택뿐이다. */
const FILTERABLE = new Set(['text', 'enum', 'url'])

interface Props {
  defs: PropertyDef[]
  /** 트리로 쓸 수 있는 관계를 선택 위해 받는다. */
  relationTypes: RelationType[]
  /** 이 타입의 slug — 허용 타입에 걸린 관계만 고르게 한다. */
  typeSlug: string
  value: ListView
  onChange: (next: ListView) => void
}

function fieldOptions(defs: PropertyDef[]): { id: string; label: string }[] {
  return [
    ...BUILT_IN,
    ...defs
      .filter((def) => !NOT_A_GOOD_COLUMN.has(def.data_type))
      .map((def) => ({ id: `properties.${def.key}`, label: def.label })),
  ]
}

/**
 * 지금 설정대로 그려지는 목록 — **가짜 데이터로 모양만** 보여 준다.
 *
 * 진짜 데이터를 끌어오면 「비어 있는 타입」 에서는 미리보기가 비고, 그러면 설정이
 * 잘못된 것인지 데이터가 없는 것인지 구별되지 않는다.
 */
function Preview({ defs, value }: { defs: PropertyDef[]; value: ListView }) {
  const all = fieldOptions(defs)
  const columns = value.columns?.length ? value.columns : FALLBACK_PREVIEW
  const filters = value.filters ?? []

  function labelOf(id: string): string {
    return all.find((one) => one.id === id)?.label ?? id
  }

  return (
    <div className="bg-muted/30 space-y-2 rounded-md border border-dashed p-3">
      <p className="text-muted-foreground text-xs">
        이 타입의 목록 화면(
        <code>
          /o/{'{'}타입{'}'}
        </code>
        )이 이렇게 그려집니다.
      </p>

      <div className="bg-background space-y-2 rounded border p-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-muted-foreground rounded border px-2 py-1 text-xs">
            {(value.search ?? ['label', 'key']).map(labelOf).join('·')} (으)로 검색
          </span>
          {filters.map((id) => (
            <span key={id} className="text-muted-foreground rounded border px-2 py-1 text-xs">
              {labelOf(id)} ▾
            </span>
          ))}
        </div>

        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted-foreground border-b">
              {columns.map((id, index) => (
                <th key={id} className="py-1 text-left font-medium">
                  {labelOf(id)}
                  {index === 0 && <span className="ml-1 text-[10px]">(링크)</span>}
                  {value.sort?.field === id && (
                    <span className="ml-1">{value.sort.dir === 'desc' ? '▼' : '▲'}</span>
                  )}
                </th>
              ))}
              <th className="text-muted-foreground py-1 text-left font-medium">부서</th>
              <th className="text-muted-foreground py-1 text-left font-medium">상태</th>
            </tr>
          </thead>
          <tbody className="text-muted-foreground">
            {[0, 1].map((row) => (
              <tr key={row}>
                {columns.map((id) => (
                  <td key={id} className="py-1">
                    ···
                  </td>
                ))}
                <td className="py-1">···</td>
                <td className="py-1">···</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function ListViewEditor({ defs, relationTypes, typeSlug, value, onChange }: Props) {
  const all = fieldOptions(defs)
  const chosen = value.columns ?? []
  const rest = all.filter((one) => !chosen.includes(one.id))

  function labelOf(id: string): string {
    return all.find((one) => one.id === id)?.label ?? id
  }

  function setColumns(next: string[]) {
    onChange({ ...value, columns: next })
  }

  function move(index: number, by: number) {
    const next = [...chosen]
    const target = index + by
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    setColumns(next)
  }

  const searchable = [
    { id: 'label', label: '이름' },
    { id: 'key', label: '식별자' },
    ...defs
      .filter((def) => FILTERABLE.has(def.data_type))
      .map((def) => ({ id: `properties.${def.key}`, label: def.label })),
  ]
  const filterable = defs
    .filter((def) => FILTERABLE.has(def.data_type))
    .map((def) => ({ id: `properties.${def.key}`, label: def.label }))

  function toggle(key: 'search' | 'filters', id: string) {
    const current = value[key] ?? []
    onChange({
      ...value,
      [key]: current.includes(id) ? current.filter((one) => one !== id) : [...current, id],
    })
  }

  return (
    <div className="space-y-5">
      <Preview defs={defs} value={value} />

      {/* --- 열 --------------------------------------------------------- */}
      <div className="space-y-2">
        <Label>목록에 보일 열</Label>
        {chosen.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            안 고르면 <b>식별자 · 이름 · 고친 때</b>로 떨어집니다 — 빈 화면이 되지는 않지만, 정의한
            속성은 안 보입니다.
          </p>
        ) : (
          <ul className="space-y-1">
            {chosen.map((id, index) => (
              <li key={id} className="flex items-center gap-2 rounded-md border px-2 py-1">
                <span className="flex-1 text-sm">{labelOf(id)}</span>
                <span className="text-muted-foreground font-mono text-xs">{id}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`${labelOf(id)} 위로`}
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  <ArrowUp className="size-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`${labelOf(id)} 아래로`}
                  disabled={index === chosen.length - 1}
                  onClick={() => move(index, 1)}
                >
                  <ArrowDown className="size-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`${labelOf(id)} 빼기`}
                  onClick={() => setColumns(chosen.filter((one) => one !== id))}
                >
                  <X className="size-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        )}

        {rest.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {rest.map((one) => (
              <Button
                key={one.id}
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setColumns([...chosen, one.id])}
              >
                <Plus className="mr-1 size-3.5" />
                {one.label}
              </Button>
            ))}
          </div>
        )}
        <p className="text-muted-foreground text-xs">
          <b>첫 열이 상세로 가는 링크가 됩니다.</b> 파일 속성은 값이 아니라 첨부라 열로 세울 수
          없습니다.
        </p>
      </div>

      {/* --- 정렬 ------------------------------------------------------- */}
      <div className="space-y-2">
        <Label>기본 정렬</Label>
        <div className="flex gap-2">
          <Select
            value={value.sort?.field ?? 'label'}
            onValueChange={(field) =>
              onChange({ ...value, sort: { field, dir: value.sort?.dir ?? 'asc' } })
            }
          >
            <SelectTrigger className="flex-1">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {all.map((one) => (
                <SelectItem key={one.id} value={one.id}>
                  {one.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={value.sort?.dir ?? 'asc'}
            onValueChange={(dir) =>
              onChange({
                ...value,
                sort: { field: value.sort?.field ?? 'label', dir: dir as 'asc' | 'desc' },
              })
            }
          >
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="asc">오름차순</SelectItem>
              <SelectItem value="desc">내림차순</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* --- 트리 ------------------------------------------------------- */}
      <TreeSection
        relationTypes={relationTypes}
        typeSlug={typeSlug}
        value={value}
        onChange={onChange}
      />

      {/* --- 롤업 ------------------------------------------------------- */}
      {value.tree?.relation && <RollupSection defs={defs} value={value} onChange={onChange} />}

      {/* --- 검색·필터 ------------------------------------------------ */}
      <Toggles
        title="검색이 훑을 자리"
        hint="안 고르면 이름과 식별자를 봅니다 — 빈 결과보다 그럴듯한 기본이 낫습니다."
        options={searchable}
        chosen={value.search ?? []}
        onToggle={(id) => toggle('search', id)}
      />

      <Toggles
        title="목록 위에 세울 필터 칸"
        hint="글과 선택 속성만 됩니다 — 지금은 문자열 비교만 하고, 범위 질의는 없습니다. 없는 것을 있는 척하지 않습니다."
        options={filterable}
        chosen={value.filters ?? []}
        onToggle={(id) => toggle('filters', id)}
      />
    </div>
  )
}

function Toggles({
  title,
  hint,
  options,
  chosen,
  onToggle,
}: {
  title: string
  hint: string
  options: { id: string; label: string }[]
  chosen: string[]
  onToggle: (id: string) => void
}) {
  return (
    <div className="space-y-2">
      <Label>{title}</Label>
      {options.length === 0 ? (
        <p className="text-muted-foreground text-sm">쓸 수 있는 속성이 없습니다.</p>
      ) : (
        <div className="flex flex-wrap gap-3">
          {options.map((one) => (
            <label key={one.id} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                className="size-4"
                checked={chosen.includes(one.id)}
                onChange={() => onToggle(one.id)}
              />
              {one.label}
            </label>
          ))}
        </div>
      )}
      <p className="text-muted-foreground text-xs">{hint}</p>
    </div>
  )
}

/**
 * 목록 왼쪽에 세울 트리.
 *
 * **재귀로 펼치는(`transitive`) 관계만** 고르게 한다. 아닌 관계로 트리를 그리면
 * 한 단계밖에 안 펼쳐지는데, 화면은 그것이 「자식이 없어서」 인지 「관계가 그런
 * 종류라서」 인지 말해 주지 못한다.
 */
function TreeSection({
  relationTypes,
  typeSlug,
  value,
  onChange,
}: {
  relationTypes: RelationType[]
  typeSlug: string
  value: ListView
  onChange: (next: ListView) => void
}) {
  const usable = relationTypes.filter(
    (one) =>
      one.is_active &&
      one.transitive &&
      // 양끝 중 어느 쪽이든 이 타입이 낄 수 있어야 트리가 그려진다.
      (!one.src_type_slugs || one.src_type_slugs.includes(typeSlug)) &&
      (!one.dst_type_slugs || one.dst_type_slugs.includes(typeSlug)),
  )
  const chosen = value.tree?.relation ?? NO_TREE
  const parent = value.tree?.parent ?? 'dst'
  const kind = usable.find((one) => one.slug === chosen) ?? null

  return (
    <div className="space-y-2">
      <Label>목록 왼쪽 트리</Label>
      {usable.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          쓸 수 있는 관계가 없습니다. <b>「재귀로 펼친다」 로 정의된 관계</b>라야 트리를 세울 수
          있습니다 — 관리 → 온톨로지 → 관계 종류에서 만드세요.
        </p>
      ) : (
        <>
          <Select
            value={chosen}
            onValueChange={(next) =>
              onChange({
                ...value,
                tree: next === NO_TREE ? undefined : { relation: next, parent },
              })
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_TREE}>트리 없음</SelectItem>
              {usable.map((one) => (
                <SelectItem key={one.slug} value={one.slug}>
                  {one.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {kind && (
            <>
              <Select
                value={parent}
                onValueChange={(next) =>
                  onChange({
                    ...value,
                    tree: { relation: kind.slug, parent: next as 'src' | 'dst' },
                  })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="dst">
                    자식이 부모를 가리킨다 (A가 B에 「{kind.label}」)
                  </SelectItem>
                  <SelectItem value="src">
                    부모가 자식을 가리킨다 (A가 B를 「{kind.label}」)
                  </SelectItem>
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">
                <b>방향을 잘못 고르면 트리가 뒤집힌 채 그려지고</b>, 화면은 그것을 말해 주지
                못합니다. 이은 뒤 목록에서 한번 확인하세요.
              </p>
            </>
          )}
        </>
      )}
    </div>
  )
}

/**
 * 롤업 — 「아래 전부」 의 숫자를 모아 상세에 띄운다. 트리가 있을 때만 뜻이 있고,
 * 숫자 속성만 고를 수 있다. 저장하지 않고 볼 때마다 세므로 여기서 정의만 한다.
 */
function RollupSection({
  defs,
  value,
  onChange,
}: {
  defs: PropertyDef[]
  value: ListView
  onChange: (next: ListView) => void
}) {
  const numeric = defs.filter((def) => def.data_type === 'number')
  const rows = value.rollups ?? []
  const set = (next: RollupSpec[]) =>
    onChange({ ...value, rollups: next.length > 0 ? next : undefined })

  return (
    <div className="space-y-2">
      <Label>아래 전부 모으기 (롤업)</Label>
      {numeric.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          숫자 속성이 없습니다. 무게·수량·금액 같은 숫자 칸이 있어야 모을 것이 있습니다.
        </p>
      ) : (
        <>
          {rows.map((row, index) => (
            <div key={index} className="flex flex-wrap items-center gap-2">
              <Select
                value={row.property}
                onValueChange={(next) =>
                  set(rows.map((one, i) => (i === index ? { ...one, property: next } : one)))
                }
              >
                <SelectTrigger className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {numeric.map((def) => (
                    <SelectItem key={def.key} value={def.key}>
                      {def.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={row.fn}
                onValueChange={(next) =>
                  set(rows.map((one, i) => (i === index ? { ...one, fn: next as RollupFn } : one)))
                }
              >
                <SelectTrigger className="w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ROLLUP_FNS.map(([key, text]) => (
                    <SelectItem key={key} value={key}>
                      {text}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                value={row.label ?? ''}
                placeholder="표시 이름 (비우면 「무게 합계」 처럼)"
                className="w-52"
                onChange={(event) =>
                  set(
                    rows.map((one, i) =>
                      i === index ? { ...one, label: event.target.value || undefined } : one,
                    ),
                  )
                }
              />
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label="롤업 삭제"
                onClick={() => set(rows.filter((_one, i) => i !== index))}
              >
                <X className="size-4" />
              </Button>
            </div>
          ))}
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => set([...rows, { property: numeric[0].key, fn: 'sum' }])}
          >
            <Plus className="mr-1 size-4" />
            모을 것 추가
          </Button>
          <p className="text-muted-foreground text-xs">
            상세 화면에 「아래 전부」 의 값이 뜹니다 — 어셈블리의 총 무게, 과제의 예산 합계처럼.
            저장하지 않고 볼 때마다 세므로 부품을 고치면 바로 바뀝니다. 값이 빈 것은 몇 개인지 함께
            보입니다.
          </p>
        </>
      )}
    </div>
  )
}
