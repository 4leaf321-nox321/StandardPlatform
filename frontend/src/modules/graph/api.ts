/**
 * 지식 그래프 API — **읽기만.**
 *
 * 관계를 잇고 끊는 것은 `modules/objects/api` 가 한다. 그리는 화면에 쓰기가 들어가면
 * 「그림에서 선을 지웠는데 상세에는 남아 있는」 어긋남이 생긴다.
 */

import type { LogCount } from '@/modules/objects/api'
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
  /** 선 굵기를 찍어 둔 때(ADR 0038) — 비면 지금 센 것이거나 아직 안 센 것(`counting`). */
  counted_at?: string | null
  /** 선 굵기를 아직 안 찍었다 — 세는 중. 그동안 선의 수는 0 이 아니라 「모름」. */
  counting?: boolean
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
  /** 유효 기간의 시작일(포함, ADR 0039) — 유효 기간을 받는 관계 종류의 선만. */
  valid_from?: string | null
  /** 유효 기간의 끝날(제외 — 그날부터 무효). */
  valid_to?: string | null
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
  /** 이 그림의 기준일(ADR 0039) — 그날 유효한 관계만 그렸다. */
  as_of?: string | null
  /** 시작점을 가리키는 **기록**(ADR 0011) — 이웃으로 안 싣고 수로 온다. */
  log_counts?: LogCount[]
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
  as_of?: string | null
}

export interface SubgraphQuery {
  types: string[]
  relations?: string[]
  q?: string
  limit?: number
  offset?: number
  /** 기준일(`YYYY-MM-DD`, ADR 0039) — 그날 유효한 관계만. 비우면 오늘. */
  asOf?: string | null
}

export interface SearchHit {
  id: string
  label: string
  key: string | null
  type_slug: string
  type_label: string
}

/** 길 하나 — `edges[k]` 가 `nodes[k]` 와 `nodes[k+1]` 을 잇는다. */
export interface PathSteps {
  nodes: string[]
  edges: string[]
}

/** 두 객체 사이의 가장 짧은 길들. 못 찾았어도 출발 · 도착은 `nodes` 에 든다. */
export interface PathResult {
  source: string
  target: string
  found: boolean
  /** 선 수. 못 찾았으면 null. */
  length: number | null
  paths: PathSteps[]
  nodes: GraphNode[]
  edges: GraphEdge[]
  max_depth: number
  /** 찾느라 들른 노드 수. */
  visited: number
  /** 상한에 닿아 덜 찾았다 — **못 찾았어도 「길이 없다」 가 아니다.** */
  truncated: boolean
  /** 이 길의 기준일(ADR 0039) — 그날 유효한 관계만 따라갔다. */
  as_of?: string | null
}

export interface PathQuery {
  source: string
  target: string
  relations?: string[]
  types?: string[]
  /** 기준일(`YYYY-MM-DD`, ADR 0039) — 비우면 오늘. */
  asOf?: string | null
}

export interface NeighborhoodQuery {
  focus: string
  depth?: number
  fanout?: number
  limit?: number
  relations?: string[]
  types?: string[]
  /** 기준일(`YYYY-MM-DD`, ADR 0039) — 비우면 오늘. */
  asOf?: string | null
}

function neighborhoodQuery(query: NeighborhoodQuery): string {
  const params = new URLSearchParams({ focus: query.focus })
  if (query.depth !== undefined) params.set('depth', String(query.depth))
  if (query.fanout !== undefined) params.set('fanout', String(query.fanout))
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  if (query.relations?.length) params.set('relations', query.relations.join(','))
  if (query.types?.length) params.set('types', query.types.join(','))
  if (query.asOf) params.set('as_of', query.asOf)
  return `?${params.toString()}`
}

function subgraphQuery(query: SubgraphQuery): string {
  const params = new URLSearchParams({ types: query.types.join(',') })
  if (query.relations?.length) params.set('relations', query.relations.join(','))
  if (query.q) params.set('q', query.q)
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  if (query.offset) params.set('offset', String(query.offset))
  if (query.asOf) params.set('as_of', query.asOf)
  return `?${params.toString()}`
}

/**
 * 정적 그리기의 재료(ADR 0045) — 서버의 `WholeOut`(`graph/schemas.py`)과 같은 모양.
 *
 * 생성 타입(`@/shared/api/schema`)을 쓰지 않는 까닭은 `objects/api.ts` 의 「채울 곳」 과 같다 — 그
 * 파일은 커밋하지 않는 생성물이라 CI 의 빌드 단계에는 아직 없다.
 */
export interface WholeType {
  slug: string
  label: string
  /** 이 그림에 실린(거절이면 센) 그 타입의 노드 수. */
  count: number
}

export interface WholeRelation {
  slug: string
  label: string
  inverse_label: string
  directed: boolean
  count: number
}

