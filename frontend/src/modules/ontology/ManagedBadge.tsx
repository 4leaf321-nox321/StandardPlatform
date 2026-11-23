/**
 * 원천이 잠근 정의의 표시 — 「<원천> 관리」 와, 시스템 관리자에게 「잠금 해제」(ADR 0090).
 *
 * 원천(datarelay · 허브)이 보낸 정의는 그 원천만 고친다. 원천을 끊었거나 시험으로 보낸 것을 치울
 * 때는 그 잠금이 정의 · 객체를 영영 붙든다 — 그래서 사람이 여기서 푼다. **원천이 같은 정의를 다시
 * 보내면 다시 잠근다**(아직 보내는 중이면 다음 받기가 다시 잠그고 덮어쓴다) — 확인 창이 그것을
 * 먼저 말한다.
 */

import { useState } from 'react'

import { ontologyApi } from '@/modules/ontology/api'
import type { UnlockKind } from '@/modules/ontology/api'
import { useOptionalAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin } from '@/shared/auth/roles'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { Button } from '@/shared/components/ui/button'

const WHAT: Record<UnlockKind, string> = {
  types: '타입',
  'relation-types': '관계 종류',
  interfaces: '인터페이스',
}

export function ManagedBadge({
  kind,
  slug,
  label,
  owner,
  onUnlocked,
}: {
  kind: UnlockKind
  slug: string
  label: string
  /** 잠근 원천(`managed_by`). */
  owner: string
  onUnlocked: () => void
}) {
  const auth = useOptionalAuth()
  const [open, setOpen] = useState(false)
  return (
    // 표의 줄을 누르면 수정 창이 열린다 — 여기서 누른 것(확인 창 안까지)은 줄로 안 올린다.
    <span onClick={(event) => event.stopPropagation()}>
      <span className="ml-2 rounded border px-1.5 text-xs" title={`${owner} 가 보낸 정의`}>
        {owner} 관리
      </span>
      {isSystemAdmin(auth?.user) && (
        <Button
          variant="link"
          size="xs"
          className="ml-1 h-auto p-0 text-xs"
          onClick={() => setOpen(true)}
        >
          잠금 해제
        </Button>
      )}
      <ConfirmDialog
        open={open}
        title={`${label} 의 잠금 해제`}
        description={
          <div className="space-y-2">
            <p>
              이 {WHAT[kind]}는 <b>{owner}</b> 가 보낸 정의라 지금은 {owner} 만 수정합니다. 잠금을
              해제하면 이 화면에서 정의와 객체를 평소처럼 수정 · 삭제할 수 있습니다.
            </p>
            <p className="text-destructive">
              {owner} 가 아직 보내는 중이면 다음 적재가 다시 잠그고 덮어씁니다 — 원천 연동을
              해제했거나 시험으로 보낸 정의를 삭제할 때 사용합니다.
            </p>
            <p className="text-muted-foreground">감사 기록에 남습니다(시스템 관리자만).</p>
          </div>
        }
        confirmLabel="잠금 해제"
        destructive
        onConfirm={async () => {
          await ontologyApi.unlock(kind, slug)
          onUnlocked()
        }}
        onClose={() => setOpen(false)}
      />
    </span>
  )
}
