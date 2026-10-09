/**
 * 별칭 후보 — **못 찾은 이름을 어떤 객체의 별칭으로.**
 *
 * 검색 · 이름 풀이에서 아무것도 못 찾은 말이 여기 모인다(많이 · 여럿이 찾은 것부터). 찾던 쪽은
 * 사람이든 AI 든 없는 줄 알고 새로 만든다 — 그 말이 어떤 객체의 다른 이름이었다면, 별칭으로
 * 추가하는 순간 다음부터 찾힌다(ADR 0025).
 *
 * 「이것 아닐까」 는 이름이 비슷한 것일 뿐 **짐작이다** — 사람이 고르고, 추가하기 전에 무엇이
 * 바뀌는지(별칭 전 · 후, 막는 것)를 먼저 보인다. 별칭이 될 말이 아니면 「무시」 — 또 찾아도
 * 다시 안 뜬다. 누가 찾았는지는 서버가 남기지 않는다(몇 사람인지만).
 */

import { useState } from 'react'
import { Loader2 } from 'lucide-react'

import { aliasCandidateApi } from '@/modules/objects/api'
import type { AliasAttachPlan, AliasCandidate } from '@/modules/objects/api'
import { searchApi } from '@/modules/search/api'
import type { SearchHit } from '@/modules/search/api'
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
import { useResource } from '@/shared/hooks/useResource'

const VIAS: Record<string, string> = {
  resolve: '이름 풀이',
  search: '통합 검색',
  list: '목록 검색',
}

interface AliasCandidatesDialogProps {
  onClose: () => void
  onDone: () => void
}

