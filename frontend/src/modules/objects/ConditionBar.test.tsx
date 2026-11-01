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
  it('칩은 읽히는 말로 서고, 참조 값은 이름으로, 삭제하면 빠진다', async () => {
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
    await userEvent.click(screen.getByRole('button', { name: /조건 삭제: 지역/ }))
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
    await userEvent.click(screen.getByRole('combobox', { name: '칸' }))
    expect(await screen.findByText('개발사 (기업)')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '개발사 › 국가' })).toBeInTheDocument()
  })

  it('두 걸음 너머의 칸도 이름으로 찾아 조건을 건다', async () => {
    // 「모델의 과제의 프로젝트」 — 한 걸음 고르개로는 안 닿던 물음.
    const { ConditionBar } = await import('@/modules/objects/ConditionBar')
    const onChange = vi.fn()
    render(
      <ConditionBar
        defs={DEFS}
        conditions={[]}
        onChange={onChange}
        linked={[
          {
            field: 'ref.model.series',
            label: '모델 › 시리즈',
            heading: '모델 (개발모델)',
            data_type: 'text',
            multi: false,
            enum_options: null,
            ref_type_slug: null,
          },
          {
            field: 'ref.model.ref.task.label',
            label: '모델 › 과제 › 이름',
            heading: '모델 › 과제 (과제)',
            data_type: 'text',
            multi: false,
            enum_options: null,
            ref_type_slug: null,
          },
        ]}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /조건 추가/ }))
    await userEvent.click(screen.getByRole('combobox', { name: '칸' }))
    await userEvent.type(screen.getByPlaceholderText(/칸 이름으로 찾기/), '과제')
    await userEvent.click(screen.getByRole('button', { name: '모델 › 과제 › 이름' }))
    expect(screen.getByRole('combobox', { name: '칸' })).toHaveTextContent('모델 › 과제 › 이름')
    await userEvent.type(screen.getByRole('textbox'), '엔진')
    await userEvent.click(screen.getByRole('button', { name: '걸기' }))
    expect(onChange).toHaveBeenCalledWith([
      { field: 'ref.model.ref.task.label', op: 'eq', value: '엔진' },
    ])
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

  it('제외 묶음은 테두리 안에 모여 서고, 묶음째 · 하나씩 삭제한다', async () => {
    const { ConditionBar } = await import('@/modules/objects/ConditionBar')
    const onChange = vi.fn()
    const conditions = [
      { field: 'weight', op: 'gte' as const, value: '10' },
      { field: 'region', op: 'eq' as const, value: '영남', exclude: 1 },
      { field: 'weight', op: 'lt' as const, value: '20', exclude: 1 },
      { field: 'region', op: 'eq' as const, value: '호남', exclude: 2 },
    ]
    render(<ConditionBar defs={DEFS} conditions={conditions} onChange={onChange} />)
    const first = screen.getByRole('group', { name: '제외 묶음 1' })
    expect(first).toHaveTextContent('제외')
    expect(first).toHaveTextContent('지역 = 영남')
    expect(first).toHaveTextContent('무게 < 20')
    expect(screen.getByRole('group', { name: '제외 묶음 2' })).toHaveTextContent('지역 = 호남')
    await userEvent.click(screen.getByRole('button', { name: '제외 묶음 1 삭제' }))
    expect(onChange).toHaveBeenLastCalledWith([conditions[0], conditions[3]])
    await userEvent.click(
      screen.getByRole('button', { name: '제외 묶음 1 의 조건 삭제: 무게 < 20' }),
    )
    expect(onChange).toHaveBeenLastCalledWith([conditions[0], conditions[1], conditions[3]])
  })

  it('「넣을 곳」 에서 제외를 고르면 새 묶음이나 걸린 묶음으로 간다', async () => {
    const { ConditionBar } = await import('@/modules/objects/ConditionBar')
    const onChange = vi.fn()
    const conditions = [{ field: 'region', op: 'eq' as const, value: '영남', exclude: 3 }]
    render(<ConditionBar defs={DEFS} conditions={conditions} onChange={onChange} />)
    await userEvent.click(screen.getByRole('button', { name: /조건 추가/ }))
    await userEvent.type(await screen.findByRole('textbox'), '볼')
    await userEvent.click(screen.getByRole('combobox', { name: '넣을 곳' }))
    await userEvent.click(await screen.findByRole('option', { name: /새 묶음/ }))
    await userEvent.click(screen.getByRole('button', { name: '걸기' }))
    expect(onChange).toHaveBeenLastCalledWith([
      conditions[0],
      { field: 'label', op: 'eq', value: '볼', exclude: 4 },
    ])
  })

  it('제외를 받지 않는 자리(지표의 거르기 · 규칙)는 「넣을 곳」 이 없다', async () => {
    const { ConditionBar } = await import('@/modules/objects/ConditionBar')
    render(<ConditionBar defs={DEFS} conditions={[]} onChange={vi.fn()} allowExclude={false} />)
    await userEvent.click(screen.getByRole('button', { name: /조건 추가/ }))
    await screen.findByRole('button', { name: '걸기' })
    expect(screen.queryByRole('combobox', { name: '넣을 곳' })).not.toBeInTheDocument()
  })
})

