/**
 * 타입 삭제 — **서버의 미리 보기를 읽는다**(`GET /ontology/delete-plan`).
 *
 * 예전 창은 살아 있는 객체 수만 보고 「삭제할 수 있습니다」 라고 했고, 누르면 서버가 지운 객체까지
 * 세어 거절했다 — 사람은 둘 중 무엇이 맞는지 알 수 없었다. 이제 삭제 경로와 같은 함수가 센 것을
 * 그대로 적는다. 지운 객체만 남았으면 그것까지 **영구 삭제**해야 지워진다(ADR 0008) — 수와 함께
 * 사라지는 것을 적고, 단추 이름이 그것을 말한다.
 */

import { useEffect, useState } from 'react'

import { ontologyApi } from '@/modules/ontology/api'
import type { DeletePlan } from '@/modules/ontology/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'

interface TypeDeleteDialogProps {
  type: { slug: string; label: string }
  /** 삭제됐다 — 목록을 다시 읽고 수정 창을 닫는다. */
  onDone: () => void
  onClose: () => void
}

export function TypeDeleteDialog({ type, onDone, onClose }: TypeDeleteDialogProps) {
  const [plan, setPlan] = useState<DeletePlan | null>(null)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    let alive = true
    ontologyApi
      .deletePlan('type', type.slug)
      .then((got) => alive && setPlan(got))
      .catch((caught: unknown) => {
        if (alive) setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      })
    return () => {
      alive = false
    }
  }, [type.slug])

  const purge = plan?.purge_deleted ?? 0
  const blocked = (plan?.blocking.length ?? 0) > 0

  return (
    <ConfirmDialog
      open
      destructive
      title={`${type.label} 타입을 삭제합니다`}
      description={<PlanText plan={plan} error={error} />}
      confirmLabel={purge > 0 ? '영구 삭제' : '삭제'}
      confirmDisabled={plan === null || blocked}
      onConfirm={async () => {
        await ontologyApi.removeType(type.slug, purge > 0)
        onDone()
      }}
      onClose={onClose}
    />
  )
}

function PlanText({ plan, error }: { plan: DeletePlan | null; error: Error | null }) {
  if (error) return <ErrorNotice error={error} />
  if (!plan) return <p>삭제하면 무엇이 사라지는지 확인하는 중입니다…</p>
  if (plan.blocking.length > 0) {
    return (
      <div className="space-y-2">
        <p>
          <b>지금은 삭제할 수 없습니다.</b> 먼저 할 일:
        </p>
        <ul className="list-disc space-y-1 pl-5">
          {plan.blocking.map((one) => (
            <li key={one.code}>{one.message}</li>
          ))}
        </ul>
      </div>
    )
  }
  return (
    <div className="space-y-2">
      {plan.purge_deleted > 0 && (
        <p>
          살아 있는 객체는 없지만 <b>지운 객체 {plan.purge_deleted}개</b>가 기록으로 남아 있습니다.
          타입을 삭제하려면 그것까지 <b>영구 삭제</b>해야 하고, <b>되돌릴 수 없습니다.</b>
        </p>
      )}
      <Lines title="함께 사라지는 것" lines={plan.removes} />
      <Lines title="남는 것" lines={plan.keeps} />
      <Lines title="알아 둘 것" lines={plan.warnings} />
      <p className="text-muted-foreground text-xs">
        삭제 직전의 정의는 스냅샷으로 남습니다 — 가져오기·이력에서 되살릴 수 있지만 객체는 스냅샷에
        없습니다.
      </p>
    </div>
  )
}

function Lines({ title, lines }: { title: string; lines: string[] }) {
  if (lines.length === 0) return null
  return (
    <div>
      <p className="font-medium">{title}</p>
      <ul className="list-disc space-y-0.5 pl-5">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  )
}
