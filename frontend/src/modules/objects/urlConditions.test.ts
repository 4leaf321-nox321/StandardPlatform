/**
 * 주소 ↔ 조건 — **붙여 넣으면 같은 목록이 선다.** 제외 묶음(`nf<번호>.`)도 같은 규칙으로 읽고
 * 쓴다(ADR 0015) — 지표의 「N건 보기」 가 싣고 온 주소를 목록이 그대로 받아야 셀의 수와 같다.
 */

import { describe, expect, it } from 'vitest'

import { conditionKey, conditionsFromParams, withConditions } from '@/modules/objects/urlConditions'

describe('주소의 조건', () => {
  it('보통 조건과 제외 묶음을 읽는다', () => {
    const params = new URLSearchParams(
      'f.parts.notempty=&nf1.action.starts=SW&nf2.diag.in=C01%7CC02&nf2.ref.model.series.eq=Z&page=2',
    )
    expect(conditionsFromParams(params)).toEqual([
      { field: 'parts', op: 'notempty', value: '' },
      { field: 'action', op: 'starts', value: 'SW', exclude: 1 },
      { field: 'diag', op: 'in', value: 'C01|C02', exclude: 2 },
      { field: 'ref.model.series', op: 'eq', value: 'Z', exclude: 2 },
    ])
  })

  it('같은 관계 묶음(fe) · 제외 묶음 안의 같은 관계 묶음을 읽고 다시 적는다', () => {
    const params = new URLSearchParams(
      'fe1.out.bom.eq=X&fe1.out.bom~qty.gte=4&nf2.fe3.out.bom~opts.eq=B&f.out.bom~memo.notempty=',
    )
    const read = conditionsFromParams(params)
    expect(read).toEqual([
      { field: 'out.bom', op: 'eq', value: 'X', edge: 1 },
      { field: 'out.bom~qty', op: 'gte', value: '4', edge: 1 },
      { field: 'out.bom~opts', op: 'eq', value: 'B', exclude: 2, edge: 3 },
      { field: 'out.bom~memo', op: 'notempty', value: '' },
    ])
    expect(read.map(conditionKey)).toEqual([
      'fe1.out.bom.eq',
      'fe1.out.bom~qty.gte',
      'nf2.fe3.out.bom~opts.eq',
      'f.out.bom~memo.notempty',
    ])
    const after = withConditions(params, { q: '', conditions: [] })
    expect(after.toString()).toBe('')
  })

  it('다시 적으면 옛 조건(보통 · 제외)을 지우고 다른 키는 둔다', () => {
    const before = new URLSearchParams('f.a.eq=1&nf1.b.eq=2&types=x&q=old')
    const after = withConditions(before, {
      q: '',
      conditions: [{ field: 'c', op: 'ne', value: '3', exclude: 4 }],
    })
    expect(after.toString()).toBe('types=x&nf4.c.ne=3')
    expect(conditionKey({ field: 'c', op: 'eq', value: '' })).toBe('f.c.eq')
    expect(conditionsFromParams(after)).toEqual([
      { field: 'c', op: 'ne', value: '3', exclude: 4 },
    ])
  })
})
