/**
 * 밤 그래프 계산(ADR 0067)의 읽기 — 기대는 수 · 다리 역할 · 무리 · 비슷한 것.
 *
 * 값은 **밤에 센 사본**이다 — 화면은 늘 「언제 센 값인지」(`run.computed_at`)를 함께 적는다.
 */

import { api } from '@/shared/api/client'

export interface CalcRun {
  id: number
  computed_at: string | null
  engine: string
  stats: Record<string, unknown>
}

export interface CalcScore {
  /** `depends_top`(기대는 수 — 관계마다) · `bridge`(다리 역할, 가장 큰 다리 대비). */
  metric: string
  relation: string
  value: number
  rank: number
  /** 순위가 매겨진 객체 수(값이 0 인 것은 순위가 없다). */
  of: number
}

export interface CalcObjectCluster {
  cluster: number
  rank: number
  size: number | null
  label: string | null
  representative_id: string | null
  types: Record<string, number>
}

export interface CalcSimilar {
  id: string
  label: string
  key: string | null
  type_slug: string
  relation: string
  rank: number
  /** 끝까지 펼친 구성의 자카드(0 ~ 1). */
  score: number
}

export interface CalcObject {
  run: CalcRun | null
  scores: CalcScore[]
  cluster: CalcObjectCluster | null
  similar: CalcSimilar[]
}

export const graphCalcApi = {
  object: (objectId: string) =>
    api.get<CalcObject>(`/graph/calc/object/${encodeURIComponent(objectId)}`),
}
