/**
 * 목록 화면 편집의 롤업 줄(ADR 0040) — 「수량 반영」 · 「말단만」 을 줄마다 켜고, 모으는 법에 「곱」 이
 * 있다. 끈 설정은 키를 싣지 않는다. 두 번 세는 일을 화면이 먼저 말한다.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ListView, PropertyDef, RelationType } from '@/modules/ontology/api'

const WEIGHT = { key: 'weight', label: '무게', data_type: 'number', multi: false } as PropertyDef

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
      defs={[WEIGHT]}
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
