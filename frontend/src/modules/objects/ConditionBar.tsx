/**
 * 조건 거르기 — **칸 안에서는 OR, 칸끼리는 AND.**
 *
 * 걸린 조건은 칩으로 서고(「무게 ≥ 10」), 「조건 추가」 는 칸 → 연산 → 값 세 단계다.
 * 연산은 칸의 종류가 정한다(숫자·날짜는 범위, 선택은 「그 중 하나」, 모두 「비어 있음」).
 * 서버 `objects/conditions.py` 의 표와 같다 — 어긋나면 서버가 422 로 말한다.
 *
 * 전체를 OR 로 잇는 트리는 안 만든다. 화면에 그릴 수 없고, 그려도 사람이 못 읽는다.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Plus, X } from 'lucide-react'

import { useObjectOptions } from '@/modules/objects/useObjectOptions'
import type { Condition, ConditionOp, LinkedField } from '@/modules/objects/api'
import { CONDITION_MULTI_SEP } from '@/modules/objects/api'
import type { DataType, PropertyDef } from '@/modules/ontology/api'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
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
export function opsFor(dataType: DataType | 'fixed' | 'relation'): ConditionOp[] {
  const any: ConditionOp[] = ['empty', 'notempty']
  if (dataType === 'number' || dataType === 'date' || dataType === 'datetime') {
    return ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in', ...any]
  }
  if (
    dataType === 'text' ||
    dataType === 'text_long' ||
    dataType === 'url' ||
    dataType === 'fixed'
  ) {
    return ['eq', 'ne', 'contains', 'starts', 'in', ...any]
  }
  if (dataType === 'enum' || dataType === 'object_ref') return ['eq', 'ne', 'in', ...any]
  if (dataType === 'bool') return ['eq', ...any]
  return any
}

interface Field {
  key: string
  label: string
  /** `relation` — 상대 타입이 하나로 정해지지 않은 관계. 있음/없음만. */
  data_type: DataType | 'relation'
  enum_options?: string[] | null
  ref_type_slug?: string | null
  /** 이어진 것 너머의 칸이면 그 제목 — 「개발사 (시뮬레이션 기업)」. 자기 칸은 없다. */
  heading?: string
}

function fieldsOf(defs: PropertyDef[], linked: LinkedField[]): Field[] {
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
    // **이어진 것 너머의 칸은 자기 칸 뒤에, 제목 아래로.** 「개발사 › 국가」 가 자기 칸
    // 사이에 섞이면 어느 것이 이 타입의 칸인지 안 읽힌다.
    ...linked.map((one) => ({
      key: one.field,
      label: one.label,
      data_type: one.data_type as DataType | 'relation',
      enum_options: one.enum_options,
      ref_type_slug: one.ref_type_slug,
      heading: one.heading,
    })),
  ]
}

const NO_LINKED: LinkedField[] = []

interface ConditionBarProps {
  defs: PropertyDef[]
  conditions: Condition[]
  onChange: (next: Condition[]) => void
  /** 참조 값을 이름으로 보여 주려고 — id → 이름. 없으면 id 그대로. */
  refLabels?: Record<string, string>
  /** 이어진 것 너머의 칸(`/objects/{slug}/fields`). */
  linked?: LinkedField[]
}

