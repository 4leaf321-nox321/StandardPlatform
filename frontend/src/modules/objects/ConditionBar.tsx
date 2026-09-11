/**
 * 조건 거르기 — **칸 안에서는 OR, 칸끼리는 AND.**
 *
 * 걸린 조건은 칩으로 서고(「무게 ≥ 10」), 「조건 추가」 는 칸 → 연산 → 값 세 단계다.
 * 연산은 칸의 종류가 정한다(숫자·날짜는 범위, 선택은 「그 중 하나」, 모두 「비어 있음」).
 * 서버 `objects/conditions.py` 의 표와 같다 — 어긋나면 서버가 422 로 말한다.
 *
 * 전체를 OR 로 잇는 트리는 안 만든다. 화면에 그릴 수 없고, 그려도 사람이 못 읽는다.
 */

import { useEffect, useMemo, useState } from 'react'
import { Plus, X } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import type { Condition, ConditionOp } from '@/modules/objects/api'
import { CONDITION_MULTI_SEP } from '@/modules/objects/api'
import type { DataType, PropertyDef } from '@/modules/ontology/api'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'

/** 고정 칸 — 속성이 아니라 객체 자체의 것. */
const FIXED: { key: string; label: string; data_type: DataType }[] = [
  { key: 'label', label: '이름', data_type: 'text' },
  { key: 'key', label: '식별자', data_type: 'text' },
]

const OP_LABEL: Record<ConditionOp, string> = {
  eq: '같음',
  ne: '다름',
  gt: '보다 큼',
  gte: '이상',
  lt: '보다 작음',
  lte: '이하',
  in: '그 중 하나',
  contains: '포함',
  starts: '로 시작',
  empty: '비어 있음',
  notempty: '비어 있지 않음',
}

const OP_SIGN: Record<ConditionOp, string> = {
  eq: '=',
  ne: '≠',
  gt: '>',
  gte: '≥',
  lt: '<',
  lte: '≤',
  in: '∈',
  contains: '∋',
  starts: '^',
  empty: '= ∅',
  notempty: '≠ ∅',
}

/** 칸 종류별 연산 — 서버 `ops_for` 와 같은 표. */
export function opsFor(dataType: DataType | 'fixed'): ConditionOp[] {
  const any: ConditionOp[] = ['empty', 'notempty']
  if (dataType === 'number' || dataType === 'date' || dataType === 'datetime') {
    return ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in', ...any]
  }
  if (dataType === 'text' || dataType === 'text_long' || dataType === 'url' || dataType === 'fixed') {
    return ['eq', 'ne', 'contains', 'starts', 'in', ...any]
  }
  if (dataType === 'enum' || dataType === 'object_ref') return ['eq', 'ne', 'in', ...any]
  if (dataType === 'bool') return ['eq', ...any]
  return any
}

interface Field {
  key: string
  label: string
  data_type: DataType
  enum_options?: string[] | null
  ref_type_slug?: string | null
}

function fieldsOf(defs: PropertyDef[]): Field[] {
  return [
    ...FIXED,
    ...defs
      .filter((def) => def.data_type !== 'file')
      .map((def) => ({
        key: def.key,
        label: def.label,
        data_type: def.data_type,
        enum_options: def.enum_options,
        ref_type_slug: def.ref_type_slug,
      })),
  ]
}

interface ConditionBarProps {
  defs: PropertyDef[]
  conditions: Condition[]
  onChange: (next: Condition[]) => void
  /** 참조 값을 이름으로 보여 주려고 — id → 이름. 없으면 id 그대로. */
  refLabels?: Record<string, string>
}

