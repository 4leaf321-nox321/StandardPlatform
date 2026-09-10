/**
 * 메타모델 API — **읽기는 누구나, 고치기는 시스템 관리자.**
 *
 * 이 응답이 화면의 모양을 정한다. 타입 정의를 못 읽으면 폼을 그릴 수 없으므로
 * 읽기는 열려 있다.
 */

import { api } from '@/shared/api/client'

export type DataType = 'text' | 'number' | 'date' | 'bool' | 'enum' | 'object_ref' | 'file'

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
  sort_order: number
}

/** 목록 화면의 모양. **없으면 모든 목록이 똑같아지고, 똑같으면 아무도 안 쓴다.** */
export interface ListView {
  columns?: string[]
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
  is_active: boolean
  object_count: number
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
  updateGroup: (slug: string, body: Record<string, unknown>) =>
    api.patch<NavGroupRow>(`/ontology/groups/${slug}`, body),

  types: () => api.get<ObjectType[]>('/ontology/types'),
  createType: (body: Record<string, unknown>) => api.post<ObjectType>('/ontology/types', body),
  updateType: (slug: string, body: Record<string, unknown>) =>
    api.patch<ObjectType>(`/ontology/types/${slug}`, body),

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
