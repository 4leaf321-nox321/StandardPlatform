/**
 * 많은 것 중에서 하나 고르기.
 *
 * **고를 것이 스물을 넘으면 `<Select>` 를 쓰지 않는다.** 통째로 펼쳐 놓고 눈으로
 * 찾으라고 하면 그 일은 실패하고, **못 찾은 사람은 없다고 결론 내리고 새로 만든다**
 * — 그러면 같은 값이 둘로 갈린다. 닫힌 축(부서·기준정보처럼 값이 정해져 있어야
 * 하는 것)에서는 특히 나쁘다.
 *
 * ## 두 길을 함께 낸다
 *
 *   치는 것    이름의 일부를 알 때
 *   훑는 것    무엇이 있는지 모를 때 — 열자마자 전부 보인다
 *
 * 하나만 있으면 반쪽이다. 검색만 있으면 뭘 쳐야 할지 모르는 사람이 막히고, 목록만
 * 있으면 아는 사람이 스크롤을 해야 한다.
 *
 * ## 고를 수 없는 줄은 이유를 적는다
 *
 * 비활성만 시키고 말 안 하면 버그로 읽힌다. `disabledReason` 이 배지로 붙는다.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronsUpDown, Search } from 'lucide-react'

import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { cn } from '@/shared/lib/utils'

export interface PickerOption {
  value: string
  label: string
  /** 같은 이름이 여럿일 때 구별해 주는 줄. 부서라면 경로, 기종이라면 계열. */
  hint?: string
  /** 검색에 걸리게 할 추가 문자열(별칭·코드). 화면에는 안 보인다. */
  keywords?: string
  /** 값이 있으면 못 고른다. **그 이유가 배지로 뜬다.** */
  disabledReason?: string
}

interface SearchablePickerProps {
  options: PickerOption[]
  value: string | null
  onChange: (value: string) => void
  placeholder?: string
  /** 검색 칸의 안내. 무엇으로 찾을 수 있는지 적는다. */
  searchPlaceholder?: string
  emptyText?: string
  id?: string
  className?: string
}

/** 눈에 같아 보이는 값을 같게 본다 — 전각·대소문자·군더더기 공백. */
function normalize(raw: string): string {
  return raw.normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim()
}

export function SearchablePicker({
  options,
  value,
  onChange,
  placeholder = '고르세요',
  searchPlaceholder = '이름으로 찾기',
  emptyText = '맞는 것이 없습니다',
  id,
  className,
}: SearchablePickerProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const boxRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  const selected = options.find((one) => one.value === value) ?? null

  const shown = useMemo(() => {
    const needle = normalize(query)
    if (!needle) return options
    return options.filter((one) =>
      normalize(`${one.label} ${one.hint ?? ''} ${one.keywords ?? ''}`).includes(needle),
    )
  }, [options, query])

  // 열면 검색 칸으로 바로 간다 — 열고 나서 한 번 더 클릭하게 하지 않는다.
  useEffect(() => {
    if (open) searchRef.current?.focus()
    else setQuery('')
  }, [open])

  // 바깥을 누르거나 Esc 면 닫는다.
  useEffect(() => {
    if (!open) return
    function onPointerDown(event: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) setOpen(false)
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  function pick(option: PickerOption) {
    if (option.disabledReason) return
    onChange(option.value)
    setOpen(false)
  }

  return (
    <div ref={boxRef} className={cn('relative', className)}>
      <Button
        id={id}
        type="button"
        variant="outline"
        role="combobox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="w-full justify-between font-normal"
      >
        <span className={cn('truncate', !selected && 'text-muted-foreground')}>
          {selected ? (selected.hint ?? selected.label) : placeholder}
        </span>
        <ChevronsUpDown className="size-4 shrink-0 opacity-50" />
      </Button>

      {open && (
        <div className="bg-popover absolute z-50 mt-1 w-full rounded-md border shadow-md">
          <div className="relative border-b p-2">
            <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-4 size-4 -translate-y-1/2" />
            <Input
              ref={searchRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={searchPlaceholder}
              className="h-8 pl-8"
            />
          </div>

          {/* **열자마자 전부 보인다.** 무엇이 있는지 모르는 사람의 길이 이것이다. */}
          <ul className="max-h-64 overflow-y-auto p-1">
            {shown.length === 0 && (
              <li className="text-muted-foreground px-2 py-6 text-center text-sm">
                {emptyText}
              </li>
            )}
            {shown.map((one) => (
              <li key={one.value}>
                <button
                  type="button"
                  onClick={() => pick(one)}
                  disabled={Boolean(one.disabledReason)}
                  className={cn(
                    'flex w-full items-start gap-2 rounded-sm px-2 py-1.5 text-left text-sm',
                    one.disabledReason
                      ? 'cursor-not-allowed opacity-60'
                      : 'hover:bg-accent hover:text-accent-foreground',
                  )}
                >
                  <Check
                    className={cn(
                      'mt-0.5 size-4 shrink-0',
                      one.value === value ? 'opacity-100' : 'opacity-0',
                    )}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{one.label}</span>
                    {one.hint && (
                      <span className="text-muted-foreground block truncate text-xs">
                        {one.hint}
                      </span>
                    )}
                  </span>
                  {/* **왜 못 고르는지 적는다.** 비활성만 시키고 말 안 하면
                      버그로 읽힌다. */}
                  {one.disabledReason && (
                    <span className="text-muted-foreground shrink-0 rounded border px-1 text-[10px] leading-4">
                      {one.disabledReason}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>

          {/* 몇 개 중 몇 개를 보고 있는지. 거르고 나서 「이게 전부인가」 를 묻지
              않게 한다. */}
          {options.length > 0 && (
            <p className="text-muted-foreground border-t px-3 py-1.5 text-xs tabular-nums">
              {shown.length} / {options.length}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
