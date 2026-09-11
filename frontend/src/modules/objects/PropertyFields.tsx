/**
 * 속성 정의를 폼으로 그린다 — **정의가 곧 화면이다.**
 *
 * `shared/` 에 두지 않는 이유: 객체를 찾아 주는 picker 가 `modules/objects/api` 를
 * 부른다. `shared/` 에 두면 shared -> 모듈 방향이 거꾸로 서고, 그것은 백엔드에서
 * 금지한 그 방향이다.
 *
 * 관계 속성(2단계)도 같은 정의 모양(`property_defs`)을 쓰므로 이 컴포넌트를 그대로
 * 다시 쓴다. **두 벌로 만들면 위젯이 갈리고, 갈린 것은 한쪽만 고쳐진다.**
 */

import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ChevronRight, Plus, X } from 'lucide-react'

import type { DataType, PropertyDef, SectionView } from '@/modules/ontology/api'
import { useObjectOptions } from '@/modules/objects/useObjectOptions'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
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
import { Textarea } from '@/shared/components/ui/textarea'
import { cn } from '@/shared/lib/utils'

/** 고를 것이 이보다 많으면 통째로 펼치지 않는다 — 눈으로 찾는 일은 실패한다. */
const PICKER_THRESHOLD = 20

export type PropertyValues = Record<string, unknown>

interface Props {
  defs: PropertyDef[]
  values: PropertyValues
  onChange: (values: PropertyValues) => void
  disabled?: boolean
  /**
   * 묶음의 순서와 모양(`form_view`). 안 주면 한 덩어리로 선다.
   *
   * **속성이 40개인 폼이 일렬로 서면 사람은 그 폼을 안 채운다.**
   */
  view?: SectionView
}