describe('같은 관계 묶음(ADR 0043)', () => {
  const LINKED = [
    {
      field: 'out.bom.label',
      label: '구성품 › 이름',
      heading: '관계 · 구성품 (부품)',
      data_type: 'text',
      multi: true,
      enum_options: null,
      ref_type_slug: null,
    },
    {
      field: 'out.bom~qty',
      label: '구성품 관계 › 수량',
      heading: '관계 · 구성품 (부품)',
      data_type: 'number',
      multi: true,
      enum_options: null,
      ref_type_slug: null,
      edge: true,
    },
  ]

  it('묶은 조건은 실선 테두리 안에 모여 서고, 제외 묶음 안에서도 따로 선다', async () => {
    const { ConditionBar } = await import('@/modules/objects/ConditionBar')
    const onChange = vi.fn()
    const conditions = [
      { field: 'out.bom.label', op: 'eq' as const, value: 'X', edge: 1 },
      { field: 'out.bom~qty', op: 'gte' as const, value: '4', edge: 1 },
      { field: 'out.bom~qty', op: 'gt' as const, value: '9' },
      { field: 'out.bom.label', op: 'eq' as const, value: 'Y', exclude: 1, edge: 2 },
      { field: 'out.bom~qty', op: 'lt' as const, value: '2', exclude: 1, edge: 2 },
    ]
    render(<ConditionBar defs={DEFS} conditions={conditions} onChange={onChange} linked={LINKED} />)
    const bound = screen.getByRole('group', { name: '같은 관계 1' })
    expect(bound).toHaveTextContent('구성품 › 이름 = X')
    expect(bound).toHaveTextContent('구성품 관계 › 수량 ≥ 4')
    expect(bound).not.toHaveTextContent('> 9')
    const excluded = screen.getByRole('group', { name: '제외 묶음 1' })
    expect(screen.getByRole('group', { name: '같은 관계 2' })).toHaveTextContent('수량 < 2')
    expect(excluded).toContainElement(screen.getByRole('group', { name: '같은 관계 2' }))
    await userEvent.click(screen.getByRole('button', { name: '같은 관계 1 삭제' }))
    expect(onChange).toHaveBeenLastCalledWith([conditions[2], conditions[3], conditions[4]])
  })

  it('관계 걸음을 지나는 칸이면 「같은 관계」 에서 새 묶음 · 걸린 묶음을 고른다', async () => {
    const { ConditionBar } = await import('@/modules/objects/ConditionBar')
    const onChange = vi.fn()
    const conditions = [
      { field: 'out.bom.label', op: 'eq' as const, value: 'X', edge: 1 },
      { field: 'out.bom.label', op: 'eq' as const, value: 'Z', exclude: 1, edge: 2 },
    ]
    render(<ConditionBar defs={DEFS} conditions={conditions} onChange={onChange} linked={LINKED} />)
    await userEvent.click(screen.getByRole('button', { name: /조건 추가/ }))
    // 자기 칸에는 「같은 관계」 가 없다.
    await screen.findByRole('button', { name: '걸기' })
    expect(screen.queryByRole('combobox', { name: '같은 관계' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('combobox', { name: '칸' }))
    await userEvent.click(screen.getByRole('button', { name: '구성품 관계 › 수량' }))
    await userEvent.click(screen.getByRole('combobox', { name: '연산' }))
    await userEvent.click(await screen.findByRole('option', { name: '이상' }))
    await userEvent.type(screen.getByRole('spinbutton'), '4')
    await userEvent.click(screen.getByRole('combobox', { name: '같은 관계' }))
    // 보통 조건 자리의 묶음(1)만 — 제외 묶음 안의 것(2)은 다른 자리다.
    expect(screen.queryByRole('option', { name: /같은 관계 2/ })).not.toBeInTheDocument()
    await userEvent.click(await screen.findByRole('option', { name: /같은 관계 1 에 추가/ }))
    await userEvent.click(screen.getByRole('button', { name: '걸기' }))
    expect(onChange).toHaveBeenLastCalledWith([
      ...conditions,
      { field: 'out.bom~qty', op: 'gte', value: '4', edge: 1 },
    ])
  })

  it('관계 걸음을 지나는 주소를 가른다 — 나를 가리키는 것은 선이 아니다', async () => {
    const { bindable } = await import('@/modules/objects/ConditionBar')
    expect(bindable('out.bom~qty')).toBe(true)
    expect(bindable('out.bom.label')).toBe(true)
    expect(bindable('ref.model.in.bom')).toBe(true)
    expect(bindable('ref.vendor.country')).toBe(false)
    expect(bindable('in.svc_case:model.symptom')).toBe(false)
    expect(bindable('weight')).toBe(false)
  })
})

describe('하위 전체(ADR 0035)', () => {
  const REFS = [
    {
      key: 'part',
      label: '교체 부품',
      data_type: 'object_ref',
      multi: false,
      ref_type_slug: 'part',
    },
    { key: 'model', label: '모델', data_type: 'object_ref', multi: false, ref_type_slug: 'model' },
  ] as unknown as PropertyDef[]

  it('트리가 있는 타입을 가리키는 칸에만 연산이 선다', async () => {
    const { opsFor } = await import('@/modules/objects/ConditionBar')
    expect(opsFor('object_ref')).toEqual(['eq', 'ne', 'in', 'empty', 'notempty'])
    expect(opsFor('object_ref', { tree: true })).toEqual([
      'eq',
      'ne',
      'in',
      'under',
      'empty',
      'notempty',
    ])
    // 목록 자신은 「하위 전체」 하나뿐이다.
    expect(opsFor('object_ref', { self: true })).toEqual(['under'])
  })

  it('칩은 「…: 이름 하위 전체」 로 읽히고 이름은 상세에서 채운다', async () => {
    const { ConditionBar } = await import('@/modules/objects/ConditionBar')
    render(
      <ConditionBar
        defs={REFS}
        conditions={[{ field: 'part', op: 'under', value: 'w1' }]}
        onChange={vi.fn()}
        treeTypes={new Set(['part'])}
      />,
    )
    expect(await screen.findByText('교체 부품: 해석팀 하위 전체')).toBeInTheDocument()
  })

  it('목록 자신이 트리 타입이면 「자신」 칸이 서고, 아니면 안 선다', async () => {
    const { ConditionBar } = await import('@/modules/objects/ConditionBar')
    const { unmount } = render(
      <ConditionBar
        defs={REFS}
        conditions={[]}
        onChange={vi.fn()}
        treeTypes={new Set(['part'])}
        selfType="part"
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /조건 추가/ }))
    await userEvent.click(screen.getByRole('combobox', { name: '칸' }))
    expect(screen.getByRole('button', { name: /자신\(이 목록의 객체\)/ })).toBeInTheDocument()
    unmount()
    render(
      <ConditionBar
        defs={REFS}
        conditions={[]}
        onChange={vi.fn()}
        treeTypes={new Set(['part'])}
        selfType="model"
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /조건 추가/ }))
    await userEvent.click(screen.getByRole('combobox', { name: '칸' }))
    expect(screen.queryByRole('button', { name: /자신\(이 목록의 객체\)/ })).not.toBeInTheDocument()
  })
})

