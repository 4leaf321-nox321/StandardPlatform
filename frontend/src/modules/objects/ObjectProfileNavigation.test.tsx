/**
 * 상세에서 **다른 타입의 객체로 건너갈 때** — 앞 객체의 응답이 남아 있는 사이에 「실제 타입
 * 주소로 옮기기」 가 앞 타입에 새 id 를 붙여 없는 주소로 갔다(기본 모델 → 판매 모델 404,
 * 2026-10-15). 화면이 객체마다 새로 서고, 옮기기는 지금 주소의 객체 응답만 본다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/shared/api/client'

const objectApi = vi.hoisted(() => ({
  profile: vi.fn(),
  setWatch: vi.fn(),
  history: vi.fn().mockResolvedValue([]),
  rollup: vi.fn().mockResolvedValue([]),
  years: vi.fn().mockResolvedValue([]),
}))
const ontologyApi = vi.hoisted(() => ({ schema: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))
vi.mock('@/modules/metrics/api', () => ({
  metricsApi: { list: vi.fn().mockResolvedValue([]) },
}))

function profileOf(id: string, typeSlug: string, label: string, typeLabel: string) {
  return {
    object: {
      id,
      type_slug: typeSlug,
      key: id.toUpperCase(),
      label,
      description: '',
      properties: {},
      ref_labels: {},
      status: 'active',
      owner_workspace_slug: null,
      valid_from_year: null,
      valid_to_year: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
    type_label: typeLabel,
    properties_schema: [],
    attachments: [],
    related: [],
    can_edit: true,
    watching: false,
    watcher_count: 0,
  }
}

describe('다른 타입의 객체로 건너가기', () => {
  it('앞 객체의 타입에 새 id 를 붙인 주소로 가지 않는다', async () => {
    const base = profileOf('b1', 'base_model', 'F4 폴드', '기본 모델')
    const sku = profileOf('s1', 'sku', 'SM-F400A (F4 폴드 · 동남아)', '판매 모델')
    let releaseSku: (value: unknown) => void = () => undefined
    objectApi.profile.mockImplementation((typeSlug: string, objectId: string) => {
      if (typeSlug === 'base_model' && objectId === 'b1') return Promise.resolve(base)
      // 판매 모델 응답은 늦게 온다 — 그 사이가 앞 객체의 응답이 남아 있던 틈이다.
      if (typeSlug === 'sku' && objectId === 's1') {
        return new Promise((resolve) => {
          releaseSku = resolve
        })
      }
      return Promise.reject(
        new ApiError(404, {
          error: {
            code: 'APP-OBJECTS-0011',
            message: '객체를 찾을 수 없습니다.',
          },
        }),
      )
    })
    ontologyApi.schema.mockResolvedValue({
      types: [
        { slug: 'base_model', label: '기본 모델' },
        { slug: 'sku', label: '판매 모델' },
      ],
    })
    const { default: ObjectProfilePage } = await import('@/modules/objects/ObjectProfilePage')
    render(
      <MemoryRouter initialEntries={['/o/base_model/b1']}>
        <Link to="/o/sku/s1">판매 모델로</Link>
        <Routes>
          <Route path="/o/:typeSlug/:objectId" element={<ObjectProfilePage />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByRole('heading', { name: /F4 폴드/ })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('link', { name: '판매 모델로' }))
    await waitFor(() => expect(objectApi.profile).toHaveBeenCalledWith('sku', 's1'))
    releaseSku(sku)

    expect(await screen.findByRole('heading', { name: /SM-F400A/ })).toBeInTheDocument()
    expect(objectApi.profile).not.toHaveBeenCalledWith('base_model', 's1')
    expect(screen.queryByText(/객체를 찾을 수 없습니다/)).not.toBeInTheDocument()
  })
})
