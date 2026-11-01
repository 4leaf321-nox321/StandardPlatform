/**
 * 주소 ↔ 조건. `f.<칸>.<연산>=<값>` — 붙여 넣으면 같은 목록이 선다. **제외 묶음**은
 * `nf<번호>.<칸>.<연산>=<값>` — 같은 번호끼리 AND 로 묶어 그 묶음에 맞는 것을 뺀다(ADR 0015).
 * **같은 관계 묶음**은 `fe<번호>.<칸>.<연산>=<값>`(제외 묶음 안이면 `nf<n>.fe<m>.`) — 같은 번호의
 * 조건이 같은 선 하나에서 함께 맞는다(ADR 0043). 서버의 `conditions.parse` · `key_of` 와 같다.
 *
 * 타입 목록과 인터페이스 목록이 **같은 규칙**으로 읽는다 — 두 벌이면 한쪽 주소만 붙여 넣어도
 * 같은 목록이 서는 상태가 깨진다.
 */

import type { Condition, ConditionOp } from '@/modules/objects/api'

/** `nf<번호>.`(제외 묶음) · `fe<번호>.`(같은 관계 묶음) — 둘 다면 제외 묶음이 앞. */
const GROUPED_KEY = /^(?:nf([0-9]{1,2})\.)?(?:fe([0-9]{1,2})\.)?(.+)\.([a-z]+)$/

/**
 * 조건 하나의 쿼리 키 — `f.<칸>.<연산>` · 제외 묶음이면 `nf<번호>.`, 같은 관계 묶음이면 `fe<번호>.` 로
 * 시작한다. 서버의 `key_of`. 목록 API · 주소 · 홈 위젯의 링크가 모두 이것으로 적는다(두 벌이면 한쪽만
 * 묶음을 잃는다).
 */
export function conditionKey(one: Condition): string {
  const head = `${one.exclude ? `nf${one.exclude}.` : ''}${one.edge ? `fe${one.edge}.` : ''}`
  return `${head || 'f.'}${one.field}.${one.op}`
}

/** 이 키가 조건(보통 · 제외 · 같은 관계)인가 — 주소를 다시 적을 때 지울 것. */
function isConditionKey(key: string): boolean {
  return key.startsWith('f.') || /^(nf|fe)[0-9]{1,2}\./.test(key)
}

export function conditionsFromParams(params: URLSearchParams): Condition[] {
  const out: Condition[] = []
  for (const [key, value] of params.entries()) {
    if (key.startsWith('f.')) {
      const dot = key.lastIndexOf('.')
      if (dot <= 2) continue
      out.push({ field: key.slice(2, dot), op: key.slice(dot + 1) as ConditionOp, value })
      continue
    }
    const grouped = GROUPED_KEY.exec(key)
    if (!grouped || !(grouped[1] || grouped[2])) continue
    const made: Condition = { field: grouped[3], op: grouped[4] as ConditionOp, value }
    if (grouped[1]) made.exclude = Number(grouped[1])
    if (grouped[2]) made.edge = Number(grouped[2])
    out.push(made)
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
