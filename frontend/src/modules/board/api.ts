/**
 * 게시판(ADR 0033) — 설치 전체가 함께 쓰는 글 목록.
 *
 * 읽기 · 작성은 누구나, 수정 · 삭제는 작성자와 시스템 관리자(`can_edit` — 서버가 다시 판정한다),
 * 고정은 시스템 관리자. 첨부는 글이 생긴 뒤 `attachmentApi` 로(`owner_table=board_posts`).
 */

import { api } from '@/shared/api/client'

/** 첨부가 글을 가리키는 표 이름 — 서버의 `POST_TABLE`. */
export const POST_TABLE = 'board_posts'

export interface PostSummary {
  id: string
  title: string
  author_id: string | null
  author_name: string
  pinned: boolean
  created_at: string
  edited_at: string | null
  comment_count: number
  attachment_count: number
}

export interface BoardComment {
  id: string
  body: string
  author_id: string | null
  author_name: string
  created_at: string
  edited_at: string | null
  can_edit: boolean
}

export interface Post extends PostSummary {
  body: string
  can_edit: boolean
  comments: BoardComment[]
}

export interface PostPage {
  items: PostSummary[]
  total: number
  limit: number
  offset: number
}

export interface PostQuery {
  q?: string
  mine?: boolean
  limit?: number
  offset?: number
}

function query(params: PostQuery): string {
  const out = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === false || value === '') continue
    out.set(key, String(value))
  }
  const text = out.toString()
  return text ? `?${text}` : ''
}

export const boardApi = {
  list: (params: PostQuery = {}) => api.get<PostPage>(`/board/posts${query(params)}`),
  get: (id: string) => api.get<Post>(`/board/posts/${id}`),
  create: (body: { title: string; body: string }) => api.post<Post>('/board/posts', body),
  /** 부분 수정 — 안 보낸 칸은 그대로. */
  update: (id: string, body: { title?: string; body?: string }) =>
    api.patch<Post>(`/board/posts/${id}`, body),
  remove: (id: string) => api.delete<void>(`/board/posts/${id}`),
  pin: (id: string, pinned: boolean) => api.put<Post>(`/board/posts/${id}/pinned`, { pinned }),
  comment: (postId: string, body: string) =>
    api.post<BoardComment>(`/board/posts/${postId}/comments`, { body }),
  updateComment: (id: string, body: string) =>
    api.patch<BoardComment>(`/board/comments/${id}`, { body }),
  removeComment: (id: string) => api.delete<void>(`/board/comments/${id}`),
}
