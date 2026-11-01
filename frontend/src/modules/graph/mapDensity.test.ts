/**
 * 지도의 원반 셈 — 상자의 기대 수(집계 x 넓이 몫)와 밀도 칸이 기록 수를 지키나(ADR 0046).
 */

import { describe, expect, it } from 'vitest'

import type { MapData } from '@/modules/graph/api'
import {
  boxFraction,
  densityColor,
  densityGrid,
  discsOf,
  expectedIn,
} from '@/modules/graph/mapDensity'

const DATA = {
  types: [
    { slug: 'svc', label: '서비스', record: true, count: 1000 },
    { slug: 'sales', label: '판매', record: true, count: 10 },
  ],
  discs: {
    x: [0, 100],
    y: [0, 0],
    r: [10, 1],
    hub: [0, 1],
    slot: [-1, -1],
    count_disc: [0, 0, 1],
    count_type: [0, 1, 1],
    count_n: [1000, 50, 10],
  },
} as unknown as MapData

describe('지도의 원반 셈', () => {
  it('켠 기록 타입의 수만 더한다', () => {
    expect([...discsOf(DATA, new Set(['svc'])).count]).toEqual([1000, 0])
    expect([...discsOf(DATA, new Set(['svc', 'sales'])).count]).toEqual([1050, 10])
  })

  it('상자에 든 넓이 몫 — 통째로 들면 1, 안 닿으면 0, 반쪽이면 절반 가까이', () => {
    const box = { x0: -20, y0: -20, x1: 20, y1: 20 }
    expect(boxFraction(0, 0, 10, box)).toBe(1)
    expect(boxFraction(100, 0, 10, box)).toBe(0)
    expect(boxFraction(0, 0, 10, { x0: 0, y0: -20, x1: 20, y1: 20 })).toBeCloseTo(0.5, 1)
  })

  it('기대 수는 집계 x 넓이 몫이다(기록을 안 읽는다)', () => {
    const discs = discsOf(DATA, new Set(['svc', 'sales']))
    expect(expectedIn(discs, { x0: -20, y0: -20, x1: 20, y1: 20 })).toBe(1050)
    expect(expectedIn(discs, { x0: 0, y0: -20, x1: 200, y1: 20 })).toBeCloseTo(535, -1)
  })

  it('밀도 칸은 화면 안 원반의 기록 수를 지킨다 — 작은 원반은 한 칸에, 큰 원반은 고르게', () => {
    const discs = discsOf(DATA, new Set(['svc', 'sales']))
    const view = {
      width: 400,
      height: 200,
      unitPx: 2,
      project: (x: number, y: number): [number, number] => [x * 2 + 50, y * 2 + 100],
    }
    const grid = densityGrid(discs, view, 3)
    const total = grid.values.reduce((sum, one) => sum + one, 0)
    // 큰 원반은 칸 가운데로 판정해 몇 % 어긋날 수 있다.
    expect(total).toBeGreaterThan(1060 * 0.95)
    expect(total).toBeLessThan(1060 * 1.05)
  })

  it('색은 밀도가 짙을수록 진하다(밝은 바탕)', () => {
    const light = densityColor(0.01, false)
    const dark = densityColor(100, false)
    expect(light[0] + light[1] + light[2]).toBeGreaterThan(dark[0] + dark[1] + dark[2])
  })
})
