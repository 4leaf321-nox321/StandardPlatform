/** 부서 API. */

import { api, downloadFile } from '@/shared/api/client'
import type { Member, Workspace, WorkspaceOption, WorkspaceReference } from '@/shared/api/types'

export type { Member, Workspace, WorkspaceOption, WorkspaceReference }

/** 이 부서가 **가진** 것 한 종류 — 통폐합 때 다른 부서로 넘길 수 있다. */
export interface WorkspaceContent {
  kind: string
  label: string
  count: number
}

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
  /**
   * 상위와 **형제 사이 자리까지 한 번에.** 끌어 놓기가 이것을 부른다.
   *
   * 형제 순서를 화면이 제 손으로 다시 매겨 여러 번 저장하면, 중간에 하나가 실패했을
   * 때 트리가 반쯤 뒤섞인 채로 남는다 — 서버가 한 트랜잭션으로 한다.
   */
  move: (slug: string, parentSlug: string | null, position?: number) =>
    api.post<Workspace>(`/workspaces/${slug}/move`, {
      parent_slug: parentSlug,
      ...(position === undefined ? {} : { position }),
    }),
  reorder: (slug: string, direction: 'up' | 'down') =>
    api.post<Workspace>(`/workspaces/${slug}/reorder`, { direction }),
  /** **누르기 전에 무엇이 딸려 있는지 보여 준다.** */
  references: (slug: string) => api.get<WorkspaceReference[]>(`/workspaces/${slug}/references`),
  /** 옮길 수 있는 것 — **0 건도 온다.** 빠지면 사람은 그 종류가 안 옮겨지는 줄 안다. */
  contents: (slug: string) => api.get<WorkspaceContent[]>(`/workspaces/${slug}/contents`),
  /** 자료를 다른 부서로 통째 옮긴다 — 부서 통폐합의 앞 단계. */
  reassign: (slug: string, targetSlug: string, kinds: string[]) =>
    api.post<{ moved: Record<string, number> }>(`/workspaces/${slug}/reassign`, {
      target_slug: targetSlug,
      kinds,
    }),
  remove: (slug: string) => api.delete<void>(`/workspaces/${slug}`),

  members: (slug: string) => api.get<Member[]>(`/workspaces/${slug}/members`),
  addMember: (slug: string, email: string, role: string) =>
    api.post<Member>(`/workspaces/${slug}/members`, { email, role }),
  setRole: (slug: string, userId: string, role: string) =>
    api.patch<Member>(`/workspaces/${slug}/members/${userId}`, { role }),
  removeMember: (slug: string, userId: string) =>
    api.delete<void>(`/workspaces/${slug}/members/${userId}`),
}
