/**
 * 「작업」 화면이 지키는 것 — **계획을 여기서 읽고 여기서 적용한다.**
 *
 * 대화상자를 닫아도 일은 계속된다고 해 놓고 돌아와서 적용할 자리가 없으면, 그 말은 절반만
 * 참이다. 그리고 워커가 없으면 그 사실부터 말해야 한다 — 안 그러면 「대기」 가 영영 대기인
 * 이유를 사람이 알 길이 없다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Job } from '@/modules/jobs/api'
import { ApiError } from '@/shared/api/client'

const jobsApi = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  rowsCsv: vi.fn(),
  kinds: vi.fn(),
  workers: vi.fn(),
  apply: vi.fn(),
  cancel: vi.fn(),
  applyMany: vi.fn(),
  cancelMany: vi.fn(),
  download: vi.fn(),
}))
const auth = vi.hoisted(() => ({ admin: true, id: 'u1' }))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { id: auth.id, is_system_admin: auth.admin, memberships: [] } }),
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
  counts: { create: 1, update: 0, unchanged: 0, unlink: 0, error: 0 },
}

async function mount(jobs: Job[], alive = true) {
  jobsApi.list.mockResolvedValue({ items: jobs, total: jobs.length })
  jobsApi.kinds.mockResolvedValue([
    { name: 'objects_import', label: '객체 일괄 입력', needs_file: true, two_step: true },
    { name: 'datasource_sync', label: '데이터 소스 동기화', needs_file: false, two_step: false },
    { name: 'filestore_gc', label: '고아 첨부 파일 정리', needs_file: false, two_step: true },
    { name: 'ontology_retype', label: '속성 종류 변경', needs_file: false, two_step: true },
    { name: 'objects_photos', label: '사진 일괄 업로드', needs_file: true, two_step: true },
    { name: 'workspace_reassign', label: '부서 자료 이동', needs_file: false, two_step: true },
    { name: 'ontology_move', label: '객체 옮기기', needs_file: false, two_step: true },
    { name: 'ontology_rename', label: '속성 키 변경', needs_file: false, two_step: true },
    {
      name: 'ontology_export',
      label: '온톨로지 통째로 내보내기',
      needs_file: false,
      two_step: false,
    },
    { name: 'review_apply', label: '검토함 일괄 확인', needs_file: false, two_step: false },
    { name: 'bundle_export', label: '묶음 내보내기', needs_file: false, two_step: false },
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
  // 줄이 여럿일 수 있다 — 하나만 찾으면 「여러 개」 로 터진다.
  if (jobs.length) await screen.findAllByRole('button', { name: /펼치기|접기/ })
  else await waitFor(() => expect(jobsApi.list).toHaveBeenCalled())
}

describe('작업 화면', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.admin = true
  })

  it('남의 작업에는 「취소」 · 「적용」 을 안 세운다 — 시킨 사람과 시스템 관리자만', async () => {
    auth.admin = false
    await mount([
      job({ id: 'theirs', requested_by_id: 'u2', result: CLEAN_PLAN }),
      job({ id: 'running', requested_by_id: 'u2', status: 'running' }),
    ])
    await waitFor(() => expect(jobsApi.kinds).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: '취소' })).not.toBeInTheDocument()
    expect(screen.queryByText('적용 대기')).not.toBeInTheDocument()
  })

  it('속성 종류 변경의 계획도 펼쳐 읽고 적용한다 — rows · counts 가 없는 모양이어도', async () => {
    // 그 결과(`RetypeOut`)를 못 알아보던 때는 창을 닫고 돌아오면 「남길 결과가 없는
    // 작업입니다」 만 보였다 — 서버는 적용을 받아 주는데(2026-10-08).
    const retype = (over: Record<string, unknown> = {}) => ({
      applied: false,
      data_type_before: 'text',
      data_type_after: 'number',
      types: [
        {
          type_slug: 'part',
          type_label: '부품',
          key: 'weight',
          via: '',
          with_value: 120,
          converted: 118,
          unchanged: 0,
          cleared: 2,
        },
      ],
      failures: [],
      failures_total: 0,
      mapped: [],
      errors: [],
      warnings: ['무게: 소수 둘째 자리 아래는 버립니다 3개.'],
      core_consumers: [],
      snapshot_id: null,
      fingerprint: 'abc',
      ...over,
    })
    await mount([
      job({
        id: 'r1',
        kind: 'ontology_retype',
        kind_label: '속성 종류 변경',
        params: { owner: 'type', slug: 'part', key: 'weight', request: { accept_core: false } },
        input_file_name: null,
        result: retype(),
      }),
      job({
        id: 'r2',
        kind: 'ontology_retype',
        kind_label: '속성 종류 변경',
        params: { owner: 'type', slug: 'part', key: 'grade', request: { accept_core: false } },
        input_file_name: null,
        result: retype({ errors: ['등급: 변환할 수 없는 값이 3종류 5건 있습니다'] }),
      }),
    ])
    await waitFor(() => expect(jobsApi.kinds).toHaveBeenCalled())
    // 목록에서도 무엇이 되는지 한 줄로 — 오류 없는 계획만 「적용 대기」.
    expect(await screen.findAllByText(/계획 — 변환 118 · 비움 2/)).toHaveLength(2)
    expect(screen.getAllByText('적용 대기')).toHaveLength(1)

    const [first] = screen.getAllByRole('button', { name: '펼치기' })
    await userEvent.click(first)
    expect(screen.queryByText('남길 결과가 없는 작업입니다.')).not.toBeInTheDocument()
    expect(screen.getByText(/소수 둘째 자리/)).toBeInTheDocument()
    jobsApi.apply.mockResolvedValue(job({ id: 'r1-apply', kind: 'ontology_retype' }))
    await userEvent.click(screen.getByRole('button', { name: '적용' }))
    await waitFor(() => expect(jobsApi.apply).toHaveBeenCalledWith('r1'))
  })

  it('적용 단계가 없는 종류(데이터 소스 동기화)의 계획에는 「적용」 을 안 세운다', async () => {
    // 눌러도 서버가 거절한다 — 동기화의 적용은 그 화면에서 「동기화」 를 다시 누르는 것이다.
    await mount([
      job({
        kind: 'datasource_sync',
        kind_label: '데이터 소스 동기화',
        result: CLEAN_PLAN,
      }),
    ])
    await waitFor(() => expect(jobsApi.kinds).toHaveBeenCalled())
    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    expect(screen.getByText('볼트')).toBeInTheDocument()
    expect(await screen.findByText(/계획만 본 것입니다/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '적용' })).not.toBeInTheDocument()
    expect(screen.queryByText('적용 대기')).not.toBeInTheDocument()
  })

  it('끝난 계획을 펼쳐 읽고 적용한다 — 창을 닫았어도', async () => {
    await mount([job({ result: CLEAN_PLAN })])
    // 종류 목록(적용 단계가 있나)이 온 뒤에 선다.
    expect(await screen.findByText('적용 대기')).toBeInTheDocument()
    jobsApi.apply.mockResolvedValue(job({ id: 'j2' }))

    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    expect(screen.getByText('볼트')).toBeInTheDocument() // 계획 표가 그대로 보인다
    await userEvent.click(screen.getByRole('button', { name: '적용' }))
    await waitFor(() => expect(jobsApi.apply).toHaveBeenCalledWith('j1'))
  })

  it('목록은 줄 목록 없이 오고, 펼치면 상세를 읽어 그린다 · 나머지 줄은 CSV 로', async () => {
    // 5만 줄 계획을 목록이 작업마다 통째로 싣던 때는 목록 한 번이 93MB 였다(2026-10-09). 목록은
    // 건수만 — 「적용」 은 그것으로 가르고, 표는 펼칠 때 상세(앞의 500줄)를 읽어 그린다.
    const brief = { ...CLEAN_PLAN, rows: [], rows_omitted: 50_000 }
    await mount([job({ result: brief })])
    expect(await screen.findByText('적용 대기')).toBeInTheDocument()
    jobsApi.get.mockResolvedValue(job({ result: { ...CLEAN_PLAN, rows_omitted: 49_999 } }))
    jobsApi.rowsCsv.mockResolvedValue(undefined)

    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    expect(await screen.findByText('볼트')).toBeInTheDocument()
    expect(jobsApi.get).toHaveBeenCalledWith('j1')
    expect(screen.getByText(/나머지 49,999줄/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '모든 줄 받기(CSV)' }))
    await waitFor(() => expect(jobsApi.rowsCsv).toHaveBeenCalledWith('j1'))
  })

  it('줄 목록이 다 실린 결과는 상세를 다시 읽지 않는다', async () => {
    await mount([job({ result: CLEAN_PLAN })])
    await userEvent.click(await screen.findByRole('button', { name: '펼치기' }))
    expect(await screen.findByText('볼트')).toBeInTheDocument()
    expect(jobsApi.get).not.toHaveBeenCalled()
  })

  it('결과가 스스로 한 줄로 말하는 계획(고아 첨부 정리)도 그 말과 함께 적용이 선다', async () => {
    await mount([
      job({
        kind: 'filestore_gc',
        kind_label: '고아 첨부 파일 정리',
        params: {},
        input_file_name: null,
        result: {
          applied: false,
          ok: true,
          orphans: 3,
          summary: '삭제할 것 — 고아 파일 3개 · 임시 파일 1개(0.1MB)',
        },
      }),
    ])
    expect(screen.getByText(/삭제할 것 — 고아 파일 3개/)).toBeInTheDocument()
    expect(await screen.findByText('적용 대기')).toBeInTheDocument()
  })

  it('사진 일괄 업로드의 계획도 파일마다 읽고 적용한다', async () => {
    // 못 붙는 파일이 있어도 붙는 것은 붙는다 — `ok`(붙는 것이 있나)로 「적용」 이 선다.
    await mount([
      job({
        kind: 'objects_photos',
        kind_label: '사진 일괄 업로드',
        input_file_name: '사진.zip',
        result: {
          photos: true,
          applied: false,
          ok: true,
          field: 'photo',
          field_label: '사진',
          existing: 'skip',
          files: [
            {
              name: 'P-1.jpg',
              status: 'attach',
              size_bytes: 10,
              object_id: 'o1',
              object_label: '볼트',
              object_key: 'P-1',
              matched_by: 'key',
              message: '',
              replaces: 0,
              attachment_id: null,
            },
            {
              name: '없는것.jpg',
              status: 'not_found',
              size_bytes: 10,
              object_id: null,
              object_label: '',
              object_key: null,
              matched_by: '',
              message: '「없는것」 에 맞는 부품이(가) 없습니다.',
              replaces: 0,
              attachment_id: null,
            },
          ],
          tally: { attach: 1, not_found: 1 },
          hidden: 0,
          summary: '계획 — 업로드 1 · 못 찾음 1',
        },
      }),
    ])
    expect(screen.getByText('계획 — 업로드 1 · 못 찾음 1')).toBeInTheDocument()
    expect(await screen.findByText('적용 대기')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    expect(screen.getByText('없는것.jpg')).toBeInTheDocument()
    expect(screen.getByText('볼트 (P-1)')).toBeInTheDocument()
    jobsApi.apply.mockResolvedValue(job({ id: 'j2' }))
    await userEvent.click(screen.getByRole('button', { name: '적용' }))
    await waitFor(() => expect(jobsApi.apply).toHaveBeenCalledWith('j1'))
  })

  it('부서 자료 이동의 계획도 펼쳐 읽고 적용한다 — 막히는 것이 있으면 적용이 안 선다', async () => {
    // 한 부서 객체 20만 개면 적용이 90초 남짓이라 통폐합 창을 닫는 사람이 있다 — 돌아와서
    // 여기서 같은 계획을 읽고 적용한다(2026-10-10).
    const reassign = (over: Record<string, unknown> = {}) => ({
      reassign: true,
      applied: false,
      ok: true,
      source_slug: 'old',
      source_name: '없어지는 팀',
      target_slug: 'new',
      target_name: '합쳐지는 팀',
      items: [
        { kind: 'members', label: '멤버', count: 3 },
        { kind: 'objects', label: '객체', count: 200_000 },
      ],
      total: 200_003,
      moved: {},
      errors: [],
      summary: '계획 — 이동할 것: 멤버 3 · 객체 200,000 (없어지는 팀 → 합쳐지는 팀)',
      ...over,
    })
    const blocked = {
      ok: false,
      errors: ['객체: 옮겨 갈 부서에 같은 것이 이미 있어 객체를 옮기지 않습니다: 부품 식별자 「K-1」'],
      summary: '계획 — 이동할 것: 객체 3 (없어지는 팀 → 합쳐지는 팀) · 막힘 1',
    }
    await mount([
      job({
        id: 'r1',
        kind: 'workspace_reassign',
        kind_label: '부서 자료 이동',
        params: {},
        input_file_name: null,
        result: reassign(),
      }),
      job({
        id: 'r2',
        kind: 'workspace_reassign',
        kind_label: '부서 자료 이동',
        params: {},
        input_file_name: null,
        result: reassign(blocked),
      }),
    ])
    expect(screen.getByText(/이동할 것: 멤버 3 · 객체 200,000/)).toBeInTheDocument()
    expect(await screen.findAllByText('적용 대기')).toHaveLength(1)
    const [first, second] = screen.getAllByRole('button', { name: '펼치기' })
    await userEvent.click(first)
    expect(screen.getByText('200,000')).toBeInTheDocument()
    jobsApi.apply.mockResolvedValue(job({ id: 'r3' }))
    await userEvent.click(screen.getByRole('button', { name: '적용' }))
    await waitFor(() => expect(jobsApi.apply).toHaveBeenCalledWith('r1'))

    await userEvent.click(second)
    expect(screen.getByText(/「K-1」/)).toBeInTheDocument()
    expect(screen.getByText(/막히는 것이 있거나 이동할 것이 없어/)).toBeInTheDocument()
  })

  it('정제 도구가 준 링크(?job=)로 오면 그 계획이 펼쳐진 채로 열린다', async () => {
    // 사람은 명령 창 대신 여기서 계획을 읽고 「적용」 을 누른다.
    window.history.replaceState(null, '', '/jobs?job=j1')
    try {
      await mount([job({ result: CLEAN_PLAN })])
      expect(screen.getByText('볼트')).toBeInTheDocument() // 누르지 않아도 계획 표가 보인다
      expect(screen.getByRole('button', { name: '적용' })).toBeInTheDocument()
    } finally {
      window.history.replaceState(null, '', '/')
    }
  })

  it('링크의 작업을 서버가 모르면(404) 그렇다고 말한다 — 조용히 아무것도 안 열리지 않게', async () => {
    window.history.replaceState(null, '', '/jobs?job=다른-계정의-것')
    jobsApi.get.mockRejectedValue(
      new ApiError(404, { error: { code: 'APP-JOBS-0005', message: '작업을 찾을 수 없습니다.' } }),
    )
    try {
      await mount([job({ result: CLEAN_PLAN })])
      expect(await screen.findByText('링크의 작업을 찾을 수 없습니다')).toBeInTheDocument()
      expect(jobsApi.get).toHaveBeenCalledWith('다른-계정의-것')
    } finally {
      window.history.replaceState(null, '', '/')
    }
  })

  it('링크의 작업이 이 쪽에 없을 뿐이면 따로 읽어 맨 위에 펼친다 — 「없습니다」 라고 하지 않는다', async () => {
    // 첫 쪽이 아니거나 「내가 시킨 것만」 에 빠졌을 뿐인데 「목록에 없습니다」 라고 하던 때는 링크가
    // 맞는데도 사람은 링크가 틀린 줄 알았다.
    window.history.replaceState(null, '', '/jobs?job=j7')
    jobsApi.get.mockResolvedValue(
      job({
        id: 'j7',
        requested_by_id: 'u1',
        created_at: '2026-09-01T09:00:00+09:00',
        result: CLEAN_PLAN,
      }),
    )
    try {
      await mount([job({ id: 'j1', kind: 'objects_export', result: { rows: 3, format: 'csv' } })])
      expect(await screen.findByText('링크의 작업')).toBeInTheDocument()
      await waitFor(() => expect(jobsApi.get).toHaveBeenCalledWith('j7'))
      // 누르지 않아도 펼쳐져 있다 — 계획 표와 「적용」.
      expect(screen.getByText('볼트')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: '적용' })).toBeInTheDocument()
      expect(screen.queryByText(/찾을 수 없습니다|목록에 없습니다/)).not.toBeInTheDocument()
      // 맨 위 줄이다.
      const [, first] = screen.getAllByRole('row')
      expect(within(first).getByText('링크의 작업')).toBeInTheDocument()
    } finally {
      window.history.replaceState(null, '', '/')
    }
  })

  it('오류가 있는 계획은 적용이 안 선다 — 고쳐서 다시 올리라고 말한다', async () => {
    const broken = {
      ...CLEAN_PLAN,
      rows: [{ ...CLEAN_PLAN.rows[0], action: 'error' as const, message: '무게: 숫자여야 합니다' }],
      counts: { create: 0, update: 0, unchanged: 0, unlink: 0, error: 1 },
    }
    await mount([job({ result: broken })])
    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    expect(screen.queryByRole('button', { name: '적용' })).not.toBeInTheDocument()
    expect(screen.getByText(/고쳐서 다시 올리세요/)).toBeInTheDocument()
    expect(screen.getByText('무게: 숫자여야 합니다')).toBeInTheDocument()
  })

  it('이미 적용한 계획은 「적용」 을 거둔다 — 화면에서 두 번 누르지 않게', async () => {
    // 계획의 result.applied 는 영영 거짓이다(적용은 새 작업) — 서버가 applied_by 로 알려 준다.
    await mount([job({ result: CLEAN_PLAN, applied_by: 'j9-적용-작업', applied_status: 'done' })])
    expect(screen.queryByText('적용 대기')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    expect(screen.queryByRole('button', { name: '적용' })).not.toBeInTheDocument()
    expect(screen.getByText(/적용함 — 적용 작업/)).toBeInTheDocument()
  })

  it('적용 작업이 아직 대기 · 진행 중이면 「적용함」 이 아니라 그 상태를 말하고, 그 작업으로 잇는다', async () => {
    // applied_by 만 보던 때는 워커가 집기도 전에 「적용했습니다」 였다(ADR 0101 의 applied_status).
    await mount([
      job({ id: 'p1', result: CLEAN_PLAN, applied_by: 'a1-queued', applied_status: 'queued' }),
      job({ id: 'p2', result: CLEAN_PLAN, applied_by: 'a2-running', applied_status: 'running' }),
    ])
    // 목록 줄에서도 — 펼치지 않아도.
    expect(screen.getByText('적용 대기 중')).toBeInTheDocument()
    expect(screen.getByText('적용 중')).toBeInTheDocument()
    expect(screen.queryByText(/적용했습니다|적용함/)).not.toBeInTheDocument()

    const [first, second] = screen.getAllByRole('button', { name: '펼치기' })
    await userEvent.click(first)
    expect(screen.getByText(/적용 대기 중 — 적용 작업/)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: 'a1-queue' })
    expect(link).toHaveAttribute('href', '/jobs?job=a1-queued')
    await userEvent.click(second)
    expect(screen.getByText(/적용 중 — 적용 작업/)).toBeInTheDocument()

    // 적용 작업을 누르면 그 작업을 맨 위에 펼친다(이 쪽에 없으면 따로 읽어).
    jobsApi.get.mockResolvedValue(
      job({ id: 'a2-running', status: 'running', parent_id: 'p2', result: null }),
    )
    await userEvent.click(screen.getByRole('link', { name: 'a2-runni' }))
    await waitFor(() => expect(jobsApi.get).toHaveBeenCalledWith('a2-running'))
    expect(await screen.findByText('링크의 작업')).toBeInTheDocument()
    expect(window.location.search).toBe('?job=a2-running')
    window.history.replaceState(null, '', '/')
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

describe('작업 화면 · 고른 것을 한 번에', () => {
  it('오류 없는 계획만 적용에 들어간다 — 고른 수와 갈 수를 함께 말한다', async () => {
    const clean = job({ id: 'j1', result: CLEAN_PLAN })
    const broken = job({
      id: 'j2',
      result: { ...CLEAN_PLAN, counts: { create: 0, update: 0, unchanged: 0, unlink: 0, error: 1 } },
    })
    jobsApi.applyMany.mockResolvedValue([
      { id: 'j1', status: 'ok', message: 'objects_import 적용을 시작했습니다.', job_id: 'j3' },
    ])
    await mount([clean, broken])

    await userEvent.click(screen.getByRole('checkbox', { name: '이 쪽 전부 선택' }))
    expect(screen.getByText('2건 선택')).toBeInTheDocument()
    // **고른 것과 갈 것이 다르면 그 차이를 말한다.**
    expect(screen.getByText(/1건은 빠집니다/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /선택 적용/ }))
    // 확인 전에는 아무 일도 없다.
    expect(jobsApi.applyMany).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: '적용' }))

    await waitFor(() => expect(jobsApi.applyMany).toHaveBeenCalledWith(['j1']))
    expect(await screen.findByText(/성공 1 · 오류 0/)).toBeInTheDocument()
  })

  it('끝난 작업은 취소에 안 들어간다', async () => {
    const running = job({ id: 'j9', status: 'running', result: null })
    const finished = job({ id: 'j8', status: 'done', result: CLEAN_PLAN })
    jobsApi.cancelMany.mockResolvedValue([{ id: 'j9', status: 'ok', message: '취소를 요청했습니다.', job_id: null }])
    await mount([running, finished])

    await userEvent.click(screen.getByRole('checkbox', { name: '이 쪽 전부 선택' }))
    await userEvent.click(screen.getByRole('button', { name: /선택 취소/ }))
    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: '취소 요청' }))
    await waitFor(() => expect(jobsApi.cancelMany).toHaveBeenCalledWith(['j9']))
  })
})

describe('작업 화면 · 모르는 모양 · 다른 모양의 결과', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.admin = true
  })

  it('온톨로지 통째로 내보내기(objects 가 수)를 펼쳐도 화면이 멈추지 않고 값을 적는다', async () => {
    // 묶음의 타입 목록으로 펼치려다 「objects is not iterable」 로 화면 전체가 멈췄다(2026-11-28).
    await mount([
      job({
        kind: 'ontology_export',
        kind_label: '온톨로지 통째로 내보내기',
        params: {},
        input_file_name: null,
        has_output: true,
        result: { format: 'json', types: 12, objects: 3400, relations: 800, skipped: [] },
      }),
    ])
    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    expect(screen.getByText('3,400')).toBeInTheDocument()
    expect(screen.getByText('objects')).toBeInTheDocument()
    expect(screen.getByText('만든 파일을 받으세요.')).toBeInTheDocument()
  })

  it('검토함 일괄 확인의 결과를 펼쳐 결정마다 읽는다 — 목록 줄은 처리한 수', async () => {
    // 줄(rows)만 보고 가져오기 계획 표에 넘기던 때는 「changes」 가 없어 화면 전체가 멈췄다.
    await mount([
      job({
        kind: 'review_apply',
        kind_label: '검토함 일괄 확인',
        params: {},
        input_file_name: null,
        result: {
          applied: true,
          batch_id: 'b1',
          counts: { done: 2, refused: 1 },
          rows: [
            {
              kind: 'alias',
              kind_label: '별칭 후보',
              item_id: 'i1',
              question: '「앤시스」 는 무엇인가',
              action: 'accept',
              choice: 'o1',
              choice_label: 'Ansys',
              status: 'done',
              effect: '별칭 추가',
              message: '',
              rows: 1,
              undoable: true,
              recommended: 'o1',
              confidence: 0.92,
              followed: true,
            },
            {
              kind: 'alias',
              kind_label: '별칭 후보',
              item_id: 'i2',
              question: '「아바쿠스」 는 무엇인가',
              action: 'accept',
              choice: 'o2',
              choice_label: 'Abaqus',
              status: 'refused',
              effect: '',
              message: '다른 객체가 이미 쓰는 별칭입니다',
              rows: 0,
              undoable: false,
              recommended: null,
              confidence: null,
              followed: null,
            },
          ],
        },
      }),
    ])
    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    expect(screen.getByText('「앤시스」 는 무엇인가')).toBeInTheDocument()
    expect(screen.getByText('Ansys')).toBeInTheDocument()
    expect(screen.getByText('다른 객체가 이미 쓰는 별칭입니다')).toBeInTheDocument()
    expect(screen.getByText('그 사이 막힘')).toBeInTheDocument()
    expect(screen.queryByText('남길 결과가 없는 작업입니다.')).not.toBeInTheDocument()
    // 목록 줄은 처리한 수 — 가져오기의 셈(「새로 0 · 고침 0」)이 아니다.
    expect(screen.getByText('적용 — 처리 2건 · 그 사이 막힘 1건')).toBeInTheDocument()
    expect(screen.queryByText(/새로 0/)).not.toBeInTheDocument()
  })

  it('묶음 내보내기의 목록 줄은 내보낸 수 — 「새로 0 · 고침 0」 이 아니다', async () => {
    await mount([
      job({
        kind: 'bundle_export',
        kind_label: '묶음 내보내기',
        params: {},
        input_file_name: null,
        has_output: true,
        result: {
          group: 'plm',
          counts: { types: 3, relation_types: 2, objects: 120, relations: 40, tombstones: 0 },
          exported_at: '2026-11-28T00:00:00+00:00',
        },
      }),
    ])
    expect(
      screen.getByText('타입 3 · 관계 종류 2 · 객체 120 · 관계 40 · 삭제 표식 0'),
    ).toBeInTheDocument()
    expect(screen.queryByText(/새로 0/)).not.toBeInTheDocument()
  })

  it('모르는 모양의 결과는 값을 짧게 적는다 — 「남길 결과가 없는 작업입니다」 가 아니라', async () => {
    await mount([
      job({
        kind: 'webhook_dispatch',
        kind_label: '웹훅 보내기',
        params: {},
        input_file_name: null,
        result: { left: 3, nested: { deep: { deeper: 1 } } },
      }),
    ])
    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    expect(screen.getByText('left')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.queryByText('남길 결과가 없는 작업입니다.')).not.toBeInTheDocument()
  })

  it('객체 옮기기의 계획(objects 가 수)을 읽고 여기서 적용한다 — 막는 것이 있으면 안 선다', async () => {
    const move = (over: Record<string, unknown> = {}) => ({
      source: 'supplier',
      target: 'partner',
      label: '공급사',
      applied: false,
      objects: 25_000,
      live: 24_990,
      duplicates: 0,
      fields: [
        {
          source: 'grade',
          label: '등급',
          target: 'grade',
          action: 'same',
          values: 24_000,
          note: '',
          blocked: false,
        },
      ],
      places: [{ what: '저장된 뷰', count: 2 }],
      warnings: ['원 타입은 빈 채로 남습니다.'],
      errors: [],
      core_consumers: [],
      snapshot_id: null,
      moved: 0,
      merged: 0,
      fingerprint: 'f1',
      ...over,
    })
    await mount([
      job({
        id: 'm1',
        kind: 'ontology_move',
        kind_label: '객체 옮기기',
        params: { slug: 'supplier', type_slug: 'partner', request: { accept_core: false } },
        input_file_name: null,
        result: move(),
      }),
      job({
        id: 'm2',
        kind: 'ontology_move',
        kind_label: '객체 옮기기',
        params: { slug: 'supplier', type_slug: 'partner', request: { accept_core: false } },
        input_file_name: null,
        result: move({ errors: ['속성 「등급」(grade): 모양이 다릅니다'] }),
      }),
    ])
    expect(screen.getByText('계획 — 객체 25,000건 이동')).toBeInTheDocument()
    expect(await screen.findAllByText('적용 대기')).toHaveLength(1)
    const [first, second] = screen.getAllByRole('button', { name: '펼치기' })
    await userEvent.click(first)
    expect(screen.getByText('원 타입은 빈 채로 남습니다.')).toBeInTheDocument()
    expect(screen.getByText('저장된 뷰 2')).toBeInTheDocument()
    jobsApi.apply.mockResolvedValue(job({ id: 'm3', kind: 'ontology_move' }))
    await userEvent.click(screen.getByRole('button', { name: '적용' }))
    await waitFor(() => expect(jobsApi.apply).toHaveBeenCalledWith('m1'))

    await userEvent.click(second)
    expect(screen.getByText('속성 「등급」(grade): 모양이 다릅니다')).toBeInTheDocument()
    expect(screen.getByText(/수신 시스템 통보 확인이 없어 적용할 수 없습니다/)).toBeInTheDocument()
  })

  it('고아 첨부 파일 정리는 무엇이 삭제되는지 · 경고를 보이고, 확인 창을 거쳐야 적용한다', async () => {
    const warning =
      '저장소의 파일 20개 가운데 20개를 이 DB 의 어느 첨부도 가리키지 않습니다(하루 지난 것 20개) — 다른 DB 의 저장소를 보고 있을 수 있습니다'
    await mount([
      job({
        id: 'g1',
        kind: 'filestore_gc',
        kind_label: '고아 첨부 파일 정리',
        params: {},
        input_file_name: null,
        result: {
          applied: false,
          ok: true,
          orphans: 20,
          orphan_bytes: 3 * 1024 * 1024,
          temp: 1,
          recent: 0,
          scanned: 20,
          sample: ['ab/abcdef.bin', 'cd/cdef01.bin'],
          warnings: [warning],
          summary: '삭제할 것 — 고아 파일 20개 · 임시 파일 1개(3.0MB)',
        },
      }),
    ])
    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    // 「남길 결과가 없는 작업입니다」 가 아니라 — 무엇을 삭제하나와 경고.
    expect(screen.queryByText('남길 결과가 없는 작업입니다.')).not.toBeInTheDocument()
    expect(screen.getByText('ab/abcdef.bin')).toBeInTheDocument()
    expect(screen.getByText(warning)).toBeInTheDocument()

    jobsApi.apply.mockResolvedValue(job({ id: 'g2', kind: 'filestore_gc' }))
    await userEvent.click(screen.getByRole('button', { name: '적용' }))
    // **확인 전에는 아무 일도 없다.**
    expect(jobsApi.apply).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/복원할 수 없습니다/)).toBeInTheDocument()
    expect(within(dialog).getByText(/개\(3\.0MB\)와 임시/)).toBeInTheDocument()
    expect(within(dialog).getByText(warning)).toBeInTheDocument()
    // 경고가 있으면 「이 설치의 저장소가 맞다」 를 선택해야 삭제할 수 있다.
    const confirm = within(dialog).getByRole('button', { name: '삭제' })
    expect(confirm).toBeDisabled()
    await userEvent.click(within(dialog).getByRole('checkbox'))
    await userEvent.click(confirm)
    await waitFor(() => expect(jobsApi.apply).toHaveBeenCalledWith('g1'))
  })

  it('고아 첨부 파일 정리는 한 번에 적용에 들지 않는다 — 펼쳐서 하나씩', async () => {
    await mount([
      job({ id: 'j1', result: CLEAN_PLAN }),
      job({
        id: 'g1',
        kind: 'filestore_gc',
        kind_label: '고아 첨부 파일 정리',
        params: {},
        input_file_name: null,
        result: { applied: false, ok: true, orphans: 3, sample: [], warnings: [], scanned: 10 },
      }),
    ])
    await screen.findAllByText('적용 대기')
    await userEvent.click(screen.getByRole('checkbox', { name: '이 쪽 전부 선택' }))
    expect(screen.getByText(/파일을 삭제하는 계획 1건/)).toBeInTheDocument()
    jobsApi.applyMany.mockResolvedValue([])
    await userEvent.click(screen.getByRole('button', { name: /선택 적용/ }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '적용' }))
    await waitFor(() => expect(jobsApi.applyMany).toHaveBeenCalledWith(['j1']))
  })
})
