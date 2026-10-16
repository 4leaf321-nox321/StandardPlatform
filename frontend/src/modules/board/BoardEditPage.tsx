/**
 * 글 작성 · 수정(ADR 0033).
 *
 * **첨부는 글이 생긴 뒤에 붙는다** — 첨부는 글의 id 를 가리키기 때문이다. 그래서 새 글에서는
 * 파일을 미리 골라 두고, 「등록」 에서 글을 만든 다음 한 장씩 업로드한다. 업로드에 실패한
 * 파일은 글 화면이 이름을 붙여 알린다(글은 이미 생겼으니 거기서 다시 업로드한다). 수정에서는
 * 첨부 패널을 그대로 쓴다.
 */

import { useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Paperclip, X } from 'lucide-react'

import { POST_TABLE, boardApi } from '@/modules/board/api'
import type { Post } from '@/modules/board/api'
import { AttachmentList } from '@/modules/files/AttachmentList'
import { attachmentApi } from '@/modules/files/api'
import { shownSize } from '@/modules/files/ImageViewer'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import { Textarea } from '@/shared/components/ui/textarea'
import { useResource } from '@/shared/hooks/useResource'

export default function BoardEditPage() {
  const { postId } = useParams()
  if (!postId) return <PostForm />
  return <ExistingPost key={postId} postId={postId} />
}

function ExistingPost({ postId }: { postId: string }) {
  const post = useResource(() => boardApi.get(postId), [postId])
  if (post.error) {
    return (
      <div className="space-y-4">
        <PageHeader title="게시글 수정" back={{ to: '/board', label: '게시판' }} />
        <ErrorNotice error={post.error} />
      </div>
    )
  }
  return post.data ? <PostForm existing={post.data} /> : null
}

function PostForm({ existing }: { existing?: Post }) {
  const navigate = useNavigate()
  const [title, setTitle] = useState(existing?.title ?? '')
  const [body, setBody] = useState(existing?.body ?? '')
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<Error | null>(null)
  const picker = useRef<HTMLInputElement>(null)
  const back = existing ? `/board/${existing.id}` : '/board'

  async function submit() {
    setBusy(true)
    setFailed(null)
    try {
      if (existing) {
        await boardApi.update(existing.id, { title, body })
        navigate(back)
        return
      }
      const made = await boardApi.create({ title, body })
      const uploadFailed: string[] = []
      for (const file of files) {
        try {
          await attachmentApi.upload({
            ownerTable: POST_TABLE,
            ownerId: made.id,
            workspaceSlug: null,
            file,
          })
        } catch {
          uploadFailed.push(file.name)
        }
      }
      navigate(`/board/${made.id}`, { state: { uploadFailed } })
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title={existing ? '게시글 수정' : '글 작성'}
        back={{ to: back, label: existing ? '게시글' : '게시판' }}
      />
      <ErrorNotice error={failed} />
      <div className="space-y-2">
        <Label htmlFor="board-title">제목</Label>
        <Input
          id="board-title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          maxLength={200}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="board-body">본문</Label>
        <Textarea
          id="board-body"
          value={body}
          onChange={(event) => setBody(event.target.value)}
          rows={14}
          maxLength={20000}
        />
      </div>
      {existing ? (
        <AttachmentList
          ownerTable={POST_TABLE}
          ownerId={existing.id}
          workspaceSlug={null}
          canEdit={existing.can_edit}
        />
      ) : (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => picker.current?.click()}
            >
              <Paperclip className="size-4" />
              파일 첨부
            </Button>
            <span className="text-muted-foreground text-xs">
              등록하면 글과 함께 업로드됩니다. 파일 하나에 50MB 까지.
            </span>
            <input
              ref={picker}
              type="file"
              multiple
              hidden
              aria-label="첨부할 파일"
              onChange={(event) => {
                const chosen = Array.from(event.target.files ?? [])
                setFiles((before) => [...before, ...chosen])
                event.target.value = ''
              }}
            />
          </div>
          {files.length > 0 && (
            <ul className="divide-y rounded-md border text-sm">
              {files.map((file, index) => (
                <li key={`${file.name}-${index}`} className="flex items-center gap-2 px-3 py-1.5">
                  <span className="min-w-0 flex-1 truncate">{file.name}</span>
                  <span className="text-muted-foreground text-xs">{shownSize(file.size)}</span>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={`${file.name} 제외`}
                    onClick={() => setFiles((before) => before.filter((_, at) => at !== index))}
                  >
                    <X className="size-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={() => navigate(back)} disabled={busy}>
          취소
        </Button>
        <Button onClick={() => void submit()} disabled={busy || !title.trim() || !body.trim()}>
          {existing ? '저장' : '등록'}
        </Button>
      </div>
    </div>
  )
}
