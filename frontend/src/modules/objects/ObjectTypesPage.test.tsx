/**
 * 객체 타입 전부 — **백 개짜리 색인이 지켜야 하는 것.**
 *
 * 계층대로 묶이나, 묶음에 안 걸린 타입이 드러나나(사이드바는 그것을 아예 안 그린다),
 * 찾기가 이름·식별자를 다 보나, 뜻의 계층이 없으면 그 탭을 안 만드나.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const ontologyApi = vi.hoisted(() => ({ types: vi.fn(), groups: vi.fn() }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

function type(slug: string, label: string, group: string | null, extra: object = {}) {
  return {
    id: slug,
    slug,
    label,
    icon: '',
    description: '',
    sort_order: 0,
    nav_group_id: group,
    nav_group_slug: group,
    kind_class: 'reference',
    system_source: '',
    entry_policy: 'open',
    key_policy: 'optional',
    key_scope: 'global',
    temporal_kind: 'evergreen',
    list_view: {},
    form_view: {},
    detail_view: {},
    title_template: '',
    is_active: true,
    object_count: 0,
    ...extra,
  }
}

function group(slug: string, label: string, parent: string | null = null) {
  return {
    id: slug,
    slug,
    label,
    icon: '',
    audience: 'everyone',
    parent_slug: parent,
    sort_order: 0,
    is_active: true,
  }
}

async function open() {
  const { default: ObjectTypesPage } = await import('@/modules/objects/ObjectTypesPage')
  render(
    <MemoryRouter>
      <ObjectTypesPage />
    </MemoryRouter>,
  )
}

describe('객체 타입 전부', () => {
  it('묶음 계층대로 묶고, 묶음에 없는 타입을 드러내고, 건수를 보인다', async () => {
    ontologyApi.groups.mockResolvedValue([group('base', '설계 기준정보'), group('mech', '기계 부품', 'base')])
    ontologyApi.types.mockResolvedValue([
      type('part', '부품', 'mech', { object_count: 1842 }),
      type('loose', '미분류타입', null),
    ])
    await open()

    await waitFor(() => expect(screen.getByText('부품')).toBeInTheDocument())
    expect(screen.getByText('설계 기준정보')).toBeInTheDocument()
    expect(screen.getByText('기계 부품')).toBeInTheDocument()
    // 건수를 보여 준다 — 비어 있는 타입과 만 건짜리를 같은 줄로 보이면 어디부터 볼지 모른다.
    expect(screen.getByText('1,842')).toBeInTheDocument()
    // **사이드바에는 안 나오는 타입** — 여기서만 보인다.
    expect(screen.getByText('묶음에 없는 타입')).toBeInTheDocument()
    expect(screen.getByText('미분류타입')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /부품/ })).toHaveAttribute('href', '/o/part')
  })

  it('찾기는 식별자로도 걸린다', async () => {
    ontologyApi.groups.mockResolvedValue([group('base', '기준정보')])
    ontologyApi.types.mockResolvedValue([
      type('part', '부품', 'base'),
      type('vendor', '공급사', 'base'),
    ])
    await open()
    await waitFor(() => expect(screen.getByText('공급사')).toBeInTheDocument())

    await userEvent.type(screen.getByLabelText('타입 찾기'), 'vend')
    await waitFor(() => expect(screen.queryByText('부품')).not.toBeInTheDocument())
    expect(screen.getByText('공급사')).toBeInTheDocument()
  })

  it('뜻의 계층이 있으면 그 탭이 서고, 없으면 안 선다', async () => {
    ontologyApi.groups.mockResolvedValue([group('base', '기준정보')])
    ontologyApi.types.mockResolvedValue([type('part', '부품', 'base')])
    await open()
    await waitFor(() => expect(screen.getByText('부품')).toBeInTheDocument())
    expect(screen.queryByRole('tab', { name: '뜻의 계층' })).not.toBeInTheDocument()

    vi.resetModules()
    ontologyApi.types.mockResolvedValue([
      type('product', '제품', 'base'),
      type('model', '개발모델', 'base', { parent_slug: 'product' }),
    ])
    await open()
    const tab = await screen.findByRole('tab', { name: '뜻의 계층' })
    await userEvent.click(tab)
    await waitFor(() => expect(screen.getByText(/rdfs:subClassOf/)).toBeInTheDocument())
  })
})
