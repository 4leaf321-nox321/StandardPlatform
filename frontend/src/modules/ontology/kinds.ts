/**
 * 속성 종류의 성질 — 화면 여기저기가 같은 판단을 하게 한 곳에.
 *
 * `api.ts` 와 따로 둔다 — 시험이 `api.ts` 를 통째로 가짜로 바꾸므로, 거기 두면 이 판단까지 사라진다.
 */

/**
 * **값이 속성 칸에 없는 종류** — 파일은 첨부 표, 문서는 문서 표에 산다(ADR 0053). 목록 열 · 조건 ·
 * 지표 · 일괄 입력 · 동기화 · 장표에 서지 않는다. 서버의 `DETACHED_KINDS` 와 같다.
 */
export function isDetached(kind: string): boolean {
  return kind === 'file' || kind === 'document'
}
