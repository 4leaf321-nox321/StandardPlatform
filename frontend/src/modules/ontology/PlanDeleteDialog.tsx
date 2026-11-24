/**
 * 속성 · 관계 종류 삭제 — **서버의 미리 보기를 읽는다**(`GET /ontology/delete-plan`).
 *
 * 정의 점검(ADR 0092)이 후보 줄에서 바로 여는 창이다. 타입은 영구 삭제를 함께 묻는
 * `TypeDeleteDialog` 가 맡고, 여기는 그 밖의 둘 — 막는 것 · 함께 사라지는 것 · 남는 것을 같은
 * 함수(`PlanText`)로 적는다. 막는 것이 있으면 삭제 단추가 서지 않는다.
 */

import { useEffect, useState } from 'react'

import { ontologyApi } from '@/modules/ontology/api'
import type { DeletePlan } from '@/modules/ontology/api'
import { PlanText } from '@/modules/ontology/TypeDeleteDialog'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'

interface PlanDeleteDialogProps {
  kind: 'property' | 'relation_type'
  /** 속성이면 그 타입, 관계 종류면 그 종류의 slug. */
  slug: string
  propertyKey?: string
  label: string
  onDone: () => void
  onClose: () => void
}

export function PlanDeleteDialog({
  kind,
  slug,
  propertyKey,
  label,
  onDone,
  onClose,
}: PlanDeleteDialogProps) {
  const [plan, setPlan] = useState<DeletePlan | null>(null)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    let alive = true
    ontologyApi
      .deletePlan(kind, slug, propertyKey)
      .then((got) => alive && setPlan(got))
      .catch((caught: unknown) => {
        if (alive) setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      })
    return () => {
      alive = false
    }
  }, [kind, slug, propertyKey])

  const blocked = (plan?.blocking.length ?? 0) > 0
  const what = kind === 'property' ? '속성' : '관계 종류'

  return (
    <ConfirmDialog
      open
      destructive
      title={`${label} ${what}을(를) 삭제합니다`}
      description={<PlanText plan={plan} error={error} />}
      confirmLabel="삭제"
      confirmDisabled={plan === null || blocked}
      onConfirm={async () => {
        if (kind === 'property' && propertyKey) {
          await ontologyApi.removeProperty(slug, propertyKey)
        } else {
          await ontologyApi.removeRelationType(slug)
        }
        onDone()
      }}
      onClose={onClose}
    />
  )
}
