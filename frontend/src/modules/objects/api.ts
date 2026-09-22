/** 객체 API. */

import { jobsApi } from '@/modules/jobs/api'
import type { Job } from '@/modules/jobs/api'
import type { PropertyDef } from '@/modules/ontology/api'
import { api, downloadFile } from '@/shared/api/client'
import type { Page } from '@/shared/api/paging'

export interface ObjectRow {
  id: string
  type_slug: string
  key: string | null
  label: string
  description: string
  properties: Record<string, unknown>
  /** `object_ref` 가 가리키는 객체의 이름 (id -> 이름). **서버가 한 번에 모아 준다.** */
  ref_labels: Record<string, string>
  /** 사람이 붙인 다른 이름. 검색·참조 풀이·파일이 이것으로도 찾는다. 시험 자료에는 없을 수 있다. */
  aliases?: string[]
  /** {데이터 소스 slug: 그쪽 식별자}. 동기화가 남긴다 — 보기만. */
  external_ids?: Record<string, string>
  status: string
  owner_workspace_slug: string | null
  /** 부서 **이름**. 화면은 이것을 쓴다 — slug(hq)는 주소지 부서 이름이 아니다. */
  owner_workspace_name: string | null
  valid_from_year: number | null
  valid_to_year: number | null
  created_at: string
  updated_at: string
}

export interface AttachmentBrief {
  id: string
  owner_field: string | null
  original_name: string
  size_bytes: number
  created_at: string
}

export interface RelatedObject {
  relation_id: string
  relation: string
  /** 이 줄에 적을 말 — **방향에 맞는 쪽**(정방향이면 label, 역방향이면 inverse_label). */
  label: string
  /** 내가 출발점인가. 거짓이면 저쪽이 나를 가리킨다. */
  outgoing: boolean
  object_id: string
  object_label: string
  object_key: string | null
  object_type_slug: string
  object_type_label: string
  properties: Record<string, unknown>
  evidence_note: string
  created_at: string
  /** `relation`(관계 줄) · `field`(참조 칸 — 칸에 저장한 관계, 끊는 대신 칸을 고친다). */
  stored_as?: 'relation' | 'field'
  field_key?: string | null
}

export interface ObjectProfile {
  object: ObjectRow
  type_label: string
  properties_schema: PropertyDef[]
  attachments: AttachmentBrief[]
  /** 이 객체에 걸린 관계들. **양방향 다 온다.** */
  related: RelatedObject[]
  /** **서버가 판정한 것이다.** 화면이 스스로 정하면 화면마다 단추가 달라진다. */
  can_edit: boolean
  /**
   * 관계를 맺고 끊을 수 있나. 허브가 내려준 객체는 값을 못 고쳐도(`can_edit` 거짓)
   * 이 설치의 관계로 가리키는 것은 된다 — 과제의 산출 문서처럼.
   */
  can_link?: boolean
  /** 내가 지켜보고 있나 — 바뀌면 알림이 온다. */
  watching: boolean
  /** 몇 사람이 지켜보나. **혼자가 아니라는 것을 아는 것**이 고칠 때의 조심을 만든다. */
  watcher_count: number
}

export interface TreeNode {
  id: string
  label: string
  key: string | null
  status: string
  /** **자식 수를 미리 준다** — 없는데 펼침 화살표가 보이면 눌러 보고서야 안다. */
  child_count: number
}

export interface TreeOut {
  nodes: TreeNode[]
  /** 부모도 자식도 없는 것의 수. **안 보여 주면 눈에서 사라진 채 남는다.** */
  orphan_count: number
}

/** 일괄 계획의 한 줄 — 행마다 무엇이 되는지. */
export interface ImportRow {
  row: number
  action: 'create' | 'update' | 'unchanged' | 'error'
  label: string
  key: string | null
  object_id: string | null
  /** 바뀌는 칸. 「고침」 이 무엇을 고치는지 — 안 보여 주면 사람은 안 누른다. */
  changes: string[]
  message: string
}

