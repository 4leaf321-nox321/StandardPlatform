/**
 * 작업 — **오래 걸리는 일은 요청이 아니라 표에 산다.**
 *
 * 일괄 입력는 올리는 순간 202 로 작업이 되고, 워커가 뒤에서 돈다. 화면은 그 작업을
 * 2초마다 보며 진행률을 그리고, 끝나면 결과(가져오기면 계획 표)를 꺼내 쓴다.
 * 설계는 `docs/작업-워커-설계.md`.
 */

import { api, downloadFile } from '@/shared/api/client'

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface JobProgress {
  stage: string
  done: number
  total: number
}

export interface Job {
  id: string
  kind: string
  kind_label: string
  status: JobStatus
  params: Record<string, unknown>
  progress: JobProgress
  /** 끝난 뒤의 결과 — 가져오기면 `ImportPlan` 모양 + `fingerprint`. */
  result: Record<string, unknown> | null
  error: string | null
  parent_id: string | null
  input_file_name: string | null
  has_output: boolean
  requested_by_name: string | null
  workspace_slug: string | null
  cancel_requested: boolean
  attempts: number
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface JobList {
  items: Job[]
  total: number
}

export interface WorkerInfo {
  worker_id: string
  host: string
  pid: number
  started_at: string
  last_seen: string
  current_job_id: string | null
  alive: boolean
}

export const TERMINAL: ReadonlySet<JobStatus> = new Set(['done', 'failed', 'cancelled'])

export function isTerminal(job: Pick<Job, 'status'>): boolean {
  return TERMINAL.has(job.status)
}

export const STATUS_LABEL: Record<JobStatus, string> = {
  queued: '대기',
  running: '진행 중',
  done: '완료',
  failed: '실패',
  cancelled: '취소됨',
}

/** 폴링 간격. 워커의 진행률 갱신(200행마다)보다 촘촘할 이유가 없다. */
export const POLL_MS = 1500

/**
 * 이만큼 「대기」 에 머물면 워커를 의심한다.
 *
 * **워커가 안 떠 있으면 작업은 영영 대기다.** 그때 화면이 말없이 돌기만 하면 사람은 제 파일이
 * 잘못된 줄 알고 몇 번을 다시 올린다 — 원인은 서버에 있는데.
 */
export const STUCK_MS = 15_000

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export interface JobKind {
  name: string
  label: string
  needs_file: boolean
  two_step: boolean
}

export const jobsApi = {
  get: (id: string) => api.get<Job>(`/jobs/${id}`),
  /**
   * 목록. `kind` · `mine` 은 **타이머가 넣은 것에 파묻히지 않으려고** 있다 — 데이터 소스마다
   * 5분에 한 줄이면 하루 288행이고, 사람이 올린 작업은 그 사이에 한 줄이다.
   */
  list: (opts: {
    limit: number
    offset: number
    status?: string
    kind?: string
    mine?: boolean
  }) => {
    const params = new URLSearchParams({ limit: String(opts.limit), offset: String(opts.offset) })
    if (opts.status) params.set('status', opts.status)
    if (opts.kind) params.set('kind', opts.kind)
    if (opts.mine) params.set('mine', 'true')
    return api.get<JobList>(`/jobs?${params.toString()}`)
  },
  kinds: () => api.get<JobKind[]>('/jobs/kinds'),
  workers: () => api.get<WorkerInfo[]>('/jobs/workers'),
  /** 계획을 본 뒤 **사람이 누르는 자리** — 같은 파일 · 같은 지문으로 적용 작업을 만든다. */
  apply: (id: string) => api.post<Job>(`/jobs/${id}/apply`),
  cancel: (id: string) => api.post<Job>(`/jobs/${id}/cancel`),
  /**
   * 끝날 때까지 본다. 매 바퀴 `onTick` 으로 진행률을 준다.
   *
   * **끝난 작업을 돌려줄 뿐 실패를 던지지 않는다** — 실패도 결과다. 부르는 쪽이
   * `status` 를 보고 `error` 를 사람에게 보인다. 던지면 「어느 행이 왜」 가 사라진다.
   */
  waitFor: async (id: string, onTick?: (job: Job) => void, pollMs = POLL_MS): Promise<Job> => {
    for (;;) {
      const job = await api.get<Job>(`/jobs/${id}`)
      onTick?.(job)
      if (isTerminal(job)) return job
      await sleep(pollMs)
    }
  },
  /**
   * 결과 파일을 받는다 — 내보내기 작업이 끝난 뒤. 파일 이름은 서버가 준 것을 쓴다.
   */
  download: (id: string, filename: string) => downloadFile(`/jobs/${id}/download`, filename),
  /**
   * 내보내기 한 바퀴 — 작업을 넣고, 끝나기를 기다리고, 파일을 받는다. 실패하면 그 이유로 던진다.
   *
   * 큰 타입의 내보내기는 요청 안에서 만들면 끊긴다 — 그래서 작업이고, 화면은 잠깐 기다린다.
   */
  exportAndDownload: async (start: () => Promise<Job>, filename: string): Promise<void> => {
    const started = await start()
    const done = await jobsApi.waitFor(started.id)
    if (done.status !== 'done') {
      throw new Error(
        done.error ??
          (done.status === 'cancelled' ? '취소됐습니다.' : '내보내기가 끝나지 않았습니다.'),
      )
    }
    await jobsApi.download(done.id, filename)
  },
}
