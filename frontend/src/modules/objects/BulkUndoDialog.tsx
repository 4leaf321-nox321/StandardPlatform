/**
 * 같이 바뀐 것 한 번에 되돌리기 — **계획 먼저.**
 *
 * 한 번에 수백 건을 바꾸는 길이 있는데 되돌리는 길이 한 건씩뿐이면, 실수 한 번은
 * 사실상 되돌릴 수 없다. 그래서 여럿 골라 고친 것은 묶음으로 기록되고, 여기서 그
 * 묶음을 통째로 그때 값으로 돌린다.
 *
 * ## 그 뒤에 누가 고친 행은 건드리지 않는다
 *
 * 묶음이 넣은 값이 아직 그대로인 행만 되돌린다. 그 사이 다른 사람이 새로 고친 행을
 * 덮어쓰면 되돌리기가 남의 작업을 지운다 — 그 행은 「못 되돌림」 과 이유로 선다.
 */

import { useEffect, useState } from 'react'
import { Loader2, RotateCcw } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import type { BulkEditPlan } from '@/modules/objects/api'
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

const ACTION: Record<string, string> = {
  change: '되돌림',
  unchanged: '이미 그때 값',
  error: '못 되돌림',
}

interface Props {
  typeSlug: string
  batchId: string
  onClose: () => void
  onApplied: () => void
}

export function BulkUndoDialog({ typeSlug, batchId, onClose, onApplied }: Props) {
  const [plan, setPlan] = useState<BulkEditPlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  // 열자마자 계획을 본다 — 무엇이 돌아가는지 모르는 채로 단추를 보게 두지 않는다.
  useEffect(() => {
    let cancelled = false
    objectApi
      .bulkEditUndo(typeSlug, batchId, false)
      .then((found) => {
        if (!cancelled) setPlan(found)
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      })
    return () => {
      cancelled = true
    }
  }, [typeSlug, batchId])

  async function run() {
    setBusy(true)
    setError(null)
    try {
      const found = await objectApi.bulkEditUndo(typeSlug, batchId, true)
      setPlan(found)
      if (found.applied) onApplied()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const change = plan?.counts.change ?? 0
  // 그대로인 행은 많으면 접는다 — 읽을 것은 되돌릴 것과 못 되돌리는 것이다.
  const shown = plan
    ? plan.rows.filter((one) => one.action !== 'unchanged' || plan.rows.length <= 20)
    : []

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>같이 바뀐 것 한 번에 되돌리기</DialogTitle>
          <DialogDescription>
            {plan ? `「${plan.field_label}」 칸을 ` : ''}여럿 골라 고치기 전의 값으로 돌립니다.{' '}
            <strong>그 뒤에 누가 또 고친 행은 건드리지 않습니다</strong> — 되돌리기가 남의 새 작업을
            지우면 안 되니까요.
          </DialogDescription>
        </DialogHeader>

        <ErrorNotice error={error} />

        {plan === null && !error && (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Loader2 className="size-4 animate-spin" />
            무엇이 돌아가는지 보는 중…
          </p>
        )}

        {plan && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span>
                되돌림 <strong>{change}</strong>
              </span>
              <span className="text-muted-foreground">
                이미 그때 값 {plan.counts.unchanged ?? 0}
              </span>
              {(plan.counts.error ?? 0) > 0 && (
                <span className="text-destructive">못 되돌림 {plan.counts.error}</span>
              )}
              {plan.applied && (
                <span className="text-emerald-600 dark:text-emerald-400">되돌렸습니다.</span>
              )}
            </div>
            {!plan.applied && change === 0 && (
              <p className="text-muted-foreground text-sm">
                되돌릴 것이 없습니다 — 이미 되돌렸거나, 전부 그 뒤에 다시 바뀌었습니다.
              </p>
            )}
            {shown.length > 0 && (
              <div className="max-h-64 overflow-auto rounded-md border text-xs">
                <table className="w-full">
                  <thead className="text-muted-foreground">
                    <tr className="border-b">
                      <th className="px-2 py-1 text-left font-normal">객체</th>
                      <th className="px-2 py-1 text-left font-normal">결과</th>
                      <th className="px-2 py-1 text-left font-normal">지금 → 되돌릴 값</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((one) => (
                      <tr key={one.id} className="border-b">
                        <td className="px-2 py-1">{one.label}</td>
                        <td
                          className={
                            one.action === 'error' ? 'text-destructive px-2 py-1' : 'px-2 py-1'
                          }
                        >
                          {ACTION[one.action] ?? one.action}
                        </td>
                        <td className="text-muted-foreground px-2 py-1">
                          {one.message
                            ? one.before
                              ? `${one.message} (지금: ${one.before})`
                              : one.message
                            : `${one.before} → ${one.after}`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {plan?.applied ? '닫기' : '취소'}
          </Button>
          {plan && !plan.applied && (
            <Button onClick={() => void run()} disabled={busy || change === 0}>
              {busy ? (
                <Loader2 className="mr-1 size-4 animate-spin" />
              ) : (
                <RotateCcw className="mr-1 size-4" />
              )}
              {change}건 되돌리기
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
