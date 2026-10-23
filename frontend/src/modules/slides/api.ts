/**
 * 장표(ADR 0037) — 여러 타입의 기록을 틀 한 장에 모아 본다. 값은 열 때 센다(살아 있는 값).
 *
 * 틀은 시스템 관리자, 장표는 누구나 만들고 작성자 · 시스템 관리자가 고친다(`can_edit` — 서버가
 * 다시 판정한다). 화면의 말은 「장표 · 틀 · 영역」(`docs/용어.md`).
 */

import { api } from '@/shared/api/client'

export interface AreaCondition {
  field: string
  op: string
  value: string
  exclude: number
}

/** 틀의 영역 하나 — 서버의 `AreaIn`. */
export interface AreaDef {
  key: string
  title: string
  row: number
  col: number
  row_span: number
  col_span: number
  kind: 'records' | 'text'
  type_slug: string | null
  fields: string[]
  /** 조건에 맞는 것 전부 · 장표마다 선택(후보는 조건으로 좁힘) · 다른 영역을 따라옴. */
  fill: 'condition' | 'pick' | 'follow'
  subject_field: string | null
  subject_op: 'eq' | 'under'
  conditions: AreaCondition[]
  ref_line: string[]
  /** 따라갈 영역과, 이 기록에서 그 영역의 기록을 가리키는 칸(주소). */
  follow_area: string | null
  follow_field: string | null
  pair_of: string | null
  /** 표시할 건수 — 선택 영역이면 선택할 수 있는 건수(1 이면 기록 하나). */
  limit: number
  order: 'newest' | 'oldest' | 'label'
}

export interface TemplateBody {
  name: string
  description: string
  subject_type_slug: string
  rows: number
  cols: number
  areas: AreaDef[]
}

export interface Template extends TemplateBody {
  id: string
  subject_type_label: string
  slides: number
  updated_at: string
}

export interface LinkItem {
  id: string
  label: string
  type_slug: string | null
}

/** 연결 항목 줄의 한 칸 — 「모델: A」. */
export interface RecordLink {
  label: string
  items: LinkItem[]
}

export interface SlideRecord {
  id: string
  label: string
  type_slug: string
  values: Record<string, string>
  links: RecordLink[]
}

export interface RenderedArea {
  key: string
  title: string
  row: number
  col: number
  row_span: number
  col_span: number
  kind: 'records' | 'text'
  fill: 'condition' | 'pick' | 'follow'
  pair_of: string | null
  type_slug: string | null
  type_label: string | null
  /** 따라오는 영역이면 따라가는 영역의 머리글. */
  follow_title: string | null
  limit: number
  fields: { key: string; label: string }[]
  text: string
  records: SlideRecord[]
  total: number
  /** 「목록에서 보기」 — 같은 조건의 목록 주소. */
  list_params: Record<string, string>
  /** 이 영역만 못 그린 이유. */
  problem: string | null
}

export interface SlideSubject {
  id: string
  label: string
  type_slug: string
  deleted: boolean
}

export interface SlideSummary {
  id: string
  title: string
  template_id: string
  template_name: string
  subject: SlideSubject | null
  author_name: string
  created_at: string
  edited_at: string | null
}

export interface Slide extends SlideSummary {
  rows: number
  cols: number
  areas: RenderedArea[]
  can_edit: boolean
  rendered_at: string
}

export interface Rendered {
  rows: number
  cols: number
  areas: RenderedArea[]
}

export interface SlidePage {
  items: SlideSummary[]
  total: number
  limit: number
  offset: number
}

export interface SlideQuery {
  q?: string
  mine?: boolean
  template_id?: string
  subject_id?: string
  limit?: number
  offset?: number
}

function query(params: SlideQuery): string {
  const out = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === false || value === '') continue
    out.set(key, String(value))
  }
  const text = out.toString()
  return text ? `?${text}` : ''
}

export const slideApi = {
  list: (params: SlideQuery = {}) => api.get<SlidePage>(`/slides${query(params)}`),
  get: (id: string) => api.get<Slide>(`/slides/${id}`),
  create: (body: { template_id: string; subject_id: string; title?: string }) =>
    api.post<Slide>('/slides', body),
  /** 보낸 것만 바뀐다 — 글은 영역마다, 고른 기록은 영역마다 통째로. */
  update: (
    id: string,
    body: { title?: string; texts?: Record<string, string>; picks?: Record<string, string[]> },
  ) => api.patch<Slide>(`/slides/${id}`, body),
  remove: (id: string) => api.delete<void>(`/slides/${id}`),

  templates: () => api.get<Template[]>('/slides/templates'),
  template: (id: string) => api.get<Template>(`/slides/templates/${id}`),
  createTemplate: (body: TemplateBody) => api.post<Template>('/slides/templates', body),
  updateTemplate: (id: string, body: TemplateBody) =>
    api.put<Template>(`/slides/templates/${id}`, body),
  removeTemplate: (id: string) => api.delete<void>(`/slides/templates/${id}`),
  /** 선택 영역의 후보 — 틀의 조건에 맞는 것, 이름 · 식별자로 좁힌다. */
  candidates: (slideId: string, areaKey: string, q = '') =>
    api.get<{ items: { id: string; label: string }[]; total: number }>(
      `/slides/${slideId}/areas/${areaKey}/candidates${q ? `?q=${encodeURIComponent(q)}` : ''}`,
    ),
  /** 저장하지 않은 틀을 주제 하나로 세워 본다. */
  preview: (template: TemplateBody, subjectId: string) =>
    api.post<Rendered>('/slides/templates/preview', { template, subject_id: subjectId }),
}

/** 새 영역의 기본값 — 기록 영역, 조건으로 채움. */
export function blankArea(key: string, row: number, col: number): AreaDef {
  return {
    key,
    title: '',
    row,
    col,
    row_span: 1,
    col_span: 1,
    kind: 'records',
    type_slug: null,
    fields: [],
    fill: 'condition',
    subject_field: null,
    subject_op: 'eq',
    conditions: [],
    ref_line: [],
    follow_area: null,
    follow_field: null,
    pair_of: null,
    limit: 10,
    order: 'newest',
  }
}
