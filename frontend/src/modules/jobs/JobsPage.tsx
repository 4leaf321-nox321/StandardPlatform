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

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Download, Loader2 } from 'lucide-react'

import { jobsApi, isTerminal, STATUS_LABEL } from '@/modules/jobs/api'
import type { Job, JobBulkResult, JobStatus } from '@/modules/jobs/api'
import {
  GcConfirmText,
  GcResultView,
  MovePlanView,
  RenamePlanView,
  ResultFields,
  ReviewResultView,
  countsLine,
  gcPlanOf,
  isGcResult,
  isImportCounts,
  moveLine,
  moveOf,
  renameLine,
  renameOf,
  resultFields,
  reviewLine,
  reviewOf,
} from '@/modules/jobs/JobResultViews'
import { TombstonePlanView, graveSummary, tombstonesOf } from '@/modules/jobs/TombstonePlanView'
import type { ImportPlan } from '@/modules/objects/api'
import type { RetypeOut } from '@/modules/ontology/api'
import { DATA_TYPE_LABELS } from '@/modules/ontology/PropertyEditDialog'
import {
  ImportPlanTable,
  planChangesSomething,
  planIsClean,
} from '@/modules/objects/ImportPlanTable'
import { PhotoPlanTable, photoPlanOf } from '@/modules/objects/PhotoPlanTable'
import { reassignOf } from '@/modules/workspaces/api'
import { ReassignPlanView } from '@/modules/workspaces/ReassignPlanView'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
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
import { ApiError } from '@/shared/api/client'
import { useAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin } from '@/shared/auth/roles'
import { PUBLIC_PATH } from '@/shared/base'
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
  // **결과가 스스로 한 줄로 말하는 종류**(고아 첨부 정리 …) — 그 말을 그대로.
  const summary = (job.result as { summary?: unknown } | null)?.summary
  if (typeof summary === 'string') {
    return <span className="text-muted-foreground text-xs">{summary}</span>
  }
  const retype = retypeOf(job)
  if (retype) {
    const converted = retype.types.reduce((sum, one) => sum + one.converted, 0)
    const cleared = retype.types.reduce((sum, one) => sum + one.cleared, 0)
    return (
      <span className="text-muted-foreground text-xs">
        {retype.applied ? '적용 — ' : '계획 — '}
        변환 {converted.toLocaleString()} · 비움 {cleared.toLocaleString()}
        {retype.errors.length > 0 ? ` · 막는 것 ${retype.errors.length}` : ''}
      </span>
    )
  }
  // 검토함 · 옮기기 · 키 변경 — 셈이 가져오기와 다르다. 가져오기의 한 줄로 읽던 때는 처리한
  // 결정이 몇이든 「적용 — 새로 0 · 고침 0 · 그대로 0」 이었다(2026-11-28 점검).
  const review = reviewOf(job.result)
  if (review) return <span className="text-muted-foreground text-xs">{reviewLine(review)}</span>
  const move = moveOf(job.result)
  if (move) return <span className="text-muted-foreground text-xs">{moveLine(move)}</span>
  const rename = renameOf(job.result)
  if (rename) return <span className="text-muted-foreground text-xs">{renameLine(rename)}</span>
  const counts = (job.result as { counts?: Record<string, number>; applied?: boolean } | null)
    ?.counts
  if (!counts || typeof counts !== 'object') return null
  // 가져오기가 아닌 셈(묶음 내보내기의 타입 · 객체 · 관계 수) — 그 수 그대로.
  if (!isImportCounts(counts)) {
    const line = countsLine(counts)
    return line ? <span className="text-muted-foreground text-xs">{line}</span> : null
  }
  const applied = (job.result as { applied?: boolean }).applied
  // 묶음의 무덤 — 「지움」 과 「사용 중지」 를 따로(ADR 0090).
  const graves = graveSummary(counts)
  return (
    <span className="text-muted-foreground text-xs">
      {applied ? '적용 — ' : '계획 — '}
      새로 {counts.create ?? 0} · 고침 {counts.update ?? 0} · 그대로 {counts.unchanged ?? 0}
      {counts.error ? ` · 오류 ${counts.error}` : ''}
      {graves ? ` · ${graves}` : ''}
    </span>
  )
}

