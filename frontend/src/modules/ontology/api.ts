/**
 * 메타모델 API — **읽기는 누구나, 수정은 시스템 관리자.**
 *
 * 이 응답이 화면의 모양을 정한다. 타입 정의를 못 읽으면 폼을 그릴 수 없으므로
 * 읽기는 열려 있다.
 */

import { jobsApi } from '@/modules/jobs/api'
import type { Job } from '@/modules/jobs/api'
import { api, downloadFile } from '@/shared/api/client'

export type DataType =
  | 'text'
  | 'text_long'
  | 'number'
  | 'date'
  | 'datetime'
  | 'bool'
  | 'enum'
  | 'url'
  | 'object_ref'
  | 'file'

export interface PropertyDef {
  id: string
  owner_kind: string
  owner_id: string
  key: string
  label: string
  data_type: DataType
  unit: string
  help: string
  required: boolean
  multi: boolean
  enum_options: string[] | null
  ref_type_slug: string | null
  /** 참조 칸의 역방향 이름 — 「과제」 칸을 과제 쪽에서 읽으면 「개발모델」. 참조 칸은 칸에 저장한 관계다. */
  inverse_label?: string
  /** `number` 의 아래·위 끝. **없으면 두께가 -5mm 여도 통과한다.** */
  min_value: number | null
  max_value: number | null
  /** 소수 자릿수. 넘으면 **반올림하지 않고 거절한다.** */
  decimals: number | null
  /** `text` 계열의 모양 규칙(정규식). */
  pattern: string | null
  /** 안 채웠을 때 들어가는 값. **만들 때만** 적용된다. */
  default_value: unknown
  /** 유일해야 하는가. 범위는 타입의 `key_scope` 를 따른다. */
  unique: boolean
  /** 속성 묶음. 폼과 상세가 함께 쓴다(3단계). */
  section: string
  sort_order: number
  /**
   * 이 속성이 **공통 속성**이면 그것을 정한 인터페이스(ADR 0006) — 모양(종류 · 고를 값 · 규칙)은
   * 인터페이스에서 수정한다. 타입의 속성에만 붙는다.
   */
  interface_slug?: string | null
}

/** 목록 화면의 모양. **없으면 모든 목록이 똑같아지고, 똑같으면 아무도 안 쓴다.** */
export type RollupFn = 'sum' | 'min' | 'max' | 'avg' | 'count'
export const ROLLUP_FNS: [RollupFn, string][] = [
  ['sum', '합계'],
  ['min', '최소'],
  ['max', '최대'],
  ['avg', '평균'],
  ['count', '개수'],
]

export interface RollupSpec {
  property: string
  fn: RollupFn
  label?: string
}

export interface ListView {
  columns?: string[]
  /**
   * 목록 왼쪽에 세울 트리.
   *
   * **관계를 고르는 이유**: `transitive` 인 관계가 둘 이상일 수 있어서, 아무거나
   * 골라 그리면 그 트리는 무엇을 보여 주는지 말할 수 없다. `parent` 는 **부모가
   * 어느 끝인가** — `part_of`(자식→부모)면 `dst`, `contains`(부모→자식)면 `src`.
   */
  tree?: { relation: string; parent?: 'src' | 'dst' }
  /**
   * 「아래 전부」 의 숫자를 트리 관계로 모은 것 — 상세에 뜬다. 저장하지 않고 볼 때마다
   * 센다. 트리가 있어야 하고, 숫자 속성만 된다.
   */
  rollups?: RollupSpec[]
  sort?: { field: string; dir?: 'asc' | 'desc' }
  filters?: string[]
  search?: string[]
}

