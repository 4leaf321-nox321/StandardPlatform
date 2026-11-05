/**
 * 상세의 기본 정보 — **한눈에 읽히게**(2026-11-05).
 *
 * - 뷰가 열 수를 안 정한 묶음은 폭에 맞춰 2 · 3열로 편다(1열이면 칸 열일곱이 한 화면을 채웠다).
 *   정했으면(1열 포함) 그대로 따른다.
 * - 숫자는 세 자리마다 쉼표.
 * - 참조 칸은 누르면 그 객체로 간다 — 못 찾은 객체에는 링크를 걸지 않는다(누르면 404).
 */

import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const objectApi = vi.hoisted(() => ({
  profile: vi.fn(),
  setWatch: vi.fn(),
  history: vi.fn().mockResolvedValue([]),
  rollup: vi.fn().mockResolvedValue([]),
  years: vi.fn().mockResolvedValue([]),
  relationProposals: vi.fn().mockResolvedValue([]),
}))
const ontologyApi = vi.hoisted(() => ({ schema: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))
vi.mock('@/modules/metrics/api', () => ({
  metricsApi: { list: vi.fn().mockResolvedValue([]) },
}))

function def(key: string, label: string, extra: Record<string, unknown> = {}) {
  return {
    key,
    label,
    data_type: 'text',
    unit: '',
    section: '',
    ref_type_slug: null,
    multi: false,
    ...extra,
  }
}

function profileWith(properties: Record<string, unknown>, schema: unknown[]) {
  return {
    object: {
      id: 'c1',
      type_slug: 'svc_case',
      key: 'SR-1',
      label: '외관 파손',
      description: '',
      properties,
      ref_labels: { s1: 'SM-N100K (N10 · 국내)' },
      status: 'active',
      owner_workspace_slug: null,
      valid_from_year: null,
      valid_to_year: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
    type_label: '시장 서비스',
    properties_schema: schema,
    attachments: [],
    related: [],
    can_edit: false,
    watching: false,
    watcher_count: 0,
  }
}

async function mount(detailView: unknown = {}) {
  ontologyApi.schema.mockResolvedValue({
    types: [{ slug: 'svc_case', label: '시장 서비스', detail_view: detailView }],
    groups: [],
    relation_types: [],
    interfaces: [],
  })
  const { default: ObjectProfilePage } = await import('@/modules/objects/ObjectProfilePage')
  render(
    <MemoryRouter initialEntries={['/o/svc_case/c1']}>
      <Routes>
        <Route path="/o/:typeSlug/:objectId" element={<ObjectProfilePage />} />
      </Routes>
    </MemoryRouter>,
  )
  return screen.findByRole('heading', { name: /외관 파손/ })
}

describe('상세의 기본 정보', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('숫자에 쉼표를 넣고, 참조 칸은 그 객체로 가는 링크다', async () => {
    objectApi.profile.mockResolvedValue(
      profileWith({ cost: 85000, sku: 's1', gone: 'x9' }, [
        def('cost', '비용', { data_type: 'number', unit: '원' }),
        def('sku', '판매 모델', { data_type: 'object_ref', ref_type_slug: 'sku' }),
        def('gone', '전작', { data_type: 'object_ref', ref_type_slug: 'base_model' }),
      ]),
    )
    await mount()

    expect(screen.getByText('85,000')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'SM-N100K (N10 · 국내)' })).toHaveAttribute(
      'href',
      '/o/sku/s1',
    )
    // 못 찾은 객체 — 글자로만.
    expect(screen.getByText('(찾을 수 없는 객체)').closest('a')).toBeNull()
  })

  it('열 수를 안 정했으면 폭에 맞춰 펴고, 정했으면 따른다', async () => {
    objectApi.profile.mockResolvedValue(
      profileWith({ a: '1', b: '2' }, [def('a', '가'), def('b', '나', { section: '처리' })]),
    )
    await mount({ sections: [{ name: '처리', columns: 1 }] })

    const loose = screen.getByText('가').closest('dl')
    const fixed = screen.getByText('나').closest('dl')
    expect(loose?.className).toContain('xl:grid-cols-3')
    expect(fixed?.className).not.toContain('grid-cols')
  })
})