/** 노드 — **열 배열.** i 번째 노드는 각 배열의 i 번째 값이다. */
export interface WholeNodes {
  ids: string[]
  /** `types` 표의 번호. */
  type: number[]
  label: string[]
  /** 씨앗에서 몇 단계(씨앗 = 0) — 처음 닿은 단계. */
  depth: number[]
  /** 그 노드를 처음 데려온 노드의 번호. 씨앗은 -1. */
  parent: number[]
  /** 서버가 정한 자리 — **지금은 늘 비어 있다**(ADR 0045 「다음」). 있으면 그대로 그린다. */
  x?: number[] | null
  y?: number[] | null
}

/** 선 — `src[k]` → `dst[k]` 가 `relations[rel[k]]` 종류다. */
export interface WholeEdges {
  src: number[]
  dst: number[]
  rel: number[]
}

/** 상한을 넘어 그리지 않았다 — 무엇이 얼마나 많은지. */
export interface WholeOver {
  /** `nodes` · `edges`. */
  what: string
  count: number
  /** 거짓이면 「count 개 이상」 — 넘은 자리에서 세기를 멈췄다. */
  exact: boolean
  limit: number
  types: WholeType[]
  /** 단계별 노드 수(센 데까지). */
  layers: number[]
  relations: WholeRelation[]
}

export interface Whole {
  /** `focus` · `types`. */
  seed: string
  focus: string | null
  seed_types: string[]
  depth: number
  records: boolean
  as_of?: string | null
  max_nodes: number
  max_edges: number
  types: WholeType[]
  relations: WholeRelation[]
  nodes: WholeNodes
  edges: WholeEdges
  over?: WholeOver | null
}

export interface WholeQuery {
  /** 객체 하나에서 — `types` 와 둘 중 하나. */
  focus?: string
  /** 타입(들)의 인스턴스 전부에서. */
  types?: string[]
  /** 씨앗에서 몇 단계. 0 이면 씨앗만. */
  depth: number
  relations?: string[]
  /** 이웃 타입 — 탐색의 「이웃 타입」 거르기와 같은 뜻. */
  neighborTypes?: string[]
  /** 기준일(`YYYY-MM-DD`, ADR 0039) — 비우면 오늘. */
  asOf?: string | null
}

function wholeQuery(query: WholeQuery): string {
  const params = new URLSearchParams({ depth: String(query.depth) })
  if (query.focus) params.set('focus', query.focus)
  if (query.types?.length) params.set('types', query.types.join(','))
  if (query.relations?.length) params.set('relations', query.relations.join(','))
  if (query.neighborTypes?.length) params.set('neighbor_types', query.neighborTypes.join(','))
  if (query.asOf) params.set('as_of', query.asOf)
  return `?${params.toString()}`
}

// --- 전체 지도(ADR 0046) --------------------------------------------------------
//
// 서버 `graph/schemas.py` 의 `Map*Out` 과 같은 모양(생성 타입을 안 쓰는 까닭은 위 「정적 그리기」 와
// 같다). 기준정보는 판에 적힌 자리, 기록은 허브 원반 안의 id 해시 자리 — 멀리서는 원반 · 밀도, 확대하면
// 상자 안의 점(5만까지).

export interface MapType {
  slug: string
  label: string
  /** 기록 타입이면 참 — 원반 안의 점. 아니면 기준정보(점). */
  record: boolean
  /** 기록 타입이면 이 판의 허브 칸(비면 미지정 — 모든 기록이 미지정 자리에). */
  hub_key?: string | null
  hub_label?: string | null
  /** 이 지도에서 보이는 수. */
  count: number
}

export interface MapLayout {
  /** 판 번호 — 늘기만 한다. */
  version: number
  /** `full`(전체 배치) · `append`(매일 덧붙임). */
  kind: string
  laid_at: string
  full_version: number
  full_at: string
  by_timer: boolean
  masters: number
  moved_p50?: number | null
  moved_p95?: number | null
}

export interface MapJob {
  id: string
  kind: string
  status: string
}

export interface MapData {
  ready: boolean
  layout?: MapLayout | null
  /** 원반의 수 · 밀도가 언제 센 것인가(한 시간마다). */
  counted_at?: string | null
  /** 반지름 = scale x √(얼린 수). */
  scale: number
  /** 지도 틀 [x0, y0, x1, y1]. */
  bbox: number[]
  max_points: number
  types: MapType[]
  masters: { ids: string[]; type: number[]; label: string[]; x: number[]; y: number[] }
  /** 원반 — 허브(`hub` 가 masters 번호) 또는 미지정 자리(`slot` 이 types 번호). 수는 세 열. */
  discs: {
    x: number[]
    y: number[]
    r: number[]
    hub: number[]
    slot: number[]
    count_disc: number[]
    count_type: number[]
    count_n: number[]
  }
  /** 판 뒤에 생긴 기준정보를 가리켜 아직 자리가 없는 기록. */
  unplaced: { type: number; count: number }[]
  /** 선의 종류 — 지도에 나올 수 있는 것 전부(slug 차례 — 이 차례로 색을 정한다). 수는 `lines` 의 것. */
  relations: WholeRelation[]
  /** 보이는 기준정보끼리의 선 — 끝은 masters 번호, `rel` 은 relations 번호. */
  lines: WholeEdges
  job?: MapJob | null
  last_error?: string | null
  can_relayout: boolean
  /** 허브 칸 설정이 판과 다른 기록 타입(slug). */
  drift: string[]
}