/** 값 하나짜리 입력. 종류마다 위젯이 다르다. */
function OneValue({
  def,
  value,
  onChange,
  disabled,
}: {
  def: PropertyDef
  value: unknown
  onChange: (next: unknown) => void
  disabled?: boolean
}) {
  if (def.data_type === 'bool') {
    return (
      <input
        type="checkbox"
        className="size-4"
        checked={value === true}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
    )
  }

  if (def.data_type === 'number') {
    return (
      <Input
        type="number"
        // **정의가 정한 범위를 브라우저도 안다.** 서버가 최종 판정을 하지만,
        // 여기서 막으면 거절당하고 나서 무엇을 고칠지 찾을 일이 없다.
        min={def.min_value ?? undefined}
        max={def.max_value ?? undefined}
        step={def.decimals != null ? 10 ** -def.decimals : undefined}
        value={value === undefined || value === null ? '' : String(value)}
        disabled={disabled}
        // **빈 칸은 값이 없는 것이지 0 이 아니다.** 0 으로 바꿔 두면 「안 적었다」 와
        // 「0 이라고 적었다」 가 구별되지 않는다.
        onChange={(event) =>
          onChange(event.target.value === '' ? null : Number(event.target.value))
        }
      />
    )
  }

  if (def.data_type === 'text_long') {
    return (
      <Textarea
        rows={4}
        value={typeof value === 'string' ? value : ''}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
    )
  }

  if (def.data_type === 'url') {
    return (
      <Input
        type="url"
        placeholder="https://"
        value={typeof value === 'string' ? value : ''}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
    )
  }

  if (def.data_type === 'datetime') {
    return (
      <Input
        type="datetime-local"
        value={typeof value === 'string' ? value : ''}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value || null)}
      />
    )
  }

  if (def.data_type === 'date') {
    return (
      <Input
        type="date"
        value={typeof value === 'string' ? value : ''}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value || null)}
      />
    )
  }

  if (def.data_type === 'enum') {
    const options = def.enum_options ?? []
    if (options.length > PICKER_THRESHOLD) {
      return (
        <SearchablePicker
          options={options.map((option) => ({ value: option, label: option }))}
          value={typeof value === 'string' ? value : null}
          onChange={onChange}
        />
      )
    }
    return (
      <Select
        value={typeof value === 'string' ? value : ''}
        onValueChange={onChange}
        disabled={disabled}
      >
        <SelectTrigger>
          <SelectValue placeholder="고르세요" />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    )
  }

  if (def.data_type === 'object_ref') {
    return (
      <ObjectRefPicker
        typeSlug={def.ref_type_slug}
        value={typeof value === 'string' ? value : null}
        onChange={onChange}
      />
    )
  }

  return (
    <Input
      value={typeof value === 'string' ? value : ''}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

/**
 * 객체를 고르는 picker.
 *
 * **치는 길과 훑는 길을 함께 낸다** — 이름의 일부를 알 때와 무엇이 있는지 모를 때가
 * 둘 다 있다. 후보는 **서버가 찾는다**(`useObjectOptions`) — 처음 200개만 받아 화면에서
 * 거르면 201번째부터 없는 것으로 보이고, 못 찾은 사람은 새로 만든다.
 */
function ObjectRefPicker({
  typeSlug,
  value,
  onChange,
}: {
  typeSlug: string | null
  value: string | null
  onChange: (next: string) => void
}) {
  const sources = useMemo(() => (typeSlug ? [{ slug: typeSlug }] : []), [typeSlug])
  const found = useObjectOptions(sources, { value })

  if (!typeSlug) {
    return (
      <p className="text-muted-foreground text-sm">
        가리킬 타입이 정해져 있지 않습니다. 속성 정의에서 대상 타입을 고르세요.
      </p>
    )
  }
  if (found.failed) {
    return <p className="text-destructive text-sm">고를 것을 불러오지 못했습니다.</p>
  }

  return (
    <SearchablePicker
      options={found.options}
      pinned={found.pinned}
      total={found.total}
      loading={found.loading}
      onQueryChange={found.setQuery}
      value={value}
      onChange={onChange}
      placeholder="객체를 고르세요"
      searchPlaceholder="이름·식별자로 찾기"
      emptyText="맞는 객체가 없습니다"
    />
  )
}

/** 여러 값을 받는 속성. 줄을 더하고 지운다. */
function ManyValues({
  def,
  values,
  onChange,
  disabled,
}: {
  def: PropertyDef
  values: unknown[]
  onChange: (next: unknown[]) => void
  disabled?: boolean
}) {
  return (
    <div className="space-y-2">
      {values.map((item, index) => (
        <div key={index} className="flex items-center gap-2">
          <div className="flex-1">
            <OneValue
              def={def}
              value={item}
              disabled={disabled}
              onChange={(next) => {
                const copy = [...values]
                copy[index] = next
                onChange(copy)
              }}
            />
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled={disabled}
            aria-label={`${def.label} ${index + 1}번째 지우기`}
            onClick={() => onChange(values.filter((_, at) => at !== index))}
          >
            <X className="size-4" />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={() => onChange([...values, def.data_type === 'bool' ? false : ''])}
      >
        <Plus className="mr-1 size-3.5" />
        추가
      </Button>
    </div>
  )
}

/**
 * 정의를 묶음으로 나눈다.
 *
 * 뷰가 적어 둔 순서를 먼저 쓰고, **거기 없는 묶음은 뒤에 붙인다** — 새 속성을
 * 만들었는데 뷰를 안 고쳤다고 그 속성이 화면에서 사라지면, 만든 사람은 저장이
 * 안 된 줄 안다.
 */
export function groupBySection(
  defs: PropertyDef[],
  view?: SectionView,
): { name: string; columns: number; collapsed: boolean; defs: PropertyDef[] }[] {
  const byName = new Map<string, PropertyDef[]>()
  for (const def of defs) {
    const name = def.section || ''
    byName.set(name, [...(byName.get(name) ?? []), def])
  }

  const ordered: string[] = []
  // 묶음 없는 것들이 맨 위에 선다 — 대개 이름·식별자 같은 기본 칸이다.
  if (byName.has('')) ordered.push('')
  for (const one of view?.sections ?? []) {
    if (byName.has(one.name) && !ordered.includes(one.name)) ordered.push(one.name)
  }
  for (const name of byName.keys()) {
    if (!ordered.includes(name)) ordered.push(name)
  }

  return ordered.map((name) => {
    const spec = view?.sections?.find((one) => one.name === name)
    return {
      name,
      columns: spec?.columns ?? 1,
      collapsed: spec?.collapsed ?? false,
      defs: byName.get(name) ?? [],
    }
  })
}

export function PropertyFields({ defs, values, onChange, disabled, view }: Props) {
  if (defs.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        이 타입에는 아직 속성이 없습니다. 온톨로지 관리에서 정의하면 여기에 칸이 생깁니다.
      </p>
    )
  }

  function set(key: string, next: unknown) {
    onChange({ ...values, [key]: next })
  }

  const groups = groupBySection(defs, view)

  return (
    <div className="space-y-5">
      {groups.map((group) => (
        <Section key={group.name || '__none__'} group={group}>
          <div
            className={
              group.columns === 3
                ? 'grid gap-4 sm:grid-cols-3'
                : group.columns === 2
                  ? 'grid gap-4 sm:grid-cols-2'
                  : 'space-y-4'
            }
          >
            {group.defs.map((def) => (
              <div key={def.key} className="space-y-1.5">
                <Label htmlFor={`prop-${def.key}`}>
                  {def.label}
                  {def.unit && <span className="text-muted-foreground ml-1">({def.unit})</span>}
                  {def.required && <span className="text-destructive ml-1">*</span>}
                </Label>

                {def.data_type === 'file' ? (
                  /* **첨부는 저장한 뒤에 붙는다.** 파일은 객체 id 에 매달리므로, 만들기
               화면에서 미리 올릴 자리가 없다. 빈 칸을 놓아 두면 「올렸는데 안
               붙었다」 가 되므로 무엇을 해야 하는지 적는다. */
                  <p className="text-muted-foreground text-sm">
                    저장한 뒤 상세 화면에서 파일을 올립니다.
                  </p>
                ) : def.multi ? (
                  <ManyValues
                    def={def}
                    values={Array.isArray(values[def.key]) ? (values[def.key] as unknown[]) : []}
                    onChange={(next) => set(def.key, next)}
                    disabled={disabled}
                  />
                ) : (
                  <OneValue
                    def={def}
                    value={values[def.key]}
                    onChange={(next) => set(def.key, next)}
                    disabled={disabled}
                  />
                )}

                {def.help && <p className="text-muted-foreground text-xs">{def.help}</p>}
              </div>
            ))}
          </div>
        </Section>
      ))}
    </div>
  )
}

/** 묶음 하나. 이름이 없으면 제목 없이 칸만 선다 — **한 덩어리에 제목을 달면
 *  「여기 여럿이 있다」 로 읽힌다.** */
function Section({
  group,
  children,
}: {
  group: { name: string; collapsed: boolean }
  children: ReactNode
}) {
  const [open, setOpen] = useState(!group.collapsed)
  if (!group.name) return <>{children}</>

  return (
    <div className="space-y-2">
      <button
        type="button"
        className="text-muted-foreground hover:text-foreground flex items-center gap-1 text-xs font-medium"
        onClick={() => setOpen((now) => !now)}
      >
        <ChevronRight className={cn('size-3.5 transition-transform', open && 'rotate-90')} />
        {group.name}
      </button>
      {open && children}
    </div>
  )
}

/**
 * 보기 전용 — 값 하나를 사람이 읽는 말로.
 *
 * `refLabels` 는 서버가 실어 준 「id -> 이름」 이다. **없으면 UUID 가 그대로
 * 보이는데, 그 칸은 아무것도 말해 주지 못한다.**
 */
export function propertyText(
  def: PropertyDef,
  value: unknown,
  refLabels: Record<string, string> = {},
): string {
  if (value === undefined || value === null || value === '') return '—'
  if (Array.isArray(value)) {
    return value.length === 0
      ? '—'
      : value.map((item) => oneText(def.data_type, item, refLabels)).join(', ')
  }
  return oneText(def.data_type, value, refLabels)
}

function oneText(kind: DataType, value: unknown, refLabels: Record<string, string>): string {
  if (kind === 'bool') return value === true ? '예' : '아니오'
  if (kind === 'object_ref' && typeof value === 'string') {
    // **못 찾으면 「지워진 객체」 라고 말한다.** id 를 그대로 두면 사람은 그것이
    // 값인 줄 알고, 없어진 것인지 원래 그런 것인지 구별할 수 없다.
    return refLabels[value] ?? '(찾을 수 없는 객체)'
  }
  return String(value)
}
