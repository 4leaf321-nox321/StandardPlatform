/**
 * 메타모델 API — **읽기는 누구나, 수정은 시스템 관리자.**
 *
 * 이 응답이 화면의 모양을 정한다. 타입 정의를 못 읽으면 폼을 그릴 수 없으므로
 * 읽기는 열려 있다.
 */

import { api } from '@/shared/api/client'

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
  key_policy: 'none' | 'optional' | 'required'
  key_scope: 'global' | 'workspace'
  /** 상위 타입 — RDF/OWL 의 rdfs:subClassOf. 화면 동작은 바꾸지 않는다. */
  parent_slug?: string | null
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
  sort_order: number
  is_active: boolean
}

/** 사이드바 한 묶음. 서버가 그룹과 그 안의 타입을 함께 준다. */
export interface NavGroupNode {
  slug: string
  label: string
  icon: string
  audience: string
  items: { label: string; icon: string; to: string; slug: string }[]
}

export interface SystemSource {
  key: string
  label: string
}

export interface OntologySchema {
  groups: NavGroupRow[]
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

export interface PropertyUsage {
  key: string
  label: string
  objects_with_value: number
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
  removeType: (slug: string) => api.delete<void>(`/ontology/types/${slug}`),

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
  propertyUsage: (slug: string, key: string) =>
    api.get<PropertyUsage>(`/ontology/types/${slug}/properties/${key}/usage`),
  removeProperty: (slug: string, key: string) =>
    api.delete<void>(`/ontology/types/${slug}/properties/${key}`),

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
