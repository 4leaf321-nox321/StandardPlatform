/**
 * 지표의 **주기** — 저장은 시간(`interval_hours`), 뜻은 셋이다(`metrics/services.due`).
 *
 * - 0: 손으로만
 * - 1 ~ 23: N시간마다 — 낮에도 센다
 * - 24 의 배수: 매일 밤 · N일마다 밤 — 밤 시간(기본 2시)에만 센다. 무거운 계산이 낮에 안
 *   돌고, 백업에 그날 값이 든다.
 *
 * 예전에는 「주기(시간)」 숫자 칸 하나였다. 타이머가 하루 한 번만 돌아 「6시간」 이라 적어도
 * 하루 한 번 셌다 — 적은 것과 도는 것이 달랐다. 이제 고르는 말이 곧 도는 방식이다.
 */

export type IntervalMode = 'nights' | 'hours' | 'manual'

export const MAX_HOURS = 23
export const MAX_NIGHTS = 30

export function splitInterval(hours: number): { mode: IntervalMode; count: number } {
  if (hours <= 0) return { mode: 'manual', count: 1 }
  if (hours < 24) return { mode: 'hours', count: hours }
  return { mode: 'nights', count: Math.max(1, Math.floor(hours / 24)) }
}

export function joinInterval(mode: IntervalMode, count: number): number {
  if (mode === 'manual') return 0
  const n = Math.max(1, Math.floor(count) || 1)
  return mode === 'hours' ? Math.min(n, MAX_HOURS) : Math.min(n, MAX_NIGHTS) * 24
}

/** 목록 · 정의 창에 쓰는 말. */
export function intervalText(hours: number): string {
  const { mode, count } = splitInterval(hours)
  if (mode === 'manual') return '손으로만'
  if (mode === 'hours') return `${count}시간마다`
  return count === 1 ? '매일 밤' : `${count}일마다 밤`
}
