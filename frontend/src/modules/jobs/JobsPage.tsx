/**
 * 작업 — **뒤에서 도는 일이 어디까지 됐나.**
 *
 * 일괄 입력은 보낸 순간 여기 한 줄이 된다. 대화상자를 닫아도 일은 계속되고, 여기서
 * 진행률 · 실패 이유를 본다. 워커가 한 대도 살아 있지 않으면 **그것부터 말한다** — 안 그러면
 * 「대기」 가 영영 대기인 이유를 사람이 알 길이 없다.
 *
 * **계획을 여기서 보고 여기서 적용한다.** 대화상자를 닫아도 계속된다고 해 놓고 돌아와서 적용할
 * 자리가 없으면, 그 말은 절반만 참이다 — 5만 행을 올린 사람이 창을 닫았다는 이유로 처음부터
 * 다시 해야 한다.
 */

import { Fragment, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Download, Loader2 } from 'lucide-react'

import { jobsApi, isTerminal, STATUS_LABEL } from '@/modules/jobs/api'
import type { Job, JobStatus } from '@/modules/jobs/api'
import type { ImportPlan } from '@/modules/objects/api'
import {
  ImportPlanTable,
  planChangesSomething,
  planIsClean,
} from '@/modules/objects/ImportPlanTable'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Pagination } from '@/shared/components/Pagination'
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

const PER_PAGE = 50
/** 돌고 있는 것이 있을 때만 이 간격으로 다시 읽는다. */
const REFRESH_MS = 3000

const STATUS_VARIANT: Record<JobStatus, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  queued: 'outline',
  running: 'default',
  done: 'secondary',
  failed: 'destructive',
  cancelled: 'outline',
}

