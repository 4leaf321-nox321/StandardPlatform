/**
 * 기준일 고르개 — **그날 유효한 관계만 본다**(ADR 0039).
 *
 * 관계 종류가 유효 기간을 받으면 같은 트리가 날마다 다르다 — 3월에 빠진 부품은 4월의 트리에
 * 없다. 비우면 오늘이다(서버의 기본). 트리 · 하위 포함 · 관계 조건 · 통계 · 롤업 · 지식 그래프가
 * 같은 값을 싣는다 — 화면마다 따로 고르면 트리에는 없는 부품이 통계에는 든다.
 */

import { CalendarClock } from 'lucide-react'

import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { cn } from '@/shared/lib/utils'

interface Props {
  /** `YYYY-MM-DD`. 빈 글자면 오늘. */
  value: string
  onChange: (next: string) => void
  className?: string
  /** 입력 칸의 id — 시험 · 라벨이 찾는다. */
  id?: string
}

export function AsOfPicker({ value, onChange, className, id = 'as-of' }: Props) {
  return (
    <div className={cn('flex items-center gap-1.5', className)}>
      <label
        htmlFor={id}
        className="text-muted-foreground flex items-center gap-1 text-sm whitespace-nowrap"
        title="그날 유효한 관계만 표시합니다(트리 · 하위 포함 · 관계 조건 · 통계 · 롤업). 비우면 오늘입니다."
      >
        <CalendarClock className="size-3.5" />
        기준일
      </label>
      <Input
        id={id}
        type="date"
        aria-label="기준일"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 w-40"
      />
      {value ? (
        <Button variant="ghost" size="sm" className="h-8 px-2" onClick={() => onChange('')}>
          오늘
        </Button>
      ) : (
        <span className="text-muted-foreground text-xs">오늘</span>
      )}
    </div>
  )
}
