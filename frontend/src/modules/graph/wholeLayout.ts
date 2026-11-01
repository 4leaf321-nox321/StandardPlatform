/**
 * 정적 그리기의 배치 — **자리만 정한다**(ADR 0045). 그리는 쪽(`WholeCanvas`)은 정해진 자리를 받아
 * 그리기만 하고, 그 자리가 어느 배치에서 왔는지 모른다 — 다음 단계에서 서버가 미리 계산한 자리를
 * 줄 때도 그리는 쪽은 그대로다.
 *
 *   단계(rings)    출발점이 가운데, BFS 단계마다 동심원. 원 안에서는 부모의 각도 → 타입 차례로
 *                  늘어세워 자식이 부모 곁에 선다. O(n log n) — 거의 즉시
 *   무리(cluster)  ForceAtlas2(Barnes-Hut). 작업자 스레드에서 돈다(`wholeLayout.worker.ts`)
 *
 * ## 무리의 함정 — 반복 한 번이 2초
 *
 * graphology 의 ForceAtlas2 는 노드 13.6만 개에서 반복 한 번이 0.6 ~ 1.9초다(개발 DB 의 시장 서비스
 * 전부, 실측). 식을 때까지 백 번이면 분 단위다. 그래서 노드가 `CORE_NODES` 를 넘으면 **연결 많은
 * 노드(핵심)만 ForceAtlas2 로 놓고**, 나머지는 핵심에서 넓혀 가며 **이미 놓인 이웃 자리의 가운데**에
 * 놓는다. 같은 자리에 모이는 것(같은 모델 · 같은 센터를 가리키는 기록 수천 건)은 해바라기 모양으로
 * 펼친다 — 그 원반의 크기가 곧 그 자리의 수다.
 *
 * 시작 자리는 타입별 부채꼴 + 놓인 이웃의 가운데라, 처음부터 대략의 모양이 서서 빨리 식는다.
 * 난수는 씨앗을 박는다 — 같은 그림은 열 때마다 같은 모양이어야 「아까 그 무리」 를 다시 찾는다.
 */

import iterate from 'graphology-layout-forceatlas2/iterate.js'

import { degreesOf } from '@/modules/graph/wholeData'

/** 자리 — x0, y0, x1, y1, … */
export type Positions = Float32Array

export interface RingInput {
  n: number
  depth: ArrayLike<number>
  parent: ArrayLike<number>
  type: ArrayLike<number>
}

export interface ClusterInput {
  n: number
  src: Int32Array
  dst: Int32Array
  type: Int32Array
}

export interface ClusterOptions {
  /** 0 ~ 1. 작업자가 화면에 「자리 잡는 중 40%」 로 보낸다. */
  onProgress?: (fraction: number) => void
  /** ForceAtlas2 로 놓을 핵심 노드 수 — 시험이 작게 준다. */
  core?: number
  /** 반복 수 — 비우면 노드 수로 정한다. */
  iterations?: number
}

/** 이보다 많으면 연결 많은 것만 ForceAtlas2 로 놓는다 — 1.2만 개면 반복 한 번이 수십 ms 다. */
export const CORE_NODES = 12_000
const TAU = Math.PI * 2
/** 해바라기 배치의 각 — 황금각. 몇 개를 놓든 고르게 퍼진다. */
const GOLDEN = Math.PI * (3 - Math.sqrt(5))
/** 노드의 자리를 정할 때 보는 이웃 수 — 허브의 이웃을 다 더하면 그것만 O(선 수 x 이웃 수)다. */
const NEIGHBOR_SAMPLE = 16

/** 노드 수에 맞춘 반복 수 — 작을수록 많이(빨리 끝나므로), 클수록 적게(시작 자리가 이미 대략 맞다). */
export function clusterIterations(n: number): number {
  if (n <= 1_000) return 500
  if (n <= 5_000) return 250
  return 120
}

