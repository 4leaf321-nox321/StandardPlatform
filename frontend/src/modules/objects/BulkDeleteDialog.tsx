/**
 * 여럿 골라 지우기 — **계획 먼저.**
 *
 * 「속성 변경」 의 짝이다. 고른 것을 한 번에 고칠 수 있는데 지우는 것만 한 건씩 창을 열어야
 * 하면, 사람은 목록을 앞에 두고 창을 스무 번 연다.
 *
 * ## 무엇이 걸렸는지 먼저 말한다
 *
 * 가리키는 것이 있는 객체를 조용히 지우면 다른 화면의 칸이 빈 채로 남는다. 기본은 그 행을
 * 거절하고 **몇 개가 걸렸는지** 적는다. 그래도 지우려면 「참조를 비우고 삭제」 를 고른다 —
 * 가리키던 칸이 비고 관계가 끊기며, 그 객체마다 기록이 남는다.
 *
 * ## 걸린 하나 때문에 나머지를 취소하지 않는다
 *
 * 스무 개 중 하나가 걸렸다고 열아홉을 못 지우면, 사람은 그 하나를 찾아 빼고 처음부터 다시
 * 고른다. 되는 것은 지우고 안 되는 것만 이유와 함께 남긴다.
 *
 * 병합은 여기 없다 — 합칠 상대는 한 건마다 다르다. 목록에서 한 건씩 여는 삭제 창에 있다.
 */

import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import type { BulkDeletePlan } from '@/modules/objects/api'
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
  delete: '지움',
  error: '오류',
}

interface Props {
  typeSlug: string
  typeLabel: string
  ids: string[]
  onClose: () => void
  onApplied: () => void
}

export function BulkDeleteDialog({ typeSlug, typeLabel, ids, onClose, onApplied }: Props) {
  const [mode, setMode] = useState<'block' | 'detach'>('block')
  const [plan, setPlan] = useState<BulkDeletePlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  // 방식이 바뀌면 계획은 지난 것이 된다 — 남겨 두면 그 계획을 보고 적용을 누른다.
  useEffect(() => setPlan(null), [mode])

  async function run(apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      const found = await objectApi.bulkDelete(typeSlug, { ids, mode, apply })
      setPlan(found)
      if (found.applied) onApplied()
    } catch (caught) {
      // **창을 닫지 않는다.** 닫으면 오류가 어디에도 안 남고, 사람은 일이 된 줄 안다.
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const removable = plan?.counts.delete ?? 0

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            고른 {ids.length}건의 {typeLabel} 삭제
          </DialogTitle>
          <DialogDescription>
            <strong>먼저 계획을 봅니다</strong> — 무엇이 지워지고, 무엇이 왜 안 되는지. 목록에서
            사라지지만 기록은 남습니다.
          </DialogDescription>
        </DialogHeader>

        <fieldset className="space-y-2 text-sm">
          <legend className="text-muted-foreground mb-1 text-xs">
            가리키는 것이 있으면 어떻게 할까요
          </legend>
          <label className="flex cursor-pointer items-start gap-2 rounded-md border p-2">
            <input
              type="radio"
              name="bulk-delete-mode"
              checked={mode === 'block'}
              onChange={() => setMode('block')}
              className="mt-1"
            />
            <span>
              <span className="font-medium">그 행은 두기</span>
              <span className="text-muted-foreground block text-xs">
                가리키는 것이 있는 객체는 지우지 않고 몇 개가 걸렸는지 적습니다. 나머지는 지웁니다.
              </span>
            </span>
          </label>
          <label className="flex cursor-pointer items-start gap-2 rounded-md border p-2">
            <input
              type="radio"
              name="bulk-delete-mode"
              checked={mode === 'detach'}
              onChange={() => setMode('detach')}
              className="mt-1"
            />
            <span>
              <span className="font-medium">참조를 비우고 삭제</span>
              <span className="text-muted-foreground block text-xs">
                가리키던 칸이 비고 관계가 끊깁니다. 그 객체마다 「왜 비었는지」 기록이 남습니다.
              </span>
            </span>
          </label>
        </fieldset>

        <ErrorNotice error={error} />

        {plan && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span>
                지움 <strong>{plan.counts.delete ?? 0}</strong>
              </span>
              {(plan.counts.error ?? 0) > 0 && (
                <span className="text-destructive">안 됨 {plan.counts.error}</span>
              )}
              {plan.applied && (
                <span className="text-emerald-600 dark:text-emerald-400">지웠습니다.</span>
              )}
            </div>
            <div className="max-h-64 overflow-auto rounded-md border text-xs">
              <table className="w-full">
                <tbody>
                  {plan.rows.map((one) => (
                    <tr key={one.id} className="border-b">
                      <td className="px-2 py-1">{one.label}</td>
                      <td
                        className={
                          one.action === 'error' ? 'text-destructive px-2 py-1' : 'px-2 py-1'
                        }
                      >
                        {ACTION[one.action] ?? one.action}
                      </td>
                      <td className="text-muted-foreground px-2 py-1">{one.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {plan?.applied ? '닫기' : '취소'}
          </Button>
          {!plan?.applied && (
            <>
              <Button variant="outline" onClick={() => void run(false)} disabled={busy}>
                {busy && <Loader2 className="mr-1 size-4 animate-spin" />}
                계획 보기
              </Button>
              <Button
                variant="destructive"
                onClick={() => void run(true)}
                disabled={busy || !plan || removable === 0}
              >
                {plan ? `${removable}건 삭제` : '삭제'}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
