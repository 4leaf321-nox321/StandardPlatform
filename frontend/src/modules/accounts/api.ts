/** 계정 관리 API — 시스템 관리자 전용. */

import { api } from '@/shared/api/client'
import type {
  Account,
  AccountSummary,
  AccountWorkspace,
  TemporaryPassword,
} from '@/shared/api/types'

export type { Account, AccountSummary, AccountWorkspace, TemporaryPassword }

export const accountApi = {
  summary: () => api.get<AccountSummary>('/accounts/summary'),
  list: (status?: string) => api.get<Account[]>(`/accounts${status ? `?status=${status}` : ''}`),
  create: (body: Record<string, unknown>) => api.post<TemporaryPassword>('/accounts', body),
  approve: (id: string, body: { workspace_slug?: string | null; role?: string }) =>
    api.post<Account>(`/accounts/${id}/approve`, body),
  reject: (id: string, note: string) => api.post<Account>(`/accounts/${id}/reject`, { note }),
  suspend: (id: string) => api.post<Account>(`/accounts/${id}/suspend`),
  activate: (id: string) => api.post<Account>(`/accounts/${id}/activate`),
  /**
   * 소속을 **통째로** 정한다 — 적힌 부서가 소속의 전부가 된다.
   *
   * 「빼기」 와 「넣기」 를 따로 보내지 않는 이유: 화면은 지금 소속을 보고 고친 결과를
   * 보낸다. 차이를 화면이 계산하면, 그 사이에 다른 관리자가 넣은 부서를 모른 채 지운다.
   */
  setWorkspaces: (
    id: string,
    body: { workspace_slugs: string[]; home_workspace_slug?: string | null },
  ) => api.put<Account>(`/accounts/${id}/workspaces`, body),
  setHome: (id: string, workspaceSlug: string) =>
    api.post<Account>(`/accounts/${id}/home-workspace`, { workspace_slug: workspaceSlug }),
  setSystemAdmin: (id: string, grant: boolean) =>
    api.post<Account>(`/accounts/${id}/system-admin`, { is_system_admin: grant }),
  resetPassword: (id: string) => api.post<TemporaryPassword>(`/accounts/${id}/reset-password`),
  remove: (id: string) => api.delete<Account>(`/accounts/${id}`),
}
