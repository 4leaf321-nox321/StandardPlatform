/**
 * 글 안의 사진 표시 — 백엔드 `app/modules/files/photomarks.py` 의 짝. **첨부의 자리와 폭을 글 한
 * 줄로** 적는다:
 *
 *     ![캡션](photo:<첨부 id>?w=<폭>)
 *
 * 두 자리가 같은 표기를 쓴다 — 자주검증의 글 칸(ADR 0044, 폭은 칸 너비의 %)과 문서 속성의 그림
 * (ADR 0056, 폭은 본문 너비의 %). 처음에는 자주검증 확장 안에 있었는데, 문서(코어)가 같은 표기를
 * 쓰려면 확장을 import 해야 해서 여기로 옮겼다 — 확장을 끈 설치에서 코어가 안 뜨면 안 된다.
 *
 * - 제 줄 하나를 통째로 차지할 때가 표시다(`markerOf`). 문서는 마크다운 그림이라 그림 주소만
 *   본다(`photoSource`).
 * - 폭은 10 ~ 100(정수). 손잡이로 끌면 10 단위로 붙는다(`snapWidth`).
 */

export const DEFAULT_WIDTH = 50
export const MIN_WIDTH = 10
export const MAX_WIDTH = 100

const MARKER = /^[ \t]*!\[([^\]\n]*)\]\(photo:([0-9a-fA-F-]{36})(?:\?w=(\d{1,3}))?\)[ \t]*$/
const SOURCE = /^photo:([0-9a-fA-F-]{36})(?:\?w=(\d{1,3}))?$/

export interface Marker {
  id: string
  width: number
  alt: string
}

/** 10 ~ 100 으로 묶는다 — 저장하는 폭. */
export function clampWidth(width: number): number {
  return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.round(width)))
}

/** 손잡이로 끌 때 — 10 단위로 붙는다. */
export function snapWidth(width: number): number {
  return clampWidth(Math.round(width / 10) * 10)
}

/** 그 줄이 표시면 그 사진 · 폭. */
export function markerOf(line: string): Marker | null {
  const found = MARKER.exec(line)
  if (!found) return null
  return {
    alt: found[1],
    id: found[2].toLowerCase(),
    width: found[3] ? clampWidth(Number(found[3])) : DEFAULT_WIDTH,
  }
}

export function markerLine(id: string, width: number = DEFAULT_WIDTH, alt = ''): string {
  return `![${alt}](photo:${id}?w=${clampWidth(width)})`
}

/**
 * 마크다운 그림의 주소가 사진 표시면 그 첨부 id 와 폭 — 폭을 안 적었으면 `null`(그림 크기대로).
 * 문서의 그림(ADR 0056)이 쓴다. 다른 주소(바깥 그림)는 `null` 이다.
 */
export function photoSource(src: string): { id: string; width: number | null } | null {
  const found = SOURCE.exec(src.trim())
  if (!found) return null
  return { id: found[1].toLowerCase(), width: found[2] ? clampWidth(Number(found[2])) : null }
}

/** 본문의 사진 표시가 가리키는 첨부 id — 글 사이의 것까지, 나온 차례대로(겹치면 한 번). */
export function photoIdsIn(text: string): string[] {
  const out: string[] = []
  for (const found of text.matchAll(/!\[[^\]\n]*\]\(photo:([0-9a-fA-F-]{36})(?:\?w=\d{1,3})?\)/g)) {
    const id = found[1].toLowerCase()
    if (!out.includes(id)) out.push(id)
  }
  return out
}
