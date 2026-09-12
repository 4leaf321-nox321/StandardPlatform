/**
 * 조건 줄이 지키는 것 — **칸의 종류가 연산을 정하고, 칩은 읽히는 말로 서고, 지우면 빠진다.**
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { PropertyDef } from '@/modules/ontology/api'

vi.mock('@/modules/objects/api', async (original) => {
  const real = await original<typeof import('@/modules/objects/api')>()
  return {
    ...real,
    objectApi: {
      list: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 }),
      // 상세 — 칩의 이름을 채울 때 읽는다. 모르는 id 는 볼 수 없는 것처럼 실패한다.
      profile: vi.fn((_slug: string, id: string) =>
        id === 'w1'
          ? Promise.resolve({ object: { id, label: '해석팀' } })
          : Promise.reject(new Error('볼 수 없음')),
      ),
    },
  }
})

const DEFS = [
  { key: 'weight', label: '무게', data_type: 'number', multi: false, unit: 'kg' },
  { key: 'region', label: '지역', data_type: 'enum', multi: false, enum_options: ['영남', '호남'] },
] as unknown as PropertyDef[]

describe('조건 줄', () => {
  it('칩은 읽히는 말로 서고, 참조 값은 이름으로, 지우면 빠진다', async () => {
    const { ConditionBar } = await import('@/modules/objects/ConditionBar')
    const onChange = vi.fn()
    render(
      <ConditionBar
        defs={DEFS}
        conditions={[
          { field: 'weight', op: 'gte', value: '10' },
          { field: 'region', op: 'in', value: '영남|호남' },
          { field: 'weight', op: 'empty', value: '' },
        ]}
        onChange={onChange}
      />,
    )
    expect(screen.getByText('무게 ≥ 10')).toBeInTheDocument()
    expect(screen.getByText('지역 ∈ 영남, 호남')).toBeInTheDocument()
    expect(screen.getByText('무게 = ∅')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /조건 지우기: 지역/ }))
    expect(onChange).toHaveBeenCalledWith([
      { field: 'weight', op: 'gte', value: '10' },
      { field: 'weight', op: 'empty', value: '' },
    ])
  })

  it('연산 표는 칸의 종류를 따른다 — 서버와 같은 표', async () => {
    const { opsFor } = await import('@/modules/objects/ConditionBar')
    expect(opsFor('number')).toEqual([
      'eq',
      'ne',
      'gt',
      'gte',
      'lt',
      'lte',
      'in',
      'empty',
      'notempty',
    ])
    expect(opsFor('enum')).toEqual(['eq', 'ne', 'in', 'empty', 'notempty'])
    expect(opsFor('bool')).toEqual(['eq', 'empty', 'notempty'])
    expect(opsFor('text')).toEqual(['eq', 'ne', 'contains', 'starts', 'in', 'empty', 'notempty'])
  })

  it('「비어 있음」 은 값 없이 걸린다', async () => {
    const { ConditionBar } = await import('@/modules/objects/ConditionBar')
    const onChange = vi.fn()
    render(<ConditionBar defs={DEFS} conditions={[]} onChange={onChange} />)
    await userEvent.click(screen.getByRole('button', { name: /조건 추가/ }))
    // 기본 칸은 「이름」(글자) — 값이 없으면 「걸기」 가 안 선다.
    const add = await screen.findByRole('button', { name: '걸기' })
    expect(add).toBeDisabled()
    await userEvent.type(screen.getByRole('textbox'), '볼')
    expect(add).toBeEnabled()
    await userEvent.click(add)
    expect(onChange).toHaveBeenCalledWith([{ field: 'label', op: 'eq', value: '볼' }])
  })

  it('이어진 것 너머의 칸은 자기 칸 뒤에 제목 아래로 서고, 칩은 그 이름으로 읽힌다', async () => {
    const { ConditionBar } = await import('@/modules/objects/ConditionBar')
    render(
      <ConditionBar
        defs={DEFS}
        conditions={[{ field: 'ref.developer.country', op: 'eq', value: '미국' }]}
        onChange={vi.fn()}
        linked={[
          {
            field: 'ref.developer.country',
            label: '개발사 › 국가',
            heading: '개발사 (기업)',
            data_type: 'enum',
            multi: false,
            enum_options: ['미국', '한국'],
            ref_type_slug: null,
          },
        ]}
      />,
    )
    expect(screen.getByText('개발사 › 국가 = 미국')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /조건 추가/ }))
    // 첫 고르개가 「칸」 이다.
    await userEvent.click(screen.getAllByRole('combobox')[0])
    expect(await screen.findByText('개발사 (기업)')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '개발사 › 국가' })).toBeInTheDocument()
  })

  it('상대가 정해지지 않은 관계는 있음·없음만 걸 수 있다', async () => {
    const { opsFor } = await import('@/modules/objects/ConditionBar')
    expect(opsFor('relation')).toEqual(['empty', 'notempty'])
  })

  it('목록에 이름이 안 실려 오는 참조 값도 새로 열었을 때 이름으로 선다', async () => {
    // **주소로 들어오면 고르던 기억이 없다.** 그래도 칩은 id 가 아니라 이름이어야 읽힌다.
    const { ConditionBar } = await import('@/modules/objects/ConditionBar')
    render(
      <ConditionBar
        defs={DEFS}
        conditions={[
          { field: 'out.used_by', op: 'in', value: 'w1|w9' },
          { field: 'out.used_by', op: 'notempty', value: '' },
        ]}
        onChange={vi.fn()}
        linked={[
          {
            field: 'out.used_by',
            label: '사용 부서',
            heading: '관계 · 사용 부서',
            data_type: 'object_ref',
            multi: true,
            enum_options: null,
            ref_type_slug: 'workspace',
          },
        ]}
      />,
    )
    // 볼 수 없는 w9 는 id 그대로 — 이름을 지어내지 않는다.
    expect(await screen.findByText('사용 부서 ∈ 해석팀, w9')).toBeInTheDocument()
  })
})
