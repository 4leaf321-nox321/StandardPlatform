/** 전역 검색 API — 한 경로뿐이다. 좁히는 것도 같은 경로가 한다. */

import { api } from '@/shared/api/client'

export interface SearchTypeCount {
  type_slug: string
  type_label: string
  icon: string
  count: number
  /** `log` 면 기록(ADR 0011) — 섞어 볼 때 줄에 안 섞고 이 수로만 선다. */
  usage?: 'axis' | 'log'
}

export interface SearchHit {
  id: string
  type_slug: string
  type_label: string
  icon: string
  label: string
  key: string | null
  /** label · key · alias — **왜 이게 나왔나.** */
  matched: string
  matched_text: string
}

export interface SearchResult {
  q: string
  /** 줄로 볼 수 있는 수 — 섞어 볼 때는 기록을 뺀 수. */
  total: number
  types: SearchTypeCount[]
  /** 섞어 볼 때 줄에 안 섞은 기록의 수 — 그 타입을 고르면 나온다. */
  records?: number
  items: SearchHit[]
  limit: number
  offset: number
  /** 이 글자 수부터 찾는다. 화면이 「두 글자 이상」 이라고 말할 근거. */
  min_query: number
}

export const searchApi = {
  find: (q: string, options: { type?: string | null; offset?: number } = {}) => {
    const params = new URLSearchParams({ q })
    if (options.type) params.set('type', options.type)
    if (options.offset) params.set('offset', String(options.offset))
    return api.get<SearchResult>(`/search?${params.toString()}`)
  },
}