export interface MapPoints {
  box: number[]
  expected: number
  over: boolean
  limit: number
  count: number
  types: string[]
  ids: string[]
  x: number[]
  y: number[]
  type: number[]
  labels?: string[] | null
  skipped: MapType[]
  /** 점의 선 — 점이 `lines_up_to` 이하일 때만. */
  lines?: MapPointLines | null
  lines_up_to: number
  /** 점은 문턱 이하인데 선이 너무 많아 싣지 않았다. */
  lines_over?: boolean
}

/**
 * 확대한 점의 선 — 끝 번호가 점 수(`count`)보다 작으면 그 점, 아니면 `masters[번호 - count]`
 * 기준정보. `rel` 은 `kinds`(slug) 번호 — 이름 · 색은 MapData.relations 에서 slug 로 찾는다.
 */
export interface MapPointLines {
  src: number[]
  dst: number[]
  rel: number[]
  kinds: string[]
  masters: string[]
}

export interface MapLink {
  id: string
  label: string
  type_slug: string
  count: number
  x: number
  y: number
}

export interface MapLinks {
  hub: string
  items: MapLink[]
  withheld: boolean
  counted_at?: string | null
}

export interface MapLocate {
  id: string
  /** `master` · `record` · `none`. */
  kind: string
  x?: number | null
  y?: number | null
  r?: number | null
  label: string
  type_slug: string
  reason?: string | null
}

export interface MapHubCandidate {
  key: string
  label: string
  target_label: string
  /** 가리킬 수 있는 타입 slug(인터페이스면 구현 타입들). */
  target_types: string[]
  required: boolean
  hubs: number
}

export interface MapHubKey {
  type_slug: string
  type_label: string
  /** `auto` · `admin` · `none`. */
  mode: string
  key: string | null
  auto_key: string | null
  used_key: string | null
  in_layout: boolean
  stale: boolean
  candidates: MapHubCandidate[]
}

/** 지도 상자 — 그래프 좌표. */
export interface MapBox {
  x0: number
  y0: number
  x1: number
  y1: number
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
  /** 두 객체 사이의 가장 짧은 길들 — 노드마다 자르지 않고 찾고, 상한에 닿으면 그렇다고 말한다. */
  path: (query: PathQuery) => {
    const params = new URLSearchParams({ source: query.source, target: query.target })
    if (query.relations?.length) params.set('relations', query.relations.join(','))
    if (query.types?.length) params.set('types', query.types.join(','))
    if (query.asOf) params.set('as_of', query.asOf)
    return api.get<PathResult>(`/graph/path?${params.toString()}`)
  },
  /** 한 타입의 인스턴스 전부 — **「전부」 를 묻는 사람에게 "N개 중 M개" 라고 답한다.** */
  subgraph: (query: SubgraphQuery) => api.get<Subgraph>(`/graph/subgraph${subgraphQuery(query)}`),
  /** 정적 그리기 — **자르지 않고 통째로.** 넘으면 그리지 않고 수를 말한다(`over`). */
  whole: (query: WholeQuery) => api.get<Whole>(`/graph/whole${wholeQuery(query)}`),
  /** 전체 지도 — 보이는 기준정보의 자리와 허브 원반(판 번호로 ETag — 브라우저가 다시 묻는다). */
  map: () => api.get<MapData>('/graph/map'),
  /** 상자 안의 기록 점 — 기대 수가 상한을 넘으면 `over`. */
  mapPoints: (box: MapBox, types: string[]) => {
    const params = new URLSearchParams({
      x0: String(box.x0),
      y0: String(box.y0),
      x1: String(box.x1),
      y1: String(box.y1),
    })
    if (types.length) params.set('types', types.join(','))
    return api.get<MapPoints>(`/graph/map/points?${params.toString()}`)
  },
  /** 허브의 주 참조 밖의 연결 — 공동 참조로 이어진 기준정보. */
  mapLinks: (hub: string) =>
    api.get<MapLinks>(`/graph/map/links?${new URLSearchParams({ hub }).toString()}`),
  /** 객체 하나의 자리 — 주소의 `at`. */
  mapLocate: (id: string) =>
    api.get<MapLocate>(`/graph/map/locate?${new URLSearchParams({ id }).toString()}`),
  /** 「전체 다시 배치」(시스템 관리자) — 작업이 된다. */
  mapRelayout: () => api.post<{ id: string; status: string }>('/graph/map/relayout'),
  /** 기록 타입마다 허브 칸. */
  hubKeys: () => api.get<MapHubKey[]>('/graph/map/hub-keys'),
  /** 허브 칸 설정(시스템 관리자) — 다음 전체 배치부터. */
  setHubKey: (typeSlug: string, body: { mode: 'auto' | 'key' | 'none'; key?: string | null }) =>
    api.put<MapHubKey>(`/graph/map/hub-keys/${encodeURIComponent(typeSlug)}`, body),
}