/** 씨앗을 박은 난수(mulberry32) — 같은 그림이 열 때마다 같은 모양이게. */
export function seededRandom(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function wrap(angle: number): number {
  return ((angle % TAU) + TAU) % TAU
}

/**
 * 단계 — 출발점이 가운데, 단계마다 동심원.
 *
 * 원 안의 차례는 **부모의 각도 → 타입 → 번호**다. 그대로 고르게 늘어세우면 자식이 많은 부모의
 * 자식들이 원을 거의 다 차지해 부모와 엇갈리므로, 원 전체를 돌려 「자식과 부모의 각도 차」 의 평균이
 * 0 이 되게 맞춘다. 원의 반지름은 그 단계의 노드 수에 맞춰 넓힌다(옆 노드와 한 칸씩).
 */
export function ringLayout({ n, depth, parent, type }: RingInput): Positions {
  const out = new Float32Array(n * 2)
  const angle = new Float64Array(n)
  let deepest = 0
  for (let i = 0; i < n; i += 1) deepest = Math.max(deepest, depth[i])
  const rings: number[][] = Array.from({ length: deepest + 1 }, () => [])
  for (let i = 0; i < n; i += 1) rings[depth[i]].push(i)

  let radius = 0
  rings.forEach((ring, d) => {
    if (ring.length === 0) return
    if (d === 0 && ring.length === 1) {
      angle[ring[0]] = 0
      return // 가운데(0, 0)
    }
    const parentAngle = (i: number) => (parent[i] >= 0 ? wrap(angle[parent[i]]) : 0)
    ring.sort((a, b) => parentAngle(a) - parentAngle(b) || type[a] - type[b] || a - b)
    // 옆 노드와 한 칸 — 그 단계가 많으면 원이 넓어진다. 단계 사이도 앞 원에 비례해 벌린다.
    radius = Math.max(radius + Math.max(2, radius * 0.6), ring.length / TAU)
    const step = TAU / ring.length
    let sx = 0
    let sy = 0
    if (d > 0) {
      ring.forEach((node, k) => {
        if (parent[node] < 0) return
        const off = (k + 0.5) * step - angle[parent[node]]
        sx += Math.cos(off)
        sy += Math.sin(off)
      })
    }
    const turn = d > 0 && (sx !== 0 || sy !== 0) ? -Math.atan2(sy, sx) : 0
    ring.forEach((node, k) => {
      const a = (k + 0.5) * step + turn
      angle[node] = a
      out[node * 2] = radius * Math.cos(a)
      out[node * 2 + 1] = radius * Math.sin(a)
    })
  })
  return out
}

/** 이웃 표(CSR) — 노드 i 의 이웃은 `list[offsets[i] .. offsets[i + 1])`. */
export function neighborsOf(
  n: number,
  src: ArrayLike<number>,
  dst: ArrayLike<number>,
): { offsets: Uint32Array; list: Int32Array } {
  const offsets = new Uint32Array(n + 1)
  for (let k = 0; k < src.length; k += 1) {
    if (src[k] === dst[k]) continue
    offsets[src[k] + 1] += 1
    offsets[dst[k] + 1] += 1
  }
  for (let i = 0; i < n; i += 1) offsets[i + 1] += offsets[i]
  const fill = offsets.slice(0, n)
  const list = new Int32Array(offsets[n])
  for (let k = 0; k < src.length; k += 1) {
    if (src[k] === dst[k]) continue
    list[fill[src[k]]++] = dst[k]
    list[fill[dst[k]]++] = src[k]
  }
  return { offsets, list }
}

/** 연결 많은 것부터의 차례(같으면 번호 순) — 셈 정렬이라 O(n). */
function byDegree(degree: Uint32Array): Int32Array {
  let most = 0
  for (const one of degree) most = Math.max(most, one)
  const count = new Uint32Array(most + 2)
  for (const one of degree) count[most - one + 1] += 1
  for (let d = 0; d <= most; d += 1) count[d + 1] += count[d]
  const out = new Int32Array(degree.length)
  for (let i = 0; i < degree.length; i += 1) out[count[most - degree[i]]++] = i
  return out
}

/**
 * 무리 — ForceAtlas2. 작업자 스레드에서 부른다(같은 스레드에서 부르면 그동안 화면이 선다).
 *
 * 노드가 `core` 이하면 전부를 ForceAtlas2 로, 넘으면 연결 많은 `core` 개만 그렇게 놓고 나머지는
 * 놓인 이웃의 가운데에 해바라기로 놓는다(머리 주석). 진행률은 ForceAtlas2 가 90%, 나머지가 10%.
 */
export function clusterLayout(input: ClusterInput, options: ClusterOptions = {}): Positions {
  const { n, src, dst, type } = input
  const report = options.onProgress ?? (() => {})
  const random = seededRandom(0x5eed)
  const pos = new Float32Array(n * 2)
  if (n === 0) {
    report(1)
    return pos
  }
  const degree = degreesOf(n, src, dst)
  const near = neighborsOf(n, src, dst)
  const order = byDegree(degree)
  const coreCount = Math.min(n, options.core ?? CORE_NODES)
  const slot = new Int32Array(n).fill(-1)
  for (let k = 0; k < coreCount; k += 1) slot[order[k]] = k
  const placed = new Uint8Array(n)

  // 타입별 부채꼴 — 핵심 안의 수에 비례한 각. 이웃이 아직 안 놓인 노드는 제 타입의 부채꼴에서 시작한다.
  let types = 0
  for (let i = 0; i < n; i += 1) types = Math.max(types, type[i] + 1)
  const share = new Float64Array(types + 1)
  for (let k = 0; k < coreCount; k += 1) share[type[order[k]] + 1] += 1
  for (let t = 0; t < types; t += 1) share[t + 1] += share[t]
  const scale = Math.sqrt(coreCount) * 10
  const inSector = (node: number, reach: number) => {
    const t = type[node]
    const from = share[t] / coreCount
    const width = Math.max((share[t + 1] - share[t]) / coreCount, 0.02)
    const a = TAU * (from + random() * width)
    const r = reach * Math.sqrt(random())
    return [r * Math.cos(a), r * Math.sin(a)] as const
  }
  /** 놓인 이웃의 가운데 — 없으면 null. */
  const centroid = (node: number): [number, number] | null => {
    let sx = 0
    let sy = 0
    let seen = 0
    for (let at = near.offsets[node]; at < near.offsets[node + 1]; at += 1) {
      const other = near.list[at]
      if (!placed[other]) continue
      sx += pos[other * 2]
      sy += pos[other * 2 + 1]
      seen += 1
      if (seen >= NEIGHBOR_SAMPLE) break
    }
    return seen ? [sx / seen, sy / seen] : null
  }

  // 1. 핵심의 시작 자리 — 연결 많은 것부터, 놓인 이웃의 가운데(조금 흔들어) 또는 제 부채꼴.
  const jitter = scale / Math.sqrt(coreCount)
  for (let k = 0; k < coreCount; k += 1) {
    const node = order[k]
    const middle = centroid(node)
    const [x, y] = middle
      ? [middle[0] + (random() - 0.5) * jitter, middle[1] + (random() - 0.5) * jitter]
      : inSector(node, scale)
    pos[node * 2] = x
    pos[node * 2 + 1] = y
    placed[node] = 1
  }

  // 2. 핵심을 ForceAtlas2 로 — 행렬 모양은 graphology-layout-forceatlas2 의 `graphToByteArrays` 와 같다.
  const PPN = 10
  const PPE = 3
  const nodes = new Float32Array(coreCount * PPN)
  for (let k = 0; k < coreCount; k += 1) {
    const node = order[k]
    nodes[k * PPN] = pos[node * 2]
    nodes[k * PPN + 1] = pos[node * 2 + 1]
    nodes[k * PPN + 6] = 1 // 질량 — 아래에서 선마다 더한다
    nodes[k * PPN + 7] = 1 // 수렴
    nodes[k * PPN + 8] = 1 // 크기
  }
  let coreEdges = 0
  for (let e = 0; e < src.length; e += 1) {
    if (src[e] !== dst[e] && slot[src[e]] >= 0 && slot[dst[e]] >= 0) coreEdges += 1
  }
  const edges = new Float32Array(coreEdges * PPE)
  let at = 0
  for (let e = 0; e < src.length; e += 1) {
    const a = slot[src[e]]
    const b = slot[dst[e]]
    if (src[e] === dst[e] || a < 0 || b < 0) continue
    edges[at * PPE] = a * PPN
    edges[at * PPE + 1] = b * PPN
    edges[at * PPE + 2] = 1
    nodes[a * PPN + 6] += 1
    nodes[b * PPN + 6] += 1
    at += 1
  }
  const iterations = options.iterations ?? clusterIterations(coreCount)
  const settings = {
    linLogMode: false,
    outboundAttractionDistribution: false,
    adjustSizes: false,
    edgeWeightInfluence: 1,
    scalingRatio: 10,
    strongGravityMode: true,
    gravity: 0.05,
    slowDown: 1 + Math.log(coreCount),
    barnesHutOptimize: coreCount > 2000,
    barnesHutTheta: 0.8,
  }
  const chunk = Math.max(1, Math.round(iterations / 40))
  for (let done = 0; done < iterations; done += chunk) {
    const steps = Math.min(chunk, iterations - done)
    for (let s = 0; s < steps; s += 1) iterate(settings, nodes, edges)
    report((0.9 * (done + steps)) / iterations)
  }
  for (let k = 0; k < coreCount; k += 1) {
    const x = nodes[k * PPN]
    const y = nodes[k * PPN + 1]
    // 수가 깨지면(겹친 두 점 사이의 힘) 시작 자리를 둔다 — 한 점 때문에 그림 전체가 사라지지 않게.
    if (Number.isFinite(x) && Number.isFinite(y)) {
      pos[order[k] * 2] = x
      pos[order[k] * 2 + 1] = y
    }
  }

  // 3. 나머지 — 핵심에서 넓혀 가며 놓인 이웃의 가운데에. 같은 자리는 해바라기로 펼친다.
  if (coreCount < n) {
    let length = 0
    for (let k = 0; k < coreEdges; k += 1) {
      const a = edges[k * PPE] / PPN
      const b = edges[k * PPE + 1] / PPN
      length += Math.hypot(nodes[a * PPN] - nodes[b * PPN], nodes[a * PPN + 1] - nodes[b * PPN + 1])
    }
    let extent = 0
    for (let k = 0; k < coreCount; k += 1) {
      const node = order[k]
      extent = Math.max(extent, Math.hypot(pos[node * 2], pos[node * 2 + 1]))
    }
    // 간격 — 나머지를 다 펼쳐도 그 원반들의 넓이가 핵심이 차지한 원의 3할을 넘지 않게(원반끼리
    // 덜 겹친다). 선의 길이에 견주어 넓게는 안 둔다 — 그러면 이웃 곁이 아니게 된다.
    const byArea = extent > 0 ? extent * Math.sqrt(0.3 / (n - coreCount)) : Infinity
    const byEdge = coreEdges ? (length / coreEdges) * 0.12 : jitter * 0.3
    const spacing = Math.max(Math.min(byArea, byEdge), 1e-3)
    const crowd = new Map<string, number>()
    const settle = (node: number, x: number, y: number) => {
      const key = `${Math.round(x / spacing)},${Math.round(y / spacing)}`
      const k = crowd.get(key) ?? 0
      crowd.set(key, k + 1)
      const r = spacing * Math.sqrt(k + 0.5)
      pos[node * 2] = x + r * Math.cos(k * GOLDEN)
      pos[node * 2 + 1] = y + r * Math.sin(k * GOLDEN)
      placed[node] = 1
    }
    const queue = new Int32Array(n)
    let head = 0
    let tail = 0
    for (let k = 0; k < coreCount; k += 1) queue[tail++] = order[k]
    let next = 0 // 핵심에서 안 닿는 덩어리 — 연결 많은 것부터 바깥 고리에 새로 심는다
    while (tail < n || head < tail) {
      if (head === tail) {
        while (placed[order[next]]) next += 1
        const seed = order[next]
        const [x, y] = inSector(seed, 1)
        const r = extent * (1.1 + 0.3 * random()) || scale
        const norm = Math.hypot(x, y) || 1
        settle(seed, (x / norm) * r, (y / norm) * r)
        queue[tail++] = seed
      }
      const here = queue[head++]
      for (let k = near.offsets[here]; k < near.offsets[here + 1]; k += 1) {
        const other = near.list[k]
        if (placed[other]) continue
        const middle = centroid(other) ?? [pos[here * 2], pos[here * 2 + 1]]
        settle(other, middle[0], middle[1])
        queue[tail++] = other
      }
      if ((head & 0x3fff) === 0) report(0.9 + (0.1 * head) / n)
    }
  }
  report(1)
  return pos
}