describe('계보 조건(ADR 0041)', () => {
  it('「낡은 것만」 · 「바로 낡은 것만」 · 「낡지 않은 것」 으로 읽히고 삭제하면 빠진다', async () => {
    // 속성 목록에 없는 칸이다 — 그대로 두면 「lineage = stale」 로 선다.
    const { ConditionBar } = await import('@/modules/objects/ConditionBar')
    const onChange = vi.fn()
    render(
      <ConditionBar
        defs={DEFS}
        conditions={[
          { field: 'lineage', op: 'eq', value: 'stale' },
          { field: 'lineage', op: 'eq', value: 'direct', exclude: 1 },
          { field: 'lineage', op: 'ne', value: 'stale' },
          { field: 'lineage', op: 'eq', value: 'odd' },
        ]}
        onChange={onChange}
      />,
    )
    expect(screen.getByText('낡은 것만')).toBeInTheDocument()
    expect(screen.getByText('바로 낡은 것만')).toBeInTheDocument()
    expect(screen.getByText('낡지 않은 것')).toBeInTheDocument()
    // 모르는 값은 그대로 — 칸 이름은 「계보」.
    expect(screen.getByText('계보 = odd')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '조건 삭제: 낡은 것만' }))
    expect(onChange).toHaveBeenCalledWith([
      { field: 'lineage', op: 'eq', value: 'direct', exclude: 1 },
      { field: 'lineage', op: 'ne', value: 'stale' },
      { field: 'lineage', op: 'eq', value: 'odd' },
    ])
  })

  it('같은 키의 속성이 있으면 속성이 이긴다(서버와 같다)', async () => {
    const { ConditionBar } = await import('@/modules/objects/ConditionBar')
    render(
      <ConditionBar
        defs={[
          ...DEFS,
          { key: 'lineage', label: '혈통', data_type: 'text', multi: false } as PropertyDef,
        ]}
        conditions={[{ field: 'lineage', op: 'eq', value: 'stale' }]}
        onChange={vi.fn()}
      />,
    )
    expect(screen.getByText('혈통 = stale')).toBeInTheDocument()
  })
})
