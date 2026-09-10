/** 객체 API. */

import type { PropertyDef } from '@/modules/ontology/api'
import { api } from '@/shared/api/client'
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
  status: string
  owner_workspace_slug: string | null
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
}

export interface ObjectQuery {
  q?: string
  limit?: number
  offset?: number
  /** `?p.<키>=<값>` 으로 나간다. */
  properties?: Record<string, string>
}

function queryString(query: ObjectQuery): string {
  const params = new URLSearchParams()
  if (query.q) params.set('q', query.q)
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  if (query.offset) params.set('offset', String(query.offset))
  for (const [key, value] of Object.entries(query.properties ?? {})) {
    if (value) params.set(`p.${key}`, value)
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

export const objectApi = {
  list: (typeSlug: string, query: ObjectQuery = {}) =>
    api.get<Page<ObjectRow>>(`/objects/${typeSlug}${queryString(query)}`),
  profile: (typeSlug: string, id: string) =>
    api.get<ObjectProfile>(`/objects/${typeSlug}/${id}`),
  create: (typeSlug: string, body: Record<string, unknown>) =>
    api.post<ObjectRow>(`/objects/${typeSlug}`, body),
  update: (typeSlug: string, id: string, body: Record<string, unknown>) =>
    api.patch<ObjectRow>(`/objects/${typeSlug}/${id}`, body),
  remove: (typeSlug: string, id: string) => api.delete<void>(`/objects/${typeSlug}/${id}`),

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
}
