/**
 * 유효 기간 두 칸 — 시작일(포함) · 끝날(제외)(ADR 0039). 관계 추가 · 수정 창이 같이 쓴다.
 *
 * **끝날은 「그날부터 무효」 다.** 닫힌 구간으로 읽으면 「3월 1일부터 B 로 바뀜」 을 A 의 끝날
 * 2월 28일(윤년이면 29일)로 적어야 한다 — 사람은 바뀌는 날을 알지, 그 전날을 알지 않는다.
 */

import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'

interface Props {
  from: string
  to: string
  onFrom: (next: string) => void
  onTo: (next: string) => void
  idPrefix: string
}

export function PeriodFields({ from, to, onFrom, onTo, idPrefix }: Props) {
  /** 시작일이 끝날보다 늦거나 같으면 서버가 거절한다 — 저장 전에 같은 말을 한다. */
  const backwards = Boolean(from && to && from >= to)
  return (
    <div className="space-y-1.5">
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-from`}>시작일 (포함)</Label>
          <Input
            id={`${idPrefix}-from`}
            type="date"
            value={from}
            onChange={(event) => onFrom(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-to`}>끝날 (제외)</Label>
          <Input
            id={`${idPrefix}-to`}
            type="date"
            value={to}
            onChange={(event) => onTo(event.target.value)}
          />
        </div>
      </div>
      <p className="text-muted-foreground text-xs">
        <b>끝날은 그날부터 무효입니다</b> — 「3월 1일부터 바뀜」 은 앞 관계의 끝날과 뒤 관계의
        시작일을 같은 날로 적습니다. 비우면 그쪽 끝이 열립니다(둘 다 비우면 늘 유효).
      </p>
      {backwards && <p className="text-destructive text-xs">시작일은 끝날보다 앞이어야 합니다.</p>}
    </div>
  )
}
