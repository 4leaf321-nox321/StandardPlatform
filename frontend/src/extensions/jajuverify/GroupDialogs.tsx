/**
 * 묶음의 두 창 — 「새 묶음」 의 이름 묻기, 「묶음 삭제」 의 두 선택(묶음만 해제 · 줄까지 삭제).
 *
 * `window.prompt` 를 쓰지 않는다 — 서버가 거절하면 그 말을 보일 자리가 없다. 두 창 모두 실패하면 닫지
 * 않고 까닭을 보인다.
 */

import { useState } from 'react'

import type { Group } from '@/extensions/jajuverify/api'
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
import { Label } from '@/shared/components/ui/label'

function asError(caught: unknown): Error {
  return caught instanceof Error ? caught : new Error('알 수 없는 오류')
}

/** 새 묶음 — 이름을 묻는다. `rows` 는 함께 옮길(선택한) 줄의 수(0 이면 빈 묶음). */
export function GroupNameDialog({
  rows,
  onCreate,
  onClose,
}: {
  rows: number
  onCreate: (title: string) => Promise<void>
  onClose: () => void
}) {
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  async function submit() {
    const cleaned = title.trim()
    if (!cleaned) {
      setError(new Error('묶음 이름을 입력합니다.'))
      return
    }
    setBusy(true)
    setError(null)
    try {
      await onCreate(cleaned)
      onClose()
    } catch (caught) {
      setError(asError(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{rows ? '새 묶음' : '묶음 추가'}</DialogTitle>
          <DialogDescription>
            {rows
              ? `선택한 줄 ${rows}개를 새 묶음으로 묶습니다 — 묶음은 표의 맨 아래에 추가되고, 줄은 지금의 차례대로 그 안에 들어갑니다.`
              : '빈 묶음을 표의 맨 아래에 추가합니다 — 머리줄의 「+」 로 줄을 추가하거나 줄을 선택해 옮깁니다.'}
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="jv-group-title">묶음 이름</Label>
            <Input
              id="jv-group-title"
              value={title}
              maxLength={200}
              autoFocus
              placeholder="예: 설계 부문"
              onChange={(event) => setTitle(event.target.value)}
            />
          </div>
          <ErrorNotice error={error} />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
              취소
            </Button>
            <Button type="submit" disabled={busy}>
              생성
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/** 묶음 삭제 — **무엇이 사라지는지 적고** 둘 중에서 고른다. 줄이 없으면 하나만 묻는다. */
export function GroupRemoveDialog({
  group,
  rows,
  photos,
  onRemove,
  onClose,
}: {
  group: Group
  rows: number
  photos: number
  onRemove: (withRows: boolean) => Promise<void>
  onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const name = group.title ? `「${group.title}」` : '이'

  async function run(withRows: boolean) {
    setBusy(true)
    setError(null)
    try {
      await onRemove(withRows)
      onClose()
    } catch (caught) {
      // **창을 닫지 않는다.** 닫으면 오류가 어디에도 안 남고, 사람은 일이 된 줄 안다.
      setError(asError(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>묶음 삭제</DialogTitle>
          <DialogDescription>
            {rows
              ? `${name} 묶음을 삭제합니다. 이 묶음의 줄 ${rows}개를 어떻게 할지 선택합니다 — 「묶음만 해제」 는 줄을 표 맨 위(묶음 없음)의 맨 아래로 옮기고, 「줄까지 삭제」 는 줄 ${rows}개${photos ? `와 사진 ${photos}장` : ''}이 표에서 사라집니다(사진 파일은 남깁니다).`
              : `${name} 묶음을 삭제합니다 — 줄이 없는 묶음이라 머리줄만 사라집니다.`}
          </DialogDescription>
        </DialogHeader>
        <ErrorNotice error={error} />
        <DialogFooter className="gap-2 sm:gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
            취소
          </Button>
          <Button type="button" variant="outline" onClick={() => void run(false)} disabled={busy}>
            {rows ? `묶음만 해제 — 줄 ${rows}개는 묶음 없음으로` : '묶음 삭제'}
          </Button>
          {rows > 0 && (
            <Button
              type="button"
              variant="destructive"
              onClick={() => void run(true)}
              disabled={busy}
            >
              {`줄까지 삭제 — ${rows}개`}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
