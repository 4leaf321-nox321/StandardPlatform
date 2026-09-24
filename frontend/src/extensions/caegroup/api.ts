/**
 * 디지털 트윈 역량 — 서버와 주고받는 것.
 *
 * **축 이름과 척도를 여기 박지 않는다.** `defs()` 가 서버에서 받아 온다 — 문구를 고치는
 * 일이 화면 수정이 되면, 그 문구는 안 고쳐진다.
 */

import { api } from '@/shared/api/client'

export interface AxisRung {
  key: string
  label: string
  description?: string
  short?: string
}

export interface AxisDef {
  key: string
  label: string
  kind: 'value' | 'set' | 'rung' | 'matrix'
  unit?: string
  question?: string
  evidence_label?: string
  hide_empty?: boolean
  rungs: AxisRung[]
  /** 매트릭스 축의 바탕 토글(형상 · 거동). */
  base?: AxisRung[]
  /** 매트릭스 축의 열 — 불량 유형마다 무엇을 재현했나(시험 · 시장). */
  columns?: (AxisRung & { short?: string })[]
}

export interface Defs {
  sector: string
  sector_label: string
  subject_label: string
  agent_label: string
  axes: AxisDef[]
  evidence_tiers: { key: string; label: string; description: string; needs_ref: boolean }[]
  accuracy_thresholds: { rung: string; min: number }[]
  accuracy_rules: { key: string; label: string }[]
}

export interface SetupStatus {
  subject_type_slug: string | null
  subject_type_label: string | null
  agent_type_slug: string | null
  agent_type_label: string | null
  /** 거짓이면 화면은 목록 대신 「기준 정보 만들기」 를 보여 준다. */
  ready: boolean
}

export interface Assessment {
  axis: string
  value: number | null
  rung: string | null
  rungs: string[]
  defects: Record<string, Record<string, string>>
  note: string
  evidence: Record<string, unknown>
  evidence_tier: string
  evidence_ref: string
  assessed_at: string
  assessed_by_label: string
}

export interface AssessmentBody {
  value?: number | null
  rung?: string | null
  rungs?: string[]
  defects?: Record<string, Record<string, string>>
  note: string
  evidence?: Record<string, unknown>
  evidence_tier: string
  evidence_ref?: string
}

export interface HistoryRow {
  axis: string
  axis_label: string
  snapshot: Record<string, unknown>
  changed_at: string
  changed_by_label: string
}

export interface Coverage {
  pairs: number
  axes: { axis: string; label: string; assessed: number; ratio: number }[]
}

export interface Pair {
  id: string
  workspace_id: string
  subject_id: string
  subject_label: string
  agent_id: string
  agent_label: string
  created_at: string
}

const BASE = '/ext/caegroup/dt'

export const dtApi = {
  defs: () => api.get<Defs>(`${BASE}/defs`),
  setupStatus: () => api.get<SetupStatus>(`${BASE}/setup`),
  setup: () => api.post<SetupStatus>(`${BASE}/setup`),
  pairs: (workspace?: string) =>
    api.get<Pair[]>(`${BASE}/pairs${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ''}`),
  link: (body: { subject_id: string; agent_id: string; workspace_slug: string }) =>
    api.post<Pair>(`${BASE}/pairs`, body),
  unlink: (id: string) => api.delete<void>(`${BASE}/pairs/${id}`),
  assessments: (pairId: string) => api.get<Assessment[]>(`${BASE}/pairs/${pairId}/assessments`),
  save: (pairId: string, axis: string, body: AssessmentBody) =>
    api.put<Assessment>(`${BASE}/pairs/${pairId}/assessments/${axis}`, body),
  history: (pairId: string) => api.get<HistoryRow[]>(`${BASE}/pairs/${pairId}/history`),
  coverage: () => api.get<Coverage>(`${BASE}/coverage`),
}
