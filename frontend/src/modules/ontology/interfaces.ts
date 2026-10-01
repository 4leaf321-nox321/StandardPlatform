/**
 * 인터페이스의 공통 속성 — **상위 인터페이스에서 이어받은 것까지**(ADR 0006).
 *
 * 스키마는 인터페이스마다 제 공통 속성만 싣는다. 구현 타입이 실제로 갖는 것은 상위까지 모은
 * 계약이고, 서버의 목록 범위(`objects/scope.py`)도 그것으로 조건을 받는다 — 화면이 제 것만
 * 보이면 걸 수 있는 조건을 못 고른다.
 */

import type { ObjectInterface, PropertyDef } from '@/modules/ontology/api'

export type InterfaceWithProperties = ObjectInterface & { properties: PropertyDef[] }

/** 제 것이 앞, 그다음 상위를 넓이 우선으로. 같은 키는 처음 만난 것(고리는 서버가 막는다). */
export function commonProperties(
  iface: InterfaceWithProperties,
  all: InterfaceWithProperties[],
): PropertyDef[] {
  const bySlug = new Map(all.map((one) => [one.slug, one]))
  const seen = new Set<string>()
  const visited = new Set<string>()
  const out: PropertyDef[] = []
  const queue = [iface.slug]
  while (queue.length) {
    const slug = queue.shift() as string
    if (visited.has(slug)) continue
    visited.add(slug)
    const one = slug === iface.slug ? iface : bySlug.get(slug)
    if (!one) continue
    for (const def of one.properties) {
      if (seen.has(def.key)) continue
      seen.add(def.key)
      out.push(def)
    }
    queue.push(...one.extends_slugs)
  }
  return out
}

/**
 * 인터페이스 목록의 기본 열 — `list_view.columns` 를 안 정했을 때. **이름 · 타입 · 식별자 ·
 * 공통 속성 앞의 셋.** 여러 타입이 섞이는 목록이라 「타입」 열이 없으면 줄이 무엇인지 모른다.
 * 목록 화면과 편집기의 미리 보기가 같은 것을 써야 미리 보기가 거짓말을 안 한다.
 */
export function defaultInterfaceColumns(defs: PropertyDef[]): string[] {
  return [
    'label',
    'type',
    'key',
    ...defs
      .filter((one) => one.data_type !== 'file' && one.data_type !== 'text_long')
      .slice(0, 3)
      .map((one) => `properties.${one.key}`),
  ]
}
