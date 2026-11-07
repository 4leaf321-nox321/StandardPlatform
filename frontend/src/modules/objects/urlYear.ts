/**
 * 주소 ↔ 연도 — 타입 목록과 인터페이스 목록이 **같은 규칙**으로 읽는다.
 *
 * 목록의 기본은 올해다(연도를 쓰는 축에서 전체를 먼저 보이면 몇 해치가 섞여 뜬다). 그래서
 * 연도 없이 센 수를 싣고 오는 링크(홈 위젯 · 채울 곳 · MCP 답의 화면 주소)는 `year=all` 을 붙이고,
 * 그 해로 센 것은 `year=2025` 처럼 그 해를 싣는다. 안 읽던 때는 해를 버리고 올해로 열어, 눌러 연
 * 목록의 수가 답의 수와 달랐다.
 */

/** 서버(`year` 인자)가 받는 범위 — 밖이면 주소의 값을 안 읽는다. */
const MIN_YEAR = 1900
const MAX_YEAR = 2999

/** 주소의 `year` — `all` 이면 전부(null), 네 자리 해면 그 해, 없거나 못 읽으면 올해. */
export function yearFromParams(params: URLSearchParams): number | null {
  const raw = params.get('year')
  if (raw === 'all') return null
  if (raw && /^[0-9]{4}$/.test(raw)) {
    const year = Number(raw)
    if (year >= MIN_YEAR && year <= MAX_YEAR) return year
  }
  return new Date().getFullYear()
}

/**
 * 고를 수 있는 해 — 올해에서 뒤로 열 해, 그리고 **주소가 싣고 온 해**(그 밖이면). 목록에 없는
 * 해가 걸리면 고르개가 빈 칸으로 보이고, 사람은 연도가 안 걸린 줄 안다.
 */
export function yearChoices(selected: number | null): number[] {
  const thisYear = new Date().getFullYear()
  const out = Array.from({ length: 11 }, (_, index) => thisYear - index)
  if (selected !== null && !out.includes(selected)) {
    out.push(selected)
    out.sort((a, b) => b - a)
  }
  return out
}
