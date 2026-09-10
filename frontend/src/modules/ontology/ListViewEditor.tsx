/**
 * 목록 화면의 모양(`list_view`)을 정한다.
 *
 * **없으면 모든 목록이 똑같아지고, 똑같으면 아무도 안 쓴다.** 속성을 아무리
 * 정의해도 목록에는 안 나오고 기본형(식별자·이름·고친 때)으로 떨어진다 —
 * 그러면 「정의가 곧 화면」 이라는 말이 반만 참이 된다.
 */

import { ArrowDown, ArrowUp, Plus, X } from 'lucide-react'

import type { ListView, PropertyDef } from '@/modules/ontology/api'
import { Button } from '@/shared/components/ui/button'
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
 * 빈 열은 「값이 없다」 로 읽힌다.
 */
const NOT_A_COLUMN = new Set(['file'])

/** 거르기·검색에 쓸 수 있는 종류. 문자열 비교만 하므로 글과 선택뿐이다. */
const FILTERABLE = new Set(['text', 'enum'])

interface Props {
  defs: PropertyDef[]
  value: ListView
  onChange: (next: ListView) => void
}

function fieldOptions(defs: PropertyDef[]): { id: string; label: string }[] {
  return [
    ...BUILT_IN,
    ...defs
      .filter((def) => !NOT_A_COLUMN.has(def.data_type))
      .map((def) => ({ id: `properties.${def.key}`, label: def.label })),
  ]
}

export function ListViewEditor({ defs, value, onChange }: Props) {
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
      {/* --- 열 --------------------------------------------------------- */}
      <div className="space-y-2">
        <Label>목록에 보일 열</Label>
        {chosen.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            안 고르면 <b>식별자 · 이름 · 고친 때</b>로 떨어집니다 — 빈 화면이 되지는 않지만,
            정의한 속성은 안 보입니다.
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
          <b>첫 열이 상세로 가는 링크가 됩니다.</b> 파일 속성은 값이 아니라 첨부라 열로
          세울 수 없습니다.
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

      {/* --- 검색·거르기 ------------------------------------------------ */}
      <Toggles
        title="검색이 훑을 자리"
        hint="안 고르면 이름과 식별자를 봅니다 — 빈 결과보다 그럴듯한 기본이 낫습니다."
        options={searchable}
        chosen={value.search ?? []}
        onToggle={(id) => toggle('search', id)}
      />

      <Toggles
        title="목록 위에 세울 거르기 칸"
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
