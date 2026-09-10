/**
 * 어떤 자료에 붙은 첨부 — 목록 · 올리기 · 내려받기 · 떼기.
 *
 * 화면이 아니라 **패널**이다. 도메인 상세 화면이 이것을 한 줄로 끼워 쓴다:
 *
 *     <AttachmentList ownerTable="parts" ownerId={part.id} workspaceSlug={part.workspace} />
 *
 * ## 내려받기는 평범한 링크로 안 된다
 *
 * access 토큰은 메모리에만 있어서 브라우저가 스스로 여는 주소(`a href`)에는 안
 * 실린다. 그러면 새 탭에서 401 이 나는데 **화면에는 아무 표시도 안 뜬다** —
 * 사용자는 아무 일도 안 일어난 것처럼 본다. `downloadFile` 이 그것을 대신한다.
 */

import { useRef, useState } from 'react'
import { Download, Paperclip, Trash2, Upload } from 'lucide-react'

import { attachmentApi } from '@/modules/files/api'
import type { Attachment } from '@/modules/files/api'
import { ApiError } from '@/shared/api/client'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'
import { shownDate } from '@/shared/lib/datetime'

interface AttachmentListProps {
  ownerTable: string
  ownerId: string
  /**
   * 그 행의 **어느 자리**의 첨부인가. 안 주면 행 전체다.
   *
   * 온톨로지의 `file` 속성이 이것을 준다 — 속성이 둘이면 목록도 둘로 갈려야
   * 한다. 안 가르면 「도면」 칸에 「시험성적서」 가 섞여 보이고, 그 목록은
   * 무엇도 말해 주지 못한다.
   */
  ownerField?: string | null
  /** 제목. 자리별로 나눠 쓸 때 무엇의 첨부인지 적는다. */
  title?: string
  /** 어느 부서의 것인가. 비우면 전역 — **시스템 관리자만 붙일 수 있다.** */
  workspaceSlug?: string | null
  /** 고칠 수 있는 사람인가. 서버가 최종 판정을 한다 — 여기는 표시일 뿐이다. */
  canEdit?: boolean
}

/** 1.4MB 처럼. 바이트를 그대로 보여 주면 사람이 자릿수를 센다. */
function shownSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

export function AttachmentList({
  ownerTable,
  ownerId,
  ownerField,
  title = '첨부',
  workspaceSlug,
  canEdit = false,
}: AttachmentListProps) {
  const list = useResource(
    () => attachmentApi.list(ownerTable, ownerId, ownerField),
    [ownerTable, ownerId, ownerField],
  )
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  async function act(run: () => Promise<unknown>) {
    setError(null)
    setBusy(true)
    try {
      await run()
      list.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const rows = list.data ?? []

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <Paperclip className="size-4" />
          {title}
          {rows.length > 0 && (
            <span className="text-muted-foreground text-sm font-normal tabular-nums">
              {rows.length}
            </span>
          )}
        </h2>
        {canEdit && (
          <>
            <input
              ref={fileRef}
              id={`attach-${ownerTable}-${ownerId}`}
              type="file"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0]
                // **값을 비운다.** 안 비우면 같은 파일을 다시 고를 때 change 가
                // 안 나고, 사람은 화면이 먹통이라고 읽는다.
                event.target.value = ''
                if (file) {
                  act(() =>
                    attachmentApi.upload({
                      ownerTable,
                      ownerId,
                      ownerField,
                      workspaceSlug: workspaceSlug ?? null,
                      file,
                    }),
                  )
                }
              }}
            />
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => fileRef.current?.click()}
            >
              <Upload className="size-4" />
              {busy ? '올리는 중…' : '파일 올리기'}
            </Button>
          </>
        )}
      </div>

      <ErrorNotice error={error ?? list.error} />

      {rows.length === 0 ? (
        <EmptyState
          title="첨부가 없습니다"
          hint={canEdit ? '위에서 파일을 올릴 수 있습니다.' : '아직 올라온 파일이 없습니다.'}
        />
      ) : (
        <ul className="divide-y rounded-md border">
          {rows.map((one: Attachment) => (
            <li key={one.id} className="flex items-center gap-3 px-3 py-2 text-sm">
              <span className="min-w-0 flex-1 truncate">{one.original_name}</span>
              <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
                {shownSize(one.size_bytes)}
              </span>
              <span className="text-muted-foreground shrink-0 text-xs">
                {shownDate(one.created_at)}
              </span>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`${one.original_name} 내려받기`}
                onClick={() => act(() => attachmentApi.download(one.id, one.original_name))}
              >
                <Download className="size-4" />
              </Button>
              {canEdit && (
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`${one.original_name} 떼기`}
                  onClick={() => act(() => attachmentApi.remove(one.id))}
                >
                  <Trash2 className="size-4" />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