export function ConditionBar({
  defs,
  conditions,
  onChange,
  refLabels = {},
  linked = NO_LINKED,
}: ConditionBarProps) {
  const fields = useMemo(() => fieldsOf(defs, linked), [defs, linked])
  // 고르면서 본 이름 — 이어진 것 너머의 참조는 목록 행에 이름이 안 실려 온다.
  const [known, setKnown] = useState<Record<string, string>>({})
  const remember = useCallback(
    (names: Record<string, string>) => setKnown((current) => ({ ...current, ...names })),
    [],
  )
  const byKey = useMemo(() => new Map(fields.map((one) => [one.key, one])), [fields])
  const [open, setOpen] = useState(false)

  const describe = (one: Condition): string => {
    const field = byKey.get(one.field)
    const label = field?.label ?? one.field
    if (one.op === 'empty' || one.op === 'notempty') return `${label} ${OP_SIGN[one.op]}`
    const values = one.op === 'in' ? one.value.split(CONDITION_MULTI_SEP) : [one.value]
    const shown = values.map((value) => refLabels[value] ?? known[value] ?? value).join(', ')
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
            onNames={remember}
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
  onNames?: (names: Record<string, string>) => void
}

/** 칸 → 연산 → 값. 연산과 값 입력은 칸의 종류를 따라간다. */
function ConditionEditor({ fields, onAdd, onNames }: ConditionEditorProps) {
  // 제목이 같은 칸끼리 — 자기 칸(제목 없음)이 먼저, 이어진 것은 걸음마다.
  const groups = useMemo(() => {
    const out: [string, Field[]][] = []
    for (const one of fields) {
      const heading = one.heading ?? ''
      const last = out[out.length - 1]
      if (last && last[0] === heading) last[1].push(one)
      else out.push([heading, [one]])
    }
    return out
  }, [fields])
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
            {groups.map(([heading, items]) =>
              heading ? (
                <SelectGroup key={heading}>
                  <SelectLabel>{heading}</SelectLabel>
                  {items.map((one) => (
                    <SelectItem key={one.key} value={one.key}>
                      {one.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              ) : (
                items.map((one) => (
                  <SelectItem key={one.key} value={one.key}>
                    {one.label}
                  </SelectItem>
                ))
              ),
            )}
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
          <span className="text-muted-foreground text-xs">
            값{multi ? ' — 여럿 고르면 「그 중 하나」' : ''}
          </span>
          <ValueInput
            field={field}
            onNames={onNames}
            multi={multi}
            value={value}
            picked={picked}
            onValue={setValue}
            onPicked={setPicked}
          />
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
  onNames?: (names: Record<string, string>) => void
}

/** 칸의 종류대로 — 선택은 고르고, 참조는 이름으로 찾고, 숫자·날짜는 그 입력을. */
function ValueInput({ field, multi, value, picked, onValue, onPicked, onNames }: ValueInputProps) {
  if (field.data_type === 'object_ref') {
    return field.ref_type_slug ? (
      <RefValueInput
        typeSlug={field.ref_type_slug}
        onNames={onNames}
        multi={multi}
        value={value}
        picked={picked}
        onValue={onValue}
        onPicked={onPicked}
      />
    ) : (
      <p className="text-muted-foreground text-xs">가리킬 타입이 정해져 있지 않습니다.</p>
    )
  }
  return (
    <PlainValueInput
      field={field}
      multi={multi}
      value={value}
      picked={picked}
      onValue={onValue}
      onPicked={onPicked}
    />
  )
}

/**
 * 참조 칸의 값 — **서버가 찾는다.** 200개를 받아 펼치던 목록은 201번째부터 없는 것으로
 * 보였다. 하나면 picker, 여럿(`in`)이면 찾는 칸 + 고른 것을 위에 꽂은 체크 목록.
 */
function RefValueInput({
  typeSlug,
  multi,
  value,
  picked,
  onValue,
  onPicked,
  onNames,
}: Omit<ValueInputProps, 'field'> & { typeSlug: string }) {
  const sources = useMemo(() => [{ slug: typeSlug }], [typeSlug])
  const found = useObjectOptions(sources, { value: multi ? null : value })
  // 고른 것의 이름 — 후보에서 본 것을 기억해 둔다. 검색으로 좁혀도 고른 줄이 이름을 잃지 않게.
  const [seen, setSeen] = useState<Record<string, string>>({})
  useEffect(() => {
    setSeen((current) => {
      const next = { ...current }
      for (const one of found.options) next[one.value] = one.label
      return next
    })
  }, [found.options])
  useEffect(() => {
    onNames?.(seen)
  }, [seen, onNames])

  if (!multi) {
    return (
      <SearchablePicker
        options={found.options}
        pinned={found.pinned}
        total={found.total}
        loading={found.loading}
        onQueryChange={found.setQuery}
        value={value || null}
        onChange={onValue}
        placeholder="고르기"
        searchPlaceholder="이름·식별자로 찾기"
        emptyText={found.failed ? '읽지 못했습니다' : '맞는 객체가 없습니다'}
      />
    )
  }

  const rows = [
    ...picked
      .filter((id) => !found.options.some((one) => one.value === id))
      .map((id) => ({ value: id, label: seen[id] ?? id })),
    ...found.options,
  ]
  return (
    <div className="space-y-1">
      <Input
        value={found.query}
        placeholder="이름·식별자로 찾기"
        className="h-8 text-xs"
        onChange={(event) => found.setQuery(event.target.value)}
      />
      <ul className="max-h-40 space-y-0.5 overflow-y-auto rounded-md border p-1">
        {rows.map((one) => (
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
        {rows.length === 0 && (
          <li className="text-muted-foreground px-1.5 py-1 text-xs">
            {found.loading ? '찾는 중…' : '고를 것이 없습니다.'}
          </li>
        )}
      </ul>
      {found.total > found.options.length && (
        <p className="text-muted-foreground text-[11px]">
          {found.options.length} / {found.total} — 더 있습니다. 이름을 더 쳐서 좁히세요.
        </p>
      )}
    </div>
  )
}

function PlainValueInput({ field, multi, value, picked, onValue, onPicked }: ValueInputProps) {
  const choices =
    field.data_type === 'enum'
      ? (field.enum_options ?? []).map((one) => ({ value: one, label: one }))
      : field.data_type === 'bool'
        ? [
            { value: 'true', label: '예' },
            { value: 'false', label: '아니오' },
          ]
        : null

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
          {choices.length === 0 && (
            <li className="text-muted-foreground px-1.5 py-1 text-xs">고를 것이 없습니다.</li>
          )}
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