function Progress({ job }: { job: Job }) {
  if (isTerminal(job)) return null
  const { stage, done, total } = job.progress
  if (job.status === 'queued') return <span className="text-muted-foreground">워커 대기</span>
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null
  return (
    <div className="min-w-32 space-y-1">
      <div className="text-muted-foreground flex items-center gap-1 text-xs">
        <Loader2 className="size-3 animate-spin" />
        {stage || '진행 중'}
        {pct !== null && ` ${done.toLocaleString()} / ${total.toLocaleString()}`}
      </div>
      {pct !== null && (
        <div className="bg-muted h-1.5 w-full overflow-hidden rounded">
          <div className="bg-primary h-full" style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  )
}

function Outcome({ job }: { job: Job }) {
  if (job.error) return <span className="text-destructive text-xs">{job.error}</span>
  const counts = (job.result as { counts?: Record<string, number>; applied?: boolean } | null)
    ?.counts
  if (!counts) return null
  const applied = (job.result as { applied?: boolean }).applied
  return (
    <span className="text-muted-foreground text-xs">
      {applied ? '적용 — ' : '계획 — '}
      새로 {counts.create ?? 0} · 고침 {counts.update ?? 0} · 그대로 {counts.unchanged ?? 0}
      {counts.error ? ` · 오류 ${counts.error}` : ''}
    </span>
  )
}

/** 가져오기 계획인가 — 행이 있는 결과만 표로 그린다(묶음 · 내보내기는 모양이 다르다). */
function planOf(job: Job): ImportPlan | null {
  const result = job.result as unknown as ImportPlan | null
  return result && Array.isArray(result.rows) ? result : null
}

/** 묶음 결과의 타입별 묶음 — 정의 · 객체 · 관계가 한 결과에 들어 있다. */
interface BundleBatch {
  type_slug: string
  plan: ImportPlan | null
  error: string
}

function bundleBatches(job: Job): BundleBatch[] | null {
  const result = job.result as unknown as {
    objects?: BundleBatch[]
    relations?: BundleBatch[]
  } | null
  if (!result || (!result.objects && !result.relations)) return null
  return [...(result.objects ?? []), ...(result.relations ?? [])]
}

/** 아직 적용 안 한 **깨끗한** 계획인가 — 그때만 「적용」 이 선다. */
function waitingForApply(job: Job): boolean {
  if (job.status !== 'done' || !job.result) return false
  const result = job.result as { applied?: boolean; ok?: boolean }
  if (result.applied) return false
  const plan = planOf(job)
  if (plan) return planIsClean(plan) && planChangesSomething(plan)
  // 묶음 — 자체 판정(`ok`)을 쓴다.
  return result.ok === true
}

function Detail({
  job,
  busy,
  onApply,
  onDownload,
}: {
  job: Job
  busy: boolean
  onApply: () => void
  onDownload: () => void
}) {
  const plan = planOf(job)
  const batches = bundleBatches(job)
  return (
    <div className="bg-muted/30 space-y-3 px-4 py-3">
      {plan && <ImportPlanTable plan={plan} />}
      {batches && (
        <table className="text-xs">
          <tbody>
            {batches.map((one) => (
              <tr key={`${one.type_slug}-${one.error}`} className="align-top">
                <td className="py-0.5 pr-3 font-mono">{one.type_slug}</td>
                <td className="py-0.5">
                  {one.error ? (
                    <span className="text-destructive">{one.error}</span>
                  ) : (
                    <span className="text-muted-foreground">
                      새로 {one.plan?.counts.create ?? 0} · 고침 {one.plan?.counts.update ?? 0} ·
                      그대로 {one.plan?.counts.unchanged ?? 0}
                      {one.plan?.counts.error ? ` · 오류 ${one.plan.counts.error}` : ''}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!plan && !batches && !job.error && (
        <p className="text-muted-foreground text-xs">
          {job.has_output ? '만든 파일을 받으세요.' : '남길 결과가 없는 작업입니다.'}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {job.has_output && (
          <Button size="xs" variant="outline" onClick={onDownload} disabled={busy}>
            <Download className="mr-1 size-3" />
            파일 받기
          </Button>
        )}
        {waitingForApply(job) && (
          <>
            <Button size="xs" onClick={onApply} disabled={busy}>
              {busy && <Loader2 className="mr-1 size-3 animate-spin" />}
              적용
            </Button>
            <span className="text-muted-foreground text-xs">
              아직 아무것도 안 들어갔습니다 — 위 계획을 읽고 눌러 주세요.
            </span>
          </>
        )}
        {job.status === 'done' && !waitingForApply(job) && plan?.applied === false && (
          <span className="text-muted-foreground text-xs">
            오류가 있거나 바뀌는 것이 없어 적용할 수 없습니다 — 고쳐서 다시 올리세요.
          </span>
        )}
      </div>
    </div>
  )
}

export default function JobsPage() {
  const [offset, setOffset] = useState(0)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [opened, setOpened] = useState<string | null>(null)
  const [actionError, setActionError] = useState<Error | null>(null)
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('')
  /** **기본은 내 것만.** 타이머가 넣은 줄이 대부분이라, 전부 보이면 제 작업을 못 찾는다. */
  const [mine, setMine] = useState(true)
  const page = useResource(
    () =>
      jobsApi.list({
        limit: PER_PAGE,
        offset,
        kind: kind || undefined,
        status: status || undefined,
        mine,
      }),
    [offset, kind, status, mine],
  )
  const kinds = useResource(() => jobsApi.kinds(), [])
  const workers = useResource(() => jobsApi.workers(), [])

  // 돌고 있는 것이 있으면 다시 읽는다 — 끝난 목록을 3초마다 두드릴 이유는 없다.
  const active = (page.data?.items ?? []).some((one) => !isTerminal(one))
  const reload = page.reload
  useEffect(() => {
    if (!active) return
    const timer = setInterval(reload, REFRESH_MS)
    return () => clearInterval(timer)
  }, [active, reload])

  const alive = (workers.data ?? []).filter((one) => one.alive)

  /** 누르는 일 하나 — 실패하면 **그 이유를 화면에 남긴다**(조용히 삼키면 눌러도 아무 일이 없다). */
  const act = async (job: Job, run: () => Promise<unknown>) => {
    setBusyId(job.id)
    setActionError(null)
    try {
      await run()
      reload()
    } catch (caught) {
      setActionError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusyId(null)
    }
  }

  const cancel = (job: Job) => act(job, () => jobsApi.cancel(job.id))
  /** 계획을 읽고 누르는 자리 — 같은 파일 · 같은 지문으로 적용 작업이 새로 생긴다. */
  const apply = (job: Job) =>
    act(job, async () => {
      const made = await jobsApi.apply(job.id)
      setOpened(made.id)
    })
  const download = (job: Job) =>
    act(job, () => jobsApi.download(job.id, job.input_file_name ?? `${job.kind}.csv`))

  return (
    <div className="space-y-6">
      <PageHeader
        title="작업"
        description="일괄 입력처럼 뒤에서 도는 일. 대화상자를 닫아도 일은 계속되고, 여기서 진행과 결과를 봅니다."
      />

      {workers.data && alive.length === 0 && (
        <Alert variant="destructive">
          <AlertTitle>워커가 살아 있지 않습니다</AlertTitle>
          <AlertDescription>
            작업은 워커가 집어야 돕니다. 지금은 넣어도 「대기」 에 머뭅니다 — 운영자에게{' '}
            <span className="font-mono">systemctl status &lt;slug&gt;-worker</span> 를 확인해 달라고
            하세요.
          </AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="종류"
          className="border-input bg-background h-8 rounded-md border px-2 text-sm"
          value={kind}
          onChange={(event) => {
            setKind(event.target.value)
            setOffset(0)
          }}
        >
          <option value="">종류 전부</option>
          {(kinds.data ?? []).map((one) => (
            <option key={one.name} value={one.name}>
              {one.label}
            </option>
          ))}
        </select>
        <select
          aria-label="상태"
          className="border-input bg-background h-8 rounded-md border px-2 text-sm"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value)
            setOffset(0)
          }}
        >
          <option value="">상태 전부</option>
          {(Object.keys(STATUS_LABEL) as JobStatus[]).map((one) => (
            <option key={one} value={one}>
              {STATUS_LABEL[one]}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={mine}
            onChange={(event) => {
              setMine(event.target.checked)
              setOffset(0)
            }}
          />
          내가 시킨 것만
        </label>
      </div>

      <ErrorNotice error={page.error} />
      <ErrorNotice error={actionError} />

      {page.data && page.data.items.length === 0 ? (
        <EmptyState
          title="작업이 없습니다"
          hint={
            mine || kind || status
              ? '거르기를 풀면 다른 사람이 시킨 것과 타이머가 넣은 것도 보입니다.'
              : '파일을 가져오면 여기 한 줄이 생깁니다.'
          }
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>시각</TableHead>
              <TableHead>무엇</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>진행 · 결과</TableHead>
              <TableHead>누가</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(page.data?.items ?? []).map((one) => (
              <Fragment key={one.id}>
                <TableRow>
                  <TableCell className="pr-0">
                    {/* **끝난 작업은 펼쳐 본다.** 계획을 여기서 읽고 여기서 적용한다. */}
                    <Button
                      size="xs"
                      variant="ghost"
                      aria-label={opened === one.id ? '접기' : '펼치기'}
                      disabled={!isTerminal(one)}
                      onClick={() => setOpened(opened === one.id ? null : one.id)}
                    >
                      {opened === one.id ? (
                        <ChevronDown className="size-3.5" />
                      ) : (
                        <ChevronRight className="size-3.5" />
                      )}
                    </Button>
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    {shownDateTime(one.created_at)}
                  </TableCell>
                  <TableCell>
                    {one.kind_label}
                    {one.params.type_slug != null && (
                      <span className="text-muted-foreground ml-1 font-mono text-xs">
                        {String(one.params.type_slug)}
                      </span>
                    )}
                    {one.input_file_name && (
                      <p className="text-muted-foreground text-xs">{one.input_file_name}</p>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant={STATUS_VARIANT[one.status]}>{STATUS_LABEL[one.status]}</Badge>
                    {one.cancel_requested && !isTerminal(one) && (
                      <span className="text-muted-foreground ml-1 text-xs">취소 요청됨</span>
                    )}
                    {one.attempts > 1 && (
                      <span className="text-muted-foreground ml-1 text-xs">{one.attempts}번째</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Progress job={one} />
                    <Outcome job={one} />
                  </TableCell>
                  <TableCell className="text-xs">
                    {one.requested_by_name ?? '—'}
                    {one.workspace_slug && (
                      <p className="text-muted-foreground font-mono">{one.workspace_slug}</p>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {!isTerminal(one) && !one.cancel_requested && (
                      <Button
                        size="xs"
                        variant="outline"
                        disabled={busyId === one.id}
                        onClick={() => void cancel(one)}
                      >
                        취소
                      </Button>
                    )}
                    {waitingForApply(one) && opened !== one.id && (
                      <Badge variant="outline">적용 대기</Badge>
                    )}
                  </TableCell>
                </TableRow>
                {opened === one.id && (
                  <TableRow>
                    <TableCell colSpan={7} className="p-0">
                      <Detail
                        job={one}
                        busy={busyId === one.id}
                        onApply={() => void apply(one)}
                        onDownload={() => void download(one)}
                      />
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      )}

      {page.data && (
        <Pagination total={page.data.total} limit={PER_PAGE} offset={offset} onChange={setOffset} />
      )}
    </div>
  )
}
