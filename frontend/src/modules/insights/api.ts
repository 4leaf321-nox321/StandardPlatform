/**
 * 눈여겨볼 것(ADR 0032) — 플랫폼이 스스로 지표를 훑어 찾은 결론.
 *
 * 읽기는 누구나(그 지표의 셀이 든 부서를 모두 보는 사람에게만 서버가 보인다), 그만 보기 · 끄기 ·
 * 지금 훑기는 시스템 관리자.
 */

import { api } from '@/shared/api/client'

export type InsightKind = 'change' | 'worse' | 'high'

export interface Insight {
  id: string
  metric_slug: string
  metric_label: string
  kind: InsightKind
  kind_label: string
  recipe: string
  recipe_label: string
  title: string
  detail: Record<string, unknown>
  /** 세기(|ln 비|) x 근거(log10(1 + 건수)), 잠정이면 절반 — 줄 세우는 데만. */
  score: number
  first_seen_at: string
  last_seen_at: string
  /** 마지막 훑기에 다시 안 나왔다. */
  past: boolean
  dismissed: boolean
  /** 그 분석을 그 인자로 연다. */
  link: string
}

export interface Insights {
  items: Insight[]
  total: number
  scanned_at: string | null
}

export interface InsightQuery {
  metric?: string
  kind?: InsightKind
  days?: number
  include_past?: boolean
  include_dismissed?: boolean
  limit?: number
}

function query(params: InsightQuery): string {
  const out = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === false || value === '') continue
    out.set(key, String(value))
  }
  const text = out.toString()
  return text ? `?${text}` : ''
}

export const insightsApi = {
  list: (params: InsightQuery = {}) => api.get<Insights>(`/insights${query(params)}`),
  dismiss: (id: string, dismissed: boolean) =>
    api.put<Insight>(`/insights/${id}/dismissed`, { dismissed }),
  scan: (metric?: string) =>
    api.post<{ queued: number }>('/insights/scan', metric ? { metric } : {}),
  setMetric: (slug: string, off: boolean) =>
    api.put<{ slug: string; off: boolean }>(`/insights/metrics/${slug}`, { off }),
}
