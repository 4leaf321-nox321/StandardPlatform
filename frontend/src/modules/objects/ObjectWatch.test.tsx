/**
 * 알림 구독 단추가 지키는 것 — **상태가 보이고, 누르면 서버가 정한다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

// 상세 화면은 곁가지(이력·롤업·연도)도 함께 부른다 — 하나라도 빠지면 그 자식이
// 터지고, 그러면 **단추를 누른 뒤에** 화면이 통째로 사라져 시험이 엉뚱한 곳을 가리킨다.
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

const PROFILE = {
  object: {
    id: 'bolt',
    type_slug: 'part',
    key: 'P-1',
    label: '볼트',
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
  type_label: '부품',
  properties_schema: [],
  attachments: [],
  related: [],
  can_edit: true,
  watching: false,
  watcher_count: 0,
}

async function open(profile: object, at = '/o/part/bolt') {
  objectApi.profile.mockResolvedValue(profile)
  ontologyApi.schema.mockResolvedValue({ types: [{ slug: 'part', label: '부품' }] })
  const { default: ObjectProfilePage } = await import('@/modules/objects/ObjectProfilePage')
  render(
    <MemoryRouter initialEntries={[at]}>
      <Routes>
        <Route path="/o/:typeSlug/:objectId" element={<ObjectProfilePage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('알림 구독', () => {
  it('안 보고 있으면 켤 수 있다', async () => {
    objectApi.setWatch.mockResolvedValue({ watching: true, watcher_count: 1 })
    await open(PROFILE)
    const button = await screen.findByRole('button', { name: /알림 구독/ })
    expect(button).toHaveAttribute('aria-pressed', 'false')

    await userEvent.click(button)
    await waitFor(() => expect(objectApi.setWatch).toHaveBeenCalledWith('part', 'bolt', true))
  })

  it('보고 있으면 그렇다고 말하고, 몇 사람인지 붙는다', async () => {
    // **혼자가 아니라는 것을 아는 것**이 고칠 때의 조심을 만든다.
    await open({ ...PROFILE, watching: true, watcher_count: 3 })
    const button = await screen.findByRole('button', { name: /지켜보는 중/ })
    expect(button).toHaveAttribute('aria-pressed', 'true')
    expect(button).toHaveTextContent('3')
  })
})

describe('인터페이스 주소로 연 상세', () => {
  it('객체의 실제 타입 주소로 옮겨 그 타입으로 고친다', async () => {
    objectApi.setWatch.mockResolvedValue({ watching: true, watcher_count: 1 })
    // 「설비」 를 가리키는 칸에서 누르면 /o/<인터페이스>/<id> 가 된다.
    await open(PROFILE, '/o/equip/bolt')
    // 옮겨 간 뒤의 조회를 기다린 다음 단추를 잡는다 — 처음 주소의 화면이 먼저 서므로, 부하가
    // 걸리면 옮기기 전에 단추를 잡아 시험이 흔들렸다(전체 검사에서 한 번).
    await waitFor(() => expect(objectApi.profile).toHaveBeenLastCalledWith('part', 'bolt'))
    const button = await screen.findByRole('button', { name: /알림 구독/ })
    await userEvent.click(button)
    await waitFor(() => expect(objectApi.setWatch).toHaveBeenCalledWith('part', 'bolt', true))
  })
})