export interface ObjectType {
  id: string
  slug: string
  label: string
  icon: string
  description: string
  sort_order: number
  nav_group_id: string | null
  nav_group_slug: string | null
  kind_class: 'reference' | 'record' | 'system'
  /**
   * 투영(`system`)이 비추는 원 표 — `workspace`(부서) · `user`(계정) · 승격한 전용 표.
   * 그 타입에는 `objects` 행이 없다: 목록·상세·참조가 전부 원 표에서 나온다.
   */
  system_source: string
  entry_policy: 'open' | 'closed'
  /** 누가 관리하나 — 빈 값이면 이 설치, `hub` 면 허브가 내려준 것(여기서 못 고친다). */
  managed_by?: string
  /**
   * **바깥 시스템에 여는 타입인가** — 켜면 `/api/core` 로 나간다.
   *
   * 켜는 순간 약속이 된다: 이 타입의 slug 와 속성 key 가 남의 시스템 코드에 박힌다.
   */
  core?: boolean
  key_policy: 'none' | 'optional' | 'required'
  key_scope: 'global' | 'workspace'
  /**
   * 구현하는 인터페이스(ADR 0006) — 그 공통 속성을 같은 키 · 같은 모양으로 가진다. 「개발모델은
   * 제품이다」 같은 뜻의 계층은 이것으로 적는다(옛 `parent_slug` 를 대신한다).
   */
  interface_slugs?: string[]
  temporal_kind: 'evergreen' | 'lifecycle' | 'yearly' | 'derived'
  list_view: ListView
  /**
   * 폼·상세의 묶음 순서와 모양.
   *
   * **묶음의 소속은 여기서 안 정한다** — 속성의 `section` 이 들고 있다.
   * 뷰가 소속까지 정하면 두 벌이 되고, 갈린 두 벌은 한쪽만 고쳐진다.
   */
  form_view: SectionView
  detail_view: SectionView
  title_template: string
  is_active: boolean
  object_count: number
}

export type Cardinality = 'one_to_one' | 'one_to_many' | 'many_to_one' | 'many_to_many'

export interface ReferenceEdge {
  /** `ref:<타입>.<칸>` — 그래프 · 이웃 필터가 관계 slug 와 같은 자리에 쓴다. */
  slug: string
  label: string
  inverse_label: string
  src_type_slug: string
  dst_type_slug: string
  field_key: string
  multi: boolean
}

export interface RelationType {
  id: string
  slug: string
  label: string
  /** 역방향의 말. `part_of` <-> 「포함」. 없으면 도착 쪽 화면이 말을 못 만든다. */
  inverse_label: string
  description: string
  directed: boolean
  /** **재귀 펼침 대상인가.** 트리와 롤업이 이것으로 갈린다. */
  transitive: boolean
  acyclic: boolean
  cardinality: Cardinality
  src_type_slugs: string[] | null
  dst_type_slugs: string[] | null
  sort_order: number
  is_active: boolean
  /** 누가 관리하나 — `hub` 면 허브가 내려준 관계 종류(여기서 그 줄을 잇거나 끊지 않는다). */
  managed_by?: string
}

/** 폼·상세가 쓰는 묶음 스펙. 둘이 같은 모양인 이유는 **같은 묶음을 쓰기 때문**이다. */
export interface SectionView {
  sections?: { name: string; columns?: 1 | 2 | 3; collapsed?: boolean }[]
}

export interface NavGroupRow {
  id: string
  slug: string
  label: string
  icon: string
  audience: string
  /** 상위 묶음의 slug — 사이드바를 두 단계로. 없으면 맨 위 묶음이다. */
  parent_slug?: string | null
  /** 그래프에서 이 묶음의 색(`#rrggbb`). 비우면 순서대로 팔레트에서 받는다. */
  color?: string | null
  sort_order: number
  is_active: boolean
}

/** 사이드바 한 묶음. 서버가 그룹과 그 안의 타입을 함께 준다. */
export interface NavGroupNode {
  slug: string
  label: string
  icon: string
  audience: string
  /**
   * 상위 묶음의 slug — 화면이 이것으로 두 단계를 세운다. 없으면 맨 위다.
   *
   * **평평한 목록으로 온다**(트리로 감싸지 않는다) — 응답의 모양은 그대로고, 그리는 깊이만
   * 화면이 정한다.
   */
  parent?: string | null
  items: { label: string; icon: string; to: string; slug: string }[]
}

