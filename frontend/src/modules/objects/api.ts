/** 인스턴스 API. */

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

export interface ObjectProfile {
  object: ObjectRow
  type_label: string
  properties_schema: PropertyDef[]
  attachments: AttachmentBrief[]
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
}
