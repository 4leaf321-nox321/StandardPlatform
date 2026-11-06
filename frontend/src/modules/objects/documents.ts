/** 문서 속성(ADR 0053) — 객체에 붙는 마크다운 문서와 그 판, 그리고 「이 객체를 언급한 문서」. */

import { api } from '@/shared/api/client'

export interface ObjectDocument {
  key: string
  label: string
  body: string
  /** 0 이면 아직 없다(빈 문서). */
  revision: number
  chars: number
  updated_at: string | null
  updated_by_name: string | null
  /** 토큰으로 고쳤으면 그 이름(MCP · AI) — 화면에서 고쳤으면 빈 글자. */
  updated_via: string
  can_edit: boolean
}

export interface DocumentRevision {
  revision: number
  chars: number
  created_at: string
  created_by_name: string | null
  via: string
  note: string
}

export interface DocumentRevisionBody extends DocumentRevision {
  body: string
}

export interface DocumentMention {
  object_id: string
  type_slug: string
  type_label: string
  label: string
  key: string
  property_label: string
}

function base(typeSlug: string, objectId: string, key: string): string {
  return `/objects/${typeSlug}/${objectId}/documents/${encodeURIComponent(key)}`
}

export const documentApi = {
  get: (typeSlug: string, objectId: string, key: string) =>
    api.get<ObjectDocument>(base(typeSlug, objectId, key)),
  /** 본문을 통째로 — 보고 고친 판(`baseRevision`)이 그사이 바뀌었으면 409. */
  put: (
    typeSlug: string,
    objectId: string,
    key: string,
    body: string,
    baseRevision: number,
    note = '',
  ) =>
    api.put<ObjectDocument>(base(typeSlug, objectId, key), {
      body,
      base_revision: baseRevision,
      note,
    }),
  revisions: (typeSlug: string, objectId: string, key: string) =>
    api.get<DocumentRevision[]>(`${base(typeSlug, objectId, key)}/revisions`),
  revision: (typeSlug: string, objectId: string, key: string, revision: number) =>
    api.get<DocumentRevisionBody>(`${base(typeSlug, objectId, key)}/revisions/${revision}`),
  mentions: (typeSlug: string, objectId: string) =>
    api.get<DocumentMention[]>(`/objects/${typeSlug}/${objectId}/mentions`),
}
