/** 웹훅 API — 변경을 바깥 시스템에 알리는 자리. 시스템 관리자만. */

import { api } from '@/shared/api/client'

export interface Webhook {
  id: string
  name: string
  url: string
  /** 비밀은 다시 보여 주지 않는다 — 있는지만. */
  has_secret: boolean
  /** `object.*` · `object.relation.add` · `*` 같은 패턴. */
  events: string[]
  /** 객체 이벤트를 이 타입들로만. null 이면 전부. */
  type_slugs: string[] | null
  is_active: boolean
  last_status: 'ok' | 'failed' | null
  last_at: string | null
  created_at: string
}

export interface Delivery {
  id: string
  event: string
  status: 'pending' | 'ok' | 'failed'
  attempts: number
  response_code: number | null
  last_error: string | null
  payload: Record<string, unknown>
  created_at: string
  delivered_at: string | null
}

export interface WebhookWrite {
  name: string
  url: string
  secret?: string
  events: string[]
  type_slugs?: string[] | null
  is_active?: boolean
}

export const webhookApi = {
  list: () => api.get<Webhook[]>('/webhooks'),
  create: (body: WebhookWrite) => api.post<Webhook>('/webhooks', body),
  update: (id: string, body: Partial<WebhookWrite>) => api.patch<Webhook>(`/webhooks/${id}`, body),
  remove: (id: string) => api.delete<void>(`/webhooks/${id}`),
  /** 지금 이 자리에서 한 번 쏘고 결과를 돌려준다. */
  test: (id: string) => api.post<Delivery>(`/webhooks/${id}/test`),
  deliveries: (id: string) => api.get<Delivery[]>(`/webhooks/${id}/deliveries`),
  retry: (id: string, deliveryId: string) =>
    api.post<Delivery>(`/webhooks/${id}/deliveries/${deliveryId}/retry`),
}