export interface ImportPlan {
  applied: boolean
  rows: ImportRow[]
  /** 행과 무관한 오류(모르는 열, 상한). **하나라도 있으면 아무것도 안 넣는다.** */
  errors: string[]
  counts: Record<'create' | 'update' | 'unchanged' | 'error', number>
}

/** 여럿 골라 한 칸 바꾸기의 계획 — 행마다 무엇이 되는지. */
export interface BulkEditRow {
  id: string
  label: string
  action: 'change' | 'unchanged' | 'error'
  before: string
  after: string
  message: string
}

export interface BulkEditPlan {
  applied: boolean
  /** 적용했을 때만 — 같이 바뀐 것을 한 번에 되돌릴 때 이 번호를 쓴다. */
  batch_id: string | null
  field: string
  field_label: string
  rows: BulkEditRow[]
  counts: Record<string, number>
  /** 고를 수 있는 칸 — 고르개가 이것만 보여 준다. */
  fields: { field: string; label: string }[]
}

/** 여럿 골라 지우기의 계획 — 행마다 지워지나, 안 지워지면 왜. */
export interface BulkDeletePlan {
  applied: boolean
  mode: 'block' | 'detach'
  /** action 은 delete · error 뿐이다 — 지우는 데 「그대로」 는 없다. */
  rows: BulkEditRow[]
  counts: Record<string, number>
}

/** 이 객체를 가리키는 것 — 삭제 전에 보는 것. */
export interface References {
  property_refs: {
    object_id: string
    label: string
    key: string | null
    type_slug: string
    type_label: string
    property_key: string
    property_label: string
  }[]
  relations: {
    relation_id: string
    relation: string
    outgoing: boolean
    other_id: string
    other_label: string
    other_type_slug: string
  }[]
  /** 볼 수 없는 부서의 것 — **수만 온다.** */
  hidden_property_refs: number
  hidden_relations: number
  total: number
}

export interface MergeResult {
  into: string
  property_refs: number
  relations_moved: number
  relations_dropped: number
}

/** 그 시점의 값 전체 — 지금 값에서 기록을 거꾸로 대어 재구성한 것. */
export interface Snapshot {
  key: string | null
  label: string
  status: string
  properties: Record<string, unknown>
  description?: string
  valid_from_year?: number | null
  valid_to_year?: number | null
}

export interface HistoryEntry {
  id: string
  at: string
  actor_label: string
  action: string
  reason: string | null
  /** `object` 는 값이 바뀐 기록, `relation` 은 관계가 걸리거나 끊긴 기록. */
  kind: 'object' | 'relation'
  /** 칸별 `{before, after}`. 속성은 `properties.<키>`. */
  changes: Record<string, { before: unknown; after: unknown }>
  relation: {
    relation: string
    outgoing: boolean
    other_id: string
    other_label: string
  } | null
  /** 값 기록에만 있다 — 복원의 목표. */
  snapshot: Snapshot | null
  /** 일괄 수정으로 **같이 바뀐** 기록이면 그 묶음. 한 번에 되돌리는 입구다. */
  batch?: { id: string; field_label: string; size: number } | null
}

/** 조건 하나 — `f.<칸>.<연산>=<값>`. 칸 안에서는 `in` 으로 OR, 칸끼리는 AND. */
export interface Condition {
  field: string
  op: ConditionOp
  value: string
}

export type ConditionOp =
  'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte' | 'in' | 'contains' | 'starts' | 'empty' | 'notempty'

/** 값 여럿(`in`)의 구분자. 서버와 같다. */
export const CONDITION_MULTI_SEP = '|'

export interface SavedViewQuery {
  q: string
  conditions: Condition[]
  status: string | null
}

/** 이 뷰를 **그림으로** 볼 때의 설정. `group_by` 가 비면 목록일 뿐이다. */
export interface SavedViewSummary {
  group_by: string
  /** 두 번째 축. 있으면 계열이 여럿이 된다. */
  split_by: string
  metric: string
  metric_field: string | null
  chart: string
  stacked: boolean
  order: string
}