export function AliasCandidatesDialog({ onClose, onDone }: AliasCandidatesDialogProps) {
  const list = useResource(() => aliasCandidateApi.list({ limit: 50 }), [])
  const [plan, setPlan] = useState<AliasAttachPlan | null>(null)
  /** 「다른 객체 선택」 을 연 후보. */
  const [picking, setPicking] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<SearchHit[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [said, setSaid] = useState('')

  const items = list.data?.items ?? []

  async function guard(work: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await work()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const preview = (candidate: AliasCandidate, objectId: string) =>
    guard(async () => {
      setPlan(await aliasCandidateApi.attach(candidate.id, objectId, false))
    })

  const apply = () =>
    guard(async () => {
      if (!plan) return
      const done = await aliasCandidateApi.attach(plan.candidate.id, plan.object.id, true)
      const also = done.closed
        ? ` 같은 말을 다른 자리에서 못 찾은 ${done.closed}건도 함께 정리했습니다.`
        : ''
      setSaid(
        `「${done.value}」 을(를) ${done.object.label}의 별칭으로 추가했습니다 — ` +
          `다음부터 찾힙니다.${also}`,
      )
      setPlan(null)
      setPicking(null)
      list.reload()
      onDone()
    })

  const ignore = (candidate: AliasCandidate) =>
    guard(async () => {
      const done = await aliasCandidateApi.decide([candidate.id], 'ignore')
      setSaid(
        done.done
          ? `「${candidate.text}」 을(를) 무시했습니다 — 또 찾아도 다시 표시하지 않습니다.`
          : (done.refused[0] ?? ''),
      )
      if (plan?.candidate.id === candidate.id) setPlan(null)
      list.reload()
      onDone()
    })

  const find = (candidate: AliasCandidate) =>
    guard(async () => {
      const found = await searchApi.find(query.trim(), { type: candidate.scope || null })
      setHits(found.items.slice(0, 8))
    })

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] sm:max-w-[48rem]">
        <DialogHeader>
          <DialogTitle>별칭 후보 — 못 찾은 이름</DialogTitle>
          <DialogDescription>
            검색 · 이름 풀이에서 아무것도 못 찾은 말입니다. 어떤 객체의 다른 이름이면 그 객체의
            별칭으로 추가하세요 — 다음부터 찾힙니다. 별칭이 될 말이 아니면 무시하세요.
          </DialogDescription>
        </DialogHeader>

        {list.error && <ErrorNotice error={list.error} />}
        {error && <ErrorNotice error={error} />}
        {said && <p className="text-muted-foreground text-xs">{said}</p>}

        {list.data && items.length === 0 ? (
          <p className="text-muted-foreground text-sm">기다리는 별칭 후보가 없습니다.</p>
        ) : (
          <ul className="max-h-[55vh] divide-y overflow-auto rounded-md border text-sm">
            {items.map((one) => (
              <li key={one.id} className="space-y-1.5 px-3 py-2">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0">
                    <span className="font-medium">{one.text}</span>
                    <span className="text-muted-foreground ml-2 text-xs">
                      {one.scope_label} · {one.hits}번 · {one.people}명 ·{' '}
                      {one.vias.map((via) => VIAS[via] ?? via).join(' · ')}
                    </span>
                  </span>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => void ignore(one)}
                  >
                    무시
                  </Button>
                </div>
                <div className="flex flex-wrap items-center gap-1.5 text-xs">
                  {one.suggestions.length > 0 && (
                    <span className="text-muted-foreground">이것 아닐까:</span>
                  )}
                  {one.suggestions.map((hit) => (
                    <Button
                      key={hit.id}
                      size="sm"
                      variant="secondary"
                      disabled={busy}
                      onClick={() => void preview(one, hit.id)}
                    >
                      {hit.label}
                      <span className="text-muted-foreground ml-1">{hit.type_label}</span>
                    </Button>
                  ))}
                  {one.suggest_note && (
                    <span className="text-muted-foreground">{one.suggest_note}</span>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setPicking(picking === one.id ? null : one.id)
                      setQuery(one.text)
                      setHits(null)
                    }}
                  >
                    다른 객체 선택
                  </Button>
                </div>
                {picking === one.id && (
                  <div className="space-y-1.5">
                    <form
                      className="flex gap-2"
                      onSubmit={(event) => {
                        event.preventDefault()
                        void find(one)
                      }}
                    >
                      <Input
                        value={query}
                        aria-label="객체 검색"
                        placeholder="객체 이름의 일부"
                        onChange={(event) => setQuery(event.target.value)}
                      />
                      <Button size="sm" type="submit" disabled={busy || query.trim().length < 2}>
                        검색
                      </Button>
                    </form>
                    {hits && hits.length === 0 && (
                      <p className="text-muted-foreground text-xs">
                        이 말로 찾은 객체가 없습니다 — 이름의 다른 부분으로 검색하세요.
                      </p>
                    )}
                    <div className="flex flex-wrap gap-1.5">
                      {(hits ?? []).map((hit) => (
                        <Button
                          key={hit.id}
                          size="sm"
                          variant="secondary"
                          disabled={busy}
                          onClick={() => void preview(one, hit.id)}
                        >
                          {hit.label}
                          <span className="text-muted-foreground ml-1">{hit.type_label}</span>
                        </Button>
                      ))}
                    </div>
                  </div>
                )}
                {plan && plan.candidate.id === one.id && (
                  <div className="bg-muted/40 space-y-1 rounded-md border p-2 text-xs">
                    <p>
                      「{plan.value}」 → <strong>{plan.object.label}</strong>
                      <span className="text-muted-foreground"> ({plan.object.type_label})</span>
                    </p>
                    <p className="text-muted-foreground">
                      별칭: {plan.aliases_before.join(' · ') || '없음'} →{' '}
                      {plan.aliases_after.join(' · ') || '없음'}
                    </p>
                    {plan.warnings.map((line) => (
                      <p key={line} className="text-amber-700 dark:text-amber-400">
                        {line}
                      </p>
                    ))}
                    {plan.blocking.map((line) => (
                      <p key={line} className="text-destructive">
                        {line}
                      </p>
                    ))}
                    <div className="flex justify-end gap-2 pt-1">
                      <Button size="sm" variant="outline" onClick={() => setPlan(null)}>
                        취소
                      </Button>
                      <Button
                        size="sm"
                        disabled={busy || plan.blocking.length > 0}
                        onClick={() => void apply()}
                      >
                        {busy && <Loader2 className="mr-1 size-3.5 animate-spin" />}
                        별칭 추가
                      </Button>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        {list.data && list.data.total > items.length && (
          <p className="text-muted-foreground text-xs">
            모두 {list.data.total}건 — 많이 찾은 것부터 {items.length}건까지 표시합니다. 처리하면
            다음 것이 올라옵니다.
          </p>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            닫기
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
