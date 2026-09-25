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
}

export interface HistoryRow {
  axis: string
  axis_label: string
  snapshot: Record<string, unknown>
  changed_at: string
  changed_by_label: string
}

export interface TileLevel {
  rung: string | null
  rungs: string[]
  value: number | null
  note: string
}

export interface Tile extends Pair {
  /** 벽에서 묶는 단위 — 담당 부서(없으면 소속 부서). */
  group: string
  levels: Record<string, TileLevel>
}

export interface RecentChange {
  pair_id: string
  axis: string
  axis_label: string
  label: string
  note: string
  changed_at: string
  changed_by_label: string
}

export interface Board {
  tiles: Tile[]
  recent: RecentChange[]
}

export interface Coverage {
  pairs: number
  axes: { axis: string; label: string; assessed: number; ratio: number }[]
}

export interface Pair {
  id: string
  workspace_id: string
  workspace_name: string
  subject_id: string
  subject_label: string
  agent_id: string
  agent_label: string
  /** 해석이 쓰는 도구 이름 — 「무엇으로 보나」 가 목록에서 바로 읽히게. */
  agent_tools: string[]
  agent_dept: string | null
  /** 매긴 축 수 — 「어디까지 채웠나」 가 목록에서 읽히게. */
  assessed: number
  created_at: string
}

export interface Staff {
  id: string
  workspace_id: string
  workspace_name: string
  /** 표에 서는 이름(담당 A · B). */
  alias: string
  /** 실명 — 그 부서를 고칠 수 있는 사람에게만 온다. 없으면 `null`. */
  name: string | null
  agents: { id: string; label: string }[]
  skill_kinds: string[]
  outside: boolean
  note: string
  /** 담당 하나에 주는 몫(1/n). */
  share: number
  /** 이 조사에 잡히는 이 사람의 몫. */
  fte: number
}

export interface StaffBody {
  workspace_slug: string
  name: string
  agents?: string[]
  skill_kinds?: string[]
  outside?: boolean
  note?: string
}

export interface StaffSummary {
  head_count: number
  fte: number
  by_agent: { id: string; label: string; fte: number; people: number }[]
  by_kind: { kind: string; people: number }[]
}

export interface SwRow {
  name: string
  quantity: number
  unit?: string
  purpose?: string
  shared?: boolean
}

export interface HwRow {
  name: string
  cpu_cores?: number
  ram_gb?: number
  /** **사양을 글로 적는다**(「A100 4장」) — 숫자로 강제하면 적을 수 있는 것을 못 적게 만든다. */
  gpu?: string
  shared?: boolean
}

export interface Capacity {
  workspace_id: string
  workspace_name: string
  sw: SwRow[]
  hw: HwRow[]
  material_types: number | null
  has_process_std: boolean
  note: string
}

export interface CapacitySummary {
  departments: number
  sw: { name: string; quantity: number }[]
  hw: { cpu_cores: number; ram_gb: number; gpu_units: number }
  material_types: number
  process_std: number
}

export interface SheetRow {
  pair_id: string
  subject_label: string
  agent_label: string
  agent_dept: string | null
  workspace_name: string
  value: number | null
  /** 수준 **이름**(key 가 아니다) — 사람이 읽고 고치는 값이다. */
  rung: string
  rungs: string[]
  note: string
}

export interface Sheet {
  axis: string
  axis_label: string
  kind: 'value' | 'set' | 'rung' | 'matrix'
  rows: SheetRow[]
}

export interface BulkRow {
  pair_id?: string
  subject_label?: string
  agent_label?: string
  value?: string
  rung?: string
  rungs?: string
  note?: string
}

export interface BulkResult {
  line: number
  /** ok · skipped(값이 비어 있음) · error. */
  status: 'ok' | 'skipped' | 'error'
  message: string
}

export interface StaffSheetRow {
  staff_id: string
  name: string
  alias: string
  workspace_name: string
  /** 담당 해석 이름들 — `·` 로 이어 적는다. */
  agents: string
  outside: string
  skill_kinds: string
  note: string
}

export interface StaffBulkRow {
  name?: string
  workspace_name?: string
  agents?: string
  outside?: string
  skill_kinds?: string
  note?: string
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
  /** 연계에서 고칠 수 있는 것은 **소속 부서뿐**이다 — 대상 · 수단을 바꾸는 것은 다른 연계다. */
  move: (id: string, workspaceSlug: string) =>
    api.patch<Pair>(`${BASE}/pairs/${id}`, { workspace_slug: workspaceSlug }),
  unlink: (id: string) => api.delete<void>(`${BASE}/pairs/${id}`),
  bulkMove: (ids: string[], workspaceSlug: string) =>
    api.post<{ changed: number }>(`${BASE}/pairs/bulk-move`, {
      ids,
      workspace_slug: workspaceSlug,
    }),
  bulkUnlink: (ids: string[]) =>
    api.post<{ changed: number }>(`${BASE}/pairs/bulk-unlink`, { ids }),
  assessments: (pairId: string) => api.get<Assessment[]>(`${BASE}/pairs/${pairId}/assessments`),
  save: (pairId: string, axis: string, body: AssessmentBody) =>
    api.put<Assessment>(`${BASE}/pairs/${pairId}/assessments/${axis}`, body),
  history: (pairId: string) => api.get<HistoryRow[]>(`${BASE}/pairs/${pairId}/history`),
  coverage: () => api.get<Coverage>(`${BASE}/coverage`),
  board: () => api.get<Board>(`${BASE}/board`),
  staff: (workspace?: string) =>
    api.get<Staff[]>(`${BASE}/staff${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ''}`),
  staffSummary: (workspace?: string) =>
    api.get<StaffSummary>(
      `${BASE}/staff/summary${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ''}`,
    ),
  staffCreate: (body: StaffBody) => api.post<Staff>(`${BASE}/staff`, body),
  staffUpdate: (id: string, body: StaffBody) => api.put<Staff>(`${BASE}/staff/${id}`, body),
  staffDelete: (id: string) => api.delete<void>(`${BASE}/staff/${id}`),
  capacity: (workspace: string) =>
    api.get<Capacity>(`${BASE}/capacity?workspace=${encodeURIComponent(workspace)}`),
  capacitySave: (workspace: string, body: Omit<Capacity, 'workspace_id' | 'workspace_name'>) =>
    api.put<Capacity>(`${BASE}/capacity?workspace=${encodeURIComponent(workspace)}`, body),
  capacitySummary: () => api.get<CapacitySummary>(`${BASE}/capacity/summary`),
  /** 현재값 표 — 화면이 이것을 그대로 표에 채운다(현재값 불러오기). */
  sheet: (axis: string) => api.get<Sheet>(`${BASE}/assessments/sheet?axis=${axis}`),
  bulkAssess: (axis: string, rows: BulkRow[]) =>
    api.put<BulkResult[]>(`${BASE}/assessments/bulk`, { axis, rows }),
  sheetFileUrl: (axis: string, format: 'xlsx' | 'csv') =>
    `${BASE}/assessments/sheet/export?axis=${axis}&format=${format}`,
  staffSheet: () => api.get<StaffSheetRow[]>(`${BASE}/staff/sheet`),
  staffBulk: (rows: StaffBulkRow[]) =>
    api.put<BulkResult[]>(`${BASE}/staff/bulk`, { rows }),
  staffFileUrl: (format: 'xlsx' | 'csv') => `${BASE}/staff/sheet/export?format=${format}`,
  capacityFileUrl: (workspace: string) =>
    `${BASE}/capacity/sheet/export?workspace=${encodeURIComponent(workspace)}`,
}
