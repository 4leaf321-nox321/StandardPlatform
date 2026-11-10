/**
 * 문서의 그림(ADR 0056) — 본문에 사진 표시를 넣는 일. 표기는 `modules/files/photoMarker.ts`.
 *
 * 그림 하나는 **제 문단 하나**다 — 앞뒤에 빈 줄을 둔다. 글 줄에 붙여 적으면 마크다운에서 같은
 * 문단이 되어, 그 문단을 고치거나 옮길 때 그림이 따라간다(사람이 의도한 것이 아니다).
 */

import { photoIdsIn } from '@/modules/files/photoMarker'

/** 폭을 안 적은 표시 — 그림 크기대로(본문보다 넓으면 맞춘다). 폭은 `?w=50` 처럼 적어 정한다. */
export function photoLine(id: string, alt = ''): string {
  return `![${alt}](photo:${id})`
}

/**
 * 커서 자리(`at`)에 그림들을 넣는다 — 줄 중간이면 그 줄을 가른다. 넣은 뒤의 커서는 넣은 것의
 * 바로 뒤다(이어서 쓰면 그림 다음 문단이 된다).
 */
export function insertPhotos(
  text: string,
  at: number,
  ids: string[],
): { text: string; caret: number } {
  if (ids.length === 0) return { text, caret: at }
  const place = Math.max(0, Math.min(at, text.length))
  const before = text.slice(0, place)
  const after = text.slice(place)
  // 앞 · 뒤를 빈 줄로 띄운다 — 이미 띄워져 있으면 더하지 않는다.
  const lead =
    before === '' ? '' : before.endsWith('\n\n') ? '' : before.endsWith('\n') ? '\n' : '\n\n'
  const trail =
    after === '' ? '\n' : after.startsWith('\n\n') ? '' : after.startsWith('\n') ? '\n' : '\n\n'
  const block = ids.map((id) => photoLine(id)).join('\n\n')
  const inserted = `${lead}${block}${trail}`
  return { text: `${before}${inserted}${after}`, caret: before.length + inserted.length }
}

/** 본문에서 그 그림을 몇 곳에서 쓰나 — 삭제 확인 창이 센다. */
export function usesOf(text: string, id: string): number {
  const wanted = id.toLowerCase()
  let count = 0
  for (const found of text.matchAll(/\]\(photo:([0-9a-fA-F-]{36})(?:\?w=\d{1,3})?\)/g)) {
    if (found[1].toLowerCase() === wanted) count += 1
  }
  return count
}

/** 본문이 쓰는 그림 id — 「본문에 없음」 배지가 견준다. */
export function placedIn(text: string): Set<string> {
  return new Set(photoIdsIn(text))
}
