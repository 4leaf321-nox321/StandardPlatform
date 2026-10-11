/**
 * 주소 ↔ 조건. `f.<칸>.<연산>=<값>` — 붙여 넣으면 같은 목록이 선다. **제외 묶음**은
 * `nf<번호>.<칸>.<연산>=<값>` — 같은 번호끼리 AND 로 묶어 그 묶음에 맞는 것을 뺀다(ADR 0015).
 *
 * 타입 목록과 인터페이스 목록이 **같은 규칙**으로 읽는다 — 두 벌이면 한쪽 주소만 붙여 넣어도
 * 같은 목록이 서는 상태가 깨진다.
 */

import type { Condition, ConditionOp } from '@/modules/objects/api'

const EXCLUDE_KEY = /^nf([0-9]{1,2})\.(.+)\.([a-z]+)$/

/**
 * 조건 하나의 쿼리 키 — `f.<칸>.<연산>` · 제외 묶음이면 `nf<번호>.<칸>.<연산>`. 서버의 `key_of`.
 * 목록 API · 주소 · 홈 위젯의 링크가 모두 이것으로 적는다(두 벌이면 한쪽만 제외를 잃는다).
 */
export function conditionKey(one: Condition): string {
  return `${one.exclude ? `nf${one.exclude}` : 'f'}.${one.field}.${one.op}`
}

/** 이 키가 조건(보통 · 제외)인가 — 주소를 다시 적을 때 지울 것. */
function isConditionKey(key: string): boolean {
  return key.startsWith('f.') || EXCLUDE_KEY.test(key)
}

export function conditionsFromParams(params: URLSearchParams): Condition[] {
  const out: Condition[] = []
  for (const [key, value] of params.entries()) {
    const excluded = EXCLUDE_KEY.exec(key)
    if (excluded) {
      out.push({
        field: excluded[2],
        op: excluded[3] as ConditionOp,
        value,
        exclude: Number(excluded[1]),
      })
      continue
    }
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
  for (const key of Array.from(copy.keys())) if (isConditionKey(key)) copy.delete(key)
  copy.delete('q')
  if (next.q) copy.set('q', next.q)
  for (const one of next.conditions) copy.append(conditionKey(one), one.value)
  return copy
}
