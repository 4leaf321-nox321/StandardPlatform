/**
 * 주소 ↔ 조건. `f.<칸>.<연산>=<값>` — 붙여 넣으면 같은 목록이 선다.
 *
 * 타입 목록과 인터페이스 목록이 **같은 규칙**으로 읽는다 — 두 벌이면 한쪽 주소만 붙여 넣어도
 * 같은 목록이 서는 상태가 깨진다.
 */

import type { Condition, ConditionOp } from '@/modules/objects/api'

export function conditionsFromParams(params: URLSearchParams): Condition[] {
  const out: Condition[] = []
  for (const [key, value] of params.entries()) {
    if (!key.startsWith('f.')) continue
    const dot = key.lastIndexOf('.')
    if (dot <= 2) continue
    out.push({ field: key.slice(2, dot), op: key.slice(dot + 1) as ConditionOp, value })
  }
  return out
}

/** 조건 · 검색어를 주소에 다시 적는다. 다른 키(쪽 · 타입 좁히기 등)는 그대로 둔다. */
export function withConditions(
  params: URLSearchParams,
  next: { q: string; conditions: Condition[] },
): URLSearchParams {
  const copy = new URLSearchParams(params)
  // 지우면서 돌면 건너뛰는 키가 생긴다 — 먼저 복사해 둔다.
  for (const key of Array.from(copy.keys())) if (key.startsWith('f.')) copy.delete(key)
  copy.delete('q')
  if (next.q) copy.set('q', next.q)
  for (const one of next.conditions) copy.append(`f.${one.field}.${one.op}`, one.value)
  return copy
}
