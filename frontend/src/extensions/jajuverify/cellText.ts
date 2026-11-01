/**
 * 칸 글 안의 사진 표시 — 백엔드 `app/extensions/jajuverify/cells.py` 의 짝(ADR 0044).
 *
 * 글 칸의 사진은 **그 칸의 첨부 목록이 정본**이고, 칸 글 안의 한 줄 표시는 자리와 폭만 말한다:
 *
 *     ![이름](photo:<첨부 id>?w=<폭>)
 *
 * - 제 줄 하나를 통째로 차지한다(앞뒤에 다른 글이 있으면 표시가 아니라 글이다).
 * - 폭은 칸 너비의 % — 10 ~ 100(정수). 비우면 `DEFAULT_WIDTH`. 손잡이로 끌면 10 단위로 붙고(`snapWidth`),
 *   빠른 폭은 25 · 50 · 75 · 100.
 *
 * 표시가 없는 사진은 칸 끝에 기본 폭으로 서고, 그 칸의 첨부가 아닌 id 를 가리키는 표시는 그리지
 * 않는다(지운 사진 · 남의 칸 사진 — 글에서는 지우지 않는다). 모든 고치기는 **줄 단위**다 — 칸 글
 * 한 덩이를 저장하므로 덮어쓰기 보호(본 값 비교)가 그대로 따라온다.
 */

export const DEFAULT_WIDTH = 50
export const MIN_WIDTH = 10
export const MAX_WIDTH = 100
/** 빠른 폭 단추. */
export const QUICK_WIDTHS = [25, 50, 75, 100]

const MARKER = /^[ \t]*!\[([^\]\n]*)\]\(photo:([0-9a-fA-F-]{36})(?:\?w=(\d{1,3}))?\)[ \t]*$/

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

export function linesOf(text: string): string[] {
  return text === '' ? [] : text.split('\n')
}

function joined(lines: string[]): string {
  return lines.join('\n')
}

/** 표시 줄을 걷어 낸 글 — 이름표 · 확인 창처럼 글을 줄여 보이는 자리. */
export function plainText(text: string): string {
  return linesOf(text)
    .filter((line) => !markerOf(line))
    .join('\n')
    .trim()
}

/** 표시 줄을 「〔사진〕」 으로 — 충돌 상자처럼 두 글을 나란히 견주는 자리. */
export function readableText(text: string): string {
  return linesOf(text)
    .map((line) => (markerOf(line) ? '〔사진〕' : line))
    .join('\n')
}

/** 글 속 표시의 차례대로 사진 id. */
export function placedIds(text: string): string[] {
  return linesOf(text)
    .map((line) => markerOf(line)?.id)
    .filter((id): id is string => Boolean(id))
}

/** 칸을 그리는 덩어리 — 글 조각(그 줄의 범위)과 사진(그 줄). */
export type Block =
  | { kind: 'text'; start: number; count: number; text: string }
  | { kind: 'photo'; line: number; id: string; width: number; alt: string; placed: boolean }

/**
 * 글을 덩어리로 — 이어진 글 줄은 한 조각, 표시 줄은 사진 하나. `known` 은 그 칸의 첨부(정본): 모르는
 * id 의 표시는 **그리지 않고**(줄은 글에 남는다), 표시가 없는 사진은 끝에 붙인다(`placed: false`).
 * `editing` 이면 사진 앞 · 사이 · 뒤에 빈 글 조각을 끼워 그 자리에도 쓸 수 있게 한다(그 조각은
 * 비어 있으면 저장되지 않는다).
 */
export function blocksOf(text: string, known: string[], editing: boolean): Block[] {
  const lines = linesOf(text)
  const allowed = new Set(known.map((one) => one.toLowerCase()))
  const out: Block[] = []
  let run: { start: number; lines: string[] } | null = null
  const close = () => {
    if (run)
      out.push({ kind: 'text', start: run.start, count: run.lines.length, text: joined(run.lines) })
    run = null
  }
  lines.forEach((line, index) => {
    const found = markerOf(line)
    if (!found) {
      if (!run) run = { start: index, lines: [] }
      run.lines.push(line)
      return
    }
    close()
    if (allowed.has(found.id)) out.push({ kind: 'photo', line: index, ...found, placed: true })
  })
  close()
  const placed = new Set(placedIds(text))
  for (const id of known) {
    const lower = id.toLowerCase()
    if (placed.has(lower)) continue
    out.push({ kind: 'photo', line: -1, id: lower, width: DEFAULT_WIDTH, alt: '', placed: false })
  }
  if (!editing) return out
  // 쓸 자리 — 맨 앞 · 사진 사이 · 맨 뒤에 글 조각이 없으면 빈 조각을 둔다.
  const filled: Block[] = []
  const blank = (at: number): Block => ({ kind: 'text', start: at, count: 0, text: '' })
  out.forEach((one, index) => {
    const before = filled[filled.length - 1]
    if (one.kind === 'photo' && (!before || before.kind === 'photo')) {
      filled.push(blank(one.placed ? one.line : lines.length))
    }
    filled.push(one)
    if (index === out.length - 1 && one.kind === 'photo') filled.push(blank(lines.length))
  })
  return filled.length ? filled : [blank(0)]
}