export interface SystemSource {
  key: string
  label: string
}

/** 여러 타입이 따르는 공통 모양(ADR 0006). 객체를 갖지 않는다. */
export interface ObjectInterface {
  id: string
  slug: string
  label: string
  icon: string
  description: string
  sort_order: number
  /** 상위 인터페이스 — 그 공통 속성을 이어받는다. */
  extends_slugs: string[]
  list_view: ListView
  managed_by?: string
  /** 구현한 타입 — 상위 인터페이스를 거쳐 구현한 것까지. */
  implementers: string[]
  object_count: number
}

/** 지우기 전에 무엇이 가리키는지 — 하나라도 있으면 지우지 않는다. */
export interface InterfaceUsage {
  slug: string
  /** 직접 구현한 타입 — 해제해야 지울 수 있다. */
  implementers: string[]
  sub_interfaces: string[]
  /** 이 인터페이스를 참조 대상으로 적은 속성(`타입.키`). */
  referenced_by: string[]
  relation_types: string[]
}

export interface ImplementItem {
  key: string
  interface: string
  /** 맞추려고 바꾸는 칸(고를 값의 순서 · 필수 등). */
  changed: string[]
}

/** 구현하면 무엇이 되는가 — 저장 전에 보는 것. 아무것도 안 바꾼다. */
export interface ImplementPlan {
  /** 없어서 새로 만드는 속성. */
  creates: ImplementItem[]
  /** 이미 같은 모양이라 그대로 채택하는 속성. */
  adopts: ImplementItem[]
  syncs: ImplementItem[]
  /** 모양이 달라 구현할 수 없는 곳 — 무엇이 다른지 적혀 있다. 있으면 저장이 거절된다. */
  conflicts: string[]
  warnings: string[]
}

export interface OntologySchema {
  groups: NavGroupRow[]
  /** 여러 타입이 따르는 공통 모양 — 구현 타입은 `types[].interface_slugs`. */
  interfaces?: (ObjectInterface & { properties: PropertyDef[] })[]
  types: (ObjectType & { properties: PropertyDef[] })[]
  relation_types: RelationType[]
  /** 참조 칸을 관계 모양으로 — 타입 사이의 길은 이것과 relation_types 를 합친 것. */
  reference_edges?: ReferenceEdge[]
  data_types: DataType[]
  /** 투영 타입이 비출 수 있는 원 표들 — 이 설치가 등록한 것만. */
  system_sources: SystemSource[]
  generated_at: string
}

export type InferRole = 'label' | 'key' | 'description' | 'aliases' | 'property' | 'ignore'

export interface InferColumn {
  header: string
  role: InferRole
  key: string
  label: string
  data_type: DataType
  multi: boolean
  enum_options: string[]
  decimals: number | null
  filled: number
  distinct: number
  samples: string[]
  /** 왜 이렇게 맞혔나 — 사람이 읽고 고칠 근거. */
  note: string
  /** 종류가 참조면 가리키는 타입 · 인터페이스. */
  ref_type_slug?: string | null
  /** 가리킬 법한 있는 타입 — 하나로 풀리는 값이 많은 것부터(ADR 0009). */
  ref_candidates?: RefCandidate[]
  /** 참조로 제안하지 않은 까닭 · 안 본 것. */
  ref_note?: string
}

/** 열의 값이 그 타입의 객체로 얼마나 풀리나 — 값 단위, 일괄 입력의 이름 풀이 그대로. */
export interface RefCandidate {
  target_slug: string
  target_label: string
  target_kind: 'type' | 'interface'
  checked: number
  /** 하나로 풀린 값 — 넣을 때도 풀린다. */
  one: number
  /** 여럿에 맞은 값 — 넣으면 거절된다(식별자로 적어야 한다). */
  many: number
  /** 못 찾은 값. */
  none: number
  one_values: number
  many_samples: string[]
  none_samples: string[]
  /** 맞은 값이 전부 짧은 숫자 — 우연일 수 있다. */
  short: boolean
}

