/**
 * 화면에 보일 이름 — 서버의 `display_label`(타입의 앞머리 + 이름, ADR 0088 — 「3.2 측정 방법」), 없으면
 * 이름. 수정 창 · 찾기는 `label` 을 쓴다.
 *
 * `api.ts` 와 따로 둔다 — 시험이 `api` 를 통째로 흉내 내도 이것은 그대로 돈다.
 */
export function shownName(row: { label: string; display_label?: string }): string {
  return row.display_label || row.label
}
