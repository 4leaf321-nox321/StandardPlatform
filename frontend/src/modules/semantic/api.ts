/** 뜻으로 찾기의 관리(ADR 0052) — 어느 타입을 켤지 정하고 계산 차례를 넣는다. 시스템 관리자만. */

import { api } from '@/shared/api/client'

export interface SemanticField {
  key: string
  label: string
  data_type: string
}

export interface SemanticType {
  type_slug: string
  type_label: string
  icon: string
  /** axis · log — 기록은 칸을 골라야 대상이 된다(ADR 0054). */
  usage: string
  objects: number
  /** 맞춰 본 객체 수. */
  synced: number
  /** 조각 수 — 기본 글 + 문서의 절. */
  pieces: number
  /** 대상 타입에서 아직 안 맞춘 수(새것 · 고친 것). 대상이 아니면 null. */
  pending: number | null
  enabled: boolean
  /** 고른 칸 — null 이면 자동(축만). `label` 은 이름. */
  fields: string[] | null
  /** 고를 수 있는 칸 — 글 · 여러 줄 글 · 선택 · 참조 · 문서. */
  options: SemanticField[]
  why_not: string | null
  /** 대상으로 하면 생길 조각 수의 어림(지금 고른 칸 — 아니면 자동, 기록은 칸 하나). */
  estimate_pieces: number
  estimate_bytes: number
}

export interface SemanticJob {
  id: string
  status: string
  created_at: string
  finished_at: string | null
  error: string | null
  result: Record<string, number> | null
  progress: { stage?: string; done?: number; total?: number }
}

export interface SemanticState {
  /** ready · off · problem — 서버 화면의 「뜻으로 찾기」 와 같은 판단. */
  state: string
  problem: string | null
  model: string
  kb_per_object: number
  min_similarity: number
  /** 지난 큰 계산이 초당 바꾼 조각 수 — 이 서버의 속도. 아직 모르면 null. */
  per_second: number | null
  types: SemanticType[]
  /** 가장 최근의 계산 — 돌거나 기다리는 것 포함. */
  job: SemanticJob | null
}

export const semanticApi = {
  get: () => api.get<SemanticState>('/semantic'),
  /** 대상 통째로 — 타입 slug → 고른 칸(null 이면 자동, 축만). 여기 없는 타입은 뺀다. */
  setTargets: (targets: Record<string, string[] | null>) =>
    api.put<SemanticState>('/semantic/targets', {
      targets: Object.entries(targets).map(([type_slug, fields]) => ({ type_slug, fields })),
    }),
  sync: () => api.post<SemanticState>('/semantic/sync', {}),
}
