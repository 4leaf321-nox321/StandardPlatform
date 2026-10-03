/** 데이터 소스 API — OData 에서 읽어 온톨로지를 채운다. 시스템 관리자만. */

import { jobsApi } from '@/modules/jobs/api'
import type { Job } from '@/modules/jobs/api'
import { api } from '@/shared/api/client'
import type { ImportRow } from '@/modules/objects/api'

export type AuthKind = 'none' | 'basic' | 'bearer' | 'header'
export type SourceKind = 'odata' | 'rest' | 'file' | 'sp_core'
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
  /**
   * 형제 코어(`sp_core`)에서 **선까지** 받는다 — `/core/<타입>/relations`.
   *
   * 객체만 받으면 받는 쪽은 점만 있고 선이 없다. 끊긴 선은 상대가 `deleted` 로 말해 주고,
   * 무덤의 보관 기간이 지났으면 `reset` 으로 「처음부터 다시 받아라」 고 한다.
   */
  relations?: boolean
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

/**
 * **한 행이 선 하나**인 원천(BOM · 매핑 표)의 대응.
 *
 * 있으면 이 소스는 **선을 가져온다**(객체가 아니라). 둘을 섞지 않는다 — 한 소스가 객체도
 * 만들고 선도 만들면 계획이 두 겹이 되어 「무엇이 몇 건인가」 를 한 표로 못 읽는다. 원천
 * 하나가 둘 다 담고 있으면 소스를 둘로 만든다(같은 주소 · 다른 대응).
 */
export interface MappingRelations {
  /** 관계 종류 slug. 행마다 다르면 `{"column": "열"}` 로 적는다(화면은 고정만 받는다). */
  relation?: string | { column?: string; value?: string }
  src?: { column?: string }
  dst?: { column?: string }
  evidence_note?: { column?: string; value?: string }
  /** 선에 붙는 속성 — {속성 키: {column}}. */
  properties?: Record<string, { column?: string }>
  /** `add`(기본) · `replace`(온 목록의 출발 객체 · 관계 종류 범위에서 안 온 선을 끊음). */
  mode?: 'add' | 'replace'
}

export interface Mapping {
  /** 바깥 식별자 열 — 다음 동기화가 같은 객체를 다시 찾는 근거. */
  external_key?: string
  columns?: MappingColumn[]
  relations?: MappingRelations
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
  /**
   * 이 소스가 적재할 때 내보이는 **출처 이름** — 비우면 slug 를 쓴다.
   *
   * 허브가 내려준 정의는 받는 쪽에서 고치는 길을 막는다(타입의 `managed_by`). 적재가 이 이름을
   * 말하고 그것이 같을 때만 통과한다 — **막기만 하면 받기도 막힌다.**
   */
  source_name: string
  deprecate_missing: boolean
  /** `sp_core` 가 지난번에 어디까지 받았나 — 비우면 다음 동기화가 처음부터 받는다. */
  since_mark: string
  /** 선의 시계 — 객체와 따로 움직인다(형제 코어에서 선까지 받을 때). */
  relations_since_mark?: string
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
  /** 비우면 slug 로 돌아간다. 묶음 가져오기의 `source` 와 같은 글자만 받는다. */
  source_name?: string
  deprecate_missing?: boolean
  /** **비우는 것만 보낸다**(`''`) — 처음부터 다시 받는다. 시계를 앞당기면 그 사이 것을 잃는다. */
  since_mark?: string
  interval_minutes?: number
  is_active?: boolean
}

/** 형제 설치의 카탈로그를 읽어 만든 **대응 초안** — 저장은 사람이 한다. */
export interface CoreSuggest {
  system: string
  revision: string
  type_slug: string
  type_label: string
  count: number
  mapping: Mapping
  properties: {
    key: string
    label: string
    data_type: string
    /** 못 이었으면 null 이고 `note` 가 이유를 적는다. */
    target: string | null
    note: string
  }[]
  notes: string[]
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
  /**
   * 형제 설치의 `/api/core` 를 읽어 **대응 초안**을 받는다 — 옮겨 적지 않게.
   *
   * 저장은 사람이 한다: 초안을 보고 고친 뒤 저장 단추를 누른다.
   */
  coreSuggest: (slug: string) => api.post<CoreSuggest>(`/datasources/${slug}/core-suggest`),
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
