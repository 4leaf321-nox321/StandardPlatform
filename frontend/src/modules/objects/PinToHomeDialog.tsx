/**
 * 홈에 올리기 — **그림을 막 그려 놓고 보는 그 자리에서.**
 *
 * 사람이 「이거 홈에 두고 싶다」 고 생각하는 순간은 묶어 보기로 그림을 그린 직후다.
 * 그때 뷰 메뉴를 열어 저장하고, 부서와 함께 쓰기를 켜고, 메뉴를 다시 열어 올리게
 * 하면 — 그 경로를 찾아낸 사람만 이 기능을 쓴다. 여기서는 **이름 하나만** 받고
 * 나머지(부서 뷰로 저장 + 홈에 올리기)는 한 요청으로 끝낸다.
 *
 * 요청을 하나로 두는 이유: 둘로 나누면 저장은 됐는데 안 올라간 상태가 생기고, 그때
 * 사람은 자기가 무엇을 빠뜨렸는지 모른다.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { House } from 'lucide-react'

import { viewApi } from '@/modules/objects/api'
import type { SavedViewQuery, SavedViewSummary } from '@/modules/objects/api'
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
  typeSlug: string
  /** 올릴 부서 — 내 대표 소속. 그 부서의 관리자여야 단추가 보인다. */
  workspaceSlug: string
  query: SavedViewQuery
  summary: SavedViewSummary | null
  /** 이름 칸의 첫 값 — 무엇을 올리는지 사람이 이미 안다. */
  suggested: string
  onClose: () => void
}

export function PinToHomeDialog({
  typeSlug,
  workspaceSlug,
  query,
  summary,
  suggested,
  onClose,
}: Props) {
  const [name, setName] = useState(suggested)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  async function pin() {
    setBusy(true)
    setError(null)
    try {
      await viewApi.create(typeSlug, {
        name: name.trim(),
        query,
        workspace_slug: workspaceSlug,
        summary,
        on_home: true,
      })
      setDone(true)
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>부서 홈에 올리기</DialogTitle>
          <DialogDescription>
            지금 조건 {query.conditions.length}개와 {summary ? '묶어 보기 축을' : '검색 조건을'}{' '}
            <strong>{workspaceSlug}</strong> 부서 뷰로 저장하고 그 부서 홈에 올립니다. 부서 사람
            모두가 같은 것을 봅니다.
          </DialogDescription>
        </DialogHeader>

        {done ? (
          <p className="text-sm">
            올렸습니다.{' '}
            <Link to={`/w/${workspaceSlug}`} className="underline">
              홈에서 보기
            </Link>
            . 내리는 것은 목록의 「뷰」 메뉴에서 합니다.
          </p>
        ) : (
          <form
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault()
              if (name.trim()) void pin()
            }}
          >
            <Input
              autoFocus
              value={name}
              placeholder="홈에 뜰 이름 — 「등급별 공급사」 처럼"
              onChange={(event) => setName(event.target.value)}
            />
            {!summary && (
              /* 축 없이 올려도 된다 — 수 하나짜리 위젯이 된다. 그 사실을 여기서 말한다. */
              <p className="text-muted-foreground text-xs">
                묶어 보기 축이 없어 <strong>수 하나</strong>로 섭니다. 그림으로 올리려면 닫고 축을
                고른 뒤 다시 누르세요.
              </p>
            )}
            <ErrorNotice error={error} />
            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
                취소
              </Button>
              <Button type="submit" disabled={busy || !name.trim()}>
                <House className="mr-1 size-4" />
                {busy ? '올리는 중…' : '홈에 올리기'}
              </Button>
            </DialogFooter>
          </form>
        )}

        {done && (
          <DialogFooter>
            <Button onClick={onClose}>닫기</Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  )
}