export interface InferResult {
  rows: number
  columns: InferColumn[]
  raw_rows: Record<string, unknown>[]
}

export interface InferBuildRequest {
  slug: string
  label: string
  nav_group_slug?: string | null
  key_policy?: 'none' | 'optional' | 'required'
  columns: InferColumn[]
  raw_rows: Record<string, unknown>[]
}

export interface InferBuild {
  schema: Record<string, unknown>
  import_rows: Record<string, unknown>[]
}

export interface ImportChange {
  kind: string
  slug: string
  action: 'create' | 'update' | 'unchanged'
  fields: string[]
  /** 이 변경을 부른 인터페이스 — 파일에 없던 타입의 속성이 인터페이스를 따라 바뀔 때. */
  via?: string
}

export interface ImportPlan {
  applied: boolean
  changes: ImportChange[]
  /** **적용은 되지만 조용히 무언가를 잃는 것.** 사람이 읽고 판단할 자리다. */
  warnings: string[]
  /** 하나라도 있으면 **아무것도 안 바꾼다.** */
  errors: string[]
  snapshot_id: string | null
}

export interface Snapshot {
  id: string
  taken_at: string
  actor_label: string
  reason: string
  type_count: number
  relation_count: number
}

export interface RenameOptionOut {
  applied: boolean
  from_value: string
  to_value: string
  /** 함께 바뀌는(바뀐) 저장값의 수. */
  objects_with_value: number
  errors: string[]
}

export interface PromoteOut {
  applied: boolean
  target_slug: string
  target_label: string
  target_new: boolean
  options: {
    value: string
    action: 'create' | 'reuse'
    object_id: string | null
    objects_with_value: number
  }[]
  errors: string[]
  warnings: string[]
  snapshot_id: string | null
}

/** 종류 변경의 요청 — 새 종류와 그 종류의 칸, 그리고 값마다의 대체 값(ADR 0007). */
export interface RetypeRequest {
  data_type: DataType
  enum_options?: string[] | null
  min_value?: number | null
  max_value?: number | null
  decimals?: number | null
  pattern?: string | null
  unit?: string | null
  /** {변환할 수 없는 값: 대체 값 | null(값 삭제)} — 열쇠는 계획이 준 `failures[].value` 그대로. */
  mapping: Record<string, string | null>
  /** 공개 타입이면 수신 시스템에 통보했다는 확인. */
  accept_core: boolean
  apply: boolean
}

export interface RetypeSample {
  type_slug: string
  /** 기본값이면 `null`(견본 이름이 「(기본값)」). */
  object_id: string | null
  label: string
}

export interface RetypeValue {
  value: string
  count: number
  reason: string
  /** 대체 값 목록에서만 — `null` 이면 값 삭제. */
  to: string | null
  samples: RetypeSample[]
}

export interface RetypeOut {
  applied: boolean
  data_type_before: DataType
  data_type_after: DataType
  /** 타입마다(인터페이스면 구현 타입 전부) 무엇이 되는지. */
  types: {
    type_slug: string
    type_label: string
    key: string
    via: string
    with_value: number
    converted: number
    unchanged: number
    cleared: number
  }[]
  failures: RetypeValue[]
  /** 변환할 수 없는 값의 종류 수 — 목록(`failures`)은 앞의 몇백 개까지다. */
  failures_total: number
  mapped: RetypeValue[]
  errors: string[]
  warnings: string[]
  /** 공개 타입이면 조회 가능한 토큰들 — 비어 있지 않으면 확인을 받아야 적용된다. */
  core_consumers: string[]
  snapshot_id: string | null
}

/** 코어 현황 — 무엇이 열려 있고, 누가 읽을 수 있고, 누가 받아 갔나. */
export interface CoreStatus {
  base: string
  types: {
    slug: string
    label: string
    description: string
    count: number
    updated_at: string | null
    endpoint: string
    properties: { key: string; label: string; data_type: string; multi: boolean }[]
  }[]
  consumers: {
    name: string
    owner: string
    last_used_at: string | null
    expires_at: string | null
    /** `core:read` 만 가진 좁은 자격인가. 거짓이면 `read` 라 코어 밖도 읽는다. */
    narrow: boolean
    created_at: string
  }[]
  recent: {
    at: string
    actor: string
    token: string | null
    type_slug: string
    rows: number
    since: string
  }[]
}

