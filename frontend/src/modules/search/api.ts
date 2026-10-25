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

/** 게시판 글 하나(ADR 0033) — 객체가 아니라 사람이 쓴 글이라 따로 선다. */
export interface SearchPost {
  id: string
  title: string
  author_name: string
  created_at: string
  comment_count: number
  /** 본문에서 그 말이 든 자리 앞뒤 — 왜 걸렸나. */
  snippet: string
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
  /** 제목 · 본문에 그 말이 든 게시판 글, 새 글부터 다섯 — 좁히지 않은 첫 쪽에만. */
  posts?: SearchPost[]
  /** 그 말이 든 글 전부의 수 — 다섯보다 많으면 「게시판에서 모두 보기」. */
  posts_total?: number
}

export const searchApi = {
  find: (q: string, options: { type?: string | null; offset?: number } = {}) => {
    const params = new URLSearchParams({ q })
    if (options.type) params.set('type', options.type)
    if (options.offset) params.set('offset', String(options.offset))
    return api.get<SearchResult>(`/search?${params.toString()}`)
  },
}
