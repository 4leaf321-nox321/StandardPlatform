/**
 * 최근 적용 · 추천이 얼마나 맞나 — 검토함 아래 자리.
 *
 * 적용한 묶음은 **복원**할 수 있다(가능한 종류만 — 병합은 없다). 복원도 두 걸음이다: 무엇이
 * 돌아가나를 먼저 보이고(그 뒤에 누가 또 고친 줄은 덮어쓰지 않는다고 그 줄에 적는다) 적용한다.
 * 정확도는 「사람이 추천대로 한 몫」 — 확신도 구간마다 보면 「0.9 이상은 믿어도 되나」 에 답한다.
 */

import { useState } from 'react'
import { Loader2 } from 'lucide-react'

import { reviewApi } from '@/modules/review/api'
import type { ReviewUndo } from '@/modules/review/api'
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
import { useResource } from '@/shared/hooks/useResource'

const MODES: Record<string, string> = {
  one: '한 줄',
  selected: '고른 것',
  all: '거른 전부',
  recommended: '추천대로 모두',
}

function percent(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)}%`
}

interface UndoDialogProps {
  batchId: string
  onClose: () => void
  onDone: (said: string) => void
}

/** 묶음 복원 — 미리 보기 뒤 적용. 결과 화면(적용 직후)에서도 이것을 연다. */
export function ReviewUndoDialog({ batchId, onClose, onDone }: UndoDialogProps) {
  const preview = useResource(() => reviewApi.undo(batchId, false), [batchId])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const lines = preview.data?.lines ?? []
  const going = lines.filter((one) => one.status === 'undo').length

  async function apply() {
    setBusy(true)
    setError(null)
    try {
      const done: ReviewUndo = await reviewApi.undo(batchId, true)
      const back = done.counts.done ?? 0
      const refused = done.counts.refused ?? 0
      onDone(
        `${back}건을 복원했습니다.` + (refused ? ` ${refused}건은 그 사이 막혔습니다.` : ''),
      )
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="max-h-[80vh] sm:max-w-[40rem]">
        <DialogHeader>
          <DialogTitle>적용한 묶음 복원</DialogTitle>
          <DialogDescription>
            그 묶음이 바꾼 것을 그때 값으로 복원합니다. 그 뒤에 다시 바뀐 것과 병합은 복원하지
            않습니다.
          </DialogDescription>
        </DialogHeader>
        {preview.error && <ErrorNotice error={preview.error} />}
        {preview.loading && <Loader2 className="text-muted-foreground size-4 animate-spin" />}
        <ul className="max-h-[40vh] divide-y overflow-auto rounded-md border text-sm">
          {lines.map((one) => (
            <li key={one.decision_id} className="space-y-0.5 px-3 py-1.5">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 truncate">{one.question}</span>
                <Badge variant={one.status === 'undo' ? 'secondary' : 'destructive'}>
                  {one.status === 'undo' ? '복원' : '복원 안 함'}
                </Badge>
              </div>
              <p className="text-muted-foreground text-xs">{one.message}</p>
            </li>
          ))}
        </ul>
        {error && <ErrorNotice error={error} />}
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            취소
          </Button>
          <Button disabled={busy || going === 0} onClick={() => void apply()}>
            {busy && <Loader2 className="mr-1 size-4 animate-spin" />}
            {going}건 복원
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface ReviewRecentProps {
  /** 바뀔 때마다 다시 읽는다 — 적용 · 복원 뒤. */
  version: number
  onChanged: (said: string) => void
}

export function ReviewRecent({ version, onChanged }: ReviewRecentProps) {
  const batches = useResource(() => reviewApi.batches(), [version])
  const accuracy = useResource(() => reviewApi.accuracy(), [version])
  const [undoing, setUndoing] = useState<string | null>(null)
  const rows = batches.data ?? []
  const bands = (accuracy.data?.bands ?? []).filter((one) => one.recommended > 0)

  if (rows.length === 0 && bands.length === 0) return null
  return (
    <section className="space-y-3">
      <h2 className="text-base font-medium">최근 적용</h2>
      {batches.error && <ErrorNotice error={batches.error} />}
      <ul className="divide-y rounded-md border text-sm">
        {rows.map((one) => (
          <li key={one.batch_id} className="flex items-center justify-between gap-3 px-3 py-1.5">
            <span className="min-w-0 truncate">
              {new Date(one.decided_at).toLocaleString()} · {MODES[one.mode] ?? one.mode} ·{' '}
              {one.kinds.join(' · ')} · {one.done}건
              {one.refused > 0 && ` · 막힘 ${one.refused}건`}
              {one.undone > 0 && ` · 복원함 ${one.undone}건`}
              {one.decided_by_name && (
                <span className="text-muted-foreground"> · {one.decided_by_name}</span>
              )}
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={one.undoable === 0}
              title={one.undoable === 0 ? '복원할 수 있는 결정이 남지 않았습니다' : undefined}
              onClick={() => setUndoing(one.batch_id)}
            >
              복원
            </Button>
          </li>
        ))}
      </ul>
      {bands.length > 0 && (
        <p className="text-muted-foreground text-xs">
          추천대로 한 몫(확신도 구간마다) —{' '}
          {bands.map((one) => `${one.label} ${percent(one.rate)}(${one.recommended}건)`).join(' · ')}
        </p>
      )}
      {undoing && (
        <ReviewUndoDialog
          batchId={undoing}
          onClose={() => setUndoing(null)}
          onDone={(said) => {
            setUndoing(null)
            onChanged(said)
          }}
        />
      )}
    </section>
  )
}
