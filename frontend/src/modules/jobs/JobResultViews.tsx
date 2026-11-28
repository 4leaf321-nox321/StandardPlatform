/**
 * 「작업」 화면이 읽는 **그 밖의 결과** — 검토함 · 객체 옮기기 · 키 변경 · 고아 파일 정리, 그리고
 * 모르는 모양.
 *
 * 화면은 결과의 모양으로 무엇을 그릴지 고른다. 모양을 너그럽게 짐작하던 때는 남의 결과를 엉뚱한
 * 표에 넘겨 **화면 전체가 멈췄다**(2026-11-28 점검) — 줄 목록(`rows`)만 보고 검토함 결과를 가져오기
 * 계획 표로, `objects` 만 보고 정수(내보낸 객체 수)를 묶음의 타입 목록으로 펼쳤다. 그래서 여기의
 * 알아보기는 **그 모양에만 있는 칸**을 함께 보고, 모르는 모양은 값만 짧게 적는다 — 그것이 무엇이든
 * 화면을 멈추게 하지 않는다.
 */

import { Fragment } from 'react'

import type { Job } from '@/modules/jobs/api'
import type { MoveOut, RenameOut } from '@/modules/ontology/api'
import { MOVE_ACTION_LABEL } from '@/modules/ontology/MoveDialog'
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert'
import { Badge } from '@/shared/components/ui/badge'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function num(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function texts(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((one): one is string => typeof one === 'string') : []
}

/** 1.4MB 처럼 — 서버의 요약(`_megabytes`)과 같은 셈. */
function megabytes(bytes: number): string {
  const value = (bytes / 1024 / 1024).toLocaleString(undefined, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })
  return `${value}MB`
}

// --- 검토함 --------------------------------------------------------------------------

/** 검토함 결정 한 줄의 결과(`review.services.outcome_dict`). */
export interface ReviewOutcomeRow {
  kind: string
  kind_label?: string
  item_id?: string
  question: string
  choice?: string
  choice_label?: string
  /** 미리 보기: `apply` · `blocked`. 적용 뒤: `done` · `refused`. */
  status: string
  effect?: string
  message?: string
}

export interface ReviewResult {
  /** 적용한 결과인가(셈이 `done` · `refused`) — 아니면 「전부 작업으로」 의 미리 보기. */
  applied: boolean
  counts: Record<string, number>
  rows: ReviewOutcomeRow[]
  /** 서버가 싣지 않은 줄 수 — 수는 `counts` 에 모두 있다. */
  omitted: number
  notes: string[]
  skipped: number
}

function isReviewRow(value: unknown): value is ReviewOutcomeRow {
  return (
    isRecord(value) &&
    typeof value.kind === 'string' &&
    typeof value.question === 'string' &&
    typeof value.status === 'string'
  )
}

/**
 * 검토함 작업의 결과인가 — 일괄 확인(`review_apply`) · 전부 확인(`review_bulk`) · 자동 확인
 * (`review_auto`). 적용한 결과는 결정마다의 결과를 `rows` 에, 「전부 작업으로」 의 미리 보기는
 * `outcomes` 에 싣는다(그때 `rows` 는 줄 수다). 셈이 가져오기와 다르다(`done` · `refused` /
 * `apply` · `blocked`).
 */
export function reviewOf(result: unknown): ReviewResult | null {
  if (!isRecord(result) || !isRecord(result.counts)) return null
  const counts = result.counts
  if ('create' in counts || 'update' in counts) return null
  const done = 'done' in counts || 'refused' in counts
  if (!done && !('apply' in counts || 'blocked' in counts)) return null
  const list = Array.isArray(result.rows)
    ? result.rows
    : Array.isArray(result.outcomes)
      ? result.outcomes
      : null
  if (!list || !list.every(isReviewRow)) return null
  return {
    applied: done || result.applied === true,
    counts: Object.fromEntries(Object.entries(counts).map(([key, value]) => [key, num(value)])),
    rows: list,
    omitted: num(result.rows_omitted) + num(result.outcomes_omitted),
    notes: texts(result.notes),
    skipped: num(result.skipped),
  }
}

