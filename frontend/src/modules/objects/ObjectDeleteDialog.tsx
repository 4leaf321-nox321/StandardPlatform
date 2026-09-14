/**
 * 삭제 — **누르기 전에 무엇이 걸렸는지 보고, 걸렸으면 어떻게 할지 고른다.**
 *
 * 「정말 삭제하시겠습니까」 만 묻는 창은 아무도 안 읽는다. 이 창은 먼저 「이 객체를
 * 가리키는 것」 을 세어 보여 준다. 없으면 그냥 지운다. 있으면 셋 중 하나:
 *
 *   그대로 두기          취소 — 먼저 끊거나 고치러 간다
 *   참조 해제 후 삭제   가리키던 칸이 비고 관계가 끊긴다. 그 객체마다 기록이 남는다
 *   다른 것에 병합     참조·관계가 이긴 쪽으로 옮겨 가고, 옛 주소는 새 것으로 간다
 *
 * 남의 부서 것이 가리키고 있으면 **수만** 보인다 — 안 보이면 「아무것도 안 걸렸다」 로
 * 읽고 지우게 된다.
 */

import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Loader2 } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import { useObjectOptions } from '@/modules/objects/useObjectOptions'
import type { ObjectRow } from '@/modules/objects/api'
import { ApiError } from '@/shared/api/client'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
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

type Choice = 'keep' | 'detach' | 'merge'

interface ObjectDeleteDialogProps {
  typeSlug: string
  typeLabel: string
  object: ObjectRow
  onClose: () => void
  /** 지웠거나 합쳤다. 합쳤으면 이긴 쪽 id — 그리로 간다. */
  onDone: (mergedInto: string | null) => void
}

/** 목록에 몇 개까지 적나. 그 위는 「… 외 N개」. */
const LIST_LIMIT = 8

