/**
 * 전체 지도의 원반 — **기대 수와 밀도 그림**(ADR 0046). 그리기 라이브러리를 모르는 순수 함수다.
 *
 * 원반의 넓이가 그 허브의 기록 수(판에 얼린 수)라서, 원반 안의 밀도는 어느 허브나 같다(판 뒤에 는
 * 것만큼 진해진다). 멀리서 보면 원반 여럿이 한 화면 칸에 겹치고, 그 칸의 색이 「여기에 기록이
 * 몰렸다」 를 말한다. 확대한 상자의 기대 수(원반 넓이 x 상자에 든 몫)는 기록을 안 읽고 센다 — 서버가
 * 같은 셈으로 다시 본다(`mapread.expected_in`).
 */

import type { MapBox, MapData } from '@/modules/graph/api'

export interface Discs {
  n: number
  x: Float64Array
  y: Float64Array
  r: Float64Array
  /** 켠 기록 타입의 지금 수(보이는 부서만). */
  count: Float64Array
}

/** 원반 — 켠 기록 타입의 수만 더한다. */
export function discsOf(data: MapData, on: Set<string>): Discs {
  const n = data.discs.x.length
  const count = new Float64Array(n)
  const { count_disc: disc, count_type: type, count_n: value } = data.discs
  for (let k = 0; k < disc.length; k += 1) {
    const slug = data.types[type[k]]?.slug
    if (slug && on.has(slug)) count[disc[k]] += value[k]
  }
  return {
    n,
    x: Float64Array.from(data.discs.x),
    y: Float64Array.from(data.discs.y),
    r: Float64Array.from(data.discs.r),
    count,
  }
}

/** 원반 ∩ 상자 넓이를 셀 때 x 방향 조각 수 — 서버(`maphash.box_fractions`)와 같다. 원반 위 표본으로
 * 세면 원반에 견줘 아주 작은 상자에서 기대 수가 25% 틀렸다(2,000만 DB 실측). */
const SLICES = 256

/** 원반(cx, cy, r) 넓이 중 상자 안의 몫. */
export function boxFraction(cx: number, cy: number, r: number, box: MapBox): number {
  if (r <= 0) return box.x0 <= cx && cx <= box.x1 && box.y0 <= cy && cy <= box.y1 ? 1 : 0
  if (cx - r >= box.x0 && cx + r <= box.x1 && cy - r >= box.y0 && cy + r <= box.y1) return 1
  const dx = Math.max(box.x0 - cx, 0, cx - box.x1)
  const dy = Math.max(box.y0 - cy, 0, cy - box.y1)
  if (Math.hypot(dx, dy) > r) return 0
  const a = Math.max(cx - r, box.x0)
  const b = Math.min(cx + r, box.x1)
  const width = Math.max(b - a, 0)
  let area = 0
  for (let k = 0; k < SLICES; k += 1) {
    const x = a + (width * (k + 0.5)) / SLICES - cx
    const half = Math.sqrt(Math.max(r * r - x * x, 0))
    area += Math.max(Math.min(cy + half, box.y1) - Math.max(cy - half, box.y0), 0)
  }
  return Math.min((area * width) / SLICES / (Math.PI * r * r), 1)
}

/** 상자 안의 기대 수 — 집계 x 넓이 몫(기록을 안 읽는다). */
export function expectedIn(discs: Discs, box: MapBox): number {
  let total = 0
  for (let k = 0; k < discs.n; k += 1) {
    if (discs.count[k] > 0)
      total += discs.count[k] * boxFraction(discs.x[k], discs.y[k], discs.r[k], box)
  }
  return total
}

/** 화면의 투영 — 그래프 좌표 → 화면 px. */
export interface Projection {
  width: number
  height: number
  /** 그래프 한 단위가 화면 몇 px 인가. */
  unitPx: number
  project: (x: number, y: number) => [number, number]
}

export interface Grid {
  cols: number
  rows: number
  cell: number
  /** 칸마다 기록 수. */
  values: Float32Array
}

/**
 * 칸(`cell` px)마다 기록 수 — 원반이 칸보다 작으면 그 수를 가운데 칸에, 크면 원반에 드는 칸에 고르게
 * 나눈다(넓이 = 기록 수라 원반 안은 고르다). 화면 밖 원반은 건너뛴다.
 */
