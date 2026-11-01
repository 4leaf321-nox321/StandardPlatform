/**
 * 일정 칸 — 개선대책에서 나눈 **값의 열**(사용자 요청 「개선 대책과 일정을 나눠줘」, 2026-10-31).
 *
 * 글이 아니라 단계 목록 `[{date, label}]` 이다 — 「11/15 설계 변경」 · 「11/30 재시험」. 날짜는 달력으로
 * 고르고, 단계를 더하고 뺀다. 하나만 넣으면 그냥 날짜 하나다. 저장은 목록 통째로(본 값과 함께 —
 * 덮어쓰기 보호도 목록 통째로), 서버가 날짜 차례로 맞추고 가장 늦은 날을 완료 예정일로 적는다.
 * 날짜를 비운 단계는 저장하지 않는다(빼는 것과 같다).
 */

import { useId, useState } from 'react'
import { Plus, X } from 'lucide-react'

import type { ScheduleItem } from '@/extensions/jajuverify/api'
import { today } from '@/extensions/jajuverify/MeetingCreateDialog'
import { ConflictBox } from '@/shared/components/ConflictBox'
import { SaveState } from '@/shared/components/SaveState'
import { Input } from '@/shared/components/ui/input'
import { useAutoSave } from '@/shared/hooks/useAutoSave'

/** 저장하는 모양 — 날짜가 있는 것만, 날짜 차례로(같은 날은 적은 차례), 이름은 앞뒤 빈칸 없이. */
export function normalSchedule(items: ScheduleItem[]): ScheduleItem[] {
  return items
    .filter((one) => one.date)
    .map((one) => ({ date: one.date, label: one.label.trim() }))
    .map((one, index) => ({ one, index }))
    .sort((a, b) => a.one.date.localeCompare(b.one.date) || a.index - b.index)
    .map(({ one }) => one)
}

const sameSchedule = (a: ScheduleItem[], b: ScheduleItem[]) =>
  JSON.stringify(normalSchedule(a)) === JSON.stringify(normalSchedule(b))

/** 「2026-11-15 설계 변경」 — 볼 때 · 충돌 상자 · 인쇄. */
export function scheduleText(items: ScheduleItem[]): string {
  return normalSchedule(items)
    .map((one) => (one.label ? `${one.date} ${one.label}` : one.date))
    .join('\n')
}

function asSchedule(value: unknown): ScheduleItem[] {
  return Array.isArray(value)
    ? value
        .filter((one): one is ScheduleItem => Boolean(one) && typeof one === 'object')
        .map((one) => ({ date: String(one.date ?? ''), label: String(one.label ?? '') }))
    : []
}

export function ScheduleCell({
  value,
  label,
  canWrite,
  notice,
  onSave,
  onFocusChange,
}: {
  value: ScheduleItem[]
  /** 칸의 이름 — 「1번 줄 일정」. 입력 칸 · 단추 · 시험이 이것으로 찾는다. */
  label: string
  canWrite: boolean
  notice?: string
  onSave: (next: ScheduleItem[], base: ScheduleItem[]) => Promise<void>
  onFocusChange?: (focused: boolean) => void
}) {
  const id = useId()
  const [focused, setFocused] = useState(false)
  const auto = useAutoSave<ScheduleItem[]>(
    value,
    (next, base) => onSave(normalSchedule(next), normalSchedule(base)),
    { delay: 600, equals: sameSchedule, hold: focused },
  )

  if (!canWrite) {
    const shown = normalSchedule(value)
    return shown.length ? (
      <ul aria-label={label} className="space-y-0.5 text-sm">
        {shown.map((one, index) => (
          <li key={`${one.date}-${index}`} className="tabular-nums">
            {one.date}
            {one.label && <span className="ml-1.5">{one.label}</span>}
          </li>
        ))}
      </ul>
    ) : (
      <span aria-label={label} className="text-muted-foreground text-sm print:hidden">
        —
      </span>
    )
  }

  const items = auto.draft
  const set = (next: ScheduleItem[]) => auto.change(next)
  return (
    <div
      className="space-y-1"
      onFocus={() => {
        if (focused) return
        setFocused(true)
        onFocusChange?.(true)
      }}
      onBlur={(event) => {
        if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
        setFocused(false)
        onFocusChange?.(false)
        void auto.flush()
      }}
    >
      {/* 인쇄에는 글만. */}
      <div className="hidden text-sm whitespace-pre-wrap tabular-nums print:block">
        {scheduleText(items)}
      </div>
      <ul className="space-y-1 print:hidden" aria-label={label}>
        {items.map((one, index) => (
          <li key={index} className="flex flex-wrap items-center gap-1">
            <Input
              id={index === 0 ? id : undefined}
              type="date"
              aria-label={`${label} ${index + 1}번째 날짜`}
              value={one.date}
              onChange={(event) =>
                set(
                  items.map((it, at) => (at === index ? { ...it, date: event.target.value } : it)),
                )
              }
              className="h-7 w-[8.5rem] px-1.5 text-xs"
            />
            <Input
              aria-label={`${label} ${index + 1}번째 단계`}
              value={one.label}
              maxLength={200}
              placeholder="단계(예: 설계 변경)"
              onChange={(event) =>
                set(
                  items.map((it, at) => (at === index ? { ...it, label: event.target.value } : it)),
                )
              }
              className="h-7 min-w-0 flex-1 px-1.5 text-xs"
            />
            <button
              type="button"
              aria-label={`${label} ${index + 1}번째 단계 삭제`}
              title="단계 삭제"
              onClick={() => set(items.filter((_, at) => at !== index))}
              className="text-muted-foreground hover:text-destructive rounded p-0.5"
            >
              <X className="size-3.5" />
            </button>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-1.5 print:hidden">
        <button
          type="button"
          aria-label={`${label} 단계 추가`}
          disabled={items.length >= 20}
          onClick={() => set([...items, { date: today(), label: '' }])}
          className="text-muted-foreground hover:text-foreground hover:border-foreground/30 inline-flex h-6 items-center gap-1 rounded-sm border border-dashed px-1.5 text-[11px] disabled:opacity-40"
        >
          <Plus className="size-3.5" />
          일정 추가
        </button>
        <SaveState status={auto.status} error={auto.error} onRetry={() => void auto.flush()} />
      </div>
      {focused && notice && (
        <p className="text-[11px] text-sky-700 print:hidden dark:text-sky-400">{notice}</p>
      )}
      {auto.stale && !auto.conflict && (
        <p className="text-[11px] text-amber-700 print:hidden dark:text-amber-400">
          다른 사람이 이 칸을 수정했습니다 — 저장할 때 확인합니다.
        </p>
      )}
      {auto.conflict && (
        <ConflictBox
          noun="값"
          by={auto.conflict.by}
          at={auto.conflict.at}
          theirs={scheduleText(asSchedule(auto.conflict.current)) || '—'}
          mine={scheduleText(items) || '—'}
          onTakeTheirs={auto.takeTheirs}
          onKeepMine={() => void auto.keepMine()}
        />
      )}
    </div>
  )
}
