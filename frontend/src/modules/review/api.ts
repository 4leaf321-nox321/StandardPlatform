/**
 * 검토함 API — 서버의 `review/schemas.py` 와 같은 모양(ADR 0027).
 *
 * 생성 타입(`@/shared/api/schema`)을 쓰지 않는 까닭은 `objects/api.ts` 의 별칭 후보와 같다 —
 * 그 파일은 커밋하지 않는 생성물이라 화면이 그것을 가져다 쓰면 CI 빌드가 떨어진다.
 */

import { api } from '@/shared/api/client'

export type ReviewAction = 'accept' | 'choose' | 'ignore'

export interface ReviewChoice {
  value: string
  label: string
  detail: string
  score: number | null
}

export interface ReviewItem {
  kind: string
  kind_label: string
  id: string
  question: string
  evidence: string[]
  choices: ReviewChoice[]
  recommended: string | null
  recommended_label: string | null
  confidence: number | null
  reason: string
  type_slug: string
  type_label: string
  workspace_slug: string | null
  workspace_name: string | null
  rows: number
  group: string
  group_label: string
  blocked: string | null
  link: string
  ignorable: boolean
  /** 처리 길이 있는 종류인가 — 아니면 「원래 화면으로」 만. */
  actionable: boolean
  /** 「같은 것끼리 묶기」 로 접은 줄이면 펼친 줄의 id — 결정은 이것마다 간다. */
  members: string[]
}

export interface ReviewKind {
  kind: string
  label: string
  description: string
  link: string
  /** 원래 자리에 있는 수. */
  total: number
  /** 읽은 것 중 거르기에 맞는 수. */
  matched: number
  scanned: number
  actionable: boolean
  undo_note: string
  notes: string[]
}

export interface ReviewFacet {
  value: string
  label: string
  count: number
}

export interface ReviewPage {
  kinds: ReviewKind[]
  items: ReviewItem[]
  total: number
  limit: number
  offset: number
  types: ReviewFacet[]
  workspaces: ReviewFacet[]
  notes: string[]
}

export interface ReviewFilters {
  kind?: string | null
  type?: string | null
  /** 부서 slug — `@global` 이면 전역만. */
  workspace?: string | null
  min_confidence?: number | null
  max_confidence?: number | null
  actionable?: boolean
}

export interface ReviewDecision {
  kind: string
  item_id: string
  action: ReviewAction
  value?: string | null
}

export interface ReviewOutcome {
  kind: string
  kind_label: string
  item_id: string
  question: string
  action: string
  choice: string
  choice_label: string
  /** 미리 보기: `apply` · `blocked`. 적용 뒤: `done` · `refused`. */
  status: string
  effect: string
  message: string
  rows: number
  undoable: boolean
  recommended: string | null
  confidence: number | null
  followed: boolean | null
}

export interface ReviewKindPlan {
  kind: string
  label: string
  apply: number
  blocked: number
  rows: number
  undoable: boolean
  undo_note: string
}

export interface ReviewPlan {
  fingerprint: string
  mode: string
  threshold: number | null
  decisions: ReviewDecision[]
  counts: { apply: number; blocked: number }
  by_kind: ReviewKindPlan[]
  outcomes: ReviewOutcome[]
  outcomes_omitted: number
  skipped: number
  rows: number
  as_job: boolean
  notes: string[]
}

export type ReviewPlanMode = 'decisions' | 'recommended' | 'all'

export interface ReviewPlanRequest {
  mode: ReviewPlanMode
  decisions?: ReviewDecision[]
  filters?: ReviewFilters
  threshold?: number
  action?: 'accept' | 'ignore'
}

/** 결정 기록에 남는 「어떻게 정했나」. */
export type ReviewApplyMode = 'one' | 'selected' | 'all' | 'recommended'

export interface ReviewApplied {
  applied: boolean
  batch_id: string | null
  /** 큰 묶음이면 작업으로 갔다. */
  job_id: string | null
  counts: Record<string, number>
  outcomes: ReviewOutcome[]
  undoable: number
}

export interface ReviewBatch {
  batch_id: string
  decided_at: string
  decided_by_name: string | null
  mode: string
  count: number
  done: number
  refused: number
  undoable: number
  undone: number
  kinds: string[]
}

export interface ReviewUndoLine {
  decision_id: string
  kind: string
  question: string
  choice_label: string
  /** 미리 보기: `undo` · `blocked`. 적용 뒤: `done` · `refused`. */
  status: string
  message: string
}

export interface ReviewUndo {
  applied: boolean
  batch_id: string
  counts: Record<string, number>
  lines: ReviewUndoLine[]
}

export interface ReviewAccuracyLine {
  kind: string
  label: string
  decided: number
  recommended: number
  followed: number
  rate: number | null
}

export interface ReviewAccuracy {
  kinds: ReviewAccuracyLine[]
  bands: ReviewAccuracyLine[]
}

function query(filters: ReviewFilters, extra: Record<string, string | number | boolean>) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries({ ...filters, ...extra })) {
    if (value === null || value === undefined || value === '' || value === false) continue
    params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

export const reviewApi = {
  items: (filters: ReviewFilters, options: { group?: boolean; limit?: number; offset?: number }) =>
    api.get<ReviewPage>(
      `/review/items${query(filters, {
        group: options.group ?? false,
        limit: options.limit ?? 50,
        offset: options.offset ?? 0,
      })}`,
    ),
  /** 미리 보기 — 아무것도 안 바꾼다. 적용은 이 응답의 `decisions` · `fingerprint` 그대로. */
  plan: (body: ReviewPlanRequest) => api.post<ReviewPlan>('/review/plan', body),
  apply: (plan: ReviewPlan, mode: ReviewApplyMode) =>
    api.post<ReviewApplied>('/review/apply', {
      decisions: plan.decisions,
      fingerprint: plan.fingerprint,
      mode,
      threshold: plan.mode === 'recommended' ? plan.threshold : null,
    }),
  batches: () => api.get<ReviewBatch[]>('/review/batches?limit=10'),
  undo: (batchId: string, apply: boolean) =>
    api.post<ReviewUndo>(`/review/batches/${batchId}/undo`, { apply }),
  accuracy: () => api.get<ReviewAccuracy>('/review/decisions/accuracy'),
}

/** 접은 줄이면 펼친 줄마다, 아니면 그 줄 하나 — 결정이 가는 항목들. */
export function targetsOf(item: ReviewItem): string[] {
  return item.members.length > 0 ? item.members : [item.id]
}
