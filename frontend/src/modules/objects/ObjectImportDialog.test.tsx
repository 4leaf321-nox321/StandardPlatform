/**
 * 파일 가져오기가 지키는 것 — **오류가 있으면 「적용」 이 안 서고, 계획을 본 뒤에야 넣는다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ImportPlan } from '@/modules/objects/api'
import type { ObjectType } from '@/modules/ontology/api'

vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { home_workspace_slug: 'hq', memberships: [] } }),
}))
const objectApi = vi.hoisted(() => ({
  import: vi.fn(),
  importRelations: vi.fn(),
  template: vi.fn(),
  export: vi.fn(),
  exportRelations: vi.fn(),
}))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

const TYPE = { slug: 'part', label: '부품', kind_class: 'record' } as ObjectType

const WITH_ERROR: ImportPlan = {
  applied: false,
  rows: [
    {
      row: 1,
      action: 'create',
      label: '볼트',
      key: 'P-1',
      object_id: null,
      changes: ['weight'],
      message: '',
    },
    {
      row: 2,
      action: 'error',
      label: '너트',
      key: 'P-2',
      object_id: null,
      changes: [],
      message: '무게: 숫자여야 합니다',
    },
  ],
  errors: [],
  counts: { create: 1, update: 0, unchanged: 0, error: 1 },
}

const CLEAN: ImportPlan = {
  applied: false,
  rows: [
    {
      row: 1,
      action: 'create',
      label: '볼트',
      key: 'P-1',
      object_id: null,
      changes: ['weight'],
      message: '',
    },
    {
      row: 2,
      action: 'update',
      label: '너트',
      key: 'P-2',
      object_id: 'x',
      changes: ['material'],
      message: '',
    },
  ],
  errors: [],
  counts: { create: 1, update: 1, unchanged: 0, error: 0 },
}

async function mount() {
  const { ObjectImportDialog } = await import('@/modules/objects/ObjectImportDialog')
  const onApplied = vi.fn()
  render(<ObjectImportDialog type={TYPE} onClose={() => {}} onApplied={onApplied} />)
  return onApplied
}

async function upload() {
  const file = new File(['key,label\nP-1,볼트\n'], 'rows.csv', { type: 'text/csv' })
  await userEvent.upload(document.querySelector('input[type=file]') as HTMLInputElement, file)
  await userEvent.click(screen.getByRole('button', { name: /미리 보기/ }))
}

describe('파일 가져오기', () => {
  beforeEach(() => vi.clearAllMocks())

  it('오류 행이 있으면 적용이 안 선다 — 어느 행이 왜 틀렸는지 적는다', async () => {
    objectApi.import.mockResolvedValue(WITH_ERROR)
    await mount()
    await upload()
    await waitFor(() => expect(screen.getByText('오류 1')).toBeInTheDocument())
    expect(screen.getByText('무게: 숫자여야 합니다')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^적용/ })).toBeDisabled()
    // 미리 보기는 apply=false 로 나갔다.
    expect(objectApi.import).toHaveBeenCalledWith(
      'part',
      expect.any(File),
      expect.objectContaining({ apply: false, workspaceSlug: 'hq' }),
    )
  })

  it('깨끗하면 적용이 서고, 클릭하면 apply=true 로 다시 보낸다', async () => {
    objectApi.import.mockResolvedValueOnce(CLEAN).mockResolvedValueOnce({ ...CLEAN, applied: true })
    const onApplied = await mount()
    await upload()
    const apply = await screen.findByRole('button', { name: /^적용 — 새로 1 · 고침 1/ })
    expect(apply).toBeEnabled()
    await userEvent.click(apply)
    await waitFor(() =>
      expect(objectApi.import).toHaveBeenLastCalledWith(
        'part',
        expect.any(File),
        expect.objectContaining({ apply: true }),
      ),
    )
    await waitFor(() => expect(screen.getByText('적용했습니다.')).toBeInTheDocument())
    expect(onApplied).toHaveBeenCalled()
  })

  it('관계 탭은 관계 API 로 간다', async () => {
    objectApi.importRelations.mockResolvedValue({
      ...CLEAN,
      rows: [],
      counts: { create: 0, update: 0, unchanged: 0, error: 0 },
    })
    await mount()
    await userEvent.click(screen.getByRole('tab', { name: '관계' }))
    await upload()
    await waitFor(() => expect(objectApi.importRelations).toHaveBeenCalled())
    expect(objectApi.import).not.toHaveBeenCalled()
  })
})
