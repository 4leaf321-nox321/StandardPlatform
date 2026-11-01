/**
 * 정적 그리기의 배치 · 자료 — **자리가 약속대로 서나**(ADR 0045).
 *
 * 그림은 눈으로 보는 것이고, 여기서 보는 것은 그 자리의 성질이다: 단계는 동심원이고 자식이 부모
 * 곁에 서는가, 무리는 이어진 것끼리 모이는가, 큰 그림의 「핵심 + 이웃 곁」 은 겹치지 않고 이웃
 * 곁에 놓는가, 같은 자료는 같은 모양인가.
 */

import { describe, expect, it } from 'vitest'

import type { Whole } from '@/modules/graph/api'
import { degreesOf, prepareWhole } from '@/modules/graph/wholeData'
import { clusterLayout, ringLayout } from '@/modules/graph/wholeLayout'

const radius = (pos: Float32Array, i: number) => Math.hypot(pos[i * 2], pos[i * 2 + 1])
const angle = (pos: Float32Array, i: number) => Math.atan2(pos[i * 2 + 1], pos[i * 2])
const gap = (a: number, b: number) => {
  const d = Math.abs(a - b) % (Math.PI * 2)
  return Math.min(d, Math.PI * 2 - d)
}
const distance = (pos: Float32Array, a: number, b: number) =>
  Math.hypot(pos[a * 2] - pos[b * 2], pos[a * 2 + 1] - pos[b * 2 + 1])

describe('단계 — 동심원', () => {
  /** 0 ← 부모 넷(1~4) ← 부모마다 자식 여덟. */
  function tree() {
    const depth = [0, 1, 1, 1, 1]
    const parent = [-1, 0, 0, 0, 0]
    for (let p = 1; p <= 4; p += 1) {
      for (let k = 0; k < 8; k += 1) {
        depth.push(2)
        parent.push(p)
      }
    }
    const n = depth.length
    return { n, depth, parent, type: depth.map((d) => (d === 2 ? 1 : 0)) }
  }

  it('출발점이 가운데, 단계마다 같은 반지름의 원이고 바깥일수록 넓다', () => {
    const input = tree()
    const pos = ringLayout(input)
    expect(radius(pos, 0)).toBe(0)
    const ring1 = [1, 2, 3, 4].map((i) => radius(pos, i))
    const ring2 = Array.from({ length: 32 }, (_, k) => radius(pos, 5 + k))
    for (const r of ring1) expect(r).toBeCloseTo(ring1[0], 4)
    for (const r of ring2) expect(r).toBeCloseTo(ring2[0], 4)
    expect(ring2[0]).toBeGreaterThan(ring1[0])
    expect(ring1[0]).toBeGreaterThan(0)
  })

  it('자식은 부모 곁에 선다 — 부모와의 각도 차가 원을 넷으로 나눈 것보다 작다', () => {
    const input = tree()
    const pos = ringLayout(input)
    for (let i = 5; i < input.n; i += 1) {
      expect(gap(angle(pos, i), angle(pos, input.parent[i]))).toBeLessThan(Math.PI / 4)
    }
  })

  it('같은 원 안에서는 같은 부모의 자식끼리 이어 선다', () => {
    const input = tree()
    const pos = ringLayout(input)
    const ring = Array.from({ length: 32 }, (_, k) => 5 + k).sort(
      (a, b) => ((angle(pos, a) + 7) % 7) - ((angle(pos, b) + 7) % 7),
    )
    // 각도 순으로 돌며 부모가 바뀌는 횟수 — 부모 넷이면 원을 한 바퀴 돌 때 네 번(이어 서면).
    let changes = 0
    ring.forEach((node, k) => {
      if (input.parent[node] !== input.parent[ring[(k + 1) % ring.length]]) changes += 1
    })
    expect(changes).toBe(4)
  })
})