/** 글 조각 하나를 바꾼 글 — 비우면 그 줄들을 뺀다(빈 줄을 남기지 않는다). */
export function withTextBlock(
  text: string,
  block: { start: number; count: number },
  value: string,
) {
  const lines = linesOf(text)
  lines.splice(block.start, block.count, ...(value === '' ? [] : value.split('\n')))
  return joined(lines)
}

/** 표시가 없는 사진(칸 끝에 선 것)을 글 끝에 표시로 적는다 — 옮기거나 폭을 바꾸기 전에. */
export function withPlaced(text: string, ids: string[]): string {
  const have = new Set(placedIds(text))
  const add = ids.filter((id) => !have.has(id.toLowerCase())).map((id) => markerLine(id))
  return add.length ? joined([...linesOf(text), ...add]) : text
}

function lineOf(lines: string[], id: string): number {
  return lines.findIndex((line) => markerOf(line)?.id === id.toLowerCase())
}

/** 사진을 한 줄 위 · 아래로 — 글 사이에서 자리를 옮긴다. 끝이면 그대로. */
export function movedPhoto(text: string, id: string, direction: 'up' | 'down'): string {
  const lines = linesOf(withPlaced(text, [id]))
  const at = lineOf(lines, id)
  const to = direction === 'up' ? at - 1 : at + 1
  if (at < 0 || to < 0 || to >= lines.length) return joined(lines)
  ;[lines[at], lines[to]] = [lines[to], lines[at]]
  return joined(lines)
}

/** 사진을 그 줄 자리(`line` — 그 줄 **앞**)로 — 끌어다 놓기. */
export function placedPhoto(text: string, id: string, line: number): string {
  const lines = linesOf(withPlaced(text, [id]))
  const at = lineOf(lines, id)
  if (at < 0) return joined(lines)
  const [taken] = lines.splice(at, 1)
  const to = Math.max(0, Math.min(line > at ? line - 1 : line, lines.length))
  lines.splice(to, 0, taken)
  return joined(lines)
}

/** 사진의 폭을 바꾼다(10 ~ 100). */
export function resizedPhoto(text: string, id: string, width: number): string {
  const lines = linesOf(withPlaced(text, [id]))
  const at = lineOf(lines, id)
  if (at < 0) return joined(lines)
  const found = markerOf(lines[at])
  lines[at] = markerLine(id, width, found?.alt ?? '')
  return joined(lines)
}

/** 사진의 표시 줄을 뺀다(사진을 뗄 때 — 서버도 빼지만 내 글을 먼저 맞춰 둔다). */
export function withoutPhoto(text: string, id: string): string {
  const lines = linesOf(text)
  const at = lineOf(lines, id)
  if (at < 0) return text
  lines.splice(at, 1)
  return joined(lines)
}

/** 커서 자리 — 그 칸 글의 몇째 줄, 그 줄의 몇째 글자. */
export interface Caret {
  line: number
  column: number
}

/** 글 조각 안의 커서(글자 수)를 칸 글 전체의 자리로. */
export function caretIn(block: { start: number; text: string }, offset: number): Caret {
  const before = block.text.slice(0, offset).split('\n')
  return { line: block.start + before.length - 1, column: before[before.length - 1].length }
}

/**
 * 커서 자리에 사진들을 넣는다 — 줄 중간이면 그 줄을 둘로 가른다(표시는 제 줄 하나를 차지한다).
 * 그사이 글이 바뀌어 그 줄이 없으면 끝에 넣는다.
 */
export function insertedPhotos(
  text: string,
  at: Caret | null,
  ids: string[],
  width = DEFAULT_WIDTH,
) {
  const lines = linesOf(text)
  const markers = ids.map((id) => markerLine(id, width))
  if (!at || at.line >= lines.length || at.line < 0) return joined([...lines, ...markers])
  const line = lines[at.line]
  const column = Math.min(at.column, line.length)
  const left = line.slice(0, column)
  const right = line.slice(column)
  // 빈 줄이면 그 줄이 사진 자리가 된다.
  lines.splice(at.line, 1, ...(left ? [left] : []), ...markers, ...(right ? [right] : []))
  return joined(lines)
}