export function ObjectDeleteDialog({
  typeSlug,
  typeLabel,
  object,
  onClose,
  onDone,
}: ObjectDeleteDialogProps) {
  const refs = useResource(() => objectApi.references(typeSlug, object.id), [typeSlug, object.id])
  const [choice, setChoice] = useState<Choice>('keep')
  const [into, setInto] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | Error | null>(null)

  const blocked = Boolean(refs.data && refs.data.total > 0)

  // 합칠 상대 — 같은 타입의 다른 객체. **서버가 찾는다** — 200개 안에 없는 것을 「없다」 로
  // 읽고 새로 만들면 그것이 바로 합치려던 중복이다.
  const sources = useMemo(() => [{ slug: typeSlug }], [typeSlug])
  const candidates = useObjectOptions(sources, { exclude: object.id, value: into })

  const summary = useMemo(() => {
    const data = refs.data
    if (!data) return null
    const props = data.property_refs.length + data.hidden_property_refs
    const rels = data.relations.length + data.hidden_relations
    return { props, rels, hidden: data.hidden_property_refs + data.hidden_relations }
  }, [refs.data])

  async function run() {
    setBusy(true)
    setError(null)
    try {
      if (!blocked) {
        await objectApi.remove(typeSlug, object.id, 'block')
        onDone(null)
      } else if (choice === 'detach') {
        await objectApi.remove(typeSlug, object.id, 'detach')
        onDone(null)
      } else if (choice === 'merge' && into) {
        const result = await objectApi.merge(typeSlug, object.id, into)
        onDone(result.into)
      }
    } catch (caught) {
      // **창을 닫지 않는다.** 닫으면 오류가 어디에도 안 남고, 사람은 일이 된 줄 안다.
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const canRun =
    !busy && refs.data !== null && (!blocked || choice === 'detach' || (choice === 'merge' && into))
  const runLabel = !blocked
    ? '삭제'
    : choice === 'detach'
      ? '참조 해제 후 삭제'
      : choice === 'merge'
        ? '병합 후 삭제'
        : '삭제'

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{object.label} 을(를) 지웁니다</DialogTitle>
          <DialogDescription>
            목록에서 사라집니다. <b>기록은 남습니다</b> — 첨부와 감사 기록이 밖에 있어서, 지운
            흔적까지 없애면 그것들이 무엇을 가리키는지 설명할 수 없게 됩니다.
          </DialogDescription>
        </DialogHeader>

        {refs.loading && (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Loader2 className="size-4 animate-spin" />이 객체를 가리키는 것을 세는 중…
          </p>
        )}
        {refs.error && <ErrorNotice error={refs.error} />}

        {refs.data && summary && (
          <div className="space-y-3 text-sm">
            {summary.props + summary.rels === 0 ? (
              <p className="text-muted-foreground">이 객체를 가리키는 것이 없습니다.</p>
            ) : (
              <div className="space-y-2 rounded-md border p-3">
                <p className="font-medium">
                  이 객체를 가리키는 것 {summary.props + summary.rels}개
                  <span className="text-muted-foreground font-normal">
                    {' '}
                    — 속성 참조 {summary.props} · 관계 {summary.rels}
                    {summary.hidden > 0 && ` (그중 볼 수 없는 부서의 것 ${summary.hidden})`}
                  </span>
                </p>
                {refs.data.property_refs.length > 0 && (
                  <ul className="text-muted-foreground space-y-0.5 text-xs">
                    {refs.data.property_refs.slice(0, LIST_LIMIT).map((one) => (
                      <li key={`${one.object_id}:${one.property_key}`}>
                        <Link
                          to={`/o/${one.type_slug}/${one.object_id}`}
                          className="text-foreground hover:underline"
                        >
                          {one.label}
                        </Link>{' '}
                        ({one.type_label}) 의 「{one.property_label}」
                      </li>
                    ))}
                    {refs.data.property_refs.length > LIST_LIMIT && (
                      <li>… 외 {refs.data.property_refs.length - LIST_LIMIT}개</li>
                    )}
                  </ul>
                )}
                {refs.data.relations.length > 0 && (
                  <ul className="text-muted-foreground space-y-0.5 text-xs">
                    {refs.data.relations.slice(0, LIST_LIMIT).map((one) => (
                      <li key={one.relation_id}>
                        {one.outgoing ? '→' : '←'}{' '}
                        <Link
                          to={`/o/${one.other_type_slug}/${one.other_id}`}
                          className="text-foreground hover:underline"
                        >
                          {one.other_label}
                        </Link>{' '}
                        <span className="opacity-70">({one.relation})</span>
                      </li>
                    ))}
                    {refs.data.relations.length > LIST_LIMIT && (
                      <li>… 외 {refs.data.relations.length - LIST_LIMIT}개</li>
                    )}
                  </ul>
                )}
              </div>
            )}

            {blocked && (
              <fieldset className="space-y-2">
                <legend className="text-muted-foreground mb-1 text-xs">어떻게 할까요</legend>
                <label className="flex cursor-pointer items-start gap-2 rounded-md border p-2">
                  <input
                    type="radio"
                    name="choice"
                    checked={choice === 'keep'}
                    onChange={() => setChoice('keep')}
                    className="mt-1"
                  />
                  <span>
                    <span className="font-medium">그대로 두기</span>
                    <span className="text-muted-foreground block text-xs">
                      지우지 않습니다. 위 목록에서 먼저 끊거나 고친 뒤 다시 옵니다.
                    </span>
                  </span>
                </label>
                <label className="flex cursor-pointer items-start gap-2 rounded-md border p-2">
                  <input
                    type="radio"
                    name="choice"
                    checked={choice === 'detach'}
                    onChange={() => setChoice('detach')}
                    className="mt-1"
                  />
                  <span>
                    <span className="font-medium">참조를 참조를 비우고 관계를 해제한 뒤 삭제</span>
                    <span className="text-muted-foreground block text-xs">
                      가리키던 {summary.props}개의 칸이 비고 관계 {summary.rels}개가 끊깁니다. 그
                      객체마다 「왜 비었는지」 기록이 남습니다.
                    </span>
                  </span>
                </label>
                <label className="flex cursor-pointer items-start gap-2 rounded-md border p-2">
                  <input
                    type="radio"
                    name="choice"
                    checked={choice === 'merge'}
                    onChange={() => setChoice('merge')}
                    className="mt-1"
                  />
                  <span>
                    <span className="font-medium">다른 {typeLabel}에 병합 후 삭제</span>
                    <span className="text-muted-foreground block text-xs">
                      <b>같은 것이 둘로 갈렸을 때</b>(「ACME」 와 「ACME Inc.」 처럼). 이긴 쪽만
                      남기고, 이 객체를 가리키던 참조와 관계를 전부 그리로 옮깁니다. 옛 주소로
                      들어오면 이긴 쪽으로 갑니다. 같은 {typeLabel}끼리만 됩니다.
                    </span>
                  </span>
                </label>
                {/* 피커는 라벨 **밖**에 — 안에 두면 피커를 누를 때마다 라디오가 눌리고, 접근성
                    이름도 꼬인다. */}
                {choice === 'merge' && (
                  <div className="pl-6">
                    <SearchablePicker
                      options={candidates.options}
                      pinned={candidates.pinned}
                      total={candidates.total}
                      loading={candidates.loading}
                      onQueryChange={candidates.setQuery}
                      value={into}
                      onChange={setInto}
                      placeholder="이긴 쪽 선택"
                      searchPlaceholder="이름·식별자로 검색"
                      emptyText={
                        candidates.failed
                          ? '같은 타입의 객체를 읽지 못했습니다'
                          : '합칠 상대가 없습니다'
                      }
                    />
                  </div>
                )}
              </fieldset>
            )}
          </div>
        )}

        {error && <ErrorNotice error={error} />}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            취소
          </Button>
          <Button
            variant={choice === 'merge' && blocked ? 'default' : 'destructive'}
            disabled={!canRun}
            onClick={() => void run()}
          >
            {busy && <Loader2 className="mr-1 size-3.5 animate-spin" />}
            {runLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