/** 목록 한 줄의 요약 — 「적용 — 새로 0 · 고침 0」 이 아니라 처리한 수. */
export function reviewLine(review: ReviewResult): string {
  const { counts } = review
  if (review.applied) {
    return (
      `적용 — 처리 ${(counts.done ?? 0).toLocaleString()}건` +
      (counts.refused ? ` · 그 사이 막힘 ${counts.refused.toLocaleString()}건` : '')
    )
  }
  return (
    `미리 보기 — 적용 ${(counts.apply ?? 0).toLocaleString()} · ` +
    `막힘 ${(counts.blocked ?? 0).toLocaleString()}`
  )
}

const REVIEW_STATUS: Record<string, string> = {
  apply: '적용',
  blocked: '막힘',
  done: '처리함',
  refused: '그 사이 막힘',
}

/** 표에 그리는 줄의 상한 — 나머지는 셈과 CSV 로. */
export const REVIEW_ROWS_MAX = 200

export function ReviewResultView({ review }: { review: ReviewResult }) {
  const shown = review.rows.slice(0, REVIEW_ROWS_MAX)
  const rest = review.rows.length - shown.length + review.omitted
  return (
    <section aria-label="검토함 결과" className="space-y-2 text-xs">
      <p>
        {review.applied ? (
          <>
            처리 <b>{(review.counts.done ?? 0).toLocaleString()}</b>건
            {(review.counts.refused ?? 0) > 0 &&
              ` · 그 사이 막혀 처리하지 않음 ${review.counts.refused.toLocaleString()}건`}
          </>
        ) : (
          <>
            미리 보기 — 적용 <b>{(review.counts.apply ?? 0).toLocaleString()}</b> · 막힘{' '}
            <b>{(review.counts.blocked ?? 0).toLocaleString()}</b>
            {review.skipped > 0 && ` · 선택하지 않음 ${review.skipped.toLocaleString()}`}
          </>
        )}
      </p>
      {review.notes.map((line) => (
        <p key={line} className="text-muted-foreground">
          {line}
        </p>
      ))}
      {shown.length > 0 && (
        <div className="max-h-72 overflow-auto rounded-md border">
          <table className="w-full">
            <thead className="bg-muted/50 sticky top-0">
              <tr>
                <th className="px-2 py-1 text-left font-normal">종류</th>
                <th className="px-2 py-1 text-left font-normal">질문</th>
                <th className="px-2 py-1 text-left font-normal">선택</th>
                <th className="px-2 py-1 text-left font-normal">결과</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((one, at) => {
                const bad = one.status === 'blocked' || one.status === 'refused'
                return (
                  <tr key={`${one.kind}:${one.item_id ?? ''}:${at}`} className="border-t align-top">
                    <td className="px-2 py-1 whitespace-nowrap">{one.kind_label || one.kind}</td>
                    <td className="max-w-64 px-2 py-1">{one.question}</td>
                    <td className="px-2 py-1">{one.choice_label || one.choice || '—'}</td>
                    <td className="px-2 py-1">
                      <Badge variant={bad ? 'destructive' : 'secondary'}>
                        {REVIEW_STATUS[one.status] ?? one.status}
                      </Badge>
                      {(one.message || one.effect) && (
                        <span
                          className={bad ? 'text-destructive ml-1' : 'text-muted-foreground ml-1'}
                        >
                          {one.message || one.effect}
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {rest > 0 && (
        <p className="text-muted-foreground">
          … 그 밖에 {rest.toLocaleString()}건(수는 위에 모두 셌습니다)
        </p>
      )}
    </section>
  )
}

// --- 객체 옮기기 · 키 변경 -----------------------------------------------------------

/** 객체 옮기기(`ontology_move`)의 계획 · 결과인가 — `MoveOut`. `objects` 는 수다. */
export function moveOf(result: unknown): MoveOut | null {
  return isRecord(result) &&
    typeof result.source === 'string' &&
    typeof result.target === 'string' &&
    Array.isArray(result.fields) &&
    typeof result.objects === 'number'
    ? (result as unknown as MoveOut)
    : null
}

/** 키 변경(`ontology_rename`)의 계획 · 결과인가 — `RenameOut`. */
export function renameOf(result: unknown): RenameOut | null {
  return isRecord(result) &&
    (result.kind === 'property' || result.kind === 'type') &&
    typeof result.to === 'string' &&
    Array.isArray(result.places) &&
    typeof result.objects === 'number'
    ? (result as unknown as RenameOut)
    : null
}

export function moveLine(move: MoveOut): string {
  if (move.applied) {
    return (
      `적용 — 이동 ${num(move.moved).toLocaleString()}건` +
      (num(move.merged) ? ` · 병합 ${num(move.merged).toLocaleString()}건` : '')
    )
  }
  const errors = texts(move.errors).length
  return `계획 — 객체 ${move.objects.toLocaleString()}건 이동${errors ? ` · 막는 것 ${errors}` : ''}`
}

export function renameLine(rename: RenameOut): string {
  const errors = texts(rename.errors).length
  return (
    `${rename.applied ? '적용' : '계획'} — 객체 ${rename.objects.toLocaleString()}건 · ` +
    `바뀌는 자리 ${rename.places.length}` +
    (errors ? ` · 막는 것 ${errors}` : '')
  )
}

function Places({ places }: { places: { what: string; count: number }[] }) {
  if (places.length === 0) return null
  return (
    <div>
      <p className="font-medium">바뀌는 자리</p>
      <ul className="list-disc space-y-0.5 pl-5">
        {places.map((one) => (
          <li key={one.what}>
            {one.what} {num(one.count).toLocaleString()}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** 막는 것 · 알아 둘 것 · 수신 시스템 통보 — 옮기기 · 키 변경이 같은 모양으로 낸다. */
function Blockers({
  errors,
  warnings,
  consumers,
  coreAccepted,
  what,
}: {
  errors: string[]
  warnings: string[]
  consumers: string[]
  coreAccepted: boolean
  what: string
}) {
  return (
    <>
      {errors.length > 0 && (
        <div className="text-destructive">
          <p className="font-medium">막는 것 — 지금은 {what}할 수 없습니다</p>
          <ul className="list-disc space-y-0.5 pl-5">
            {errors.map((one) => (
              <li key={one}>{one}</li>
            ))}
          </ul>
        </div>
      )}
      {warnings.length > 0 && (
        <div>
          <p className="font-medium">알아 둘 것</p>
          <ul className="list-disc space-y-0.5 pl-5 text-amber-700 dark:text-amber-400">
            {warnings.map((one) => (
              <li key={one}>{one}</li>
            ))}
          </ul>
        </div>
      )}
      {consumers.length > 0 && !coreAccepted && (
        <p className="text-destructive">
          외부에 공개한 타입입니다({consumers.join(', ')}) — 수신 시스템에 통보했다는 확인 없이
          계획했으므로 여기서 적용할 수 없습니다. 온톨로지 화면에서 확인을 선택하고 다시 계획하세요.
        </p>
      )}
    </>
  )
}

export function MovePlanView({ move, coreAccepted }: { move: MoveOut; coreAccepted: boolean }) {
  return (
    <section aria-label="객체 옮기기 계획" className="space-y-2 text-xs">
      <p>
        「{move.label}」 객체 옮기기: <span className="font-mono">{move.source}</span> →{' '}
        <span className="font-mono">{move.target}</span>
        {move.applied
          ? ` — 옮겼습니다(객체 ${num(move.moved).toLocaleString()}건` +
            (num(move.merged) ? ` · 병합 ${num(move.merged).toLocaleString()}건)` : ')')
          : ' — 계획'}
      </p>
      <p className="text-muted-foreground">
        옮길 객체 {move.objects.toLocaleString()}건(삭제한 것 포함 — 삭제하지 않은 것{' '}
        {num(move.live).toLocaleString()}건)
        {num(move.duplicates) > 0 &&
          ` · 대상에 같은 식별자가 있는 객체 ${num(move.duplicates).toLocaleString()}건`}
      </p>
      {move.fields.length > 0 && (
        <table>
          <thead className="text-muted-foreground">
            <tr>
              <th className="pr-3 text-left font-normal">원 속성</th>
              <th className="pr-3 text-left font-normal">대응</th>
              <th className="text-right font-normal">값</th>
            </tr>
          </thead>
          <tbody>
            {move.fields.map((one) => (
              <tr key={one.source} className="align-top">
                <td className="pr-3">
                  {one.label} <span className="text-muted-foreground">({one.source})</span>
                  {one.note && (
                    <span
                      className={
                        one.blocked ? 'text-destructive block' : 'text-muted-foreground block'
                      }
                    >
                      {one.note}
                    </span>
                  )}
                </td>
                <td className="pr-3">
                  {MOVE_ACTION_LABEL[one.action] ?? one.action}
                  {one.target && one.action !== 'drop' && (
                    <span className="ml-1 font-mono">{one.target}</span>
                  )}
                </td>
                <td className="text-right tabular-nums">{num(one.values).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Places places={Array.isArray(move.places) ? move.places : []} />
      <Blockers
        errors={texts(move.errors)}
        warnings={texts(move.warnings)}
        consumers={texts(move.core_consumers)}
        coreAccepted={coreAccepted}
        what="이동"
      />
    </section>
  )
}

export function RenamePlanView({
  rename,
  coreAccepted,
}: {
  rename: RenameOut
  coreAccepted: boolean
}) {
  const property = rename.kind === 'property'
  return (
    <section aria-label="키 변경 계획" className="space-y-2 text-xs">
      <p>
        「{rename.label}」 {property ? '키 변경' : 'slug 변경'}:{' '}
        <span className="font-mono">{property ? (rename.key ?? '') : rename.slug}</span> →{' '}
        <span className="font-mono">{rename.to}</span>
        {rename.applied ? ' — 변경했습니다' : ' — 계획'}
      </p>
      {property && (
        <p className="text-muted-foreground">값을 옮길 객체 {rename.objects.toLocaleString()}건</p>
      )}
      <Places places={rename.places} />
      <Blockers
        errors={texts(rename.errors)}
        warnings={texts(rename.warnings)}
        consumers={texts(rename.core_consumers)}
        coreAccepted={coreAccepted}
        what="변경"
      />
    </section>
  )
}

// --- 고아 첨부 파일 정리 --------------------------------------------------------------

/** 고아 파일 정리(`filestore_gc`)의 계획 — 세기만 했다. 적용이 지운다(다시 탐색한 뒤). */
export interface GcPlan {
  orphans: number
  orphan_bytes: number
  temp: number
  recent: number
  scanned: number
  sample: string[]
  /** 저장소 대부분을 이 DB 가 모른다 — 다른 DB 의 저장소일 수 있다(`files/gc.looks_foreign`). */
  warnings: string[]
  ok: boolean
}

/** 고아 파일 정리의 **계획**인가 — 적용한 결과(`removed`)는 아니다. */
export function gcPlanOf(job: Pick<Job, 'kind' | 'result'>): GcPlan | null {
  const result = job.result
  if (job.kind !== 'filestore_gc' || !isRecord(result) || result.applied === true) return null
  if (typeof result.orphans !== 'number') return null
  return {
    orphans: result.orphans,
    orphan_bytes: num(result.orphan_bytes),
    temp: num(result.temp),
    recent: num(result.recent),
    scanned: num(result.scanned),
    sample: texts(result.sample),
    warnings: texts(result.warnings),
    ok: result.ok === true,
  }
}

/** 고아 파일 정리를 **적용한** 결과인가. */
function gcDoneOf(job: Pick<Job, 'kind' | 'result'>): Record<string, unknown> | null {
  const result = job.result
  return job.kind === 'filestore_gc' && isRecord(result) && result.applied === true ? result : null
}

export function isGcResult(job: Pick<Job, 'kind' | 'result'>): boolean {
  return gcPlanOf(job) !== null || gcDoneOf(job) !== null
}

export function GcResultView({ job }: { job: Pick<Job, 'kind' | 'result'> }) {
  const plan = gcPlanOf(job)
  const done = gcDoneOf(job)
  if (done) {
    return (
      <section aria-label="고아 파일 정리 결과" className="space-y-1 text-xs">
        <p>
          삭제함 — 고아 파일 <b>{num(done.removed).toLocaleString()}</b>개(
          {megabytes(num(done.removed_bytes))}) · 임시 파일 {num(done.temp).toLocaleString()}개
          {num(done.kept) > 0 &&
            ` · 다시 확인하니 첨부가 사용해 남긴 파일 ${num(done.kept).toLocaleString()}개`}
        </p>
      </section>
    )
  }
  if (!plan) return null
  return (
    <section aria-label="고아 파일 정리 계획" className="space-y-2 text-xs">
      {plan.warnings.map((one) => (
        <Alert key={one} variant="destructive">
          <AlertTitle>적용 전에 확인하세요</AlertTitle>
          <AlertDescription>{one}</AlertDescription>
        </Alert>
      ))}
      <p>
        삭제할 것 — 고아 파일 <b>{plan.orphans.toLocaleString()}</b>개(
        {megabytes(plan.orphan_bytes)}) · 임시 파일 <b>{plan.temp.toLocaleString()}</b>개
      </p>
      <p className="text-muted-foreground">
        탐색한 파일 {plan.scanned.toLocaleString()}개 · 업로드한 지 하루가 안 되어 남길 것{' '}
        {plan.recent.toLocaleString()}개. 적용은 저장소를 다시 탐색해, 그 사이 첨부가 사용하게 된
        파일은 남깁니다.
      </p>
      {plan.sample.length > 0 && (
        <div>
          <p className="font-medium">
            삭제할 고아 파일
            {plan.orphans > plan.sample.length && ` — 앞의 ${plan.sample.length}개`}
          </p>
          <ul className="text-muted-foreground max-h-48 overflow-auto font-mono">
            {plan.sample.map((one) => (
              <li key={one} className="break-all">
                {one}
              </li>
            ))}
          </ul>
        </div>
      )}
      {!plan.ok && <p className="text-muted-foreground">삭제할 파일이 없습니다.</p>}
    </section>
  )
}

/**
 * 고아 파일 정리를 적용하기 전의 확인 — **무엇이 사라지나**, 그리고 다른 DB 의 저장소일 수 있다는
 * 경고. 경고가 있으면 「이 설치의 저장소가 맞다」 를 선택해야 삭제할 수 있다(`checked`) — 다른 DB 에
 * 붙은 워커가 같은 폴더를 보면 이 DB 가 모르는 파일이 모두 고아로 보인다(2026-11-26).
 */
export function GcConfirmText({
  plan,
  checked,
  onChecked,
}: {
  plan: GcPlan
  checked: boolean
  onChecked: (value: boolean) => void
}) {
  return (
    <div className="space-y-2">
      <p>
        고아 파일 <b>{plan.orphans.toLocaleString()}</b>개({megabytes(plan.orphan_bytes)})와 임시
        파일 <b>{plan.temp.toLocaleString()}</b>개를 첨부 저장소에서 삭제합니다 —{' '}
        <b>복원할 수 없습니다.</b> 적용은 저장소를 다시 탐색해, 그 사이 첨부가 사용하게 된 파일은
        남깁니다.
      </p>
      {plan.warnings.map((one) => (
        <p key={one} className="text-destructive">
          {one}
        </p>
      ))}
      {plan.warnings.length > 0 && (
        <label className="flex items-start gap-2">
          <input
            type="checkbox"
            className="mt-0.5 size-4"
            checked={checked}
            onChange={(event) => onChecked(event.target.checked)}
          />
          <span>이 설치의 저장소가 맞습니다 — FILESTORE_DIR · DATABASE_URL 을 확인했습니다.</span>
        </label>
      )}
    </div>
  )
}

// --- 그 밖의 셈 · 모르는 모양 ----------------------------------------------------------

/** 가져오기 계획의 셈인가(`create` · `update` · `unchanged` · `error`). */
export function isImportCounts(counts: Record<string, unknown>): boolean {
  return ['create', 'update', 'unchanged', 'error'].some((key) => typeof counts[key] === 'number')
}

const COUNT_LABEL: Record<string, string> = {
  types: '타입',
  relation_types: '관계 종류',
  objects: '객체',
  relations: '관계',
  tombstones: '삭제 표식',
}

/** 가져오기가 아닌 셈(묶음 내보내기 …)을 한 줄로 — 모르는 키는 키 그대로. */
export function countsLine(counts: Record<string, unknown>): string {
  return Object.entries(counts)
    .filter(([, value]) => typeof value === 'number')
    .map(([key, value]) => `${COUNT_LABEL[key] ?? key} ${(value as number).toLocaleString()}`)
    .join(' · ')
}

/** 값으로 적지 않는 칸 — 지문 · 뺀 줄 수 · 요약(따로 적는다). */
const HIDDEN_KEYS = new Set(['fingerprint', 'rows_omitted', 'outcomes_omitted', 'summary'])
const LIST_MAX = 10
const FIELDS_MAX = 30

function isScalar(value: unknown): value is string | number | boolean {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
}

function scalarText(value: string | number | boolean): string {
  if (typeof value === 'boolean') return value ? '예' : '아니요'
  if (typeof value === 'number') return value.toLocaleString()
  return value || '—'
}

function fieldText(value: unknown): string | null {
  if (isScalar(value)) return scalarText(value)
  if (Array.isArray(value)) {
    if (value.length === 0) return '없음'
    if (!value.every(isScalar)) return `${value.length.toLocaleString()}개`
    const head = value.slice(0, LIST_MAX).map(scalarText).join(', ')
    return value.length > LIST_MAX ? `${head} 외 ${value.length - LIST_MAX}개` : head
  }
  if (isRecord(value)) {
    const entries = Object.entries(value)
    if (entries.length === 0 || !entries.every(([, one]) => isScalar(one))) return null
    return entries
      .slice(0, LIST_MAX)
      .map(([key, one]) => `${key} ${scalarText(one as string | number | boolean)}`)
      .join(' · ')
  }
  return null
}

/** 모르는 모양의 결과에서 적을 수 있는 칸 — (키, 값의 글). */
export function resultFields(result: unknown): [string, string][] {
  if (!isRecord(result)) return []
  const out: [string, string][] = []
  for (const [key, value] of Object.entries(result)) {
    if (HIDDEN_KEYS.has(key)) continue
    const shown = fieldText(value)
    if (shown !== null) out.push([key, shown])
    if (out.length >= FIELDS_MAX) break
  }
  return out
}

/** 모르는 모양의 결과 — 「남길 결과가 없는 작업입니다」 대신 값을 짧게 적는다. 적을 것이
 *  없으면 아무것도 그리지 않는다. */
export function ResultFields({ result }: { result: unknown }) {
  const fields = resultFields(result)
  const summary = isRecord(result) && typeof result.summary === 'string' ? result.summary : ''
  if (fields.length === 0 && !summary) return null
  return (
    <section aria-label="결과" className="space-y-1 text-xs">
      {summary && <p>{summary}</p>}
      {fields.length > 0 && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
          {fields.map(([key, value]) => (
            <Fragment key={key}>
              <dt className="text-muted-foreground font-mono">{key}</dt>
              <dd className="break-all">{value}</dd>
            </Fragment>
          ))}
        </dl>
      )}
    </section>
  )
}