/** 정의 삭제의 미리 보기 — **삭제 경로와 같은 함수가 센다**(`GET /ontology/delete-plan`). */
export interface DeletePlan {
  kind: 'group' | 'type' | 'interface' | 'relation_type' | 'property' | 'interface_property'
  slug: string
  key: string | null
  label: string
  /** 막는 것이 없다. */
  allowed: boolean
  /** 먼저 할 일 — 첫째가 지금 삭제하면 받는 거절 그대로다. */
  blocking: { code: string; message: string }[]
  /** 함께 사라지는 것. */
  removes: string[]
  /** 삭제해도 남는 것. */
  keeps: string[]
  warnings: string[]
  core_consumers: string[]
  /** 타입에 지운 객체만 남았으면 그 수 — 함께 **영구 삭제**되고 되돌릴 수 없다(ADR 0008). */
  purge_deleted: number
}

export interface PropertyUsage {
  key: string
  label: string
  objects_with_value: number
  /** 이 타입이 **바깥에 열려 있나.** 켜져 있으면 이 칸은 남의 시스템 코드에 박혀 있다. */
  core_open: boolean
  /** 그 창구를 읽을 수 있는 자격 — 「이름(마지막 사용)」. */
  core_consumers: string[]
}

/** 초기화 계획의 한 줄 — 사라질 것 하나. */
export interface ResetItem {
  table: string
  label: string
  count: number
}

export interface ResetPlan {
  applied: boolean
  items: ResetItem[]
  total: number
  /** 적용하려면 이 문구를 그대로 보내야 한다. */
  confirm_phrase: string
  /** 초기화 직전에 남긴 정의. **정의는 여기서 되돌린다 — 데이터는 안 돌아온다.** */
  snapshot_id: string | null
}