export function densityGrid(discs: Discs, view: Projection, cell: number): Grid {
  const cols = Math.max(1, Math.ceil(view.width / cell))
  const rows = Math.max(1, Math.ceil(view.height / cell))
  const values = new Float32Array(cols * rows)
  for (let k = 0; k < discs.n; k += 1) {
    const n = discs.count[k]
    if (n <= 0) continue
    const [px, py] = view.project(discs.x[k], discs.y[k])
    const rp = discs.r[k] * view.unitPx
    if (px + rp < 0 || py + rp < 0 || px - rp > view.width || py - rp > view.height) continue
    if (rp < cell) {
      const c = Math.floor(px / cell)
      const r = Math.floor(py / cell)
      if (c >= 0 && c < cols && r >= 0 && r < rows) values[r * cols + c] += n
      continue
    }
    const c0 = Math.max(0, Math.floor((px - rp) / cell))
    const c1 = Math.min(cols - 1, Math.floor((px + rp) / cell))
    const r0 = Math.max(0, Math.floor((py - rp) / cell))
    const r1 = Math.min(rows - 1, Math.floor((py + rp) / cell))
    // 원반 넓이 중 칸 하나의 몫 — 화면 밖으로 잘린 칸은 그냥 빠진다(그 칸은 안 그린다).
    const share = (n * cell * cell) / (Math.PI * rp * rp)
    const r2 = rp * rp
    for (let r = r0; r <= r1; r += 1) {
      const cy = (r + 0.5) * cell - py
      for (let c = c0; c <= c1; c += 1) {
        const cx = (c + 0.5) * cell - px
        if (cx * cx + cy * cy <= r2) values[r * cols + c] += share
      }
    }
  }
  return { cols, rows, cell, values }
}

/** 원반 하나의 밀도(판의 얼린 수 그대로일 때) — 칸마다 기록 수. 색의 가운데가 이것이다. */
export function discDensity(scale: number, view: Projection, cell: number): number {
  if (scale <= 0 || view.unitPx <= 0) return 1
  const perUnit = 1 / (Math.PI * scale * scale)
  return (perUnit * cell * cell) / (view.unitPx * view.unitPx)
}

const LIGHT: [number, number, number][] = [
  [219, 234, 254],
  [59, 130, 246],
  [30, 58, 138],
]
const DARK: [number, number, number][] = [
  [30, 58, 95],
  [59, 130, 246],
  [191, 219, 254],
]

/** 밀도 → 색. `ratio` 는 원반 하나의 밀도에 대한 배(로그 눈금, 1/100 ~ 100 배). */
export function densityColor(ratio: number, dark: boolean): [number, number, number] {
  const t = Math.min(1, Math.max(0, (Math.log10(Math.max(ratio, 1e-6)) + 2) / 4))
  const stops = dark ? DARK : LIGHT
  const at = t * (stops.length - 1)
  const i = Math.min(stops.length - 2, Math.floor(at))
  const f = at - i
  const a = stops[i]
  const b = stops[i + 1]
  return [
    Math.round(a[0] + (b[0] - a[0]) * f),
    Math.round(a[1] + (b[1] - a[1]) * f),
    Math.round(a[2] + (b[2] - a[2]) * f),
  ]
}

let scratch: HTMLCanvasElement | null = null

/** 밀도 그림을 캔버스에 — 칸마다 색(작은 그림 한 장을 늘려 붙인다), 큰 원반은 테두리까지. `faint` 면
 * 옅게(점을 그릴 때의 밑그림). */
export function drawDensity(
  ctx: CanvasRenderingContext2D,
  discs: Discs,
  view: Projection,
  options: { scale: number; dark: boolean; faint: boolean; cell?: number },
): void {
  const cell = options.cell ?? 3
  const grid = densityGrid(discs, view, cell)
  const unit = discDensity(options.scale, view, cell)
  scratch ??= document.createElement('canvas')
  if (scratch.width !== grid.cols || scratch.height !== grid.rows) {
    scratch.width = grid.cols
    scratch.height = grid.rows
  }
  const small = scratch.getContext('2d')
  if (small) {
    const image = small.createImageData(grid.cols, grid.rows)
    const alpha = options.faint ? 64 : 255
    for (let k = 0; k < grid.values.length; k += 1) {
      const value = grid.values[k]
      if (value <= 0) continue
      const [red, green, blue] = densityColor(value / unit, options.dark)
      image.data[k * 4] = red
      image.data[k * 4 + 1] = green
      image.data[k * 4 + 2] = blue
      image.data[k * 4 + 3] = alpha
    }
    small.putImageData(image, 0, 0)
    ctx.save()
    ctx.imageSmoothingEnabled = false
    ctx.drawImage(scratch, 0, 0, grid.cols * cell, grid.rows * cell)
    ctx.restore()
  }
  ctx.save()
  ctx.lineWidth = 1
  ctx.strokeStyle = options.dark ? 'rgba(148,163,184,0.55)' : 'rgba(71,85,105,0.45)'
  for (let k = 0; k < discs.n; k += 1) {
    const rp = discs.r[k] * view.unitPx
    if (rp < 6) continue
    const [px, py] = view.project(discs.x[k], discs.y[k])
    if (px + rp < 0 || py + rp < 0 || px - rp > view.width || py - rp > view.height) continue
    ctx.beginPath()
    ctx.arc(px, py, rp, 0, 2 * Math.PI)
    ctx.stroke()
  }
  ctx.restore()
}
