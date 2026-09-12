/** 부서 API. */

import { api, downloadFile } from '@/shared/api/client'
import type { Member, Workspace, WorkspaceOption, WorkspaceReference } from '@/shared/api/types'

export type { Member, Workspace, WorkspaceOption, WorkspaceReference }

export interface WorkspaceImportRow {
  row: number
  slug: string
  action: 'create' | 'update' | 'unchanged' | 'skip' | 'error'
  label: string
  changes: string[]
  message: string
}

export interface WorkspaceImportPlan {
  applied: boolean
  rows: WorkspaceImportRow[]
  errors: string[]
  counts: Record<'create' | 'update' | 'unchanged' | 'skip' | 'error', number>
}

export const workspaceApi = {
  /** 붙여 넣은 표(다른 플랫폼의 내보내기)로 부서를 넣는다 — 계획(apply=false) 또는 적용. */
  importText: (text: string, apply: boolean) =>
    api.post<WorkspaceImportPlan>('/workspaces/import', { text, apply }),
  /**
   * 부서 정보를 CSV 로 내려받는다.
   *
   * 평범한 `<a href>` 로는 안 된다. access 토큰은 메모리에만 있어서 브라우저가
   * 스스로 여는 주소에는 안 실리고, 그러면 새 탭에서 401 이 나는데 **화면에는
   * 아무 표시도 안 뜬다** — 사용자는 아무 일도 안 일어난 것처럼 본다.
   */
  exportCsv: () => downloadFile('/workspaces/export.csv', '부서정보.csv'),

  options: () => api.get<WorkspaceOption[]>('/workspaces/options'),
  list: (all = false) => api.get<Workspace[]>(`/workspaces${all ? '?all=true' : ''}`),
  create: (body: {
    slug: string
    name: string
    description?: string
    parent_slug?: string | null
  }) => api.post<Workspace>('/workspaces', body),
  update: (slug: string, body: Record<string, unknown>) =>
    api.patch<Workspace>(`/workspaces/${slug}`, body),
  move: (slug: string, parentSlug: string | null) =>
    api.post<Workspace>(`/workspaces/${slug}/move`, { parent_slug: parentSlug }),
  reorder: (slug: string, direction: 'up' | 'down') =>
    api.post<Workspace>(`/workspaces/${slug}/reorder`, { direction }),
  /** **누르기 전에 무엇이 딸려 있는지 보여 준다.** */
  references: (slug: string) => api.get<WorkspaceReference[]>(`/workspaces/${slug}/references`),
  remove: (slug: string) => api.delete<void>(`/workspaces/${slug}`),

  members: (slug: string) => api.get<Member[]>(`/workspaces/${slug}/members`),
  addMember: (slug: string, email: string, role: string) =>
    api.post<Member>(`/workspaces/${slug}/members`, { email, role }),
  setRole: (slug: string, userId: string, role: string) =>
    api.patch<Member>(`/workspaces/${slug}/members/${userId}`, { role }),
  removeMember: (slug: string, userId: string) =>
    api.delete<void>(`/workspaces/${slug}/members/${userId}`),
}
