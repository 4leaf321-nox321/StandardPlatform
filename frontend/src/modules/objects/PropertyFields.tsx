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

import { useEffect, useState } from 'react'
import { Plus, X } from 'lucide-react'

import type { DataType, PropertyDef } from '@/modules/ontology/api'
import { objectApi } from '@/modules/objects/api'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
import type { PickerOption } from '@/shared/components/SearchablePicker'
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

/** 고를 것이 이보다 많으면 통째로 펼치지 않는다 — 눈으로 찾는 일은 실패한다. */
const PICKER_THRESHOLD = 20

export type PropertyValues = Record<string, unknown>

interface Props {
  defs: PropertyDef[]
  values: PropertyValues
  onChange: (values: PropertyValues) => void
  disabled?: boolean
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
 * 둘 다 있다. `SearchablePicker` 가 그 둘을 이미 한다.
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
  const [options, setOptions] = useState<PickerOption[]>([])
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!typeSlug) return
    let cancelled = false
    objectApi
      .list(typeSlug, { limit: 200 })
      .then((page) => {
        if (cancelled) return
        setOptions(
          page.items.map((row) => ({
            value: row.id,
            label: row.label,
            hint: row.key ?? undefined,
            // 이미 못 고르는 줄은 **이유를 적는다.** 비활성만 시키고 말 안 하면
            // 버그로 읽힌다.
            disabledReason: row.status === 'deprecated' ? '안 쓰는 값' : undefined,
          })),
        )
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [typeSlug])

  if (!typeSlug) {
    return (
      <p className="text-muted-foreground text-sm">
        가리킬 타입이 정해져 있지 않습니다. 속성 정의에서 대상 타입을 고르세요.
      </p>
    )
  }
  if (failed) {
    return <p className="text-destructive text-sm">고를 것을 불러오지 못했습니다.</p>
  }

  return (
    <SearchablePicker
      options={options}
      value={value}
      onChange={onChange}
      placeholder="객체를 고르세요"
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

export function PropertyFields({ defs, values, onChange, disabled }: Props) {
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

  return (
    <div className="space-y-4">
      {defs.map((def) => (
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
