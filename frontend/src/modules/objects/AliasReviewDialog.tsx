/**
 * 검수 대기 별칭 — **기계가 붙인 이름을 사람이 한 번 본다.**
 *
 * 적재는 별칭을 수천 개 붙인다. 그 안에는 오타 표기와 남의 이름이 섞이는데, 본 것과 안 본
 * 것을 가르는 자리가 없으면 전부 정본처럼 쓰인다. 화면에서 사람이 붙인 것은 붙이는 순간
 * 확인한 것이라 여기 안 뜬다 — 그래야 이 목록이 읽을 만한 크기로 남는다.
 *
 * **한 번에 고른다.** 한 줄씩 누르게 하면 수백 줄을 끝까지 보는 사람이 없고, 그러면 검수는
 * 안 한 것과 같다. 「지우기」 도 함께 둔다 — 확인만 되면 「아니다」 를 말할 자리가 없어
 * 사람은 둘 다 안 한다.
 */

import { useState } from 'react'
import { Check, Loader2, Trash2 } from 'lucide-react'

import { aliasReviewApi } from '@/modules/objects/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { useResource } from '@/shared/hooks/useResource'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'

interface AliasReviewDialogProps {
  typeSlug: string
  typeLabel: string
  onClose: () => void
  onDone: () => void
}

export function AliasReviewDialog({
  typeSlug,
  typeLabel,
  onClose,
  onDone,
}: AliasReviewDialogProps) {
  const pending = useResource(() => aliasReviewApi.pending(typeSlug), [typeSlug])
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<'approve' | 'remove' | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [said, setSaid] = useState<string>('')

  const items = pending.data?.items ?? []
  const toggle = (id: string) => {
    const next = new Set(picked)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setPicked(next)
  }
  const allPicked = items.length > 0 && picked.size === items.length

  const run = async (action: 'approve' | 'remove') => {
    if (!picked.size) return
    setBusy(action)
    setError(null)
    try {
      const got = await aliasReviewApi.review(typeSlug, [...picked], action)
      setSaid(
        `${action === 'approve' ? '확인' : '지움'} ${got.done}건` +
          (got.refused.length ? ` · 못 한 것 ${got.refused.length}건: ${got.refused[0]}` : ''),
      )
      setPicked(new Set())
      pending.reload()
      onDone()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] sm:max-w-[48rem]">
        <DialogHeader>
          <DialogTitle>{typeLabel} — 검수 대기 별칭</DialogTitle>
          <DialogDescription>
            기계가 붙였고 <strong>사람이 아직 안 본</strong> 이름입니다. 맞으면 확인하고, 오타나
            남의 이름이면 지우세요 — 확인한 것만 정본으로 씁니다.
          </DialogDescription>
        </DialogHeader>

        {pending.error && <ErrorNotice error={pending.error} />}
        {error && <ErrorNotice error={error} />}
        {said && <p className="text-muted-foreground text-xs">{said}</p>}

        {pending.data && items.length === 0 ? (
          <p className="text-muted-foreground text-sm">검수할 별칭이 없습니다.</p>
        ) : (
          <div className="max-h-[50vh] overflow-auto rounded-md border">
            <table className="w-full text-xs">
              <thead className="bg-muted/50 sticky top-0">
                <tr>
                  <th className="w-8 px-2 py-1">
                    <input
                      type="checkbox"
                      checked={allPicked}
                      aria-label="전부 고르기"
                      onChange={(event) =>
                        setPicked(new Set(event.target.checked ? items.map((one) => one.id) : []))
                      }
                    />
                  </th>
                  <th className="px-2 py-1 text-left">별칭</th>
                  <th className="px-2 py-1 text-left">객체</th>
                  <th className="px-2 py-1 text-left">출처 · 메모</th>
                </tr>
              </thead>
              <tbody>
                {items.map((one) => (
                  <tr key={one.id} className="border-t">
                    <td className="px-2 py-1">
                      <input
                        type="checkbox"
                        checked={picked.has(one.id)}
                        aria-label={`${one.value} 고르기`}
                        onChange={() => toggle(one.id)}
                      />
                    </td>
                    <td className="px-2 py-1 font-medium">{one.value}</td>
                    <td className="max-w-48 truncate px-2 py-1">
                      {one.object_label}
                      {one.object_key && (
                        <span className="text-muted-foreground ml-1 font-mono">
                          {one.object_key}
                        </span>
                      )}
                    </td>
                    <td className="text-muted-foreground px-2 py-1">
                      {[one.source, one.note].filter(Boolean).join(' · ') || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {pending.data && pending.data.total > items.length && (
          <p className="text-muted-foreground text-xs">
            모두 {pending.data.total}건 — 위에 {items.length}건까지 보입니다. 처리하면 다음 것이
            올라옵니다.
          </p>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={Boolean(busy)}>
            닫기
          </Button>
          <Button
            variant="outline"
            disabled={!picked.size || Boolean(busy)}
            onClick={() => void run('remove')}
          >
            {busy === 'remove' ? (
              <Loader2 className="mr-1 size-3.5 animate-spin" />
            ) : (
              <Trash2 className="mr-1 size-3.5" />
            )}
            고른 것 지우기 — {picked.size}
          </Button>
          <Button
            disabled={!picked.size || Boolean(busy)}
            onClick={() => void run('approve')}
          >
            {busy === 'approve' ? (
              <Loader2 className="mr-1 size-3.5 animate-spin" />
            ) : (
              <Check className="mr-1 size-3.5" />
            )}
            고른 것 확인 — {picked.size}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
