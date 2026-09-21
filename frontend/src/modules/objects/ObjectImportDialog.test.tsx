/**
 * 일괄 입력이 지키는 것 — **오류가 있으면 「적용」 이 안 서고, 계획을 본 뒤에야 넣는다.**
 *
 * 올리면 작업이 된다. 계획은 작업의 결과에서 꺼내고, 적용은 **파일을 다시 올리지 않고**
 * 그 작업의 id 로 간다.
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
const jobsApi = vi.hoisted(() => ({
  apply: vi.fn(),
  waitFor: vi.fn(),
}))
// STUCK_MS 도 함께 — 모듈을 통째로 가짜로 두면 상수까지 사라지고, 그러면 화면은
// 「대기가 길어졌나」 를 못 재고 조용히 돈다.
vi.mock('@/modules/jobs/api', () => ({ jobsApi, STUCK_MS: 15_000 }))
const ontologyApi = vi.hoisted(() => ({ properties: vi.fn() }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

/** 작업 흉내 — 올리면 이 작업이 오고, 기다리면 `result` 에 계획이 들어 있다. */
function job(id: string, result: ImportPlan | null, status = 'done') {
  return {
    id,
    kind: 'objects_import',
    kind_label: '객체 일괄 입력',
    status,
    params: {},
    progress: { stage: '계획', done: 2, total: 2 },
    result,
    error: status === 'failed' ? '미리 본 것과 달라졌습니다' : null,
    parent_id: null,
    input_file_name: 'rows.csv',
    has_output: false,
    requested_by_name: null,
    workspace_slug: 'hq',
    cancel_requested: false,
    attempts: 1,
    created_at: '',
    started_at: null,
    finished_at: null,
  }
}

const TYPE = {
  slug: 'part',
  label: '부품',
  kind_class: 'record',
} as ObjectType

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
  ontologyApi.properties.mockResolvedValue([
    {
      key: 'weight',
      label: '무게',
      data_type: 'number',
      unit: 'kg',
      required: false,
      multi: false,
      enum_options: null,
      ref_type_slug: null,
    },
  ])
  const { ObjectImportDialog } = await import('@/modules/objects/ObjectImportDialog')
  const onApplied = vi.fn()
  render(<ObjectImportDialog type={TYPE} onClose={() => {}} onApplied={onApplied} />)
  return onApplied
}

/** 파일 길은 이제 탭 하나 뒤에 있다 — 기본은 「표에 붙여넣기」 다. */
async function upload() {
  await userEvent.click(screen.getByRole('tab', { name: '파일 업로드' }))
  const file = new File(['key,label\nP-1,볼트\n'], 'rows.csv', {
    type: 'text/csv',
  })
  await userEvent.upload(document.querySelector('input[type=file]') as HTMLInputElement, file)
  await userEvent.click(screen.getByRole('button', { name: /미리 보기/ }))
}