export function ConditionBar({ defs, conditions, onChange, refLabels = {} }: ConditionBarProps) {
  const fields = useMemo(() => fieldsOf(defs), [defs])
  const byKey = useMemo(() => new Map(fields.map((one) => [one.key, one])), [fields])
  const [open, setOpen] = useState(false)

  const describe = (one: Condition): string => {
    const field = byKey.get(one.field)
    const label = field?.label ?? one.field
    if (one.op === 'empty' || one.op === 'notempty') return `${label} ${OP_SIGN[one.op]}`
    const values = one.op === 'in' ? one.value.split(CONDITION_MULTI_SEP) : [one.value]
    const shown = values.map((value) => refLabels[value] ?? value).join(', ')
    return `${label} ${OP_SIGN[one.op]} ${shown}`
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {conditions.map((one, index) => (
        <span
          key={`${one.field}.${one.op}.${index}`}
          className="bg-muted inline-flex items-center gap-1 rounded-full py-0.5 pr-1 pl-2.5 text-xs"
          title={`${byKey.get(one.field)?.label ?? one.field} ${OP_LABEL[one.op]} ${one.value}`}
        >
          {describe(one)}
          <button
            type="button"
            className="hover:bg-background rounded-full p-0.5"
            aria-label={`조건 지우기: ${describe(one)}`}
            onClick={() => onChange(conditions.filter((_, i) => i !== index))}
          >
            <X className="size-3" />
          </button>
        </span>
      ))}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button size="sm" variant="outline" className="h-7">
            <Plus className="mr-1 size-3.5" />
            조건 추가
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-80 p-3">
          <ConditionEditor
            fields={fields}
            onAdd={(next) => {
              onChange([...conditions, next])
              setOpen(false)
            }}
          />
        </PopoverContent>
      </Popover>
      {conditions.length > 1 && (
        <button
          type="button"
          className="text-muted-foreground text-xs underline"
          onClick={() => onChange([])}
        >
          전부 지우기
        </button>
      )}
    </div>
  )
}

interface ConditionEditorProps {
  fields: Field[]
  onAdd: (next: Condition) => void
}

