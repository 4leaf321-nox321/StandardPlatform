/**
 * 관계의 유효 기간을 사람이 읽을 말로(ADR 0039) — 서버의 `edges.describe` 와 같은 말이다.
 *
 * **끝날은 제외**라 「~ 2026-03-01」 로 적으면 3월 1일까지 유효한 것으로 읽힌다 — 그래서 「전까지」
 * 를 붙인다. 「3월 1일부터 바뀜」 은 앞 관계의 끝날과 뒤 관계의 시작일이 같은 날이다.
 */

export function hasPeriod(from?: string | null, to?: string | null): boolean {
  return Boolean(from || to)
}

export function periodText(from?: string | null, to?: string | null): string {
  if (!from && !to) return '늘 유효'
  const head = from ? `${from} 부터` : '처음부터'
  const tail = to ? `${to} 전까지` : '끝없이'
  return `${head} ${tail}`
}
