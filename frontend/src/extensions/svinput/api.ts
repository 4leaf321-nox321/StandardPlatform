/**
 * 자주검증 입력(ADR 0051) — 서버와 주고받는 것. 백엔드 `app/extensions/svinput` 의 짝.
 *
 * 문서 · 문제점 · 하위 기록은 **허브의 온톨로지 객체**다 — 목록 · 통계 · 만들기 · 고치기는 코어
 * `objectApi` 로 간다. 이 확장의 API 는 셋뿐이다: 설정(역할 → 타입 · 칸), 설정 후보, 「문서 한 장」
 * 읽기(문제점 줄 + 하위 기록 수를 한 번에).
 */

import { api } from '@/shared/api/client'

export interface RoleFieldDef {
  key: string
  label: string
  required: boolean
  default: string
}

export interface RoleDef {
  key: string
  label: string
  description: string
  default_type: string
  /** 허브 타입(`sv_pm_*`)을 고르는 역할인가 — 스파인(코어 과제 · 모델)은 거짓. */
  hub: boolean
  fields: RoleFieldDef[]
}

export interface RoleState {
  key: string
  type_slug: string | null
  type_label: string | null
  fields: Record<string, string | null>
  /** 설정은 돼 있는데 실제로 없는 것 — `type` 또는 칸 역할 key. */
  missing: string[]
}

export interface Status {
  roles: RoleState[]
  /** `report` · `issue` 가 갖춰졌고 빠진 것이 없다. 거짓이면 화면은 설정으로 안내한다. */
  ready: boolean
  defs: RoleDef[]
}

export interface CandidateType {
  slug: string
  label: string
  usage: string
  /** 허브 접두가 맞는 타입인가 — 스파인은 아닌 것(코어의 과제 · 모델)에서 고른다. */
  hub: boolean
  properties: string[]
}

export interface CandidateRelation {
  slug: string
  label: string
}

export interface Candidates {
  prefix: string
  types: CandidateType[]
  /** 관계 종류 — 연결 칸(`report_ref` · `issue_ref`)에 `rel:<slug>` 로 고를 수 있는 것. */
  relations: CandidateRelation[]
}

export interface RoleIn {
  type: string | null
  fields?: Record<string, string | null>
}

export interface ChildCounts {
  stage_result: number
  cause: number
  prevention: number
  figure: number
  passed: number
  failed: number
}

export interface SheetObject {
  id: string
  type_slug: string
  key: string | null
  label: string
  description: string
  properties: Record<string, unknown>
  ref_labels: Record<string, string>
  status: string
  owner_workspace_id: string | null
  updated_at: string | null
}

export interface IssueLine extends SheetObject {
  plan_status: string | null
  counts: ChildCounts
}

export interface ReportSheet {
  report: SheetObject
  report_type_slug: string
  issue_type_slug: string
  issues: IssueLine[]
  total_issues: number
  kpis: SheetObject[]
  can_edit: boolean
}

export const ROOT = '/ext/svinput'

export const paths = {
  reports: () => `${ROOT}/postmortems`,
  report: (id: string) => `${ROOT}/postmortems/${id}`,
  issue: (id: string) => `${ROOT}/issues/${id}`,
  dashboard: () => `${ROOT}/dashboard`,
  setup: () => `${ROOT}/setup`,
}

export const svApi = {
  status: () => api.get<Status>('/ext/svinput/status'),
  candidates: () => api.get<Candidates>('/ext/svinput/candidates'),
  saveSettings: (roles: Record<string, RoleIn>) =>
    api.put<Status>('/ext/svinput/settings', { roles }),
  sheet: (reportId: string) => api.get<ReportSheet>(`/ext/svinput/reports/${reportId}`),
}

/** 역할의 설정에서 타입 slug · 칸 키를 꺼낸다 — 화면이 코드에 박지 않는다. */
export function roleOf(status: Status | null | undefined, key: string): RoleState | undefined {
  return status?.roles.find((one) => one.key === key)
}
