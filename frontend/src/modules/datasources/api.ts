/** 데이터 소스 API — OData 에서 읽어 온톨로지를 채운다. 시스템 관리자만. */

import { jobsApi } from '@/modules/jobs/api'
import type { Job } from '@/modules/jobs/api'
import { api } from '@/shared/api/client'
import type { ImportRow } from '@/modules/objects/api'

export type AuthKind = 'none' | 'basic' | 'bearer' | 'header'
export type SourceKind = 'odata' | 'rest' | 'file'
export type RestPaging = 'none' | 'page' | 'offset' | 'cursor'

/** 종류별 설정. REST: 행 자리·쪽 넘김. 파일: 형식·시트. */
export interface SourceOptions {
  rows_path?: string
  paging?: RestPaging
  page_param?: string
  size_param?: string
  offset_param?: string
  cursor_param?: string
  cursor_path?: string
  start_page?: number
  params?: Record<string, string>
  format?: 'csv' | 'xlsx' | 'json'
  sheet?: string
}

export interface MappingColumn {
  /** 바깥 열 이름 — `Name`, `Address/Country` 처럼 안으로 들어간 것도. */
  source: string
  /** `key` · `label` · `description` · `alias` · `properties.<키>`. */
  target: string
  /** 값 대응표 — `{"US": "미국"}`. 표에 없는 값은 오류 행(strict) 또는 그대로. */
  values?: Record<string, unknown>
  values_strict?: boolean
}

export interface Mapping {
  /** 바깥 식별자 열 — 다음 동기화가 같은 객체를 다시 찾는 근거. */
  external_key?: string
  columns?: MappingColumn[]
}

export interface DataSource {
  id: string
  slug: string
  name: string
  kind: SourceKind
  base_url: string
  entity_set: string
  options: SourceOptions
  filter: string
  select: string
  auth_kind: AuthKind
  auth_user: string
  has_secret: boolean
  page_size: number
  type_slug: string
  workspace_slug: string | null
  mapping: Mapping
  deprecate_missing: boolean
  interval_minutes: number
  is_active: boolean
  last_run_at: string | null
  last_status: 'ok' | 'failed' | null
  created_at: string
}

export interface DataSourceWrite {
  slug: string
  name: string
  kind?: SourceKind
  base_url?: string
  entity_set: string
  options?: SourceOptions
  filter?: string
  select?: string
  auth_kind?: AuthKind
  auth_user?: string
  auth_secret?: string
  page_size?: number
  type_slug: string
  workspace_slug?: string | null
  mapping?: Mapping
  deprecate_missing?: boolean
  interval_minutes?: number
  is_active?: boolean
}

export interface Run {
  id: string
  status: 'planned' | 'ok' | 'failed'
  applied: boolean
  actor_label: string
  rows_seen: number
  counts: Record<string, number>
  errors: string[]
  started_at: string
  finished_at: string | null
}

export interface SyncResult {
  run: Run
  applied: boolean
  counts: Record<string, number>
  rows: ImportRow[]
  errors: string[]
  truncated: boolean
}

export interface Preview {
  columns: string[]
  rows: Record<string, unknown>[]
  mapped: { external_id: string; row: Record<string, unknown>; error: string | null }[]
  mapping_error: string | null
}

export const datasourceApi = {
  list: () => api.get<DataSource[]>('/datasources'),
  create: (body: DataSourceWrite) => api.post<DataSource>('/datasources', body),
  update: (slug: string, body: Partial<DataSourceWrite>) =>
    api.patch<DataSource>(`/datasources/${slug}`, body),
  remove: (slug: string) => api.delete<void>(`/datasources/${slug}`),
  /** 앞의 몇 행을 그대로 + 대응한 뒤로. 아무것도 안 바꾼다. */
  preview: (slug: string) => api.post<Preview>(`/datasources/${slug}/preview?limit=5`),
  /**
   * 계획(apply=false) 또는 적용. 한 행이라도 오류면 아무것도 안 넣는다.
   *
   * **작업이 된다** — 바깥 표를 읽는 시간은 그쪽이 정하므로 요청 안에서 기다리지 않는다.
   * 여기서 끝나기를 기다려 옛 모양(`SyncResult`)으로 돌려주고, 실패하면 그 이유로 던진다.
   */
  sync: async (slug: string, apply: boolean): Promise<SyncResult> => {
    const started = await api.post<Job>(
      `/datasources/${slug}/sync?apply=${apply ? 'true' : 'false'}`,
    )
    const done = await jobsApi.waitFor(started.id)
    if (done.status !== 'done' || !done.result) {
      throw new Error(
        done.error ??
          (done.status === 'cancelled' ? '취소됐습니다.' : '동기화가 끝나지 않았습니다.'),
      )
    }
    return done.result as unknown as SyncResult
  },
  runs: (slug: string) => api.get<Run[]>(`/datasources/${slug}/runs`),
}
