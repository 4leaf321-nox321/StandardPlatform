/**
 * 인터페이스 목록(ADR 0006) — **여러 타입이 한 목록에 서되, 줄마다 제 타입을 말하고 읽기만 한다.**
 *
 * - 줄의 링크는 인터페이스가 아니라 **그 객체의 타입** 상세로 간다.
 * - 타입 좁히기는 목록 요청의 `types` 로 나간다.
 * - 만들기 · 일괄 입력 · 내보내기가 없다 — 어느 타입에 넣을지 모르는 채로 받지 않는다.
 * - 구현 타입이 없으면 「객체가 없다」 가 아니라 「담을 타입이 없다」 고 말한다.
 * - 조건은 상위 인터페이스의 공통 속성까지 건다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ObjectInterface, ObjectType, PropertyDef } from '@/modules/ontology/api'

const objectApi = vi.hoisted(() => ({ list: vi.fn(), fields: vi.fn() }))
vi.mock('@/modules/objects/api', async (original) => {
  const real = await original<typeof import('@/modules/objects/api')>()
  return { ...real, objectApi }
})

function prop(key: string, label: string): PropertyDef {
  return { key, label, data_type: 'text', multi: false } as unknown as PropertyDef
}

function type(slug: string, label: string, extra: object = {}): ObjectType {
  return {
    id: `id-${slug}`,
    slug,
    label,
    temporal_kind: 'evergreen',
    ...extra,
  } as unknown as ObjectType
}

function iface(
  slug: string,
  label: string,
  extra: Partial<ObjectInterface> = {},
  properties: PropertyDef[] = [],
): ObjectInterface & { properties: PropertyDef[] } {
  return {
    id: `i-${slug}`,
    slug,
    label,
    icon: '',
    description: '',
    sort_order: 0,
    extends_slugs: [],
    list_view: {},
    implementers: [],
    object_count: 0,
    ...extra,
    properties,
  }
}

function row(id: string, typeSlug: string, label: string) {
  return {
    id,
    type_slug: typeSlug,
    key: null,
    label,
    description: '',
    properties: { maker: 'A사' },
    ref_labels: {},
    status: 'active',
    owner_workspace_slug: null,
    owner_workspace_name: null,
    valid_from_year: null,
    valid_to_year: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
  }
}

const TYPES = [type('tester', '시험장비'), type('meter', '계측기'), type('other', '남')]

async function open(target: ReturnType<typeof iface>, all: ReturnType<typeof iface>[] = [target]) {
  const { InterfaceListPage } = await import('@/modules/objects/InterfaceListPage')
  render(
    <MemoryRouter initialEntries={[`/o/${target.slug}`]}>
      <Routes>
        <Route
          path="/o/:typeSlug"
          element={<InterfaceListPage iface={target} interfaces={all} types={TYPES} />}
        />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  objectApi.list.mockReset()
  objectApi.fields.mockResolvedValue([])
})

describe('인터페이스 목록', () => {
  const EQUIP = iface('equip', '설비', { implementers: ['tester', 'meter'] }, [
    prop('maker', '제조사'),
  ])

  it('구현 타입 전부가 한 목록에 서고, 줄은 제 타입의 상세로 간다', async () => {
    objectApi.list.mockResolvedValue({
      items: [row('o1', 'tester', '시험기 1'), row('o2', 'meter', '계측기 1')],
      total: 2,
      limit: 50,
      offset: 0,
    })
    await open(EQUIP)

    await waitFor(() => expect(screen.getByText('시험기 1')).toBeInTheDocument())
    expect(objectApi.list).toHaveBeenCalledWith('equip', expect.objectContaining({ types: [] }))
    expect(screen.getByRole('link', { name: '시험기 1' })).toHaveAttribute('href', '/o/tester/o1')
    expect(screen.getByRole('link', { name: '계측기 1' })).toHaveAttribute('href', '/o/meter/o2')
    // 줄마다 제 타입을 말한다 — 타입 열과 좁히기 칩에 같은 이름이 선다.
    expect(screen.getAllByText('시험장비').length).toBeGreaterThan(1)
    // 공통 속성이 열로 선다.
    expect(screen.getByRole('columnheader', { name: '제조사' })).toBeInTheDocument()
    // 구현하지 않은 타입은 좁히기에 없다.
    expect(screen.queryByText('남')).not.toBeInTheDocument()
  })

  it('타입을 고르면 그 타입만 묻는다', async () => {
    objectApi.list.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 })
    await open(EQUIP)
    await waitFor(() => expect(objectApi.list).toHaveBeenCalled())

    await userEvent.click(screen.getByRole('checkbox', { name: '계측기만 보기' }))
    await waitFor(() =>
      expect(objectApi.list).toHaveBeenLastCalledWith(
        'equip',
        expect.objectContaining({ types: ['meter'] }),
      ),
    )
  })

  it('읽기만 한다 — 만들기 · 일괄 입력 · 내보내기가 없다', async () => {
    objectApi.list.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 })
    await open(EQUIP)
    await waitFor(() => expect(screen.getByText('아직 아무것도 없습니다')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /생성/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /일괄 입력/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /내보내기/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /통계/ })).toBeInTheDocument()
  })

  it('구현 타입이 없으면 담을 타입이 없다고 말하고 묻지 않는다', async () => {
    await open(iface('lonely', '외톨이'))
    expect(screen.getByText('이 인터페이스를 구현한 타입이 없습니다')).toBeInTheDocument()
    expect(screen.getByText(/구현 인터페이스/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /통계/ })).not.toBeInTheDocument()
    expect(objectApi.list).not.toHaveBeenCalled()
  })

  it('구현 타입 중 하나라도 연도를 쓰면 올해로 묻는다', async () => {
    objectApi.list.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 })
    const { InterfaceListPage } = await import('@/modules/objects/InterfaceListPage')
    render(
      <MemoryRouter>
        <InterfaceListPage
          iface={EQUIP}
          interfaces={[EQUIP]}
          types={[
            type('tester', '시험장비', { temporal_kind: 'lifecycle' }),
            type('meter', '계측기'),
          ]}
        />
      </MemoryRouter>,
    )
    await waitFor(() =>
      expect(objectApi.list).toHaveBeenCalledWith(
        'equip',
        expect.objectContaining({ year: new Date().getFullYear() }),
      ),
    )
  })
})

describe('공통 속성', () => {
  it('상위 인터페이스에서 이어받은 것까지, 제 것이 앞이고 같은 키는 한 번', async () => {
    const { commonProperties } = await import('@/modules/ontology/interfaces')
    const asset = iface('asset', '자산', {}, [prop('owner', '책임자'), prop('maker', '제조사')])
    const equip = iface('equip', '설비', { extends_slugs: ['asset'] }, [prop('maker', '제조사')])
    expect(commonProperties(equip, [asset, equip]).map((one) => one.key)).toEqual([
      'maker',
      'owner',
    ])
  })
})
