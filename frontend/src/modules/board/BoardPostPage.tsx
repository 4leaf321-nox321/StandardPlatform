/**
 * 게시글 하나(ADR 0033) — 본문 · 첨부 · 댓글.
 *
 * 수정 · 삭제 · 첨부 업로드는 **작성자와 시스템 관리자**(`can_edit` — 서버가 다시 판정한다),
 * 고정은 시스템 관리자. 첨부 패널은 객체 상세와 같은 것이다 — 끌어다 놓기 · 붙여넣기로도
 * 업로드된다.
 *
 * 글이 바뀌면(알림에서 다른 글로) **화면을 새로 세운다**(`key`) — 앞 글의 댓글 입력 · 확인 창이
 * 다음 글에 남지 않게.
 */

import { useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { Pin } from 'lucide-react'

import { POST_TABLE, boardApi } from '@/modules/board/api'
import type { BoardComment, Post } from '@/modules/board/api'
import { AttachmentList } from '@/modules/files/AttachmentList'
import { useAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin } from '@/shared/auth/roles'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Textarea } from '@/shared/components/ui/textarea'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

export default function BoardPostPage() {
  const { postId = '' } = useParams()
  return <PostView key={postId} postId={postId} />
}

function Written({ name, at, edited }: { name: string; at: string; edited: string | null }) {
  return (
    <span className="text-muted-foreground text-xs">
      {name} · {shownDateTime(at)}
      {edited && ` · 수정됨 ${shownDateTime(edited)}`}
    </span>
  )
}

function PostView({ postId }: { postId: string }) {
  const { user } = useAuth()
  const admin = isSystemAdmin(user)
  const navigate = useNavigate()
  const location = useLocation()
  const uploadFailed = (location.state as { uploadFailed?: string[] } | null)?.uploadFailed
  const post = useResource(() => boardApi.get(postId), [postId])
  const [removing, setRemoving] = useState(false)
  const [failed, setFailed] = useState<Error | null>(null)

  async function pin(one: Post) {
    setFailed(null)
    try {
      await boardApi.pin(one.id, !one.pinned)
      post.reload()
    } catch (caught) {
      setFailed(caught as Error)
    }
  }

  const one = post.data
  return (
    <div className="space-y-6">
      <PageHeader
        title={one?.title ?? '게시글'}
        back={{ to: '/board', label: '게시판' }}
        actions={
          one && (
            <div className="flex items-center gap-2">
              {admin && (
                <Button size="sm" variant="outline" onClick={() => void pin(one)}>
                  {one.pinned ? '고정 해제' : '고정'}
                </Button>
              )}
              {one.can_edit && (
                <>
                  <Button asChild size="sm" variant="outline">
                    <Link to={`/board/${one.id}/edit`}>수정</Link>
                  </Button>
                  <Button size="sm" variant="destructive" onClick={() => setRemoving(true)}>
                    삭제
                  </Button>
                </>
              )}
            </div>
          )
        }
      />
      {uploadFailed && uploadFailed.length > 0 && (
        <p className="text-destructive text-sm" role="alert">
          업로드하지 못한 파일이 있습니다: {uploadFailed.join(', ')} — 아래 첨부에서 다시 업로드해
          주세요.
        </p>
      )}
      <ErrorNotice error={failed ?? post.error} />
      {one && (
        <>
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              {one.pinned && (
                <Badge variant="secondary" className="gap-1">
                  <Pin className="size-3" />
                  고정
                </Badge>
              )}
              <Written name={one.author_name} at={one.created_at} edited={one.edited_at} />
            </div>
            <p className="text-sm whitespace-pre-wrap">{one.body}</p>
          </div>
          <AttachmentList
            ownerTable={POST_TABLE}
            ownerId={one.id}
            workspaceSlug={null}
            canEdit={one.can_edit}
          />
          <Comments post={one} onChanged={post.reload} />
          <ConfirmDialog
            open={removing}
            title={`「${one.title}」 을 삭제합니다`}
            description={`댓글 ${one.comment_count}개와 첨부 ${one.attachment_count}개가 함께 목록에서 사라지고, 첨부는 다운로드할 수 없게 됩니다. 삭제한 글은 화면에서 복원할 수 없습니다.`}
            confirmLabel="삭제"
            destructive
            onConfirm={async () => {
              await boardApi.remove(one.id)
              setRemoving(false)
              navigate('/board')
            }}
            onClose={() => setRemoving(false)}
          />
        </>
      )}
    </div>
  )
}

function Comments({ post, onChanged }: { post: Post; onChanged: () => void }) {
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<Error | null>(null)

  async function submit() {
    setBusy(true)
    setFailed(null)
    try {
      await boardApi.comment(post.id, draft)
      setDraft('')
      onChanged()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-3" aria-label="댓글">
      <h2 className="text-sm font-medium">댓글 {post.comments.length}</h2>
      {post.comments.length > 0 && (
        <ul className="divide-y rounded-md border">
          {post.comments.map((one) => (
            <CommentRow key={one.id} one={one} onChanged={onChanged} />
          ))}
        </ul>
      )}
      <ErrorNotice error={failed} />
      <div className="space-y-2">
        <Textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="댓글을 입력하세요"
          aria-label="새 댓글"
          rows={3}
          maxLength={2000}
        />
        <div className="flex justify-end">
          <Button size="sm" onClick={() => void submit()} disabled={busy || !draft.trim()}>
            댓글 작성
          </Button>
        </div>
      </div>
    </section>
  )
}

function CommentRow({ one, onChanged }: { one: BoardComment; onChanged: () => void }) {
  const [editing, setEditing] = useState<string | null>(null)
  const [removing, setRemoving] = useState(false)
  const [failed, setFailed] = useState<Error | null>(null)

  async function save(body: string) {
    setFailed(null)
    try {
      await boardApi.updateComment(one.id, body)
      setEditing(null)
      onChanged()
    } catch (caught) {
      setFailed(caught as Error)
    }
  }

  return (
    <li className="space-y-1 px-3 py-2 text-sm">
      <div className="flex items-center justify-between gap-2">
        <Written name={one.author_name} at={one.created_at} edited={one.edited_at} />
        {one.can_edit && editing === null && (
          <div className="flex gap-1">
            <Button size="sm" variant="ghost" onClick={() => setEditing(one.body)}>
              수정
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRemoving(true)}>
              삭제
            </Button>
          </div>
        )}
      </div>
      {editing === null ? (
        <p className="whitespace-pre-wrap">{one.body}</p>
      ) : (
        <div className="space-y-2">
          <Textarea
            value={editing}
            onChange={(event) => setEditing(event.target.value)}
            aria-label="댓글 수정"
            rows={3}
            maxLength={2000}
          />
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
              취소
            </Button>
            <Button size="sm" onClick={() => void save(editing)} disabled={!editing.trim()}>
              저장
            </Button>
          </div>
        </div>
      )}
      <ErrorNotice error={failed} />
      <ConfirmDialog
        open={removing}
        title="댓글을 삭제합니다"
        description="삭제한 댓글은 화면에서 복원할 수 없습니다."
        confirmLabel="삭제"
        destructive
        onConfirm={async () => {
          await boardApi.removeComment(one.id)
          setRemoving(false)
          onChanged()
        }}
        onClose={() => setRemoving(false)}
      />
    </li>
  )
}
