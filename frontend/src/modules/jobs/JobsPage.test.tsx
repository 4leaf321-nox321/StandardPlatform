/**
 * 「작업」 화면이 지키는 것 — **계획을 여기서 읽고 여기서 적용한다.**
 *
 * 대화상자를 닫아도 일은 계속된다고 해 놓고 돌아와서 적용할 자리가 없으면, 그 말은 절반만
 * 참이다. 그리고 워커가 없으면 그 사실부터 말해야 한다 — 안 그러면 「대기」 가 영영 대기인
 * 이유를 사람이 알 길이 없다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Job } from '@/modules/jobs/api'

const jobsApi = vi.hoisted(() => ({
  list: vi.fn(),
  kinds: vi.fn(),
  workers: vi.fn(),
  apply: vi.fn(),
  cancel: vi.fn(),
  download: vi.fn(),
}))
vi.mock('@/modules/jobs/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/jobs/api')>()),
  jobsApi,
}))

function job(over: Partial<Job> = {}): Job {
  return {
    id: 'j1',
    kind: 'objects_import',
    kind_label: '객체 일괄 입력',
    status: 'done',
    params: { type_slug: 'part' },
    progress: { stage: '계획', done: 2, total: 2 },
    result: null,
    error: null,
    parent_id: null,
    input_file_name: 'rows.csv',
    has_output: false,
    requested_by_name: '관리자',
    workspace_slug: 'hq',
    cancel_requested: false,
    attempts: 1,
    created_at: '2026-09-21T09:00:00+09:00',
    started_at: null,
    finished_at: null,
    ...over,
  }
}

const CLEAN_PLAN = {
  applied: false,
  rows: [
    {
      row: 1,
      action: 'create' as const,
      label: '볼트',
      key: 'P-1',
      object_id: null,
      changes: ['weight'],
      message: '',
    },
  ],
  errors: [],
  counts: { create: 1, update: 0, unchanged: 0, error: 0 },
}

async function mount(jobs: Job[], alive = true) {
  jobsApi.list.mockResolvedValue({ items: jobs, total: jobs.length })
  jobsApi.kinds.mockResolvedValue([
    { name: 'objects_import', label: '객체 일괄 입력', needs_file: true, two_step: true },
    { name: 'datasource_sync', label: '데이터 소스 동기화', needs_file: false, two_step: false },
  ])
  jobsApi.workers.mockResolvedValue(
    alive
      ? [
          {
            worker_id: 'w1',
            host: 'a',
            pid: 1,
            started_at: '',
            last_seen: '',
            current_job_id: null,
            alive: true,
          },
        ]
      : [],
  )
  const { default: JobsPage } = await import('@/modules/jobs/JobsPage')
  render(<JobsPage />)
  // **줄이 그려질 때까지 기다린다.** 요청이 나간 것만 보고 넘어가면, 느린 기계에서 표가
  // 아직 비어 있는 채로 단추를 찾는다.
  if (jobs.length) await screen.findByRole('button', { name: /펼치기|접기/ })
  else await waitFor(() => expect(jobsApi.list).toHaveBeenCalled())
}

describe('작업 화면', () => {
  beforeEach(() => vi.clearAllMocks())

  it('끝난 계획을 펼쳐 읽고 적용한다 — 창을 닫았어도', async () => {
    await mount([job({ result: CLEAN_PLAN })])
    expect(screen.getByText('적용 대기')).toBeInTheDocument()
    jobsApi.apply.mockResolvedValue(job({ id: 'j2' }))

    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    expect(screen.getByText('볼트')).toBeInTheDocument() // 계획 표가 그대로 보인다
    await userEvent.click(screen.getByRole('button', { name: '적용' }))
    await waitFor(() => expect(jobsApi.apply).toHaveBeenCalledWith('j1'))
  })

  it('오류가 있는 계획은 적용이 안 선다 — 고쳐서 다시 올리라고 말한다', async () => {
    const broken = {
      ...CLEAN_PLAN,
      rows: [{ ...CLEAN_PLAN.rows[0], action: 'error' as const, message: '무게: 숫자여야 합니다' }],
      counts: { create: 0, update: 0, unchanged: 0, error: 1 },
    }
    await mount([job({ result: broken })])
    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    expect(screen.queryByRole('button', { name: '적용' })).not.toBeInTheDocument()
    expect(screen.getByText(/고쳐서 다시 올리세요/)).toBeInTheDocument()
    expect(screen.getByText('무게: 숫자여야 합니다')).toBeInTheDocument()
  })

  it('이미 적용한 작업은 또 적용하지 않는다', async () => {
    await mount([job({ result: { ...CLEAN_PLAN, applied: true } })])
    expect(screen.queryByText('적용 대기')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    expect(screen.queryByRole('button', { name: '적용' })).not.toBeInTheDocument()
    expect(screen.getByText('적용했습니다.')).toBeInTheDocument()
  })

  it('내보내기는 파일을 받는다', async () => {
    await mount([
      job({
        kind: 'objects_export',
        kind_label: '객체 내보내기',
        has_output: true,
        input_file_name: null,
        result: { type_slug: 'part', rows: 12, format: 'csv' },
      }),
    ])
    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    await userEvent.click(screen.getByRole('button', { name: /파일 받기/ }))
    await waitFor(() => expect(jobsApi.download).toHaveBeenCalled())
  })

  it('기본은 내가 시킨 것만 — 타이머가 넣은 줄에 파묻히지 않게', async () => {
    await mount([job({ result: CLEAN_PLAN })])
    expect(jobsApi.list).toHaveBeenCalledWith(expect.objectContaining({ mine: true }))

    await userEvent.click(screen.getByLabelText('내가 시킨 것만'))
    await waitFor(() =>
      expect(jobsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ mine: false })),
    )

    await userEvent.selectOptions(screen.getByLabelText('종류'), 'datasource_sync')
    await waitFor(() =>
      expect(jobsApi.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ kind: 'datasource_sync' }),
      ),
    )
  })

  it('워커가 하나도 없으면 그것부터 말한다', async () => {
    await mount([job({ status: 'queued', result: null })], false)
    await waitFor(() => expect(screen.getByText('워커가 살아 있지 않습니다')).toBeInTheDocument())
    // 도는 중인 작업은 펼칠 것이 없다.
    expect(screen.getByRole('button', { name: '펼치기' })).toBeDisabled()
  })
})