describe('무리 — ForceAtlas2', () => {
  /** 여섯 개짜리 덩어리 둘(서로 다 이어짐)과 그 사이의 선 하나. */
  function twoClusters() {
    const src: number[] = []
    const dst: number[] = []
    for (const base of [0, 6]) {
      for (let a = 0; a < 6; a += 1) {
        for (let b = a + 1; b < 6; b += 1) {
          src.push(base + a)
          dst.push(base + b)
        }
      }
    }
    src.push(0)
    dst.push(6)
    return {
      n: 12,
      src: Int32Array.from(src),
      dst: Int32Array.from(dst),
      type: new Int32Array(12),
    }
  }

  it('이어진 것끼리 모인다 — 덩어리 안의 거리가 덩어리 사이보다 짧다', () => {
    const pos = clusterLayout(twoClusters(), { iterations: 300 })
    expect(pos.every(Number.isFinite)).toBe(true)
    let inside = 0
    let across = 0
    for (let a = 0; a < 12; a += 1) {
      for (let b = a + 1; b < 12; b += 1) {
        if (a < 6 === b < 6) inside += distance(pos, a, b) / 30
        else across += distance(pos, a, b) / 36
      }
    }
    expect(inside).toBeLessThan(across)
  })

  it('같은 자료는 같은 모양이다 — 열 때마다 「아까 그 무리」 를 다시 찾는다', () => {
    const a = clusterLayout(twoClusters(), { iterations: 50 })
    const b = clusterLayout(twoClusters(), { iterations: 50 })
    expect(Array.from(a)).toEqual(Array.from(b))
  })

  it('진행률은 늘기만 하고 1 로 끝난다', () => {
    const seen: number[] = []
    clusterLayout(twoClusters(), { iterations: 80, onProgress: (p) => seen.push(p) })
    expect(seen.length).toBeGreaterThan(2)
    for (let k = 1; k < seen.length; k += 1) expect(seen[k]).toBeGreaterThanOrEqual(seen[k - 1])
    expect(seen[seen.length - 1]).toBe(1)
  })

  it('핵심 밖의 노드는 이웃 곁에, 겹치지 않게 놓인다', () => {
    // 허브 둘에 잎 백 개씩 — 핵심을 열둘로 줄이면 잎 대부분이 「이웃 곁」 으로 놓인다.
    const src: number[] = []
    const dst: number[] = []
    for (let k = 0; k < 200; k += 1) {
      src.push(k < 100 ? 0 : 1)
      dst.push(2 + k)
    }
    src.push(0)
    dst.push(1)
    const input = {
      n: 202,
      src: Int32Array.from(src),
      dst: Int32Array.from(dst),
      type: new Int32Array(202),
    }
    const pos = clusterLayout(input, { core: 12, iterations: 100 })
    expect(pos.every(Number.isFinite)).toBe(true)
    // 핵심 열둘은 허브 둘 + 번호가 앞선 잎 열(셈 정렬의 차례) — 그 밖의 잎을 본다.
    for (let k = 10; k < 200; k += 1) {
      const leaf = 2 + k
      const own = k < 100 ? 0 : 1
      expect(distance(pos, leaf, own)).toBeLessThan(distance(pos, leaf, 1 - own))
    }
    const spots = new Set(Array.from({ length: 202 }, (_, i) => `${pos[i * 2]},${pos[i * 2 + 1]}`))
    expect(spots.size).toBe(202)
  })
})

describe('자료 준비', () => {
  const whole: Whole = {
    seed: 'focus',
    focus: 'a',
    seed_types: ['part'],
    depth: 1,
    records: false,
    max_nodes: 200_000,
    max_edges: 700_000,
    types: [{ slug: 'part', label: '부품', count: 3 }],
    relations: [{ slug: 'near', label: '가까움', inverse_label: '', directed: true, count: 3 }],
    nodes: {
      ids: ['a', 'b', 'c'],
      type: [0, 0, 0],
      label: ['A', 'B', 'C'],
      depth: [0, 1, 1],
      parent: [-1, 0, 0],
    },
    edges: { src: [0, 0, 1], dst: [1, 2, 1], rel: [0, 0, 0] },
  }

  it('연결 수는 선의 양 끝을 세고, 자기 자신을 가리키는 선은 한 번이다', () => {
    expect(Array.from(degreesOf(3, [0, 0, 1], [1, 2, 1]))).toEqual([2, 2, 1])
    const prepared = prepareWhole(whole)
    expect(prepared.n).toBe(3)
    expect(prepared.m).toBe(3)
    expect(prepared.index.get('c')).toBe(2)
    expect(prepared.server).toBeNull()
  })

  it('서버가 자리를 주면 그대로 쓴다 — 다음 단계(ADR 0045 「다음」)의 자리', () => {
    const placed = prepareWhole({
      ...whole,
      nodes: { ...whole.nodes, x: [1, 2, 3], y: [4, 5, 6] },
    })
    expect(Array.from(placed.server ?? [])).toEqual([1, 4, 2, 5, 3, 6])
  })
})
