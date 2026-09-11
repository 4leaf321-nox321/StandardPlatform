/**
 * 저장된 뷰 — **조건을 이름 붙여 두고, 부서와 함께 쓴다.**
 *
 * 부서 뷰가 먼저, 내 뷰가 뒤에 선다. 지금 조건을 저장하면 내 것이 되고, 부서 관리자는
 * 「부서와 함께 쓰기」 를 켤 수 있다. 전사 뷰는 여기 없다 — 타입 정의의 `list_view`
 * 가 그 자리라서, 여기까지 두면 「어느 것이 기본이지」 를 아무도 답 못 한다.
 */

import { useState } from 'react'
import { Bookmark, BookmarkPlus, Check, Trash2, Users } from 'lucide-react'

import { viewApi } from '@/modules/objects/api'
import type { SavedView, SavedViewQuery } from '@/modules/objects/api'
import { useAuth } from '@/shared/auth/AuthContext'
import { isManagerOf } from '@/shared/auth/roles'
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import { Input } from '@/shared/components/ui/input'
import { useResource } from '@/shared/hooks/useResource'

interface ViewPickerProps {
  typeSlug: string
  /** 지금 걸린 것 — 저장할 내용. */
  current: SavedViewQuery
  /** 지금 적용된 뷰(있으면). 조건을 손대면 호스트가 null 로 되돌린다. */
  activeId: string | null
  onApply: (view: SavedView) => void
  onClear: () => void
}

export function ViewPicker({ typeSlug, current, activeId, onApply, onClear }: ViewPickerProps) {
  const views = useResource(() => viewApi.list(typeSlug), [typeSlug])
  const [saving, setSaving] = useState(false)
  const list = views.data ?? []
  const shared = list.filter((one) => one.workspace_slug)
  const mine = list.filter((one) => !one.workspace_slug)
  const active = list.find((one) => one.id === activeId) ?? null
  const hasSomething = current.q.trim() !== '' || current.conditions.length > 0 || Boolean(current.status)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="sm" variant={active ? 'secondary' : 'outline'} className="h-7">
            <Bookmark className="mr-1 size-3.5" />
            {active ? active.name : '뷰'}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-64">
          {shared.length > 0 && (
            <>
              <DropdownMenuLabel className="text-muted-foreground text-xs">부서와 함께</DropdownMenuLabel>
              {shared.map((one) => (
                <DropdownMenuItem key={one.id} onSelect={() => onApply(one)}>
                  <Users className="text-muted-foreground mr-1 size-3.5" />
                  <span className="flex-1 truncate">{one.name}</span>
                  {one.id === activeId && <Check className="size-3.5" />}
                </DropdownMenuItem>
              ))}
              <DropdownMenuSeparator />
            </>
          )}
          {mine.length > 0 && (
            <>
              <DropdownMenuLabel className="text-muted-foreground text-xs">내 것</DropdownMenuLabel>
              {mine.map((one) => (
                <DropdownMenuItem key={one.id} onSelect={() => onApply(one)}>
                  <span className="flex-1 truncate">{one.name}</span>
                  {one.id === activeId && <Check className="size-3.5" />}
                </DropdownMenuItem>
              ))}
              <DropdownMenuSeparator />
            </>
          )}
          {list.length === 0 && !views.loading && (
            <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
              저장된 뷰가 없습니다. 조건을 걸고 저장하세요.
            </DropdownMenuLabel>
          )}
          <DropdownMenuItem disabled={!hasSomething} onSelect={() => setSaving(true)}>
            <BookmarkPlus className="mr-1 size-3.5" />
            지금 조건을 뷰로 저장…
          </DropdownMenuItem>
          {active && (
            <DropdownMenuItem onSelect={onClear}>뷰 해제 — 조건 전부 지우기</DropdownMenuItem>
          )}
          {active?.can_edit && (
            <DropdownMenuItem
              className="text-destructive"
              onSelect={() => {
                void viewApi.remove(typeSlug, active.id).then(() => {
                  views.reload()
                  onClear()
                })
              }}
            >
              <Trash2 className="mr-1 size-3.5" />
              「{active.name}」 지우기
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      {saving && (
        <SaveViewDialog
          typeSlug={typeSlug}
          query={current}
          onClose={() => setSaving(false)}
          onSaved={(view) => {
            setSaving(false)
            views.reload()
            onApply(view)
          }}
        />
      )}
    </>
  )
}

interface SaveViewDialogProps {
  typeSlug: string
  query: SavedViewQuery
  onClose: () => void
  onSaved: (view: SavedView) => void
}

function SaveViewDialog({ typeSlug, query, onClose, onSaved }: SaveViewDialogProps) {
  const { user } = useAuth()
  const myWorkspace = user?.home_workspace_slug ?? user?.memberships[0]?.slug ?? null
  const canShare = Boolean(myWorkspace && isManagerOf(user, myWorkspace))
  const [name, setName] = useState('')
  const [share, setShare] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  async function save() {
    setBusy(true)
    setError(null)
    try {
      const view = await viewApi.create(typeSlug, {
        name: name.trim(),
        query,
        workspace_slug: share ? myWorkspace : null,
      })
      onSaved(view)
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>뷰로 저장</DialogTitle>
          <DialogDescription>
            지금 걸린 조건 {query.conditions.length}개{query.q ? `와 검색어 「${query.q}」` : ''}를 이름
            붙여 둡니다. 열·정렬은 타입 정의를 따릅니다.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            if (name.trim()) void save()
          }}
        >
          <Input
            autoFocus
            value={name}
            placeholder="뷰 이름 — 「영남 공급사, 점수 80 미만」 처럼"
            onChange={(event) => setName(event.target.value)}
          />
          {canShare ? (
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4"
                checked={share}
                onChange={(event) => setShare(event.target.checked)}
              />
              <span>
                <span className="font-medium">{myWorkspace} 부서와 함께 쓰기</span>
                <span className="text-muted-foreground block text-xs">
                  부서 사람 모두의 목록에 뜹니다. 고치고 지우는 것은 부서 관리자만.
                </span>
              </span>
            </label>
          ) : (
            <p className="text-muted-foreground text-xs">
              내 것으로 저장됩니다. 부서와 함께 쓰는 뷰는 부서 관리자가 만듭니다.
            </p>
          )}
          {error && <ErrorNotice error={error} />}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
              취소
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              저장
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
