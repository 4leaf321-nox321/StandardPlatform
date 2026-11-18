/**
 * 목록 화면 편집의 롤업 줄(ADR 0040) — 「수량 반영」 · 「말단만」 을 줄마다 켜고, 모으는 법에 「곱」 이
 * 있다. 끈 설정은 키를 싣지 않는다. 두 번 세는 일을 화면이 먼저 말한다.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ListView, PropertyDef, RelationType } from '@/modules/ontology/api'

const WEIGHT = { key: 'weight', label: '무게', data_type: 'number', multi: false } as PropertyDef
const SECTION_NO = {
  key: 'section_no',
  label: '절 번호',
  data_type: 'text',
  multi: false,
} as PropertyDef
const PICTURE = { key: 'pic', label: '그림', data_type: 'file', multi: false } as PropertyDef

function kind(extra: Partial<RelationType> = {}): RelationType {
  return {
    slug: 'bom_of',
    label: '구성',
    transitive: true,
    is_active: true,
    src_type_slugs: null,
    dst_type_slugs: null,
    quantity_key: 'qty',
    ...extra,
  } as RelationType
}

async function mount(value: ListView, relation = kind()) {
  const { ListViewEditor } = await import('@/modules/ontology/ListViewEditor')
  const onChange = vi.fn()
  render(
    <ListViewEditor
      defs={[WEIGHT, SECTION_NO, PICTURE]}
      relationTypes={[relation]}
      typeSlug="part"
      value={value}
      onChange={onChange}
    />,
  )
  return onChange
}

const TREE: ListView = {
  tree: { relation: 'bom_of', parent: 'src' },
  rollups: [{ property: 'weight', fn: 'sum' }],
}

describe('롤업 줄의 수량 반영 · 말단만 · 곱', () => {
  it('수량 반영을 켜면 그 줄에 weighted 가 실린다', async () => {
    const onChange = await mount(TREE)
    await userEvent.click(screen.getByRole('checkbox', { name: '수량 반영' }))
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        rollups: [{ property: 'weight', fn: 'sum', weighted: true }],
      }),
    )
  })

  it('말단만을 끄면 키를 싣지 않는다', async () => {
    const onChange = await mount({
      ...TREE,
      rollups: [{ property: 'weight', fn: 'sum', leaves_only: true }],
    })
    const box = screen.getByRole('checkbox', { name: '말단만' })
    expect(box).toBeChecked()
    await userEvent.click(box)
    const next = onChange.mock.calls[0][0] as ListView
    expect(JSON.parse(JSON.stringify(next.rollups))).toEqual([{ property: 'weight', fn: 'sum' }])
  })

  it('모으는 법에 곱이 있다', async () => {
    const onChange = await mount(TREE)
    await userEvent.click(screen.getByRole('combobox', { name: '모으는 법' }))
    await userEvent.click(await screen.findByRole('option', { name: '곱' }))
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ rollups: [{ property: 'weight', fn: 'product' }] }),
    )
  })

  it('두 번 세는 일과 수량 반영의 뜻을 적고, 트리 관계에 수량 칸이 없으면 그것도 말한다', async () => {
    await mount(TREE, kind({ quantity_key: '' }))
    expect(
      screen.getByText(/중간 조립품에도 값이 있으면 두 번 셉니다 — 그래서 「말단만」 이 있습니다/),
    ).toBeInTheDocument()
    expect(screen.getByText(/곱은 총수량만큼 거듭제곱\(직렬 신뢰도\)/)).toBeInTheDocument()
    expect(screen.getByText(/「구성」 에 수량 칸이 없어/)).toBeInTheDocument()
  })
})

describe('트리의 차례', () => {
  it('차례에 칸을 더한다 — 파일 칸은 고를 수 없고, 앞머리는 타입 수정 창에서 정한다고 말한다', async () => {
    const onChange = await mount({ tree: { relation: 'bom_of', parent: 'dst' } })
    await userEvent.click(screen.getByRole('button', { name: '차례에 절 번호 추가' }))
    expect(onChange).toHaveBeenLastCalledWith({
      tree: { relation: 'bom_of', parent: 'dst', sort: ['properties.section_no'] },
    })
    expect(screen.queryByRole('button', { name: '차례에 그림 추가' })).toBeNull()
    // 앞머리는 타입 전체 설정으로 옮겼다(ADR 0088).
    expect(screen.queryByLabelText('이름 앞에 붙일 값(앞머리)')).toBeNull()
    expect(screen.getByText(/타입 수정 창의 「이름 앞에 붙일 값」 에서/)).toBeInTheDocument()
  })

  it('차례를 옮기고 빼며, 다 빼면 키째 뺀다', async () => {
    const onChange = await mount({
      tree: {
        relation: 'bom_of',
        parent: 'dst',
        sort: ['properties.section_no', 'label'],
      },
    })
    await userEvent.click(screen.getByRole('button', { name: '차례 이름 위로' }))
    expect(onChange).toHaveBeenLastCalledWith({
      tree: {
        relation: 'bom_of',
        parent: 'dst',
        sort: ['label', 'properties.section_no'],
      },
    })
    onChange.mockClear()
    const { ListViewEditor } = await import('@/modules/ontology/ListViewEditor')
    const one = vi.fn()
    render(
      <ListViewEditor
        defs={[SECTION_NO]}
        relationTypes={[kind()]}
        typeSlug="part"
        value={{ tree: { relation: 'bom_of', parent: 'dst', sort: ['label'] } }}
        onChange={one}
      />,
    )
    await userEvent.click(screen.getAllByRole('button', { name: '차례 이름 빼기' }).at(-1)!)
    expect(one).toHaveBeenLastCalledWith({ tree: { relation: 'bom_of', parent: 'dst' } })
  })

  it('관계 방향을 바꿔도 차례는 그대로 둔다', async () => {
    const onChange = await mount({
      tree: {
        relation: 'bom_of',
        parent: 'dst',
        sort: ['properties.section_no'],
      },
    })
    const direction = screen
      .getAllByRole('combobox')
      .find((one) => one.textContent?.includes('자식이 부모를 가리킨다'))
    await userEvent.click(direction!)
    await userEvent.click(await screen.findByRole('option', { name: /부모가 자식을 가리킨다/ }))
    expect(onChange).toHaveBeenLastCalledWith({
      tree: {
        relation: 'bom_of',
        parent: 'src',
        sort: ['properties.section_no'],
      },
    })
  })
})
