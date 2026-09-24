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
  rows?: AxisRung[]
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
}
