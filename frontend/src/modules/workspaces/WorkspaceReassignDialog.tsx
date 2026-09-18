/**
 * 자료 이동 — **부서 통폐합의 앞 단계.**
 *
 * 없어지는 부서의 자료를 다른 부서로 통째 넘기고, 그다음에 원본을 보관하거나 지운다.
 * 이 길이 없으면 자료를 살리는 방법이 「객체를 하나씩 손으로 수정」 뿐이고, 그 일은
 * 아무도 끝내지 못한다 — 결국 쓰지 않는 부서가 목록에 영원히 남는다.
 *
 * **고른 종류만 옮긴다.** 멤버는 두고 객체만 넘기는 개편이 흔하다(팀은 남고 업무만
 * 넘어가는 경우).
 */

import { useEffect, useState } from 'react'

import { workspaceApi } from '@/modules/workspaces/api'
import type { Workspace, WorkspaceContent } from '@/modules/workspaces/api'
import { parentOptions } from '@/modules/workspaces/tree'
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
import { Label } from '@/shared/components/ui/label'

interface Props {
  source: Workspace
  all: Workspace[]
  onClose: () => void
  onDone: () => void
}

export function WorkspaceReassignDialog({ source, all, onClose, onDone }: Props) {
  const [contents, setContents] = useState<WorkspaceContent[] | null>(null)
  const [target, setTarget] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [moved, setMoved] = useState<Record<string, number> | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    let cancelled = false
    workspaceApi
      .contents(source.slug)
      .then((found) => {
        if (cancelled) return
        setContents(found)
        // 있는 것만 미리 골라 둔다. 0 건까지 켜 두면 「무엇이 실제로 옮겨지나」 가
        // 체크 표시에서 안 읽힌다.
        setPicked(new Set(found.filter((one) => one.count > 0).map((one) => one.kind)))
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      })
    return () => {
      cancelled = true
    }
  }, [source.slug])

  const options = parentOptions(
    all.filter((one) => one.slug !== source.slug),
    null,
  )
  const total = (contents ?? [])
    .filter((one) => picked.has(one.kind))
    .reduce((sum, one) => sum + one.count, 0)

  function toggle(kind: string) {
    setPicked((before) => {
      const next = new Set(before)
      if (next.has(kind)) next.delete(kind)
      else next.add(kind)
      return next
    })
  }

  async function run() {
    setBusy(true)
    setError(null)
    try {
      const result = await workspaceApi.reassign(source.slug, target, [...picked])
      setMoved(result.moved)
      onDone()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const targetName = all.find((one) => one.slug === target)?.name ?? target

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{source.name} 의 자료 이동</DialogTitle>
          <DialogDescription>
            선택한 것을 다른 부서로 통째 넘깁니다. 자료 자체는 그대로고 소유 부서만 바뀝니다. 다
            넘긴 뒤에 이 부서를 보관하거나 삭제하면 통폐합이 끝납니다.
          </DialogDescription>
        </DialogHeader>

        {moved ? (
          <div className="space-y-2 text-sm">
            <p className="text-emerald-600 dark:text-emerald-400">{targetName} 으로 옮겼습니다.</p>
            <ul className="space-y-1">
              {(contents ?? [])
                .filter((one) => (moved[one.kind] ?? 0) > 0)
                .map((one) => (
                  <li key={one.kind}>
                    {one.label} {moved[one.kind]}건
                  </li>
                ))}
            </ul>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="reassign-target">받을 부서</Label>
              <SearchablePicker
                id="reassign-target"
                options={options}
                value={target || null}
                onChange={setTarget}
                placeholder="어느 부서로 넘길까요"
                searchPlaceholder="부서 이름이나 주소로 검색"
              />
            </div>
            <div className="space-y-2">
              <Label>넘길 것</Label>
              {contents === null ? (
                <p className="text-muted-foreground text-sm">무엇이 있는지 확인 중…</p>
              ) : (
                <ul className="space-y-1">
                  {contents.map((one) => (
                    <li key={one.kind}>
                      <label
                        className={
                          one.count === 0
                            ? 'text-muted-foreground flex items-center gap-2 text-sm'
                            : 'flex items-center gap-2 text-sm'
                        }
                      >
                        <input
                          type="checkbox"
                          checked={picked.has(one.kind)}
                          disabled={one.count === 0 || busy}
                          onChange={() => toggle(one.kind)}
                        />
                        {one.label} {one.count}건
                      </label>
                    </li>
                  ))}
                </ul>
              )}
              {/* 관계 선과 속성으로 이 부서를 **가리키는** 것은 안 옮긴다 — 자동으로
                  바꾸면 「담당 부서」 가 사람 모르게 바뀐다. */}
              <p className="text-muted-foreground text-xs">
                이 부서를 값으로 가리키는 객체(담당 부서 같은 칸)는 변경하지 않습니다. 그것은 옮기는
                것이 아니라 고치는 일이라, 사람이 정해야 합니다.
              </p>
            </div>
          </div>
        )}

        <ErrorNotice error={error} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {moved ? '닫기' : '취소'}
          </Button>
          {!moved && (
            <Button onClick={run} disabled={busy || !target || picked.size === 0}>
              {busy ? '옮기는 중…' : `${total}건 이동`}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