export interface SavedView {
  id: string
  type_slug: string
  name: string
  query: SavedViewQuery
  owner_user_id: string
  owner_label: string
  /** 있으면 그 부서가 함께 쓴다. 없으면 내 것. */
  workspace_slug: string | null
  summary: SavedViewSummary
  /** 부서 홈에 올린 자리. null 이면 홈에 없다. */
  home_order: number | null
  can_edit: boolean
  created_at: string
  updated_at: string
}

/** 부서 홈에 올라간 뷰 하나. **홈은 타입을 모르므로** 서버가 다 실어 준다. */
export interface HomeWidget {
  view: SavedView
  type_label: string
  icon: string
  /** 어느 부서의 것인가 — 여러 부서를 한 화면에 놓을 때 이것이 없으면 구별이 안 된다. */
  workspace_slug: string
  workspace_name: string
}

/** 내가 지켜보는 것 한 줄 — 최근 바뀐 것부터. */
export interface Watched {
  id: string
  type_slug: string
  type_label: string
  icon: string
  label: string
  key: string | null
  status: string
  updated_at: string
}

/** 품질 — 한 종류·한 타입의 걸린 것들. */
export interface QualityFinding {
  kind: 'missing_required' | 'orphan' | 'broken_ref' | 'duplicate' | 'alias_clash'
  kind_label: string
  type_slug: string
  type_label: string
  /** 전부 센 수. `hits` 는 상한까지만. */
  count: number
  hits: { id: string; label: string; key: string | null; detail: string }[]
}

export interface QualityReport {
  findings: QualityFinding[]
  sample_limit: number
}

export interface Rollup {
  property: string
  label: string
  fn: string
  /** 값이 하나도 없으면 null — 0 은 「합이 0」 으로 읽힌다. */
  value: number | null
  count: number
  /** 아래에 있지만 값이 빈 객체 수. 이것이 붙어야 합계가 「전부의 합」 으로 안 읽힌다. */
  missing: number
  descendants: number
}

export interface ObjectQuery {
  q?: string
  limit?: number
  offset?: number
  /** `?p.<키>=<값>` 으로 나간다. */
  properties?: Record<string, string>
  /** 트리에서 고른 노드. */
  under?: string | null
  /** 그 아래 것까지 포함할지. **기본은 포함**이다. */
  deep?: boolean
  /** 그 해에 해당하는 것만. 축의 시간 정책이 뜻을 정한다. */
  year?: number | null
  /** 조건 필터. 칸 안 OR, 칸끼리 AND. */
  conditions?: Condition[]
  status?: string | null
}

