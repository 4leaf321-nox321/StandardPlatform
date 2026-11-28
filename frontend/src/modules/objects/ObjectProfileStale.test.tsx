/**
 * 상세의 수정 폼 — **연 뒤 누가 수정했으면 덮지 않는다**(OBJECTS-0162, 2026-11-28).
 *
 * 폼은 칸 전부를 실어 보낸다. 그래서 폼을 연 뒤 AI 가 다른 칸을 고쳤으면 저장이 그 칸을 연 때의
 * 값으로 되돌렸다. 폼은 연 때의 `updated_at` 을 `base_updated_at` 으로 함께 보내고, 서버가 409 로
 * 거절하면 그 말과 「다시 불러오기」 를 보인다 — 다시 불러오면 폼이 새 값과 새 때로 채워진다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/shared/api/client'

const objectApi = vi.hoisted(() => ({
  profile: vi.fn(),
  update: vi.fn(),
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

const SEEN = '2026-11-28T10:00:00.123456+09:00'
const LATER = '2026-11-28T10:05:00.654321+09:00'

function profileAt(label: string, properties: Record<string, unknown>, updatedAt: string) {
  return {
    object: {
      id: 'c1',
      type_slug: 'part',
      key: null,
      label,
      description: '',
      properties,
      ref_labels: {},
      status: 'active',
      owner_workspace_slug: 'team',
      valid_from_year: null,
      valid_to_year: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: updatedAt,
    },
    type_label: '부품',
    properties_schema: [
      { key: 'c', label: '색', data_type: 'text', unit: '', section: '', multi: false },
      { key: 'w', label: '무게', data_type: 'number', unit: '', section: '', multi: false },
    ],
    attachments: [],
    related: [],
    can_edit: true,
    watching: false,
    watcher_count: 0,
  }
}

async function mount() {
  ontologyApi.schema.mockResolvedValue({
    types: [{ slug: 'part', label: '부품' }],
    groups: [],
    relation_types: [],
    interfaces: [],
  })
  const { default: ObjectProfilePage } = await import('@/modules/objects/ObjectProfilePage')
  render(
    <MemoryRouter initialEntries={['/o/part/c1']}>
      <Routes>
        <Route path="/o/:typeSlug/:objectId" element={<ObjectProfilePage />} />
      </Routes>
    </MemoryRouter>,
  )
  await screen.findByRole('heading', { name: /볼트/ })
}

describe('상세의 수정 폼 — 그 사이의 수정', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('연 때를 함께 보내고, 409 면 덮지 않고 말한 뒤 다시 불러오면 새 값과 새 때로 보낸다', async () => {
    objectApi.profile
      .mockResolvedValueOnce(profileAt('볼트', { c: '회색', w: 1 }, SEEN))
      // 그 사이 AI 가 무게를 고쳤다.
      .mockResolvedValue(profileAt('볼트', { c: '회색', w: 2 }, LATER))
    await mount()

    await userEvent.click(screen.getByRole('button', { name: '수정' }))
    const name = screen.getByLabelText('이름')
    await userEvent.clear(name)
    await userEvent.type(name, '볼트 M6')

    objectApi.update.mockRejectedValueOnce(
      new ApiError(409, {
        error: {
          code: 'APP-OBJECTS-0162',
          message: '그 사이 다른 사람이 이 객체를 수정했습니다 — 다시 불러온 뒤 수정하세요.',
          details: { updated_at: LATER },
        },
      }),
    )
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    expect(objectApi.update).toHaveBeenLastCalledWith('part', 'c1', {
      label: '볼트 M6',
      description: '',
      properties: { c: '회색', w: 1 },
      base_updated_at: SEEN,
    })
    expect(
      await screen.findByText(
        '그 사이 다른 사람이 이 객체를 수정했습니다 — 다시 불러온 뒤 수정하세요.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('APP-OBJECTS-0162')).toBeInTheDocument()
    expect(screen.getByText('다시 불러오면 지금 입력한 값은 사라집니다.')).toBeInTheDocument()
    // 덮지 않았다 — 폼은 열린 채 내 입력이 그대로다.
    expect(screen.getByLabelText('이름')).toHaveValue('볼트 M6')

    await userEvent.click(screen.getByRole('button', { name: '다시 불러오기' }))
    await waitFor(() => expect(objectApi.profile).toHaveBeenCalledTimes(2))
    // 폼이 지금 값으로 다시 채워진다 — 내 입력은 사라지고, 말도 사라진다.
    await waitFor(() => expect(screen.getByLabelText('이름')).toHaveValue('볼트'))
    expect(screen.queryByText(/그 사이 다른 사람이/)).not.toBeInTheDocument()

    objectApi.update.mockResolvedValueOnce(profileAt('볼트', { c: '회색', w: 2 }, LATER).object)
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    expect(objectApi.update).toHaveBeenLastCalledWith('part', 'c1', {
      label: '볼트',
      description: '',
      properties: { c: '회색', w: 2 },
      base_updated_at: LATER,
    })
  })
})
