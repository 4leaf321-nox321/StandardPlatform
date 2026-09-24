/**
 * 「여기서 확장」 이 **보고 있던 자리를 흔들지 않는다.**
 *
 * 좌표를 이어받는 것만으로는 부족했다: 데이터가 바뀌면 라이브러리가 시뮬레이션을 다시
 * 데우고, 그때 이미 자리를 잡은 노드까지 전부 다시 밀린다 — 카메라는 그대로인데 그림이
 * 흘러가므로 사람은 「여기서 확장」 을 누른 뒤 화면이 딴 데가 됐다고 느낀다.
 *
 * 그래서 새 노드가 들어올 때는 옛 노드를 붙박이로 둔다. 눈으로만 확인할 수 있는 동작이라
 * 계산하는 함수만 떼어 글자로 잡아 둔다.
 */

import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { carryPositions } from '@/modules/graph/GraphCanvas'
import type { CanvasNode } from '@/modules/graph/GraphCanvas'

const node = (id: string, extra: Partial<CanvasNode> = {}): CanvasNode => ({
  id,
  label: id,
  color: '#000',
  radius: 6,
  shape: 'circle',
  ...extra,
})

describe('확장할 때의 좌표', () => {
  it('옛 노드는 자리를 이어받고, 새 노드가 있으면 붙박이가 된다', () => {
    const before = [
      { ...node('a'), x: 10, y: 20 },
      { ...node('b'), x: 30, y: 40 },
    ]
    const { nodes, arrived } = carryPositions(before, [node('a'), node('b'), node('c')])

    expect(arrived).toEqual(['c'])
    const a = nodes.find((one) => one.id === 'a')!
    expect([a.x, a.y]).toEqual([10, 20])
    // **고정**이 걸려야 새 노드만 자리를 찾는다.
    expect([a.fx, a.fy]).toEqual([10, 20])
  })

  it('새 노드가 없으면 고정하지 않는다 — 색만 바뀐 갱신', () => {
    const before = [{ ...node('a'), x: 10, y: 20 }]
    const { nodes, arrived } = carryPositions(before, [node('a', { color: '#f00' })])

    expect(arrived).toEqual([])
    expect(nodes[0].fx).toBeUndefined()
    expect(nodes[0].fy).toBeUndefined()
    // 자리는 그대로다.
    expect([nodes[0].x, nodes[0].y]).toEqual([10, 20])
  })

  it('새 노드는 펼친 노드 곁에서 시작한다 — 화면 반대편에서 날아오지 않게', () => {
    const before = [{ ...node('a'), x: 100, y: 100 }]
    const { nodes } = carryPositions(before, [node('a'), node('b', { near: 'a' })])

    const fresh = nodes.find((one) => one.id === 'b')!
    const distance = Math.hypot((fresh.x ?? 0) - 100, (fresh.y ?? 0) - 100)
    expect(distance).toBeGreaterThan(0)
    expect(distance).toBeLessThan(200)
    // 새로 온 것에는 고정을 걸지 않는다 — 자리를 찾아야 한다.
    expect(fresh.fx).toBeUndefined()
  })

  it('처음 그릴 때는 이어받을 것이 없다', () => {
    const { nodes, arrived } = carryPositions([], [node('a'), node('b')])
    expect(arrived).toEqual(['a', 'b'])
    expect(nodes.every((one) => one.x === undefined && one.fx === undefined)).toBe(true)
  })
})

/**
 * 카메라 보존 — **배치를 그대로 둬도 배율과 위치가 바뀌면 보던 자리를 잃는다.**
 *
 * 노드를 갈아 끼우거나 캔버스가 크기를 다시 재면 라이브러리가 화면을 다시 잡는다. 그때
 * 「배치는 같은데 줌과 위치만 달라졌다」 가 된다 — 확장 뒤에 사람이 겪은 것이 이것이다.
 * 라이브러리는 시험에서 가짜로 끼우므로 동작을 직접 볼 수 없어, **규칙을 글자로** 잡아 둔다.
 */
describe('카메라 보존', () => {
  const source = readFileSync('src/modules/graph/GraphCanvas.tsx', 'utf8')

  it('사람이 움직인 카메라를 기억한다', () => {
    expect(source).toMatch(/onZoomEnd=\{rememberCamera\}/)
    // 「화면 맞춤」 직후의 값도 기억해야 그다음 갱신이 옛 카메라로 되돌리지 않는다.
    expect(source).toMatch(/setTimeout\(rememberCamera/)
  })

  it('갈아 끼우기 직전에도 기억한다 — 줌을 건드리지 않은 화면을 위해', () => {
    // `onZoomEnd` 에만 기대면 사람이 줌·팬을 한 번도 하지 않은 화면에는 기억이 없고,
    // 그러면 라이브러리가 「전체를 한 화면에」 다시 잡는 것을 그대로 보게 된다.
    const memo = source.slice(source.indexOf('const graphData = useMemo'))
    expect(memo.slice(0, 600)).toMatch(/rememberCamera\(\)/)
  })

  it('카메라 함수는 그것을 쓰는 곳보다 먼저 선언한다', () => {
    // `const` 는 TDZ 라서, 렌더 중에 도는 useMemo 가 뒤에 선언된 함수를 부르면 그 자리에서
    // 터진다 — 타입 검사로는 안 잡힌다.
    expect(source.indexOf('const rememberCamera')).toBeLessThan(
      source.indexOf('const graphData = useMemo'),
    )
  })

  it('노드 교체와 크기 변경 뒤에 되돌린다 — 첫 그림은 제외', () => {
    const effect = source.slice(source.indexOf('requestAnimationFrame(restoreCamera)') - 400)
    expect(effect).toMatch(/hadPositions\.current/)
    expect(effect).toMatch(/\[graphData, size\.width, size\.height, restoreCamera\]/)
  })
})