export const ontologyApi = {
  /** **자기 설명적 스키마.** 화면도 MCP 도 이 하나를 읽는다. */
  schema: () => api.get<OntologySchema>('/ontology/schema'),
  /**
   * 구조만 파일로 — 정의는 작아서 그 자리에서 온다.
   *
   * JSON 은 가져오기가 받는 모양 그대로다(봉투를 씌우지 않는다).
   */
  exportStructure: (format: 'xlsx' | 'json', filename: string) =>
    downloadFile(`/ontology/export?format=${format}`, filename),
  /**
   * **채워진 객체까지** 한 파일로 — 작업이 된다.
   *
   * 데이터가 붙으면 타입 수만큼 행을 읽으므로 요청 안에서 만들면 큰 설치에서 끊긴다.
   * 워커가 만든 파일을 받아 오는 것까지 `jobsApi` 가 한다.
   */
  exportEverything: (format: 'xlsx' | 'json', filename: string) =>
    jobsApi.exportAndDownload(() => api.post<Job>(`/ontology/export?format=${format}`), filename),
  nav: () => api.get<NavGroupNode[]>('/ontology/nav'),

  groups: () => api.get<NavGroupRow[]>('/ontology/groups'),
  createGroup: (body: Record<string, unknown>) => api.post<NavGroupRow>('/ontology/groups', body),
  /** **보낸 것만 바뀐다.** 안 보낸 칸은 그대로다. */
  updateGroup: (slug: string, body: Record<string, unknown>) =>
    api.patch<NavGroupRow>(`/ontology/groups/${slug}`, body),
  removeGroup: (slug: string) => api.delete<void>(`/ontology/groups/${slug}`),

  types: () => api.get<ObjectType[]>('/ontology/types'),
  createType: (body: Record<string, unknown>) => api.post<ObjectType>('/ontology/types', body),
  /** **보낸 것만 바뀐다.** `nav_group_slug: null` 을 명시하면 사이드바에서 뺀다. */
  updateType: (slug: string, body: Record<string, unknown>) =>
    api.patch<ObjectType>(`/ontology/types/${slug}`, body),
  /** `purgeDeleted` — 지운 객체만 남은 타입이면 그것까지 영구 삭제함을 확인했다(ADR 0008). */
  removeType: (slug: string, purgeDeleted = false) =>
    api.delete<void>(`/ontology/types/${slug}${purgeDeleted ? '?purge_deleted=true' : ''}`),
  deletePlan: (kind: DeletePlan['kind'], slug: string, key?: string) =>
    api.get<DeletePlan>(
      `/ontology/delete-plan?${new URLSearchParams({ kind, slug, ...(key ? { key } : {}) })}`,
    ),

  relationTypes: () => api.get<RelationType[]>('/ontology/relation-types'),
  createRelationType: (body: Record<string, unknown>) =>
    api.post<RelationType>('/ontology/relation-types', body),
  /** **보낸 것만 바뀐다.** 허용 타입을 풀려면 `null` 을 명시한다. */
  updateRelationType: (slug: string, body: Record<string, unknown>) =>
    api.patch<RelationType>(`/ontology/relation-types/${slug}`, body),
  removeRelationType: (slug: string) => api.delete<void>(`/ontology/relation-types/${slug}`),

  properties: (slug: string) => api.get<PropertyDef[]>(`/ontology/types/${slug}/properties`),
  createProperty: (slug: string, body: Record<string, unknown>) =>
    api.post<PropertyDef>(`/ontology/types/${slug}/properties`, body),
  updateProperty: (slug: string, key: string, body: Record<string, unknown>) =>
    api.patch<PropertyDef>(`/ontology/types/${slug}/properties/${key}`, body),
  /** **삭제 전에 무엇이 사라지는지.** 확인 창이 이것을 읽어 말한다. */
  /** 고를 값 이름을 바꾸면서 저장된 값도 함께 — `apply=false` 면 몇 개인지만. */
  renameOption: (slug: string, key: string, body: { from: string; to: string; apply: boolean }) =>
    api.post<RenameOptionOut>(`/ontology/types/${slug}/properties/${key}/rename-option`, body),
  /** enum 속성을 코드표(참조 타입)로 승격 — 계획 먼저. */
  promoteProperty: (
    slug: string,
    key: string,
    body: {
      target_type_slug?: string | null
      new_slug?: string | null
      new_label?: string | null
      apply: boolean
    },
  ) => api.post<PromoteOut>(`/ontology/types/${slug}/properties/${key}/promote`, body),
  /** 종류 변경 — 저장값을 새 종류로 변환한다. `apply=false` 면 계획만(ADR 0007). */
  retypeProperty: (slug: string, key: string, body: RetypeRequest) =>
    api.post<RetypeOut>(`/ontology/types/${slug}/properties/${key}/retype`, body),
  /** 공통 속성의 종류 변경 — 구현 타입 전부의 저장값을 한 번에. */
  retypeInterfaceProperty: (slug: string, key: string, body: RetypeRequest) =>
    api.post<RetypeOut>(`/ontology/interfaces/${slug}/properties/${key}/retype`, body),
  /** 관리자 전용 — **코어 창구 밖에 둔다**(수신 시스템이 다른 연동의 이름을 보면 안 된다). */
  coreStatus: () => api.get<CoreStatus>('/ontology/core-status'),
  /** 연동 키트(zip) — 주소와 공개 타입이 채워진 상태로 내려온다. 수신 측에 그대로 전달한다. */
  downloadCoreKit: () => downloadFile('/ontology/core-kit', 'sp-core-client.zip'),
  propertyUsage: (slug: string, key: string) =>
    api.get<PropertyUsage>(`/ontology/types/${slug}/properties/${key}/usage`),
  /** `acceptCore` 는 **바깥에 연 타입**의 칸을 지울 때만 — 확인 창에서 한 번 더 물은 뒤. */
  removeProperty: (slug: string, key: string, acceptCore = false) =>
    api.delete<void>(
      `/ontology/types/${slug}/properties/${key}${acceptCore ? '?accept_core=true' : ''}`,
    ),

  // --- 인터페이스 (ADR 0006) ---
  interfaces: () => api.get<ObjectInterface[]>('/ontology/interfaces'),
  createInterface: (body: Record<string, unknown>) =>
    api.post<ObjectInterface>('/ontology/interfaces', body),
  /** **보낸 것만 바뀐다.** 상위 인터페이스를 바꾸면 구현 타입 전부가 걸린다. */
  updateInterface: (slug: string, body: Record<string, unknown>) =>
    api.patch<ObjectInterface>(`/ontology/interfaces/${slug}`, body),
  removeInterface: (slug: string) => api.delete<void>(`/ontology/interfaces/${slug}`),
  /** **지우기 전에 무엇이 가리키는지.** */
  interfaceUsage: (slug: string) => api.get<InterfaceUsage>(`/ontology/interfaces/${slug}/usage`),
  interfaceProperties: (slug: string) =>
    api.get<PropertyDef[]>(`/ontology/interfaces/${slug}/properties`),
  /** 공통 속성을 더한다 — 구현 타입 전부에 같은 키의 속성이 선다. */
  createInterfaceProperty: (slug: string, body: Record<string, unknown>) =>
    api.post<PropertyDef>(`/ontology/interfaces/${slug}/properties`, body),
  /** 공통 속성을 고친다 — 구현 타입들의 속성도 같은 트랜잭션에서 바뀐다. */
  updateInterfaceProperty: (slug: string, key: string, body: Record<string, unknown>) =>
    api.patch<PropertyDef>(`/ontology/interfaces/${slug}/properties/${key}`, body),
  interfacePropertyUsage: (slug: string, key: string) =>
    api.get<PropertyUsage>(`/ontology/interfaces/${slug}/properties/${key}/usage`),
  /** 공통 속성을 뺀다 — 구현 타입의 속성은 남는다. */
  removeInterfaceProperty: (slug: string, key: string) =>
    api.delete<void>(`/ontology/interfaces/${slug}/properties/${key}`),
  /** 고를 값 이름을 **구현 타입 전부에서 한 번에** — 저장된 값까지. */
  renameInterfaceOption: (
    slug: string,
    key: string,
    body: { from: string; to: string; apply: boolean },
  ) =>
    api.post<RenameOptionOut>(`/ontology/interfaces/${slug}/properties/${key}/rename-option`, body),
  /** 구현하면 무엇이 되는가 — 아무것도 안 바꾼다. */
  implementPlan: (typeSlug: string, interfaceSlugs: string[]) =>
    api.post<ImplementPlan>(`/ontology/types/${typeSlug}/interfaces/plan`, {
      interface_slugs: interfaceSlugs,
    }),

  /** **기본이 미리 보기다.** 적용은 의도를 적어야 일어난다. */
  importSchema: (body: unknown, dryRun: boolean) =>
    api.post<ImportPlan>(`/ontology/import?dry_run=${dryRun ? 'true' : 'false'}`, body),
  /** CSV·JSON 데이터 파일에서 타입 정의를 추론 — 아무것도 안 바꾼다. */
  infer: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return api.postForm<InferResult>('/ontology/infer', form)
  },
  /** 고친 열 정의 + 행 → 정의 스키마와 가져올 행. */
  inferBuild: (body: InferBuildRequest) => api.post<InferBuild>('/ontology/infer/build', body),
  snapshots: () => api.get<Snapshot[]>('/ontology/snapshots'),
  restore: (id: string) => api.post<ImportPlan>(`/ontology/snapshots/${id}/restore`),
  /**
   * 정의를 통째로 비운다 — **되돌릴 수 없는 일.**
   *
   * `apply: false`(기본)면 계획만 센다. 적용하려면 계획이 준 `confirm_phrase` 를
   * 그대로 보내야 한다.
   */
  reset: (body: { apply: boolean; confirm?: string }) =>
    api.post<ResetPlan>('/ontology/reset', body),
}
