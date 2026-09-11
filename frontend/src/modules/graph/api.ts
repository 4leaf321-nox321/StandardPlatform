/**
 * 지식 그래프 API — **읽기만.**
 *
 * 관계를 잇고 끊는 것은 `modules/objects/api` 가 한다. 그리는 화면에 쓰기가 들어가면
 * 「그림에서 선을 지웠는데 상세에는 남아 있는」 어긋남이 생긴다.
 */

import { api } from '@/shared/api/client'

/** 정의 그래프의 노드 — 타입 하나. */
export interface TypeNode {
  slug: string
  label: string
  icon: string
  group_slug: string | null
  /** 보이는 객체 수. 남의 부서 것은 안 센다. */
  count: number
}

/** 정의 그래프의 선 — (관계 종류, 출발 타입, 도착 타입). */
export interface TypeEdge {
  relation: string
  label: string
  directed: boolean
  src_type: string
  dst_type: string
  /** 실제로 걸린 수. **0 이면 정의만 있고 아직 아무것도 안 이어진 것.** */
  count: number
}

export interface Overview {
  nodes: TypeNode[]
  edges: TypeEdge[]
  object_count: number
  edge_count: number
}

export interface GraphNode {
  id: string
  label: string
  key: string | null
  type_slug: string
  type_label: string
  status: string
  /** NULL 은 전역. 색 기준 「부서별」 이 쓴다. */
  owner_workspace_slug: string | null
  /** 이 노드에 걸린 보이는 관계의 수 — 잘렸으면 화면의 수보다 크다. */
  degree: number
  /** **화면에 실린 것보다 관계가 더 있다.** 「+N 더」 를 적는 근거. */
  truncated: boolean
}

export interface GraphEdge {
  id: string
  relation: string
  label: string
  /** 역방향의 말. hover 에 「개발사 ↔ 개발함」 으로 둘 다 보인다. */
  inverse_label: string
  directed: boolean
  src: string
  dst: string
}

export interface Neighborhood {
  focus: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  depth: number
  fanout: number
  node_limit: number
  /** 상한 때문에 어딘가가 잘렸다. */
  truncated: boolean
}

/** 한 타입(들)의 인스턴스 전부 — 쪽 단위, 상한 안에서. */
export interface Subgraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  /** 조건에 맞는 전체 수. 실린 수보다 크면 화면이 「N개 중 M개」 라고 말한다. */
  total: number
  limit: number
  offset: number
  truncated: boolean
}

export interface SubgraphQuery {
  types: string[]
  relations?: string[]
  q?: string
  limit?: number
  offset?: number
}

export interface SearchHit {
  id: string
  label: string
  key: string | null
  type_slug: string
  type_label: string
}

export interface NeighborhoodQuery {
  focus: string
  depth?: number
  fanout?: number
  limit?: number
  relations?: string[]
  types?: string[]
}

function neighborhoodQuery(query: NeighborhoodQuery): string {
  const params = new URLSearchParams({ focus: query.focus })
  if (query.depth !== undefined) params.set('depth', String(query.depth))
  if (query.fanout !== undefined) params.set('fanout', String(query.fanout))
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  if (query.relations?.length) params.set('relations', query.relations.join(','))
  if (query.types?.length) params.set('types', query.types.join(','))
  return `?${params.toString()}`
}

function subgraphQuery(query: SubgraphQuery): string {
  const params = new URLSearchParams({ types: query.types.join(',') })
  if (query.relations?.length) params.set('relations', query.relations.join(','))
  if (query.q) params.set('q', query.q)
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  if (query.offset) params.set('offset', String(query.offset))
  return `?${params.toString()}`
}

export const graphApi = {
  /** 타입과 관계 종류만 — 객체가 백만 개여도 답은 (타입 수) 노드다. */
  overview: () => api.get<Overview>('/graph/overview'),
  /** 타입을 가리지 않고 시작점을 찾는다. */
  search: (q: string) =>
    api.get<SearchHit[]>(`/graph/search?${new URLSearchParams({ q }).toString()}`),
  /** 시작점에서 depth 단계 — **서버가 상한을 강제한다.** */
  neighborhood: (query: NeighborhoodQuery) =>
    api.get<Neighborhood>(`/graph/neighborhood${neighborhoodQuery(query)}`),
  /** 한 타입의 인스턴스 전부 — **「전부」 를 묻는 사람에게 "N개 중 M개" 라고 답한다.** */
  subgraph: (query: SubgraphQuery) => api.get<Subgraph>(`/graph/subgraph${subgraphQuery(query)}`),
}
