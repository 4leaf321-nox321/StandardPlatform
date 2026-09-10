/**
 * 상태 배지 — **말과 색을 한 곳에서 정한다.**
 *
 * 계정 상태·공지 급이 화면마다 제 색을 고르면, 같은 "정지" 가 목록에서는 회색이고
 * 상세에서는 빨강이 된다. 그러면 색이 아무 뜻도 못 갖는다.
 *
 * ## 도메인이 자기 상태를 더할 때
 *
 * 아래에 표를 하나 만들고 `TABLES` 에 건다. **화면에서 색을 직접 고르지 않는다** —
 * 한 번 그렇게 하면 그 뒤로 전부 그렇게 되고, 그때 이 파일은 아무것도 보장하지
 * 못한다.
 */

import { cn } from '@/shared/lib/utils'

type Tone = 'neutral' | 'good' | 'warn' | 'bad'

const TONE_CLASS: Record<Tone, string> = {
  neutral: 'bg-muted text-muted-foreground',
  good: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
  warn: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
  bad: 'bg-destructive/10 text-destructive',
}

const ACCOUNT: Record<string, { label: string; tone: Tone }> = {
  active: { label: '정상', tone: 'good' },
  // **승인 대기는 나쁨이 아니라 주의다.** 사고가 아니라 누군가 처리해야 하는 일이다.
  pending: { label: '승인 대기', tone: 'warn' },
  suspended: { label: '정지', tone: 'bad' },
}

const NOTICE: Record<string, { label: string; tone: Tone }> = {
  info: { label: '안내', tone: 'neutral' },
  warning: { label: '주의', tone: 'warn' },
  urgent: { label: '긴급', tone: 'bad' },
}

const WORKSPACE: Record<string, { label: string; tone: Tone }> = {
  // **보관은 나쁨이 아니라 중립이다** — 사고가 아니라 조직의 생애다.
  active: { label: '사용', tone: 'good' },
  archived: { label: '보관', tone: 'neutral' },
}

const OBJECT: Record<string, { label: string; tone: Tone }> = {
  active: { label: '사용', tone: 'good' },
  // **더 이상 쓰지 않음은 나쁨이 아니다.** picker 에서 숨을 뿐, 이미 걸린 값과
  // 관계는 그대로 남는다 — 지운 것이 아니다.
  deprecated: { label: '안 씀', tone: 'neutral' },
}

const TABLES = {
  account: ACCOUNT,
  object: OBJECT,
  notice: NOTICE,
  workspace: WORKSPACE,
} as const

export function StatusBadge({
  kind,
  value,
  className,
}: {
  kind: keyof typeof TABLES
  value: string
  className?: string
}) {
  // 모르는 값도 **그대로 보여 준다.** 빈 칸으로 두면 데이터가 없는 것처럼 읽히는데,
  // 실제로는 표에 없는 새 값이 들어온 것이다.
  const found = TABLES[kind][value] ?? { label: value, tone: 'neutral' as Tone }
  return (
    <span
      className={cn(
        'inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium',
        TONE_CLASS[found.tone],
        className,
      )}
    >
      {found.label}
    </span>
  )
}
