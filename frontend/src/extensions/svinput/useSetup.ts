/**
 * 설정(역할 → 타입 · 칸)과 온톨로지 정의를 함께 든다 — 화면들이 같은 것을 묻는다.
 *
 * 타입 slug · 칸 키는 **서버의 설정에서** 온다. 화면에 박으면 형제 설치에서 깨진다(ADR 0051).
 * 연결 칸(`report_ref` · `issue_ref`)은 참조 칸이거나 `rel:<관계 종류>` 다 — `linkOf` 가 가른다.
 */

import { useMemo } from 'react'

import { objectApi } from '@/modules/objects/api'
import {
  ontologyApi,
  type ObjectType,
  type PropertyDef,
  type RelationType,
} from '@/modules/ontology/api'
import { useResource } from '@/shared/hooks/useResource'

import { roleOf, svApi, type RoleState, type Status } from './api'

export type TypeWithDefs = ObjectType & { properties: PropertyDef[] }

/** 연결의 모양 — 참조 칸(`property`) 또는 관계 종류(`relation`). */
export type Link =
  | { kind: 'property'; key: string }
  | { kind: 'relation'; slug: string; relation: RelationType | undefined }

export const REL = 'rel:'

export function parseLink(spec: string | undefined, relations: RelationType[]): Link | undefined {
  if (!spec) return undefined
  if (spec.startsWith(REL)) {
    const slug = spec.slice(REL.length)
    return { kind: 'relation', slug, relation: relations.find((one) => one.slug === slug) }
  }
  return { kind: 'property', key: spec }
}

export interface Setup {
  status: Status | null
  ready: boolean
  loading: boolean
  error: Error | null
  /** 역할 key → 설정된 타입(정의 포함). 설정이 없거나 타입이 지워졌으면 undefined. */
  typeOf: (role: string) => TypeWithDefs | undefined
  /** 역할의 칸 키 — 설정이 비면 undefined. 연결 칸은 `linkOf` 로 가른다. */
  fieldOf: (role: string, field: string) => string | undefined
  linkOf: (role: string, field: string) => Link | undefined
  role: (key: string) => RoleState | undefined
  relationTypes: RelationType[]
  reload: () => void
}

export function useSetup(): Setup {
  const status = useResource(() => svApi.status(), [])
  const schema = useResource(() => ontologyApi.schema(), [])
  const types = useMemo(
    () => new Map((schema.data?.types ?? []).map((one) => [one.slug, one as TypeWithDefs])),
    [schema.data],
  )
  const relationTypes = schema.data?.relation_types ?? []
  const data = status.data
  const fieldOf = (role: string, field: string) => roleOf(data, role)?.fields[field] ?? undefined
  return {
    status: data,
    ready: Boolean(data?.ready),
    loading: status.loading || schema.loading,
    error: status.error ?? schema.error,
    typeOf: (role) => {
      const slug = roleOf(data, role)?.type_slug
      return slug ? types.get(slug) : undefined
    },
    fieldOf,
    linkOf: (role, field) => parseLink(fieldOf(role, field), relationTypes),
    role: (key) => roleOf(data, key),
    relationTypes,
    reload: () => {
      status.reload()
      schema.reload()
    },
  }
}

/** 참조 칸 값(id 하나 또는 여럿)을 사람이 읽는 이름으로. */
export function refText(value: unknown, labels: Record<string, string> | undefined): string {
  const ids = Array.isArray(value) ? value : value == null ? [] : [value]
  return ids
    .map((one) => labels?.[String(one)] ?? String(one))
    .filter(Boolean)
    .join(' · ')
}

/**
 * 문제점(`child`)을 문서(`parent`)에 잇는다 — 연결의 모양대로. 참조 칸이면 그 칸에 더하고(여럿이면
 * 목록에 덧붙인다), 관계면 선을 긋는다(출발점은 관계 종류의 허용 타입이 정한다 — 둘 다 허용이면
 * 문서에서). **코어 API 로만** 쓴다.
 */
export async function attach(
  link: Link,
  parent: { typeSlug: string; id: string },
  child: { typeSlug: string; id: string; properties: Record<string, unknown> },
  childDefs: PropertyDef[],
): Promise<void> {
  if (link.kind === 'property') {
    const def = childDefs.find((one) => one.key === link.key)
    const before = child.properties[link.key]
    const next = def?.multi
      ? [...new Set([...(Array.isArray(before) ? before.map(String) : []), parent.id])]
      : parent.id
    await objectApi.update(child.typeSlug, child.id, { properties: { [link.key]: next } })
    return
  }
  const src = link.relation?.src_type_slugs
  const fromChild = Boolean(src && src.length && src.includes(child.typeSlug) && !src.includes(parent.typeSlug))
  if (fromChild) {
    await objectApi.addRelation(child.typeSlug, child.id, {
      relation: link.slug,
      dst_object_id: parent.id,
    })
  } else {
    await objectApi.addRelation(parent.typeSlug, parent.id, {
      relation: link.slug,
      dst_object_id: child.id,
    })
  }
}
