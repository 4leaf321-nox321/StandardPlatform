/**
 * 그래프의 색 — **묶음이 색을 쥔다.**
 *
 * 타입 순서로 색을 주던 때는 팔레트가 열둘이라 열셋째부터 전부 회색이었다. 타입이 백 개인
 * 설치에서는 거의 다 회색이고, 그때 그림은 「무엇이 무엇인지」 를 하나도 말해 주지 못한다.
 */
import { describe, expect, it } from 'vitest'

import {
  OVERFLOW_COLOR,
  groupColorScale,
  shade,
  typeColorScale,
} from '@/modules/graph/colors'

const group = (slug: string, extra: object = {}) => ({ slug, ...extra })
const type = (slug: string, group: string | null) => ({ slug, nav_group_slug: group })

describe('묶음이 색을 쥔다', () => {
  it('타입이 서른이어도 회색이 없다 — 묶음이 둘이면 색도 둘이면 된다', () => {
    const groups = [group('base'), group('sim')]
    const types = [
      ...Array.from({ length: 15 }, (_one, at) => type(`b${at}`, 'base')),
      ...Array.from({ length: 15 }, (_one, at) => type(`s${at}`, 'sim')),
    ]
    const color = typeColorScale(types, groups)
    const all = types.map((one) => color(one.slug))
    expect(all.filter((one) => one === OVERFLOW_COLOR)).toEqual([])
    // 같은 묶음끼리는 같은 계열(첫 글자가 같은 팔레트 색에서 나온다) — 다른 묶음과는 다르다.
    expect(color('b0')).not.toBe(color('s0'))
  })

  it('하위 묶음은 상위의 색을 물려받고 농도만 다르다', () => {
    const groups = [group('base'), group('mech', { parent_slug: 'base' })]
    const scale = groupColorScale(groups)
    expect(scale('base')).not.toBe(OVERFLOW_COLOR)
    expect(scale('mech')).not.toBe(OVERFLOW_COLOR)
    // 같은 계열이지만 같은 색은 아니다 — 한 영역으로 읽히면서 서로 갈린다.
    expect(scale('mech')).not.toBe(scale('base'))
  })

  it('묶음이 적어 둔 색을 그대로 쓴다 — 사내 관례를 고정하는 자리', () => {
    const scale = groupColorScale([group('sim', { color: '#ff8800' })])
    expect(scale('sim')).toBe('#ff8800')
  })

  it('묶음에 안 걸린 타입은 회색 — 그 회색은 「아직 안 넣은 것」 이라는 뜻이다', () => {
    const color = typeColorScale([type('loose', null)], [group('base')])
    expect(color('loose')).toBe(OVERFLOW_COLOR)
    expect(color('없는타입')).toBe(OVERFLOW_COLOR)
  })

  it('맨 위 묶음이 열둘을 넘으면 그때부터 회색 — 색을 더 만들지 않는다', () => {
    const groups = Array.from({ length: 14 }, (_one, at) => group(`g${at}`))
    const scale = groupColorScale(groups)
    expect(scale('g11')).not.toBe(OVERFLOW_COLOR)
    expect(scale('g12')).toBe(OVERFLOW_COLOR)
  })

  it('농도는 색조를 건드리지 않는다 — 밝기만 민다', () => {
    expect(shade('#6366f1', 0)).toBe('#6366f1')
    const lighter = shade('#6366f1', 0.2)
    const darker = shade('#6366f1', -0.2)
    expect(lighter).toMatch(/^#[0-9a-f]{6}$/)
    expect(lighter).not.toBe('#6366f1')
    expect(darker).not.toBe(lighter)
  })
})
