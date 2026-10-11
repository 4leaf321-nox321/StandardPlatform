/**
 * 규칙 기준 편집기가 지키는 것(ADR 0015) — **차례가 뜻이다.** 줄을 위아래로 옮기면 그 차례로 가고,
 * 조건은 목록의 조건 줄로 걸고, 미리 보기는 규칙마다 붙인 수 · 막힌 규칙 · 기타의 몫을 그 자리에 낸다.
 */

import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { Condition } from '@/modules/objects/api'

// 조건 줄은 목록 화면의 것을 그대로 쓴다 — 여기서는 「걸면 조건이 하나 붙는다」 만 본다.
vi.mock('@/modules/objects/ConditionBar', () => ({
  ConditionBar: ({
    conditions,
    onChange,
    allowExclude,
  }: {
    conditions: Condition[]
    onChange: (next: Condition[]) => void
    allowExclude?: boolean
  }) => (
    <div>
      <span>{conditions.map((one) => `${one.field} ${one.op} ${one.value}`).join(', ')}</span>
      <span>{allowExclude === false ? '제외 없음' : '제외 있음'}</span>
      <button
        type="button"
        onClick={() => onChange([...conditions, { field: 'action', op: 'starts', value: 'SW' }])}
      >
        조건 걸기
      </button>
    </div>
  ),
}))

const RULES = [
  { label: 'SW', when: [{ field: 'action', op: 'starts' as const, value: 'SW' }] },
  { label: '고객과실', when: [{ field: 'diag', op: 'in' as const, value: 'C01|C02' }] },
  { label: 'NTF', when: [{ field: 'parts', op: 'empty' as const, value: '' }] },
]

async function mount(plan: Parameters<typeof import('./RuleDimensionEditor').RuleDimensionEditor>[0]['plan'] = null) {
  const { RuleDimensionEditor } = await import('@/modules/metrics/RuleDimensionEditor')
  type Row = import('@/modules/metrics/RuleDimensionEditor').RuleDimRow
  const seen: Row[] = []
  const onRemove = vi.fn()
  function Holder() {
    const [value, setValue] = useState<Row>({
      kind: 'rules',
      name: 'nature',
      label: '성격',
      rules: RULES,
      other: '기타',
    })
    return (
      <RuleDimensionEditor
        index={0}
        value={value}
        defs={[]}
        linked={[]}
        plan={plan}
        onRemove={onRemove}
        onChange={(next) => {
          seen.push(next)
          setValue(next)
        }}
      />
    )
  }
  render(<Holder />)
  return { seen, onRemove }
}

describe('규칙 기준 편집기', () => {
  it('위로 · 아래로 단추가 차례를 바꾼다 — 끝에서는 그 방향이 잠긴다', async () => {
    const { seen } = await mount()
    expect(screen.getByRole('button', { name: '규칙 1 위로' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '규칙 3 아래로' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: '규칙 3 위로' }))
    expect(seen.at(-1)?.rules.map((one) => one.label)).toEqual(['SW', 'NTF', '고객과실'])
    await userEvent.click(screen.getByRole('button', { name: '규칙 1 아래로' }))
    expect(seen.at(-1)?.rules.map((one) => one.label)).toEqual(['NTF', 'SW', '고객과실'])
    // 줄의 이름 칸도 그 차례를 따라간다.
    expect(screen.getByLabelText('규칙 1 이름')).toHaveValue('NTF')
  })

  it('규칙을 더하고 이름을 적고 조건을 건다 — 조건 줄은 제외 없이', async () => {
    const { seen } = await mount()
    expect(screen.getAllByText('제외 없음')).toHaveLength(3)
    await userEvent.click(screen.getByRole('button', { name: '규칙 추가' }))
    await userEvent.type(screen.getByLabelText('규칙 4 이름'), '제품결함')
    await userEvent.click(screen.getAllByRole('button', { name: '조건 걸기' })[3])
    const last = seen.at(-1)?.rules[3]
    expect(last).toEqual({
      label: '제품결함',
      when: [{ field: 'action', op: 'starts', value: 'SW' }],
    })
    expect(screen.getByText('4 / 20')).toBeInTheDocument()
    await userEvent.clear(screen.getByLabelText('기준 1 기타 이름'))
    await userEvent.type(screen.getByLabelText('기준 1 기타 이름'), '미분류')
    expect(seen.at(-1)?.other).toBe('미분류')
  })

  it('미리 보기 — 규칙마다 붙인 수 · 막힌 규칙 · 맞는 것 없음 · 기타의 몫', async () => {
    await mount({
      name: 'nature',
      label: '성격',
      fingerprint: 'f'.repeat(64),
      rules: [
        { label: 'SW', matched: 6, won: 6, taken_by: [], blocked: false },
        {
          label: '고객과실',
          matched: 2,
          won: 0,
          taken_by: [{ rule: 1, count: 2 }],
          blocked: true,
        },
        { label: 'NTF', matched: 0, won: 0, taken_by: [], blocked: false },
      ],
      other: '기타',
      other_count: 4,
      seen: 10,
      sampled: true,
    })
    expect(screen.getByLabelText('규칙 1 미리 보기')).toHaveTextContent('6건 · 60%')
    expect(screen.getByLabelText('규칙 2 미리 보기')).toHaveTextContent(
      '막힌 규칙 — 맞는 2건을 앞 규칙이 가져감',
    )
    expect(screen.getByLabelText('규칙 3 미리 보기')).toHaveTextContent('맞는 기록 없음')
    expect(screen.getByLabelText('기타 미리 보기')).toHaveTextContent('4건 · 40%')
    expect(screen.getByText(/무작위 표본 10건에서 센 수/)).toBeInTheDocument()
  })

  it('줄 옮기기는 차례만 바꾼다', async () => {
    const { moved } = await import('@/modules/metrics/RuleDimensionEditor')
    expect(moved(['a', 'b', 'c'], 0, 2)).toEqual(['b', 'c', 'a'])
    expect(moved(['a', 'b', 'c'], 2, 0)).toEqual(['c', 'a', 'b'])
    expect(moved(['a', 'b'], 0, 5)).toEqual(['a', 'b'])
  })
})
