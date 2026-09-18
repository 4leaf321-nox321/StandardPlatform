/**
 * 온톨로지 통째로 초기화 — **되돌릴 수 없는 일 앞의 세 겹.**
 *
 * 정의를 시험 삼아 몇 번 세워 보는 동안 타입은 금세 열댓 개가 되고, 그것을 하나씩
 * 지우는 일은 순서까지 맞춰야 해서(객체 → 관계 → 타입) 사람이 포기한다. 그래서 한
 * 번에 비우는 길이 필요하다. 그리고 그렇기 때문에 무섭다.
 *
 *   1. **무엇이 몇 건 사라지는지** 세어 보여 준다. 「정말 삭제하시겠습니까」 만 묻는
 *      창은 아무도 안 읽고 예를 누른다 — 읽을 것이 없어서다.
 *   2. **문구를 손으로 적어야** 단추가 살아난다. 실수로 누르는 것과 작정하고 하는 것
 *      사이에 글자 몇 개를 둔다.
 *   3. **되돌릴 수 있는 것과 없는 것을 가른다.** 정의는 스냅샷으로 남고, 데이터는
 *      안 돌아온다. 이 비대칭을 안 적으면 사람은 「되돌리면 되지」 로 읽는다.
 */

import { useEffect, useState } from 'react'
import { TriangleAlert } from 'lucide-react'

import { ontologyApi } from '@/modules/ontology/api'
import type { ResetPlan } from '@/modules/ontology/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Input } from '@/shared/components/ui/input'

interface Props {
  onClose: () => void
  onDone: () => void
}

export function ResetDialog({ onClose, onDone }: Props) {
  const [plan, setPlan] = useState<ResetPlan | null>(null)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<ResetPlan | null>(null)
  const [error, setError] = useState<Error | null>(null)

  // 열자마자 센다 — 무엇이 사라지는지 모르는 채로 단추를 보게 두지 않는다.
  useEffect(() => {
    let cancelled = false
    ontologyApi
      .reset({ apply: false })
      .then((found) => {
        if (!cancelled) setPlan(found)
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      })
    return () => {
      cancelled = true
    }
  }, [])

  const ready = Boolean(plan && typed.trim() === plan.confirm_phrase)
  const shown = (plan?.items ?? []).filter((one) => one.count > 0)

  async function run() {
    if (!plan) return
    setBusy(true)
    setError(null)
    try {
      const result = await ontologyApi.reset({ apply: true, confirm: typed.trim() })
      setDone(result)
      onDone()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-destructive flex items-center gap-2">
            <TriangleAlert className="size-4" />
            온톨로지 초기화
          </DialogTitle>
          <DialogDescription>
            정의와 <strong>그 정의에 매달린 데이터 전부</strong>를 삭제합니다. 부서·계정·공지·
            웹훅은 그대로 둡니다 — 그것들은 온톨로지가 아니라 이 설치 자체의 것입니다.
          </DialogDescription>
        </DialogHeader>

        {done ? (
          <div className="space-y-2 text-sm">
            <p>비웠습니다. 이제 정의를 처음부터 구성하거나, 가져오기로 한 번에 추가하면 됩니다.</p>
            <p className="text-muted-foreground">
              직전의 정의는 아래 <strong>정의 이력</strong> 맨 위에 남아 있습니다. 되돌리면 정의는
              돌아오지만 <strong>객체와 관계는 안 돌아옵니다.</strong>
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {plan === null ? (
              <p className="text-muted-foreground text-sm">무엇이 사라지는지 세는 중…</p>
            ) : shown.length === 0 ? (
              <p className="text-sm">지울 것이 없습니다. 이미 비어 있습니다.</p>
            ) : (
              <>
                {/* **무엇이 사라지는지 적는다.** 숫자가 있으면 숫자를 적는다. */}
                <ul className="space-y-1 rounded-md border p-3 text-sm">
                  {shown.map((one) => (
                    <li key={one.table} className="flex justify-between">
                      <span>{one.label}</span>
                      <span className="tabular-nums">{one.count.toLocaleString()}건</span>
                    </li>
                  ))}
                </ul>
                <p className="text-muted-foreground text-xs">
                  <strong>정의는 되돌릴 수 있습니다</strong>(직전 스냅샷이 이력에 남습니다).{' '}
                  <strong className="text-destructive">객체와 관계는 안 돌아옵니다.</strong> 남겨야
                  할 것이 있으면 먼저 목록에서 CSV 로 내보내세요.
                </p>
                <label className="block space-y-1 text-sm">
                  <span>
                    계속하려면 <code className="font-mono">{plan.confirm_phrase}</code> 를 적으세요
                  </span>
                  <Input
                    autoFocus
                    value={typed}
                    placeholder={plan.confirm_phrase}
                    onChange={(event) => setTyped(event.target.value)}
                  />
                </label>
              </>
            )}
          </div>
        )}

        <ErrorNotice error={error} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {done ? '닫기' : '취소'}
          </Button>
          {!done && shown.length > 0 && (
            <Button variant="destructive" disabled={!ready || busy} onClick={run}>
              {busy ? '비우는 중…' : `${plan?.total.toLocaleString()}건 삭제`}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
