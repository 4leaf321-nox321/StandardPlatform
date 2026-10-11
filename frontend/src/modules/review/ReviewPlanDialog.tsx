/**
 * 검토함의 미리 보기 — **누르기 전에 몇 건이 무엇으로 되는지.**
 *
 * 「추천대로 모두」 · 고른 것 · 거른 전부가 모두 여기로 온다(두 걸음: 미리 보기 → 적용). 막히는
 * 것(다른 객체가 이미 쓰는 별칭 · 남의 부서 · 병합 묶음에 못 고치는 것)은 따로 세어 먼저 보이고
 * 적용에서 빠진다. 복원할 수 없는 종류(병합)는 그렇다고 적는다 — 「정말 하시겠습니까」 만 묻는
 * 창은 아무도 안 읽는다(AGENTS.md 의 `ConfirmDialog` 규칙).
 */

import { Loader2 } from 'lucide-react'

import type { ReviewPlan } from '@/modules/review/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'

interface ReviewPlanDialogProps {
  title: string
  plan: ReviewPlan
  busy: boolean
  /** 큰 묶음이 작업으로 도는 동안의 진행 — 「120 / 3,000」. */
  progress: string
  error: Error | null
  onApply: () => void
  onClose: () => void
}

export function ReviewPlanDialog({
  title,
  plan,
  busy,
  progress,
  error,
  onApply,
  onClose,
}: ReviewPlanDialogProps) {
  const going = plan.counts.apply
  const irreversible = plan.by_kind.filter((one) => one.apply > 0 && !one.undoable)
  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="max-h-[85vh] sm:max-w-[46rem]">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            아직 아무것도 바뀌지 않았습니다. 아래대로 적용합니다 — 막히는 것은 제외하고 적용합니다.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge>적용 {going.toLocaleString()}건</Badge>
          {plan.rows > 0 && (
            <span className="text-muted-foreground">손대는 줄 {plan.rows.toLocaleString()}</span>
          )}
          {plan.counts.blocked > 0 && (
            <Badge variant="destructive">막힘 {plan.counts.blocked.toLocaleString()}건</Badge>
          )}
          {plan.skipped > 0 && (
            <Badge variant="secondary">문턱 아래 · 추천 없음 {plan.skipped.toLocaleString()}건</Badge>
          )}
          {plan.as_job && <Badge variant="outline">작업으로 실행</Badge>}
        </div>

        <ul className="space-y-1 text-sm">
          {plan.by_kind.map((one) => (
            <li key={one.kind} className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-medium">{one.label}</span>
              <span className="text-muted-foreground">
                적용 {one.apply}건{one.blocked > 0 && ` · 막힘 ${one.blocked}건`}
                {one.rows > one.apply && ` · ${one.rows.toLocaleString()}줄`}
              </span>
              {one.apply > 0 && !one.undoable && (
                <span className="text-amber-700 dark:text-amber-400">
                  {one.undo_note || '복원할 수 없습니다.'}
                </span>
              )}
            </li>
          ))}
        </ul>

        {irreversible.length > 0 && (
          <p className="text-sm text-amber-700 dark:text-amber-400">
            {irreversible.map((one) => one.label).join(' · ')}은(는) 적용하면 복원할 수
            없습니다.
          </p>
        )}
        {plan.notes.map((line) => (
          <p key={line} className="text-muted-foreground text-xs">
            {line}
          </p>
        ))}

        <ul className="max-h-[38vh] divide-y overflow-auto rounded-md border text-sm">
          {plan.outcomes.map((one) => (
            <li key={`${one.kind}:${one.item_id}`} className="space-y-0.5 px-3 py-1.5">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 truncate">{one.effect || one.question}</span>
                {one.status === 'blocked' ? (
                  <Badge variant="destructive">막힘</Badge>
                ) : (
                  <Badge variant="secondary">{one.rows > 1 ? `${one.rows}줄` : '적용'}</Badge>
                )}
              </div>
              {one.message && (
                <p
                  className={
                    one.status === 'blocked'
                      ? 'text-destructive text-xs'
                      : 'text-muted-foreground text-xs'
                  }
                >
                  {one.message}
                </p>
              )}
            </li>
          ))}
          {plan.outcomes_omitted > 0 && (
            <li className="text-muted-foreground px-3 py-1.5 text-xs">
              … 그 밖에 {plan.outcomes_omitted.toLocaleString()}건(수는 위에 모두 셌습니다)
            </li>
          )}
          {plan.outcomes.length === 0 && (
            <li className="text-muted-foreground px-3 py-2 text-xs">
              적용할 것이 없습니다 — 거르기에 맞는 항목이 없거나 모두 문턱 아래입니다.
            </li>
          )}
        </ul>

        {error && <ErrorNotice error={error} />}
        {progress && <p className="text-muted-foreground text-xs">{progress}</p>}

        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            취소
          </Button>
          <Button disabled={busy || going === 0} onClick={onApply}>
            {busy && <Loader2 className="mr-1 size-4 animate-spin" />}
            {going.toLocaleString()}건 적용
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