/**
 * 줄 목록을 뺀 요약인가 — 목록 API 는 결과의 줄 목록(`rows`)을 비우고 그 수만 싣는다
 * (`rows_omitted`). 5만 줄 계획이면 결과 하나가 17MB 라, 목록이 그것을 작업마다 싣던 때는 한 번에
 * 93MB 를 3초마다 받았다(2026-10-09). 펼칠 때 상세를 따로 읽는다.
 */
function omitsRows(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(omitsRows)
  if (!value || typeof value !== 'object') return false
  const record = value as Record<string, unknown>
  if (typeof record.rows_omitted === 'number' && record.rows_omitted > 0) return true
  return Object.values(record).some((one) => typeof one === 'object' && omitsRows(one))
}

/** 결과에 줄 목록이 있나 — 있으면 모든 줄을 CSV 로 받을 수 있다. */
function hasRows(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasRows)
  if (!value || typeof value !== 'object') return false
  const record = value as Record<string, unknown>
  if (Array.isArray(record.rows)) return true
  return Object.values(record).some((one) => typeof one === 'object' && hasRows(one))
}

/** 가져오기 계획의 한 줄인가 — 결과(`action`)와 바뀌는 칸(`changes`)이 있다. */
function isPlanRow(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false
  const row = value as { action?: unknown; changes?: unknown }
  return typeof row.action === 'string' && Array.isArray(row.changes)
}

/**
 * 가져오기 계획인가 — 가져오기의 셈(`create` · `update` …)과 가져오기 모양의 줄만 표로 그린다
 * (묶음 · 내보내기 · 검토함은 모양이 다르다).
 *
 * 줄 목록(`rows`)이 배열인 것만 보던 때는 검토함 작업(`review_apply` · `review_auto`)의 결정 결과를
 * 이 표에 넘겼고, 그 줄에는 바뀌는 칸이 없어 펼치는 순간 화면 전체가 「이 화면을 그리지
 * 못했습니다」 가 됐다(2026-11-28 점검).
 */
function planOf(job: Job): ImportPlan | null {
  const result = job.result as unknown as ImportPlan | null
  if (!result || !Array.isArray(result.rows) || !result.rows.every(isPlanRow)) return null
  const counts = result.counts as Record<string, unknown> | undefined
  // 목록의 요약은 줄 목록이 비어 오므로(`rows_omitted`) 그때는 셈의 모양으로 가른다.
  const importCounts = !!counts && typeof counts === 'object' && isImportCounts(counts)
  return importCounts || result.rows.length > 0 ? result : null
}

/**
 * 속성 종류 변경(`ontology_retype`)의 계획인가 — `rows` · `counts` · `ok` 가 없는 모양이다
 * (`ontology/routes.py` 의 `run_retype_job` → `RetypeOut`). 이것을 못 알아보던 때는 창을 닫고
 * 돌아오면 「남길 결과가 없는 작업입니다」 만 보였고 적용할 자리가 없었다 — 서버는 적용을 받아
 * 주는데(2026-10-08).
 */
function retypeOf(job: Job): RetypeOut | null {
  const result = job.result as unknown as RetypeOut | null
  return result && Array.isArray(result.types) && typeof result.data_type_after === 'string'
    ? result
    : null
}

/** 공개 타입의 계획이면 수신 시스템에 통보했다는 확인(`accept_core`)을 계획 때 받았나 — 적용
 *  작업은 계획의 요청을 그대로 들고 가므로, 안 받았으면 서버가 적용을 거절한다. 종류 변경 ·
 *  객체 옮기기 · 키 변경이 같다. */
function coreAccepted(job: Job): boolean {
  const request = (job.params as { request?: { accept_core?: unknown } }).request
  return request?.accept_core === true
}

/** 종류 변경 · 객체 옮기기 · 키 변경의 계획을 적용할 수 있나 — 서버(`jobs.services.make_apply`
 *  의 `errors` · 적용 작업의 `_require_core_accepted`)와 같은 판정. */
function retypeApplicable(job: Job, retype: Pick<RetypeOut, 'errors' | 'core_consumers'>): boolean {
  return (
    (retype.errors ?? []).length === 0 &&
    ((retype.core_consumers ?? []).length === 0 || coreAccepted(job))
  )
}