function queryString(query: ObjectQuery): string {
  const params = new URLSearchParams()
  if (query.q) params.set('q', query.q)
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  if (query.offset) params.set('offset', String(query.offset))
  if (query.under) params.set('under', query.under)
  if (query.under && query.deep === false) params.set('deep', 'false')
  if (query.year) params.set('year', String(query.year))
  for (const [key, value] of Object.entries(query.properties ?? {})) {
    if (value) params.set(`p.${key}`, value)
  }
  if (query.status) params.set('status', query.status)
  for (const one of query.conditions ?? []) {
    params.append(`f.${one.field}.${one.op}`, one.value)
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

function importForm(file: File, workspaceSlug?: string | null): FormData {
  const form = new FormData()
  form.set('file', file)
  if (workspaceSlug) form.set('workspace_slug', workspaceSlug)
  return form
}

export const qualityApi = {
  report: (kind?: string) =>
    api.get<QualityReport>(`/objects/quality/report${kind ? `?kind=${kind}` : ''}`),
}

export const viewApi = {
  list: (typeSlug: string) => api.get<SavedView[]>(`/objects/${typeSlug}/views`),
  create: (
    typeSlug: string,
    body: {
      name: string
      query: SavedViewQuery
      workspace_slug?: string | null
      summary?: SavedViewSummary | null
      /** 저장하면서 바로 부서 홈에 올릴지. **한 요청이다** — 둘로 나누면 저장은 됐는데
       *  안 올라간 상태가 생기고, 그때 사람은 무엇을 빠뜨렸는지 모른다. */
      on_home?: boolean
    },
  ) => api.post<SavedView>(`/objects/${typeSlug}/views`, body),
  /** **보낸 것만 바뀐다.** `on_home` 만 보내 홈에 올리거나 내린다. */
  update: (
    typeSlug: string,
    id: string,
    body: {
      name?: string
      query?: SavedViewQuery
      summary?: SavedViewSummary | null
      on_home?: boolean
      /** 홈에서 몇 번째 자리로. 서버가 그 부서의 홈 뷰를 통째로 다시 매긴다. */
      home_position?: number
    },
  ) => api.patch<SavedView>(`/objects/${typeSlug}/views/${id}`, body),
  remove: (typeSlug: string, id: string) => api.delete<void>(`/objects/${typeSlug}/views/${id}`),
  /**
   * 홈에 올라간 것들. 부서를 주면 그 부서만, **안 주면 내 부서 전부.**
   *
   * 사람은 대개 여러 부서에 속하고, 그때 「저 부서 홈에 뭐가 있더라」 를 보려고 부서를
   * 갈아 가며 도는 일이 생긴다.
   */
  home: (workspaceSlug?: string | null) =>
    api.get<HomeWidget[]>(
      workspaceSlug
        ? `/objects/home?workspace=${encodeURIComponent(workspaceSlug)}`
        : '/objects/home',
    ),
}

/** 통계의 막대 하나. `key` 는 필터에 그대로 넣을 수 있는 값(빈 칸이면 null). */
/** 세부 기준으로 나눈 조각 하나 — 세부 기준의 값별로. 합은 그 칸의 `count` 와 맞는다. */
export interface Part {
  key: string | null
  label: string
  count: number
  value: number | null
}

export interface Bucket {
  key: string | null
  label: string
  count: number
  value: number | null
  parts: Part[]
}

/** 묶을 수 있는(또는 셀 수 있는) 축 하나. */
export interface GroupOption {
  field: string
  label: string
  kind: string
  /** 여러 값 칸 — 한 행이 여러 막대에 든다. */
  multi?: boolean
  /** 이어진 것 너머의 기준이면 그 제목 — 「개발사 (시뮬레이션 기업)」. 자기 칸은 비어 있다. */
  heading?: string
}

export interface Summary {
  group_field: string
  group_label: string
  /** desc(큰 값부터) · asc(작은 값부터). */
  order: string
  split_field: string
  split_label: string
  /** 계열의 **차례.** 칸마다 나오는 대로 만들면 첫 칸에 없던 값이 뒤에서 튀어나와 색이 밀린다. */
  splits: string[]
  other_splits: number
  metric: string
  metric_field: string | null
  metric_label: string
  /** 필터를 통과한 **전체 행 수.** 막대의 합과 다르면 그 차이가 「그 밖에」 다. */
  total: number
  buckets: Bucket[]
  other_groups: number
  other_count: number
  /** 기준·세부 기준이 여러 값 칸이면 true — **막대의 합이 total 보다 클 수 있다.** */
  overlap?: boolean
  group_options: GroupOption[]
  metric_options: GroupOption[]
}

/** 점 하나 — 상자 그림의 값, 산점도의 좌표. */
export interface Point {
  id: string
  label: string
  group: string
  x: number | null
  y: number | null
}

export interface Points {
  x_label: string
  y_label: string
  group_label: string
  rows: Point[]
  total: number
  /** 상한을 넘어 잘렸나. **잘렸는데 말 안 하면 그 그림은 「이게 전부」 로 읽힌다.** */
  truncated: boolean
  number_fields: GroupOption[]
  group_options: GroupOption[]
}

/** 통계의 기준 — 그림과 파일이 **같은 주소**를 쓴다(따로 만들면 숫자가 갈린다). */
export interface SummaryOptions {
  groupBy: string
  splitBy?: string | null
  metric?: string
  metricField?: string | null
  order?: string
}

/** 원값 그림의 칸. */
export interface PointsOptions {
  x: string
  y?: string | null
  groupBy?: string | null
}

function summaryParams(query: ObjectQuery, options: SummaryOptions): URLSearchParams {
  const params = new URLSearchParams(
    queryString({ ...query, limit: undefined, offset: 0 }).replace(/^\?/, ''),
  )
  params.set('group_by', options.groupBy)
  if (options.splitBy) params.set('split_by', options.splitBy)
  if (options.order) params.set('order', options.order)
  if (options.metric) params.set('metric', options.metric)
  if (options.metricField) params.set('metric_field', options.metricField)
  return params
}

function pointsParams(query: ObjectQuery, options: PointsOptions): URLSearchParams {
  const params = new URLSearchParams(
    queryString({ ...query, limit: undefined, offset: 0 }).replace(/^\?/, ''),
  )
  params.set('x', options.x)
  if (options.y) params.set('y', options.y)
  if (options.groupBy) params.set('group_by', options.groupBy)
  return params
}

/**
 * 이어진 것 너머의 칸 — 조건 고르개가 이 타입 자신의 칸 아래에 제목별로 붙인다.
 *
 * 주소: `ref.<참조 칸>.<칸>` · `out.<관계>` · `out.<관계>.<칸>` · `in.<관계>[.<칸>]`.
 * 통계 기준도 **같은 주소**를 쓴다 — 막대를 누르면 그대로 조건이 된다.
 */
export interface LinkedField {
  field: string
  label: string
  heading: string
  /** `relation` 이면 상대 타입이 하나로 정해지지 않아 있음/없음만 걸 수 있다. */
  data_type: string
  multi: boolean
  enum_options: string[] | null
  ref_type_slug: string | null
}

export const objectApi = {
  /** 빈 CSV — 헤더가 「무엇을 채워야 하는지」 를 말한다. */
  template: (typeSlug: string) =>
    downloadFile(`/objects/${typeSlug}/template`, `${typeSlug}-template.csv`),
  /** 지금 거른 목록 그대로 — 쪽 상한 없이 전부. **작업이 된다** — 워커가 파일을 만들면 받는다. */
  export: (typeSlug: string, format: 'csv' | 'json', query: ObjectQuery = {}) => {
    const params = new URLSearchParams(queryString(query).replace(/^\?/, ''))
    params.set('format', format)
    return jobsApi.exportAndDownload(
      () => api.post<Job>(`/objects/${typeSlug}/export?${params.toString()}`),
      `${typeSlug}.${format}`,
    )
  },
  /** 일괄 입력 — `apply=false` 면 계획만. */
  /** 파일 대신 JSON 행으로 — 표에서 타입 생성이 쓴다. 규칙은 파일과 같다. */
  importRows: (
    typeSlug: string,
    rows: Record<string, unknown>[],
    opts: { apply: boolean; workspaceSlug?: string | null },
  ) =>
    api.post<ImportPlan>(`/objects/${typeSlug}/import-rows`, {
      rows,
      apply: opts.apply,
      workspace_slug: opts.workspaceSlug ?? null,
    }),
  /**
   * 일괄 입력는 **작업이 된다**(202). 돌아오는 것은 계획이 아니라 작업이고, 계획은
   * `jobsApi.waitFor` 로 기다린 뒤 `job.result` 에서 꺼낸다. 적용은 `jobsApi.apply(job.id)`.
   */
  import: (typeSlug: string, file: File, opts: { workspaceSlug?: string | null }) =>
    api.postForm<Job>(`/objects/${typeSlug}/import`, importForm(file, opts.workspaceSlug)),
  exportRelations: (typeSlug: string, format: 'csv' | 'json') =>
    jobsApi.exportAndDownload(
      () => api.post<Job>(`/objects/${typeSlug}/relations/export?format=${format}`),
      `${typeSlug}-relations.${format}`,
    ),
  importRelations: (typeSlug: string, file: File) =>
    api.postForm<Job>(`/objects/${typeSlug}/relations/import`, importForm(file)),
  /**
   * 고른 것들의 **한 칸**을 바꾼다 — `apply: false`(기본)면 계획만.
   *
   * 한 칸씩인 이유: 여러 칸을 동시에 바꾸면 그 창은 곧 「폼」 이 되고, 실수 한 번의
   * 크기가 수백 배가 된다.
   */
  bulkEdit: (
    typeSlug: string,
    body: { ids: string[]; field: string; value: unknown; apply: boolean },
  ) => api.post<BulkEditPlan>(`/objects/${typeSlug}/bulk-edit`, body),
  /**
   * 고른 것들을 지운다 — `apply: false`(기본)면 계획만.
   *
   * 가리키는 것이 있는 객체는 기본(`block`)으로 거절하고 몇 개가 걸렸는지 말한다.
   * 그래도 지우려면 `mode: 'detach'` — 가리키던 칸이 비고 관계가 끊긴다.
   */
  bulkDelete: (
    typeSlug: string,
    body: { ids: string[]; mode: 'block' | 'detach'; apply: boolean },
  ) => api.post<BulkDeletePlan>(`/objects/${typeSlug}/bulk-delete`, body),
  /**
   * 같이 바뀐 것을 **한 번에** 그때 값으로 — `apply: false` 면 계획만.
   *
   * 그 뒤에 누가 또 고친 행은 덮어쓰지 않고 이유를 적는다.
   */
  bulkEditUndo: (typeSlug: string, batchId: string, apply: boolean) =>
    api.post<BulkEditPlan>(`/objects/${typeSlug}/bulk-edit/${batchId}/undo`, {
      apply,
    }),
  /**
   * **안 센 값들** — 상자 그림과 산점도가 쓴다.
   *
   * 분포는 집계로 안 보인다: 평균이 같은 두 공정이 전혀 다른 모양일 수 있고, 그 차이가
   * 대개 문제의 자리다.
   */
  points: (typeSlug: string, query: ObjectQuery = {}, options: PointsOptions) =>
    api.get<Points>(`/objects/${typeSlug}/points?${pointsParams(query, options).toString()}`),
  /** 상자·산점도에 그린 원값을 파일로 — 행 하나가 객체 하나. */
  exportPoints: (
    typeSlug: string,
    query: ObjectQuery,
    options: PointsOptions,
    format: 'csv' | 'xlsx',
    filename: string,
  ) => {
    const params = pointsParams(query, options)
    params.set('format', format)
    return downloadFile(`/objects/${typeSlug}/points/export?${params.toString()}`, filename)
  },
  /** 이어진 것 너머의 칸 — 「개발사 › 국가」, 「관계 · 사용 부서」. */
  fields: (typeSlug: string) => api.get<LinkedField[]>(`/objects/${typeSlug}/fields`),
  /** 내가 지켜보는 것 — 최근 바뀐 것부터. **알림은 읽으면 사라지지만 이것은 남는다.** */
  watching: (limit = 10) => api.get<Watched[]>(`/objects/watching?limit=${limit}`),
  list: (typeSlug: string, query: ObjectQuery = {}) =>
    api.get<Page<ObjectRow>>(`/objects/${typeSlug}${queryString(query)}`),
  /**
   * 통계 — **목록과 같은 필터 위에서.**
   *
   * 필터를 따로 보내면 「목록에는 12건인데 묶어 보면 15건」 이 되고, 그때 어느
   * 쪽이 맞는지 아무도 모른다. 그래서 목록이 쓰는 `ObjectQuery` 를 그대로 받는다.
   */
  summary: (typeSlug: string, query: ObjectQuery = {}, options: SummaryOptions) =>
    api.get<Summary>(`/objects/${typeSlug}/summary?${summaryParams(query, options).toString()}`),
  /**
   * 묶어 본 표를 파일로 — **화면의 그림과 같은 숫자.** 막대를 보고 손으로 옮겨 적으면
   * 그 사이에 틀리고, 틀린 숫자가 회의에 들어간다.
   */
  exportSummary: (
    typeSlug: string,
    query: ObjectQuery,
    options: SummaryOptions,
    format: 'csv' | 'xlsx',
    filename: string,
  ) => {
    const params = summaryParams(query, options)
    params.set('format', format)
    return downloadFile(`/objects/${typeSlug}/summary/export?${params.toString()}`, filename)
  },
  /**
   * 이것이 바뀌면 알려 달라(또는 그만).
   *
   * **여러 번 눌러도 같은 결과다** — 두 번 켠 사람이 두 통을 받지 않는다.
   */
  setWatch: (typeSlug: string, id: string, on: boolean) =>
    api.put<{ watching: boolean; watcher_count: number }>(`/objects/${typeSlug}/${id}/watch`, {
      on,
    }),
  /** 사람이 붙인 다른 이름을 통째로. 같은 타입의 다른 객체가 쓰는 별칭이면 거절된다. */
  setAliases: (typeSlug: string, id: string, aliases: string[]) =>
    api.put<ObjectRow>(`/objects/${typeSlug}/${id}/aliases`, { aliases }),
  /** 「아래 전부」 를 모은 수 — `list_view.rollups` 가 정한 대로. 없으면 빈 목록. */
  rollup: (typeSlug: string, id: string) => api.get<Rollup[]>(`/objects/${typeSlug}/${id}/rollup`),
  tree: (typeSlug: string, opts: { parent?: string; orphans?: boolean } = {}) => {
    const params = new URLSearchParams()
    if (opts.parent) params.set('parent', opts.parent)
    if (opts.orphans) params.set('orphans', 'true')
    const text = params.toString()
    return api.get<TreeOut>(`/objects/${typeSlug}/tree${text ? `?${text}` : ''}`)
  },
  profile: (typeSlug: string, id: string) => api.get<ObjectProfile>(`/objects/${typeSlug}/${id}`),
  create: (typeSlug: string, body: Record<string, unknown>) =>
    api.post<ObjectRow>(`/objects/${typeSlug}`, body),
  update: (typeSlug: string, id: string, body: Record<string, unknown>) =>
    api.patch<ObjectRow>(`/objects/${typeSlug}/${id}`, body),
  /** 이 객체의 이력 — 최근 것이 앞. 볼 수 있는 사람이면 누구나. */
  history: (typeSlug: string, id: string) =>
    api.get<HistoryEntry[]>(`/objects/${typeSlug}/${id}/history`),
  /** 그 시점 값으로 고친다 — 저장과 같은 검증을 거쳐서. */
  restore: (typeSlug: string, id: string, entryId: string) =>
    api.post<ObjectRow>(`/objects/${typeSlug}/${id}/restore`, {
      entry_id: entryId,
    }),
  /** 삭제 전에 — 이 객체를 가리키는 것. */
  references: (typeSlug: string, id: string) =>
    api.get<References>(`/objects/${typeSlug}/${id}/references`),
  /** `block`(기본)은 가리키는 것이 있으면 409. `detach` 는 참조를 비우고 관계를 끊고 지운다. */
  remove: (typeSlug: string, id: string, mode: 'block' | 'detach' = 'block') =>
    api.delete<void>(`/objects/${typeSlug}/${id}?mode=${mode}`),
  /** 다른 객체에 합치고 지운다 — 참조·관계가 이긴 쪽으로. */
  merge: (typeSlug: string, id: string, into: string) =>
    api.post<MergeResult>(`/objects/${typeSlug}/${id}/merge`, { into }),

  addRelation: (typeSlug: string, id: string, body: Record<string, unknown>) =>
    api.post<RelatedObject>(`/objects/${typeSlug}/${id}/relations`, body),
  updateRelation: (
    typeSlug: string,
    id: string,
    relationId: string,
    body: Record<string, unknown>,
  ) => api.patch<RelatedObject>(`/objects/${typeSlug}/${id}/relations/${relationId}`, body),
  removeRelation: (typeSlug: string, id: string, relationId: string) =>
    api.delete<void>(`/objects/${typeSlug}/${id}/relations/${relationId}`),

  years: (typeSlug: string, id: string) => api.get<number[]>(`/objects/${typeSlug}/${id}/years`),
  /** **통째로** 정한다 — 화면이 보여 준 것과 저장되는 것이 같아야 한다. */
  setYears: (typeSlug: string, id: string, years: number[]) =>
    api.put<number[]>(`/objects/${typeSlug}/${id}/years`, years),
}
