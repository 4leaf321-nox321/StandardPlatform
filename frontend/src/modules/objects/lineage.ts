/**
 * 계보(ADR 0041) — 상세의 「계보」 패널 · 목록의 「낡은 것만」 · 조건 칩이 **같은 판정**을 쓴다.
 *
 * 계보 관계 종류는 관계 종류의 `lineage_source`(원천 끝)로 정해진다. 타입이 그 끝에 설 수 있나는
 * 관계 종류의 끝 제약(`src_type_slugs` · `dst_type_slugs`, 인터페이스면 구현 타입)이 정한다 —
 * 화면마다 따로 적으면 상세에는 패널이 서는데 목록에는 「낡은 것만」 이 안 서는 식으로 갈린다.
 */

import type { Condition, ConditionOp } from '@/modules/objects/api'
import { endAllows } from '@/modules/ontology/interfaces'
import type { ObjectInterface, RelationType } from '@/modules/ontology/api'

/**
 * 조건의 칸 — 목록의 객체 **자신**이 계보로 낡았나(서버 `conditions.LINEAGE_FIELD`). 그 타입에 같은
 * 키의 속성이 있으면 속성이 이긴다(서버와 같다).
 */
export const LINEAGE_FIELD = 'lineage'

/** 「낡은 것만」 — 바로 · 위에서 낡은 것. 주소는 `f.lineage.eq=stale`. */
export const STALE_ONLY: Condition = { field: LINEAGE_FIELD, op: 'eq', value: 'stale' }

/** 보통 조건(제외 묶음이 아닌)의 계보 조건인가. */
export function isLineageCondition(one: Condition): boolean {
  return !one.exclude && one.field === LINEAGE_FIELD
}

/** 「낡은 것만」 이 걸려 있나 — 토글이 이것으로 켜져 보인다. */
export function hasStaleOnly(conditions: Condition[]): boolean {
  return conditions.some(
    (one) => isLineageCondition(one) && one.op === STALE_ONLY.op && one.value === STALE_ONLY.value,
  )
}

/**
 * 「낡은 것만」 을 켜고 끈다 — 켜면 보통 조건의 다른 계보 조건(「바로 낡은 것만」 등)을 대신하고,
 * 끄면 그 조건 하나만 뺀다. 제외 묶음 안의 계보 조건은 건드리지 않는다(묶음의 뜻이 바뀐다).
 */
export function withStaleOnly(conditions: Condition[], on: boolean): Condition[] {
  if (on) return [...conditions.filter((one) => !isLineageCondition(one)), { ...STALE_ONLY }]
  return conditions.filter(
    (one) =>
      !(isLineageCondition(one) && one.op === STALE_ONLY.op && one.value === STALE_ONLY.value),
  )
}

/** 조건 칩의 말 — 칸 이름 없이 읽히게. */
const CONDITION_TEXT: Partial<Record<ConditionOp, Record<string, string>>> = {
  eq: { stale: '낡은 것만', direct: '바로 낡은 것만', fresh: '낡지 않은 것' },
  ne: { stale: '낡지 않은 것', direct: '바로 낡지 않은 것', fresh: '낡은 것만' },
}

/** 계보 조건의 칩 — 모르는 연산 · 값이면 null(부르는 쪽이 「계보 = 값」 으로 그대로 적는다). */
export function lineageConditionText(one: Condition): string | null {
  return CONDITION_TEXT[one.op]?.[one.value] ?? null
}

/** 계보 관계 종류 — 원천 끝이 정해진 것. */
export function lineageKinds(relationTypes: RelationType[]): RelationType[] {
  return relationTypes.filter((one) => one.lineage_source === 'src' || one.lineage_source === 'dst')
}

/** 계보 관계 종류의 (원천 끝, 파생물 끝)에 적힌 slug. */
function endsOf(kind: RelationType): { source: string[] | null; derived: string[] | null } {
  return kind.lineage_source === 'src'
    ? { source: kind.src_type_slugs, derived: kind.dst_type_slugs }
    : { source: kind.dst_type_slugs, derived: kind.src_type_slugs }
}

/** 이 타입이 어느 계보 관계 종류의 끝(원천 · 파생물)에 설 수 있나 — 상세의 「계보」 패널. */
export function inLineage(
  typeSlug: string,
  relationTypes: RelationType[],
  interfaces: ObjectInterface[],
): boolean {
  return lineageKinds(relationTypes).some((kind) => {
    const ends = endsOf(kind)
    return (
      endAllows(ends.source, typeSlug, interfaces) || endAllows(ends.derived, typeSlug, interfaces)
    )
  })
}

/** 이 타입이 계보의 **파생물** 끝에 설 수 있나 — 목록의 「낡은 것만」(낡는 것은 파생물이다). */
export function canBeDerived(
  typeSlug: string,
  relationTypes: RelationType[],
  interfaces: ObjectInterface[],
): boolean {
  return lineageKinds(relationTypes).some((kind) =>
    endAllows(endsOf(kind).derived, typeSlug, interfaces),
  )
}
