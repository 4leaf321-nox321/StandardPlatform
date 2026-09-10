/**
 * 메타모델 API — **읽기는 누구나, 고치기는 시스템 관리자.**
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
  entry_policy: 'open' | 'closed'
  key_policy: 'none' | 'optional' | 'required'
  key_scope: 'global' | 'workspace'
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

export interface OntologySchema {
  groups: NavGroupRow[]
  types: (ObjectType & { properties: PropertyDef[] })[]
  relation_types: RelationType[]
  data_types: DataType[]
  generated_at: string
}

export interface PropertyUsage {
  key: string
  label: string
  objects_with_value: number
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
  /** **지우기 전에 무엇이 사라지는지.** 확인 창이 이것을 읽어 말한다. */
  propertyUsage: (slug: string, key: string) =>
    api.get<PropertyUsage>(`/ontology/types/${slug}/properties/${key}/usage`),
  removeProperty: (slug: string, key: string) =>
    api.delete<void>(`/ontology/types/${slug}/properties/${key}`),
}