/** 종류 변경 계획을 읽는 자리 — 타입마다 몇 개가 변환되고 비워지나, 막는 것 · 알릴 것. */
function RetypePlanView({ job, retype }: { job: Job; retype: RetypeOut }) {
  const before = DATA_TYPE_LABELS[retype.data_type_before] ?? retype.data_type_before
  const after = DATA_TYPE_LABELS[retype.data_type_after] ?? retype.data_type_after
  return (
    <div className="space-y-2 text-xs">
      <p>
        {String(job.params.key ?? '')} 속성: <b>{before}</b> → <b>{after}</b>
        {retype.applied ? ' — 적용했습니다' : ' — 계획'}
      </p>
      <table>
        <thead className="text-muted-foreground">
          <tr>
            <th className="pr-3 text-left font-normal">타입</th>
            <th className="pr-3 text-right font-normal">값 있음</th>
            <th className="pr-3 text-right font-normal">변환</th>
            <th className="pr-3 text-right font-normal">그대로</th>
            <th className="text-right font-normal">비움</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {retype.types.map((one) => (
            <tr key={`${one.type_slug}-${one.key}`}>
              <td className="pr-3">
                {one.type_label}
                {one.via && <span className="text-muted-foreground"> ({one.via})</span>}
              </td>
              <td className="pr-3 text-right">{one.with_value.toLocaleString()}</td>
              <td className="pr-3 text-right">{one.converted.toLocaleString()}</td>
              <td className="pr-3 text-right">{one.unchanged.toLocaleString()}</td>
              <td className="text-right">{one.cleared.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {retype.errors.map((one) => (
        <p key={one} className="text-destructive">
          {one}
        </p>
      ))}
      {retype.warnings.map((one) => (
        <p key={one} className="text-amber-700 dark:text-amber-400">
          {one}
        </p>
      ))}
      {retype.core_consumers.length > 0 && !coreAccepted(job) && (
        <p className="text-destructive">
          바깥에 연 타입입니다({retype.core_consumers.join(', ')}) — 수신 시스템에 통보했다는
          확인 없이 계획했으므로 여기서 적용할 수 없습니다. 속성 화면에서 확인을 체크하고 다시
          계획하세요.
        </p>
      )}
    </div>
  )
}

/** 묶음 결과의 타입별 묶음 — 정의 · 객체 · 관계가 한 결과에 들어 있다. */
interface BundleBatch {
  type_slug: string
  plan: ImportPlan | null
  error: string
}

/**
 * 묶음 가져오기의 결과인가 — `objects` · `relations` 가 **타입마다의 목록**일 때만.
 *
 * 다른 작업도 같은 이름의 칸을 낸다 — 온톨로지 통째로 내보내기 · 객체 옮기기 · 키 변경의
 * `objects` 는 수이고, 병합 · 참조 비우기의 `relations` 도 수다. 있기만 하면 펼치던 때는 그 수를
 * 배열로 펼치려다 펼치는 순간 화면 전체가 멈췄다(`objects is not iterable`, 2026-11-28 점검).
 */
function bundleBatches(job: Job): BundleBatch[] | null {
  const result = job.result as unknown as {
    objects?: unknown
    relations?: unknown
  } | null
  if (!result || (!Array.isArray(result.objects) && !Array.isArray(result.relations))) return null
  const batches = (value: unknown): BundleBatch[] =>
    Array.isArray(value)
      ? value.filter(
          (one): one is BundleBatch =>
            !!one && typeof one === 'object' && typeof (one as BundleBatch).type_slug === 'string',
        )
      : []
  return [...batches(result.objects), ...batches(result.relations)]
}

/** 아직 적용 안 한 **깨끗한** 계획인가 — 그때만 「적용」 이 선다. **적용 단계가 있는 종류만**
 * (`two_step`) — 데이터 소스 동기화도 계획을 내지만 적용은 「동기화」 를 다시 누르는 것이라, 여기서
 * 「적용」 을 세우면 서버가 거절한다(「이 종류의 작업은 적용 단계가 없습니다」). */
function waitingForApply(job: Job, twoStep: ReadonlySet<string>): boolean {
  if (!twoStep.has(job.kind)) return false
  if (job.status !== 'done' || !job.result) return false
  // **이미 적용한 계획** — 계획의 `result.applied` 는 영영 거짓이라(적용은 새 작업이다) 서버가
  // 따로 알려 준다. 안 보면 적용하고 나서도 「적용」 이 남아 같은 것을 두 번 넣는다.
  if (job.applied_by) return false
  const result = job.result as { applied?: boolean; ok?: boolean }
  if (result.applied) return false
  const plan = planOf(job)
  if (plan) return planIsClean(plan) && planChangesSomething(plan)
  // 종류 변경 — 막는 것(`errors`)이 없으면. 값이 하나도 안 바뀌어도 정의의 종류는 바뀐다.
  const retype = retypeOf(job)
  if (retype) return retypeApplicable(job, retype)
  // 객체 옮기기 · 키 변경 — 종류 변경과 같다(`ok` 가 없는 모양이라, 못 알아보던 때는 서버가 받아
  // 주는 계획에도 「적용」 이 안 섰다).
  const moved = moveOf(job.result) ?? renameOf(job.result)
  if (moved) return retypeApplicable(job, moved)
  // 부서 자료 이동 — 막히는 것이 없고 옮길 것이 있으면(`ok`).
  const reassign = reassignOf(job.result)
  if (reassign) return reassign.ok
  // 묶음 — 자체 판정(`ok`)을 쓴다.
  return result.ok === true
}

/** 계획을 적용한 작업의 상태(`applied_status`, ADR 0101) — **대기 · 진행 중인 적용은 아직 아무것도
 *  들어가지 않았다.** `applied_by` 만 보던 때는 워커가 집기도 전에 「적용했습니다」 였다. */
const APPLIED_LABEL: Partial<Record<JobStatus, string>> = {
  queued: '적용 대기 중',
  running: '적용 중',
  done: '적용함',
}

function appliedLabel(job: Pick<Job, 'applied_status'>): string {
  return APPLIED_LABEL[job.applied_status ?? 'done'] ?? '적용함'
}

/** 적용이 아직 끝나지 않았나 — 그동안 목록을 다시 읽어 「적용함」 으로 바뀌는 것을 보인다. */
function applyPending(job: Job): boolean {
  return Boolean(
    job.applied_by && (job.applied_status === 'queued' || job.applied_status === 'running'),
  )
}

function Detail({
  job: listed,
  busy,
  canApply,
  twoStep,
  onApply,
  onDownload,
  onDownloadRows,
  onOpenJob,
}: {
  job: Job
  busy: boolean
  /** 적용을 세우나 — `waitingForApply`. */
  canApply: boolean
  /** 적용 단계가 있는 종류인가 — 없으면 계획이어도 여기서 적용하지 않는다. */
  twoStep: boolean
  onApply: () => void
  onDownload: () => void
  onDownloadRows: () => void
  /** 다른 작업(적용 작업)을 이 화면 맨 위에 펼친다 — `?job=` 링크와 같다. */
  onOpenJob: (id: string) => void
}) {
  // 목록의 결과는 줄 목록을 뺀 요약이다 — 펼치면 상세(앞의 500줄, 오류 먼저)를 읽는다.
  const brief = omitsRows(listed.result)
  const full = useResource(
    () => (brief ? jobsApi.get(listed.id) : Promise.resolve(listed)),
    [listed.id, listed.status, brief],
  )
  const job = (brief ? full.data : null) ?? listed
  const plan = planOf(job)
  const batches = bundleBatches(job)
  // 묶음의 무덤 — 걷어내기(datarelay)는 이것만 담아 온다.
  const graves = tombstonesOf(job.result)
  const retype = retypeOf(job)
  // 사진 일괄 업로드 — 파일마다 무엇이 되나. 「적용」 은 결과의 `ok`(붙는 것이 있나)로 선다.
  const photos = photoPlanOf(job.result)
  // 부서 자료 이동(통폐합) — 무엇이 몇 건 옮겨지나 · 무엇이 막히나. 창을 닫고 와도 여기서 적용한다.
  const reassign = reassignOf(job.result)
  // 검토함 작업 — 결정마다 무엇이 됐나(가져오기 계획과 모양이 다르다).
  const review = reviewOf(job.result)
  // 객체 옮기기 · 키 변경 — 2만 건이 넘어 작업으로 계획한 것. 창을 닫고 와도 여기서 적용한다.
  const move = moveOf(job.result)
  const rename = renameOf(job.result)
  // 고아 첨부 파일 정리 — 무엇을 삭제하나 · 다른 DB 의 저장소일 수 있다는 경고.
  const gc = isGcResult(job)
  const known =
    plan || batches || graves || retype || photos || reassign || review || move || rename || gc
  // **모르는 모양도 값은 적는다** — 「남길 결과가 없는 작업입니다」 는 정말 남긴 것이 없을 때만.
  const unknown = !known && !job.error
  const fields =
    unknown &&
    (resultFields(job.result).length > 0 ||
      typeof (job.result as { summary?: unknown } | null)?.summary === 'string')
  return (
    <div className="bg-muted/30 space-y-3 px-4 py-3">
      {brief && full.loading && (
        <p className="text-muted-foreground flex items-center gap-1 text-xs">
          <Loader2 className="size-3 animate-spin" />
          계획을 읽는 중…
        </p>
      )}
      {brief && full.error && <ErrorNotice error={full.error} />}
      {plan && <ImportPlanTable plan={plan} onDownloadRows={onDownloadRows} />}
      {retype && <RetypePlanView job={job} retype={retype} />}
      {photos && <PhotoPlanTable plan={photos} />}
      {reassign && <ReassignPlanView result={reassign} />}
      {review && <ReviewResultView review={review} />}
      {move && <MovePlanView move={move} coreAccepted={coreAccepted(job)} />}
      {rename && <RenamePlanView rename={rename} coreAccepted={coreAccepted(job)} />}
      {gc && <GcResultView job={job} />}
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
                      새로 {one.plan?.counts?.create ?? 0} · 고침 {one.plan?.counts?.update ?? 0} ·
                      그대로 {one.plan?.counts?.unchanged ?? 0}
                      {one.plan?.counts?.error ? ` · 오류 ${one.plan.counts.error}` : ''}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {graves && <TombstonePlanView plan={graves} />}
      {unknown && <ResultFields result={job.result} />}
      {unknown && (job.has_output || !fields) && (
        <p className="text-muted-foreground text-xs">
          {!isTerminal(job)
            ? '아직 끝나지 않았습니다 — 끝나면 여기에 결과가 표시됩니다.'
            : job.has_output
              ? '만든 파일을 다운로드하세요.'
              : '남길 결과가 없는 작업입니다.'}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {job.has_output && (
          <Button size="xs" variant="outline" onClick={onDownload} disabled={busy}>
            <Download className="mr-1 size-3" />
            파일 다운로드
          </Button>
        )}
        {!plan && hasRows(job.result) && (
          <Button size="xs" variant="outline" onClick={onDownloadRows} disabled={busy}>
            <Download className="mr-1 size-3" />
            모든 줄 다운로드(CSV)
          </Button>
        )}
        {canApply && (
          <>
            <Button size="xs" onClick={onApply} disabled={busy}>
              {busy && <Loader2 className="mr-1 size-3 animate-spin" />}
              적용
            </Button>
            <span className="text-muted-foreground text-xs">
              아직 아무것도 적용하지 않았습니다 — 위 계획을 읽고 「적용」 을 클릭하세요.
            </span>
          </>
        )}
        {job.applied_by && (
          <span className="text-muted-foreground text-xs">
            {appliedLabel(job)} — 적용 작업{' '}
            <a
              href={`${PUBLIC_PATH}/jobs?job=${job.applied_by}`}
              className="text-primary font-mono underline-offset-4 hover:underline"
              onClick={(event) => {
                event.preventDefault()
                onOpenJob(job.applied_by as string)
              }}
            >
              {job.applied_by.slice(0, 8)}
            </a>
          </span>
        )}
        {job.status === 'done' &&
          twoStep &&
          !canApply &&
          !job.applied_by &&
          (plan?.applied === false ||
            retype?.applied === false ||
            photos?.applied === false ||
            reassign?.applied === false ||
            move?.applied === false ||
            rename?.applied === false) && (
            <span className="text-muted-foreground text-xs">
              {reassign
                ? '막히는 것이 있거나 이동할 것이 없어 적용할 수 없습니다 — 부서 관리의 「자료 이동」 에서 다시 미리 보세요.'
                : move || rename
                  ? '막는 것이 있거나 수신 시스템 통보 확인이 없어 적용할 수 없습니다 — 온톨로지 화면에서 다시 계획하세요.'
                  : '오류가 있거나 바뀌는 것이 없어 적용할 수 없습니다 — 파일을 수정해 다시 업로드하세요.'}
            </span>
          )}
        {job.status === 'done' && !twoStep && plan?.applied === false && (
          <span className="text-muted-foreground text-xs">
            계획만 본 것입니다 — 적용하려면 그 화면(데이터 소스의 「동기화」)에서 적용으로 다시
            실행합니다.
          </span>
        )}
      </div>
    </div>
  )
}

/**
 * 주소의 `?job=<id>` — **그 계획을 펼친 채로** 연다.
 *
 * 정제 도구 키트가 미리 보기를 마치면 이 링크를 준다. 사람은 명령 창 대신 여기서 계획을 읽고
 * 「적용」 을 누른다 — 그 플랫폼의 화면이라 엉뚱한 곳에 넣을 일이 없다.
 */
function linkedJob(): string | null {
  if (typeof window === 'undefined') return null
  return new URLSearchParams(window.location.search).get('job')
}

export default function JobsPage() {
  const { user } = useAuth()
  /** 이 작업을 내가 멈추거나 적용할 수 있나 — 시킨 사람과 시스템 관리자만. 「내가 시킨 것만」
   * 을 끄면 같은 부서 동료의 작업이 보이는데, 거기에 단추를 세우면 눌러 보고 거절당했다
   * (2026-10-08). */
  const ownedByMe = (job: Job) =>
    isSystemAdmin(user) || (job.requested_by_id != null && job.requested_by_id === user?.id)
  const [offset, setOffset] = useState(0)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [linked, setLinked] = useState(linkedJob)
  const [opened, setOpened] = useState<string | null>(linked)
  /** 파일을 삭제하는 계획(고아 첨부 파일 정리) — 적용 전에 무엇이 사라지는지 읽고 확인한다. */
  const [confirmGc, setConfirmGc] = useState<Job | null>(null)
  const [gcChecked, setGcChecked] = useState(false)
  // **하나씩 펼쳐 누르는 길만 있으면 스무 건에서 아무도 끝까지 안 한다.**
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [confirming, setConfirming] = useState<'apply' | 'cancel' | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [results, setResults] = useState<JobBulkResult[] | null>(null)
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
  const listed = page.data?.items ?? []
  /**
   * **링크의 작업이 이 쪽에 없으면 따로 읽어 맨 위에 펼친다.** 첫 쪽이 아니거나 거르기(「내가 시킨
   * 것만」 · 종류 · 상태)에 빠졌을 뿐인 작업에도 「목록에 없습니다」 라고 하던 때는, 링크가 맞는데
   * 사람은 링크가 틀린 줄 알았다. 「찾을 수 없다」 는 서버가 정말 없다고(404) 할 때만 말한다.
   */
  const needPinned = Boolean(linked && page.data && !listed.some((one) => one.id === linked))
  const pinnedJob = useResource(
    () => (needPinned && linked ? jobsApi.get(linked) : Promise.resolve(null)),
    [linked, needPinned],
  )
  const pinned = needPinned && pinnedJob.data?.id === linked ? pinnedJob.data : null
  const pinnedMissing =
    needPinned &&
    pinnedJob.error instanceof ApiError &&
    // 작업 번호 꼴이 아닌 링크(422)도 「찾을 수 없다」 다.
    (pinnedJob.error.status === 404 || pinnedJob.error.status === 422)
  const pinnedError = needPinned && !pinnedMissing ? pinnedJob.error : null
  const shown = pinned ? [pinned, ...listed] : listed
  const kinds = useResource(() => jobsApi.kinds(), [])
  const twoStep = useMemo(
    () => new Set((kinds.data ?? []).filter((one) => one.two_step).map((one) => one.name)),
    [kinds.data],
  )
  const workers = useResource(() => jobsApi.workers(), [])

  // 돌고 있는 것이 있으면 다시 읽는다 — 끝난 목록을 3초마다 두드릴 이유는 없다. 계획의 적용이
  // 대기 · 진행 중인 것도 — 「적용 대기 중」 이 「적용함」 으로 바뀌는 것을 보인다.
  const active = shown.some((one) => !isTerminal(one) || applyPending(one))
  const reloadPage = page.reload
  const reloadPinned = pinnedJob.reload
  const reload = useCallback(() => {
    reloadPage()
    reloadPinned()
  }, [reloadPage, reloadPinned])
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

  /** 목록에서 고른 것들 — **한 번에 적용하거나 한 번에 취소한다.** 파일을 삭제하는 계획(고아
   * 첨부 파일 정리)은 빼고 하나씩 — 한 번에 적용하는 확인 창은 무엇이 삭제되는지 말하지 않는다. */
  const chosen = shown.filter((one) => picked.has(one.id))
  const ready = chosen.filter((one) => ownedByMe(one) && waitingForApply(one, twoStep))
  const applicable = ready.filter((one) => !gcPlanOf(one))
  const oneByOne = ready.length - applicable.length
  const cancellable = chosen.filter(
    (one) => ownedByMe(one) && !isTerminal(one) && !one.cancel_requested,
  )

  async function runMany(what: 'apply' | 'cancel') {
    setActionError(null)
    setBulkBusy(true)
    try {
      const ids = (what === 'apply' ? applicable : cancellable).map((one) => one.id)
      const rows = what === 'apply' ? await jobsApi.applyMany(ids) : await jobsApi.cancelMany(ids)
      setResults(rows)
      setPicked(new Set())
      page.reload()
    } catch (caught) {
      setActionError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBulkBusy(false)
      setConfirming(null)
    }
  }

  const cancel = (job: Job) => act(job, () => jobsApi.cancel(job.id))
  /** 계획을 읽고 누르는 자리 — 같은 파일 · 같은 지문으로 적용 작업이 새로 생긴다. */
  const apply = (job: Job) =>
    act(job, async () => {
      const made = await jobsApi.apply(job.id)
      setOpened(made.id)
    })
  /** 「적용」 — 파일을 삭제하는 계획은 확인 창을 거친다(무엇이 사라지나 · 다른 DB 의 저장소일 수
   *  있다는 경고). 확인 없이 바로 적용하던 때는 그 경고를 읽을 자리가 적용 단추 옆에 없었다. */
  const requestApply = (job: Job) => {
    if (gcPlanOf(job)) {
      setGcChecked(false)
      setConfirmGc(job)
    } else {
      void apply(job)
    }
  }
  /** 다른 작업을 맨 위에 펼친다 — 주소도 그 `?job=` 으로(다시 읽어도 같은 자리). */
  const openJob = (id: string) => {
    setLinked(id)
    setOpened(id)
    try {
      window.history.replaceState(
        window.history.state,
        '',
        `${window.location.pathname}?job=${encodeURIComponent(id)}`,
      )
    } catch {
      // 주소만 못 바꾼다 — 펼치기는 이미 했다.
    }
  }
  const gcPlan = confirmGc ? gcPlanOf(confirmGc) : null
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

      {/* 링크로 왔는데 그 작업을 서버가 모른다(404) — **조용히 아무것도 안 열리면** 사람은 링크가
          고장 난 줄 안다. 계획을 본 사람만 적용할 수 있으니 대개 다른 계정으로 들어온 것이다. */}
      {pinnedMissing && (
        <Alert>
          <AlertTitle>링크의 작업을 찾을 수 없습니다</AlertTitle>
          <AlertDescription>
            오래되어 정리됐거나 이 계정으로는 볼 수 없는 작업입니다. 미리 보기를 한 사람(정제 도구에
            등록한 토큰의 주인)으로 로그인했는지 확인하세요 — 계획은 그 사람만 적용합니다.
          </AlertDescription>
        </Alert>
      )}
      <ErrorNotice error={pinnedError} />

      {picked.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border p-2">
          <span className="text-sm">{picked.size}건 선택</span>
          <Button
            size="sm"
            disabled={bulkBusy || applicable.length === 0}
            onClick={() => setConfirming('apply')}
          >
            선택 적용 {applicable.length > 0 && `(${applicable.length})`}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={bulkBusy || cancellable.length === 0}
            onClick={() => setConfirming('cancel')}
          >
            선택 취소 {cancellable.length > 0 && `(${cancellable.length})`}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setPicked(new Set())}>
            선택 해제
          </Button>
          {/* **고른 것과 할 수 있는 것이 다를 수 있다.** 그 차이를 말해 주지 않으면
              사람은 「왜 다섯을 골랐는데 셋만 갔나」 를 묻게 된다. */}
          {applicable.length < picked.size && (
            <span className="text-muted-foreground text-xs">
              적용은 오류 없는 계획만 — {picked.size - applicable.length}건은 제외됩니다
            </span>
          )}
          {oneByOne > 0 && (
            <span className="text-muted-foreground text-xs">
              파일을 삭제하는 계획 {oneByOne}건(고아 첨부 파일 정리)은 펼쳐서 하나씩 적용합니다
            </span>
          )}
        </div>
      )}

      <ConfirmDialog
        open={confirming !== null}
        title={
          confirming === 'apply'
            ? `계획 ${applicable.length}건을 적용합니다`
            : `작업 ${cancellable.length}건을 취소합니다`
        }
        description={
          confirming === 'apply' ? (
            <>
              선택한 계획마다 <b>적용 작업</b>이 생성되고 워커가 차례로 적용합니다. 오류가 있는
              계획과 이미 적용한 것은 제외됩니다 — 한 건이 막혀도 나머지는 적용됩니다. 적용한 뒤
              복원하려면 변경 이력의 <b>같이 바뀐 것 한 번에 복원</b>을 사용합니다.
            </>
          ) : (
            <>
              실행 중인 작업에 <b>멈춤을 요청</b>합니다. 워커가 다음 묶음에서 보고 멈추므로,
              이미 추가한 행은 그대로 남습니다 — 끝난 작업은 제외됩니다.
            </>
          )
        }
        confirmLabel={confirming === 'apply' ? '적용' : '취소 요청'}
        destructive={confirming === 'cancel'}
        onConfirm={() => runMany(confirming === 'apply' ? 'apply' : 'cancel')}
        onClose={() => setConfirming(null)}
      />

      <ConfirmDialog
        open={confirmGc !== null && gcPlan !== null}
        title={`첨부 저장소의 파일 ${((gcPlan?.orphans ?? 0) + (gcPlan?.temp ?? 0)).toLocaleString()}개를 삭제합니다`}
        description={
          gcPlan && <GcConfirmText plan={gcPlan} checked={gcChecked} onChecked={setGcChecked} />
        }
        confirmLabel="삭제"
        destructive
        confirmDisabled={(gcPlan?.warnings.length ?? 0) > 0 && !gcChecked}
        onConfirm={async () => {
          if (!confirmGc) return
          // 실패는 던진다 — 확인 창이 그 말을 창 안에 남긴다.
          const made = await jobsApi.apply(confirmGc.id)
          setOpened(made.id)
          reload()
        }}
        onClose={() => setConfirmGc(null)}
      />

      {results && (
        <div className="space-y-1 rounded-md border p-3">
          <p className="text-sm font-semibold">
            결과 — 성공 {results.filter((one) => one.status === 'ok').length} · 오류{' '}
            {results.filter((one) => one.status === 'error').length}
          </p>
          {results
            .filter((one) => one.status === 'error')
            .map((one) => (
              <p key={one.id} className="text-destructive text-sm">
                {one.message}
              </p>
            ))}
          <Button size="xs" variant="ghost" onClick={() => setResults(null)}>
            닫기
          </Button>
        </div>
      )}

      {page.data && shown.length === 0 ? (
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
              <TableHead className="w-8">
                <input
                  type="checkbox"
                  aria-label="이 쪽 전부 선택"
                  checked={shown.length > 0 && shown.every((one) => picked.has(one.id))}
                  onChange={(event) =>
                    setPicked(
                      event.target.checked ? new Set(shown.map((one) => one.id)) : new Set(),
                    )
                  }
                />
              </TableHead>
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
            {shown.map((one) => (
              <Fragment key={one.id}>
                <TableRow>
                  <TableCell className="pr-0">
                    <input
                      type="checkbox"
                      aria-label={`${one.kind_label} 선택`}
                      checked={picked.has(one.id)}
                      onChange={(event) =>
                        setPicked((was) => {
                          const next = new Set(was)
                          if (event.target.checked) next.add(one.id)
                          else next.delete(one.id)
                          return next
                        })
                      }
                    />
                  </TableCell>
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
                    {one.id === pinned?.id && (
                      <Badge variant="outline" className="mr-1">
                        링크의 작업
                      </Badge>
                    )}
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
                    {ownedByMe(one) && !isTerminal(one) && !one.cancel_requested && (
                      <Button
                        size="xs"
                        variant="outline"
                        disabled={busyId === one.id}
                        onClick={() => void cancel(one)}
                      >
                        취소
                      </Button>
                    )}
                    {ownedByMe(one) && waitingForApply(one, twoStep) && opened !== one.id && (
                      <Badge variant="outline">적용 대기</Badge>
                    )}
                    {applyPending(one) && opened !== one.id && (
                      <Badge variant="secondary">{appliedLabel(one)}</Badge>
                    )}
                  </TableCell>
                </TableRow>
                {opened === one.id && (
                  <TableRow>
                    <TableCell colSpan={8} className="p-0">
                      <Detail
                        job={one}
                        busy={busyId === one.id}
                        canApply={ownedByMe(one) && waitingForApply(one, twoStep)}
                        twoStep={twoStep.has(one.kind)}
                        onApply={() => requestApply(one)}
                        onDownload={() => void download(one)}
                        onDownloadRows={() => void act(one, () => jobsApi.rowsCsv(one.id))}
                        onOpenJob={openJob}
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
