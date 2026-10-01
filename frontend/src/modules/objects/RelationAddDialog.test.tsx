/**
 * 잇기 창 — **끝에 인터페이스가 적힌 관계는 그것을 구현한 타입이 후보다**(ADR 0006).
 *
 * - 출발에 인터페이스가 적혔으면, 구현 타입의 객체에서 그 관계를 고를 수 있다.
 * - 도착에 인터페이스가 적혔으면, 후보는 구현 타입들의 객체다(다른 타입은 아니다).
 * - 구현한 타입이 없는 인터페이스는 「아무 타입이나」 가 아니다 — 이을 수 없다고 말한다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RelationType } from '@/modules/ontology/api'

const ontologyApi = vi.hoisted(() => ({ schema: vi.fn() }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

/** 후보를 찾는 쪽 — 어느 타입들에 물었는지만 본다. */
const asked = vi.hoisted(() => ({ sources: [] as { slug: string }[] }))
vi.mock('@/modules/objects/useObjectOptions', () => ({
  useObjectOptions: (sources: { slug: string }[]) => {
    asked.sources = sources
    return {
      options: [],
      pinned: [],
      total: 0,
      loading: false,
      failed: false,
      setQuery: vi.fn(),
    }
  },
}))

function type(slug: string, label: string) {
  return { slug, label, kind_class: 'record' }
}

function iface(slug: string, label: string, implementers: string[]) {
  return {
    id: slug,
    slug,
    label,
    icon: '',
    description: '',
    sort_order: 0,
    extends_slugs: [],
    list_view: {},
    implementers,
    object_count: 0,
    properties: [],
  }
}

function kind(slug: string, label: string, src: string[] | null, dst: string[] | null) {
  return {
    slug,
    label,
    inverse_label: '',
    is_active: true,
    src_type_slugs: src,
    dst_type_slugs: dst,
  } as unknown as RelationType
}

async function open(relationTypes: RelationType[], objectTypeSlug = 'lab') {
  const { RelationAddDialog } = await import('@/modules/objects/RelationAddDialog')
  render(
    <RelationAddDialog
      typeSlug={objectTypeSlug}
      objectId="o1"
      objectLabel="한국교정원"
      objectTypeSlug={objectTypeSlug}
      relationTypes={relationTypes}
      onClose={vi.fn()}
      onAdded={vi.fn()}
    />,
  )
}

beforeEach(() => {
  asked.sources = []
  ontologyApi.schema.mockResolvedValue({
    types: [type('lab', '교정기관'), type('tester', '시험장비'), type('part', '부품')],
    interfaces: [iface('equip', '설비', ['tester']), iface('lonely', '외톨이', [])],
    relation_types: [],
  })
})

describe('잇기 창의 인터페이스 끝', () => {
  it('도착이 인터페이스면 구현 타입에서만 고른다', async () => {
    await open([kind('calibrates', '교정', ['lab'], ['equip'])])
    await waitFor(() => expect(asked.sources.map((one) => one.slug)).toEqual(['tester']))
    expect(
      screen.getByText('시험장비 만 선택할 수 있습니다.', { exact: false }),
    ).toBeInTheDocument()
  })

  it('출발이 인터페이스면 구현 타입의 객체에서 그 관계를 고를 수 있다', async () => {
    await open([kind('owned_by', '소유', ['equip'], ['lab'])], 'tester')
    await waitFor(() => expect(screen.getByText(/「소유」/)).toBeInTheDocument())
    expect(screen.queryByText(/출발할 수 있는 관계 종류가 없습니다/)).not.toBeInTheDocument()
  })

  it('구현한 타입이 없는 인터페이스는 아무 타입이나가 아니다', async () => {
    await open([kind('uses', '사용', ['lab'], ['lonely'])])
    await waitFor(() =>
      expect(screen.getByText(/해당하는 타입이 없어 이을 수 없습니다/)).toBeInTheDocument(),
    )
    expect(asked.sources).toEqual([])
    expect(screen.queryByText(/아무 타입이나/)).not.toBeInTheDocument()
  })
})