/** 칸 → 연산 → 값. 연산과 값 입력은 칸의 종류를 따라간다. */
function ConditionEditor({ fields, onAdd }: ConditionEditorProps) {
  const [fieldKey, setFieldKey] = useState(fields[0]?.key ?? '')
  const field = fields.find((one) => one.key === fieldKey) ?? fields[0]
  const kind = FIXED.some((one) => one.key === field?.key) ? 'fixed' : (field?.data_type ?? 'text')
  const ops = opsFor(kind)
  const [op, setOp] = useState<ConditionOp>(ops[0])
  const [value, setValue] = useState('')
  const [picked, setPicked] = useState<string[]>([])

  // 칸이 바뀌면 연산·값을 그 칸에 맞게 되돌린다.
  useEffect(() => {
    setOp(opsFor(kind)[0])
    setValue('')
    setPicked([])
  }, [fieldKey, kind])

  const needsValue = op !== 'empty' && op !== 'notempty'
  const multi = op === 'in'
  const finalValue = multi ? picked.join(CONDITION_MULTI_SEP) : value
  const ready = !needsValue || finalValue.trim() !== ''

  if (!field) return <p className="text-muted-foreground text-xs">걸 수 있는 칸이 없습니다.</p>

  return (
    <div className="space-y-2 text-sm">
      <label className="block space-y-1">
        <span className="text-muted-foreground text-xs">칸</span>
        <Select value={fieldKey} onValueChange={setFieldKey}>
          <SelectTrigger size="sm" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {fields.map((one) => (
              <SelectItem key={one.key} value={one.key}>
                {one.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>
      <label className="block space-y-1">
        <span className="text-muted-foreground text-xs">연산</span>
        <Select value={op} onValueChange={(next) => setOp(next as ConditionOp)}>
          <SelectTrigger size="sm" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ops.map((one) => (
              <SelectItem key={one} value={one}>
                {OP_LABEL[one]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>
      {needsValue && (
        <div className="space-y-1">
          <span className="text-muted-foreground text-xs">값{multi ? ' — 여럿 고르면 「그 중 하나」' : ''}</span>
          <ValueInput field={field} multi={multi} value={value} picked={picked} onValue={setValue} onPicked={setPicked} />
        </div>
      )}
      <div className="flex justify-end">
        <Button
          size="sm"
          disabled={!ready}
          onClick={() => onAdd({ field: field.key, op, value: needsValue ? finalValue : '' })}
        >
          걸기
        </Button>
      </div>
    </div>
  )
}

interface ValueInputProps {
  field: Field
  multi: boolean
  value: string
  picked: string[]
  onValue: (next: string) => void
  onPicked: (next: string[]) => void
}

/** 칸의 종류대로 — 선택은 고르고, 참조는 이름으로 찾고, 숫자·날짜는 그 입력을. */
function ValueInput({ field, multi, value, picked, onValue, onPicked }: ValueInputProps) {
  const [options, setOptions] = useState<{ value: string; label: string }[] | null>(null)

  // 참조 칸 — 상대 타입의 객체를 읽어 고르게 한다. 200개까지(목록 상한).
  useEffect(() => {
    // 칸이 바뀌면 옛 타입의 후보를 먼저 비운다 — 안 비우면 새 목록이 올 때까지 남의 객체가 고를 수 있게 보인다.
    setOptions(null)
    if (field.data_type !== 'object_ref' || !field.ref_type_slug) return
    let cancelled = false
    objectApi
      .list(field.ref_type_slug, { limit: 200 })
      .then((page) => {
        if (!cancelled) {
          setOptions(
            page.items.map((one) => ({ value: one.id, label: one.key ? `${one.label} (${one.key})` : one.label })),
          )
        }
      })
      .catch(() => {
        if (!cancelled) setOptions([])
      })
    return () => {
      cancelled = true
    }
  }, [field.data_type, field.ref_type_slug])

  const choices =
    field.data_type === 'enum'
      ? (field.enum_options ?? []).map((one) => ({ value: one, label: one }))
      : field.data_type === 'object_ref'
        ? options
        : field.data_type === 'bool'
          ? [
              { value: 'true', label: '예' },
              { value: 'false', label: '아니오' },
            ]
          : null

  if (choices === null && field.data_type === 'object_ref') {
    return <p className="text-muted-foreground text-xs">읽는 중…</p>
  }
  if (choices) {
    if (multi) {
      return (
        <ul className="max-h-40 space-y-0.5 overflow-y-auto rounded-md border p-1">
          {choices.map((one) => (
            <li key={one.value}>
              <label className="hover:bg-muted flex cursor-pointer items-center gap-2 rounded px-1.5 py-0.5 text-xs">
                <input
                  type="checkbox"
                  className="size-3.5"
                  checked={picked.includes(one.value)}
                  onChange={(event) =>
                    onPicked(
                      event.target.checked
                        ? [...picked, one.value]
                        : picked.filter((item) => item !== one.value),
                    )
                  }
                />
                {one.label}
              </label>
            </li>
          ))}
          {choices.length === 0 && <li className="text-muted-foreground px-1.5 py-1 text-xs">고를 것이 없습니다.</li>}
        </ul>
      )
    }
    return (
      <Select value={value} onValueChange={onValue}>
        <SelectTrigger size="sm" className="w-full">
          <SelectValue placeholder="고르기" />
        </SelectTrigger>
        <SelectContent>
          {choices.map((one) => (
            <SelectItem key={one.value} value={one.value}>
              {one.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    )
  }

  const inputType =
    field.data_type === 'number'
      ? 'number'
      : field.data_type === 'date'
        ? 'date'
        : field.data_type === 'datetime'
          ? 'datetime-local'
          : 'text'
  if (multi) {
    return (
      <Input
        value={picked.join(CONDITION_MULTI_SEP)}
        placeholder={`값을 ${CONDITION_MULTI_SEP} 로 갈라 여럿`}
        onChange={(event) => onPicked(event.target.value.split(CONDITION_MULTI_SEP))}
      />
    )
  }
  return (
    <Input
      type={inputType}
      value={value}
      onChange={(event) => onValue(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.form?.requestSubmit()
      }}
    />
  )
}
