/**
 * 정의 점검(ADR 0092) — **후보를 보이고, 삭제는 미리 보기를 거친다.**
 *
 * 지키는 것: 종류마다 묶어 수와 함께 보이는가, 어림 · 원천 관리를 표시하는가, 삭제 단추가 서버의
 * 미리 보기(막는 것 · 함께 사라지는 것)를 먼저 여는가, 막는 것이 있으면 삭제할 수 없는가, 삭제한
 * 뒤 점검과 정의를 다시 읽는가.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import OntologyCheckPage from '@/modules/ontology/OntologyCheckPage'

const ontologyApi = vi.hoisted(() => ({
  check: vi.fn(),
  deletePlan: vi.fn(),
  removeProperty: vi.fn(),
  removeRelationType: vi.fn(),
  removeType: vi.fn(),
}))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))
const reloadSchema = vi.hoisted(() => vi.fn())
vi.mock('@/modules/ontology/OntologyLayout', () => ({
  useOntology: () => ({ schema: null, reload: reloadSchema, setError: vi.fn() }),
}))

const KINDS = {
  unused_field: '사용하지 않는 속성',
  interface_candidate: '인터페이스 후보',
  field_mismatch: '종류가 다른 같은 속성',
  unused_option: '사용하지 않는 선택 값',
  empty_type: '빈 타입',
  unused_relation: '사용하지 않는 관계 종류',
  undescribed_core: '설명 없는 공개 타입',
}

function item(over: Record<string, unknown>) {
  return {
    key: null,
    types: [],
    count: null,
    estimated: false,
    managed_by: '',
    action: '',
    next: '',
    ...over,
  }
}

const REPORT = {
  items: [
    item({
      kind: 'unused_field',
      slug: 'part',
      key: 'memo',
      label: '부품 · 메모',
      reason: '값이 있는 객체가 없고(객체 12건 중 0건) …',
      action: 'delete_property',
    }),
    item({
      kind: 'unused_field',
      slug: 'svc',
      key: 'note',
      label: '서비스 · 비고',
      reason: '표본에서 0건',
      estimated: true,
      action: 'delete_property',
    }),
    item({
      kind: 'interface_candidate',
      slug: 'weight',
      key: 'weight',
      label: '무게(weight)',
      reason: '같은 키 · 같은 종류의 칸이 2개 타입에 따로 있습니다',
      types: ['part', 'cover'],
    }),
    item({
      kind: 'empty_type',
      slug: 'relay_test',
      label: '시험 타입',
      reason: '만든 지 30일이 넘었는데 객체가 하나도 없습니다.',
      managed_by: 'datarelay',
      action: 'delete_type',
    }),
  ],
  counts: { unused_field: 2, interface_candidate: 1, empty_type: 1 },
  kinds: KINDS,
  notes: ['큰 타입은 id 로 고른 표본에서 세어 늘렸습니다(어림).'],
}

function plan(over: Record<string, unknown> = {}) {
  return {
    kind: 'property',
    slug: 'part',
    key: 'memo',
    label: '부품 · 메모',
    allowed: true,
    blocking: [],
    removes: ['속성 정의'],
    keeps: [],
    warnings: [],
    core_consumers: [],
    purge_deleted: 0,
    ...over,
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <OntologyCheckPage />
    </MemoryRouter>,
  )
}

describe('정의 점검', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ontologyApi.check.mockResolvedValue(REPORT)
  })

  it('종류마다 묶어 수와 까닭을 보이고, 어림 · 원천 관리를 표시한다', async () => {
    renderPage()
    const fields = await screen.findByRole('heading', { name: /사용하지 않는 속성/ })
    const section = fields.closest('section') as HTMLElement
    expect(within(section).getByText('부품 · 메모')).toBeInTheDocument()
    expect(within(section).getByText('어림')).toBeInTheDocument()
    expect(screen.getByText('part · cover')).toBeInTheDocument()
    expect(screen.getByText('datarelay 관리')).toBeInTheDocument()
    expect(screen.getByText(/표본에서 세어 늘렸습니다/)).toBeInTheDocument()
    // 묶을 후보에는 삭제 단추가 없다 — 정의 화면에서 수정한다.
    const candidate = screen.getByText('무게(weight)').closest('li') as HTMLElement
    expect(within(candidate).queryByRole('button', { name: '삭제' })).toBeNull()
  })

  it('삭제는 미리 보기를 먼저 열고, 삭제한 뒤 점검과 정의를 다시 읽는다', async () => {
    ontologyApi.deletePlan.mockResolvedValue(plan())
    ontologyApi.removeProperty.mockResolvedValue(undefined)
    renderPage()
    const row = (await screen.findByText('부품 · 메모')).closest('li') as HTMLElement
    await userEvent.click(within(row).getByRole('button', { name: '삭제' }))
    expect(ontologyApi.deletePlan).toHaveBeenCalledWith('property', 'part', 'memo')
    expect(await screen.findByText('속성 정의')).toBeInTheDocument()
    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: '삭제' }))
    await waitFor(() => expect(ontologyApi.removeProperty).toHaveBeenCalledWith('part', 'memo'))
    await waitFor(() => expect(ontologyApi.check).toHaveBeenCalledTimes(2))
    expect(reloadSchema).toHaveBeenCalled()
  })

  it('막는 것이 있으면 삭제할 수 없다', async () => {
    ontologyApi.deletePlan.mockResolvedValue(
      plan({
        kind: 'type',
        slug: 'relay_test',
        allowed: false,
        blocking: [{ code: 'APP-ONTOLOGY-0082', message: 'datarelay 가 관리하는 정의라' }],
      }),
    )
    renderPage()
    const row = (await screen.findByText('시험 타입')).closest('li') as HTMLElement
    await userEvent.click(within(row).getByRole('button', { name: '삭제' }))
    expect(ontologyApi.deletePlan).toHaveBeenCalledWith('type', 'relay_test')
    expect(await screen.findByText('datarelay 가 관리하는 정의라')).toBeInTheDocument()
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('button', { name: '삭제' })).toBeDisabled()
  })

  it('후보가 없으면 그렇게 말한다', async () => {
    ontologyApi.check.mockResolvedValue({ items: [], counts: {}, kinds: KINDS, notes: [] })
    renderPage()
    expect(await screen.findByText('정리할 후보가 없습니다')).toBeInTheDocument()
  })
})