describe('일괄 입력', () => {
  beforeEach(() => vi.clearAllMocks())

  it('오류 행이 있으면 적용이 안 선다 — 어느 행이 왜 틀렸는지 적는다', async () => {
    objectApi.import.mockResolvedValue(job('j1', null, 'queued'))
    jobsApi.waitFor.mockResolvedValue(job('j1', WITH_ERROR))
    await mount()
    await upload()
    await waitFor(() => expect(screen.getByText('오류 1')).toBeInTheDocument())
    expect(screen.getByText('무게: 숫자여야 합니다')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^적용/ })).toBeDisabled()
    expect(objectApi.import).toHaveBeenCalledWith(
      'part',
      expect.any(File),
      expect.objectContaining({ workspaceSlug: 'hq' }),
    )
  })

  it('깨끗하면 적용이 서고, 클릭하면 파일을 다시 올리지 않고 그 작업으로 적용한다', async () => {
    objectApi.import.mockResolvedValue(job('j1', null, 'queued'))
    jobsApi.waitFor
      .mockResolvedValueOnce(job('j1', CLEAN))
      .mockResolvedValueOnce(job('j2', { ...CLEAN, applied: true }))
    jobsApi.apply.mockResolvedValue(job('j2', null, 'queued'))
    const onApplied = await mount()
    await upload()
    const apply = await screen.findByRole('button', {
      name: /^적용 — 새로 1 · 고침 1/,
    })
    expect(apply).toBeEnabled()
    await userEvent.click(apply)
    await waitFor(() => expect(jobsApi.apply).toHaveBeenCalledWith('j1'))
    expect(objectApi.import).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.getByText('적용했습니다.')).toBeInTheDocument())
    expect(onApplied).toHaveBeenCalled()
  })

  it('작업이 실패하면 그 이유가 보이고 적용은 안 선다', async () => {
    objectApi.import.mockResolvedValue(job('j1', null, 'queued'))
    jobsApi.waitFor.mockResolvedValue(job('j1', null, 'failed'))
    await mount()
    await upload()
    await waitFor(() => expect(screen.getByText(/미리 본 것과 달라졌습니다/)).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /^적용/ })).not.toBeInTheDocument()
  })

  it('아무도 집어 가지 않으면 워커를 의심하라고 말한다', async () => {
    // **워커가 꺼져 있으면 작업은 영영 대기다.** 화면이 말없이 돌기만 하면 사람은 제 파일이
    // 잘못된 줄 알고 몇 번을 다시 올린다 — 원인은 서버에 있는데.
    objectApi.import.mockResolvedValue(job('j1', null, 'queued'))
    jobsApi.waitFor.mockImplementation(
      (_id: string, onTick: (one: ReturnType<typeof job>) => void) => {
        // 시계를 앞으로 — 「대기」 가 길어진 것으로 본다.
        vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 20_000)
        onTick(job('j1', null, 'queued'))
        return new Promise<never>(() => {}) // 끝나지 않는다 — 집어 갈 워커가 없다
      },
    )
    await mount()
    await upload()
    expect(await screen.findByText(/작업 워커가 꺼져 있을 수 있습니다/)).toBeInTheDocument()
    vi.mocked(Date.now).mockRestore()
  })

  it('표에 붙여넣고 그대로 미리 본다 — 파일 없이', async () => {
    // 사내 DRM 이 저장을 잠그면 이 길이 유일하다. 표는 껍데기이고 서버로는 탭 글자가 간다.
    objectApi.import.mockResolvedValue(job('j1', null, 'queued'))
    jobsApi.waitFor.mockResolvedValue(job('j1', CLEAN))
    await mount()
    // 정의가 표의 열을 만든다 — 속성 `weight` 가 칸으로 서 있다.
    const cell = await screen.findByLabelText('1번 줄 key')
    cell.focus()
    await userEvent.paste('P-1\t볼트\t1.5\nP-2\t너트\t0.5')
    await userEvent.click(screen.getByRole('button', { name: /미리 보기 — 2줄/ }))

    await waitFor(() => expect(objectApi.import).toHaveBeenCalled())
    const sent = objectApi.import.mock.calls[0][1] as File
    expect(await sent.text()).toBe(
      'key\tlabel\tweight\taliases\nP-1\t볼트\t1.5\t\nP-2\t너트\t0.5\t',
    )
    await waitFor(() => expect(screen.getByText('볼트')).toBeInTheDocument())
  })

  it('관계 탭은 관계 API 로 간다', async () => {
    objectApi.importRelations.mockResolvedValue(job('j1', null, 'queued'))
    jobsApi.waitFor.mockResolvedValue(
      job('j1', {
        ...CLEAN,
        rows: [],
        counts: { create: 0, update: 0, unchanged: 0, error: 0 },
      }),
    )
    await mount()
    await userEvent.click(screen.getByRole('tab', { name: '관계' }))
    await upload()
    await waitFor(() => expect(objectApi.importRelations).toHaveBeenCalled())
    expect(objectApi.import).not.toHaveBeenCalled()
  })
})
