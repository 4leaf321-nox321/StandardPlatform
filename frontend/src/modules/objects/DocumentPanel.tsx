/**
 * 문서 속성 하나(ADR 0053) — 객체 상세의 본문 단에 선다. 백서 · 위키 페이지.
 *
 * - **보기:** 마크다운으로 그리고, 제목이 셋 이상이면 왼쪽에 목차. 길면 안쪽에서 넘긴다.
 * - **판:** 머리에 판 · 글자 수 · 마지막으로 고친 사람 · **토큰**(MCP 로 AI 가 썼으면 그 토큰 이름)을
 *   적는다 — AI 가 쓴 판을 사람이 검토하는 흐름의 근거다. 「판 이력」 이 옛 판을 열고 되돌린다.
 * - **고치기:** 작성 · 미리보기. 저장할 때 보고 고친 판을 보낸다 — 그사이 누가(사람이든 AI 든)
 *   먼저 고쳤으면 덮지 않고 회의 표와 같은 충돌 상자(「상대 글 사용」 · 「내 글로 덮어쓰기」)가 선다.
 * - **그림(ADR 0056):** 문서의 그림은 그 문서 자리(`owner_field` = 문서 키)의 첨부이고, 본문의 사진
 *   표시(`![캡션](photo:<id>?w=<폭>)`)가 자리 · 폭을 정한다. 「그림 추가」 · 붙여넣기(화면 캡처) ·
 *   끌어다 놓기는 업로드한 뒤 **커서 자리에** 표시를 넣는다. 본문에서 표시를 빼도 첨부는 남는다 —
 *   옛 판이 그 그림을 가리킨다. 삭제는 「이 문서의 그림」 에서, 본문 몇 곳이 쓰는지 보고 한다.
 */

import { useEffect, useRef, useState } from 'react'
import type { ClipboardEvent, DragEvent } from 'react'
import { useLocation } from 'react-router-dom'
import { FileText, History, ImagePlus, Pencil } from 'lucide-react'

import { attachmentApi } from '@/modules/files/api'
import type { Attachment } from '@/modules/files/api'
import { IMAGE_ACCEPT } from '@/modules/files/AttachmentList'
import { AttachmentThumbnail } from '@/modules/files/AttachmentThumbnail'
import { ImageViewer } from '@/modules/files/ImageViewer'
import { useAttachmentUpload } from '@/modules/files/useAttachmentUpload'
import { MarkdownView } from '@/modules/objects/MarkdownView'
import { insertPhotos, placedIn, usesOf } from '@/modules/objects/documentPhotos'
import { documentApi } from '@/modules/objects/documents'
import type { DocumentRevision, ObjectDocument } from '@/modules/objects/documents'
import type { PropertyDef } from '@/modules/ontology/api'
import { ApiError } from '@/shared/api/client'
import { pastedFiles } from '@/shared/components/AutoSaveText'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ConflictBox } from '@/shared/components/ConflictBox'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Input } from '@/shared/components/ui/input'
import { Textarea } from '@/shared/components/ui/textarea'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'
import { cn } from '@/shared/lib/utils'

interface Heading {
  id: string
  text: string
  level: number
}

/** 「판 3 · 2.1만 자 · 2026. 11. 6. 홍길동 · 토큰 「백필 봇」」 */
function MetaLine({ doc }: { doc: ObjectDocument }) {
  if (doc.revision === 0) return null
  return (
    <p className="text-muted-foreground flex flex-wrap items-center gap-x-2 text-xs tabular-nums">
      <span>판 {doc.revision}</span>
      <span>· {doc.chars.toLocaleString()}자</span>
      {doc.updated_at && (
        <span>
          · {shownDateTime(doc.updated_at)} {doc.updated_by_name ?? ''}
        </span>
      )}
      {doc.updated_via && (
        <span
          className="rounded border border-amber-500/50 px-1 text-amber-800 dark:text-amber-300"
          title="액세스 토큰(MCP · AI)으로 수정한 판입니다 — 사람이 검토합니다"
        >
          토큰 「{doc.updated_via}」
        </span>
      )}
    </p>
  )
}

/**
 * 이 문서의 그림 — 고칠 때만 선다. 본문에 다시 추가하거나 삭제한다. 본문이 안 쓰는 그림(AI 가
 * 올리기만 한 것 · 본문에서 뺀 것)에는 「본문에 없음」 을 붙인다 — 화면에는 안 보이므로.
 */
function PhotoShelf({
  photos,
  draft,
  disabled,
  onAdd,
  onRemove,
}: {
  photos: Attachment[]
  draft: string
  disabled: boolean
  onAdd: (id: string) => void
  onRemove: (photo: Attachment) => void
}) {
  const [open, setOpen] = useState<number | null>(null)
  const images = photos.filter((one) => one.is_image)
  if (images.length === 0) return null
  const placed = placedIn(draft)
  return (
    <div className="space-y-1">
      <p className="text-muted-foreground text-xs">이 문서의 그림 {images.length}장</p>
      <ul className="flex flex-wrap gap-2">
        {images.map((one, index) => (
          <li key={one.id} className="w-28 space-y-1 text-xs">
            <AttachmentThumbnail
              id={one.id}
              name={one.original_name}
              onOpen={() => setOpen(index)}
              className="size-28"
            />
            <p className="truncate" title={one.original_name}>
              {one.original_name}
            </p>
            {!placed.has(one.id.toLowerCase()) && (
              <span className="rounded border border-amber-500/50 px-1 text-amber-800 dark:text-amber-300">
                본문에 없음
              </span>
            )}
            <div className="flex gap-1">
              <Button
                size="sm"
                variant="outline"
                className="h-6 px-1.5 text-xs"
                disabled={disabled}
                onClick={() => onAdd(one.id)}
              >
                본문에 추가
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="text-destructive h-6 px-1.5 text-xs"
                disabled={disabled}
                onClick={() => onRemove(one)}
              >
                삭제
              </Button>
            </div>
          </li>
        ))}
      </ul>
      {open !== null && (
        <ImageViewer images={images} index={open} onIndex={setOpen} onClose={() => setOpen(null)} />
      )}
    </div>
  )
}

/** 판 목록 · 옛 판 보기 · 그 판으로 되돌리기. */
function RevisionsDialog({
  typeSlug,
  objectId,
  def,
  current,
  canEdit,
  photos,
  onClose,
  onRestore,
}: {
  typeSlug: string
  objectId: string
  def: PropertyDef
  current: number
  canEdit: boolean
  photos: Attachment[]
  onClose: () => void
  onRestore: (body: string, revision: number) => void
}) {
  const list = useResource(
    () => documentApi.revisions(typeSlug, objectId, def.key),
    [typeSlug, objectId, def.key],
  )
  const [picked, setPicked] = useState<number | null>(null)
  const shown = useResource(
    () =>
      picked === null
        ? Promise.resolve(null)
        : documentApi.revision(typeSlug, objectId, def.key, picked),
    [typeSlug, objectId, def.key, picked],
  )

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{def.label} — 판 이력</DialogTitle>
          <DialogDescription>
            고칠 때마다 판이 하나 오르고 본문 전체가 남습니다. 토큰이 적힌 판은 액세스 토큰(MCP ·
            AI)으로 수정한 것입니다.
          </DialogDescription>
        </DialogHeader>
        <ErrorNotice error={list.error ?? shown.error} />
        <div className="grid gap-4 md:grid-cols-[16rem_minmax(0,1fr)]">
          <ul className="divide-y rounded-md border text-sm">
            {(list.data ?? []).map((one: DocumentRevision) => (
              <li key={one.revision}>
                <button
                  type="button"
                  className={cn(
                    'hover:bg-muted/50 w-full px-3 py-2 text-left',
                    picked === one.revision && 'bg-muted',
                  )}
                  onClick={() => setPicked(one.revision)}
                >
                  <span className="font-medium tabular-nums">판 {one.revision}</span>
                  {one.revision === current && (
                    <span className="text-muted-foreground ml-1 text-xs">(지금)</span>
                  )}
                  <span className="text-muted-foreground block text-xs tabular-nums">
                    {shownDateTime(one.created_at)} {one.created_by_name ?? ''}
                  </span>
                  {one.via && (
                    <span className="block text-xs text-amber-800 dark:text-amber-300">
                      토큰 「{one.via}」
                    </span>
                  )}
                  {one.note && <span className="block text-xs">{one.note}</span>}
                </button>
              </li>
            ))}
          </ul>
          <div className="min-w-0">
            {shown.data ? (
              <div className="space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium tabular-nums">
                    판 {shown.data.revision} · {shown.data.chars.toLocaleString()}자
                  </p>
                  {canEdit && shown.data.revision !== current && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => onRestore(shown.data!.body, shown.data!.revision)}
                    >
                      이 판으로 복원
                    </Button>
                  )}
                </div>
                <div className="max-h-[60vh] overflow-y-auto rounded-md border p-3">
                  <MarkdownView body={shown.data.body} photos={photos} />
                </div>
              </div>
            ) : (
              <p className="text-muted-foreground text-sm">왼쪽에서 판을 선택합니다.</p>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export function DocumentPanel({
  typeSlug,
  objectId,
  def,
  onSaved,
}: {
  typeSlug: string
  objectId: string
  def: PropertyDef
  /** 저장한 뒤 — 상세가 다시 읽게(수정한 때 · 언급). */
  onSaved?: () => void
}) {
  const doc = useResource(
    () => documentApi.get(typeSlug, objectId, def.key),
    [typeSlug, objectId, def.key],
  )
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [base, setBase] = useState(0)
  const [note, setNote] = useState('')
  const [preview, setPreview] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  /** 그사이 먼저 저장된 지금 판 — 고르기 전까지 내 글은 그대로다. */
  const [conflict, setConflict] = useState<ObjectDocument | null>(null)
  const [history, setHistory] = useState(false)
  const [headings, setHeadings] = useState<Heading[]>([])
  const article = useRef<HTMLDivElement>(null)
  const editor = useRef<HTMLTextAreaElement>(null)
  const picker = useRef<HTMLInputElement>(null)
  /** 넣은 뒤 옮길 커서 — 글이 바뀐 다음 그림에서 옮긴다. */
  const caret = useRef<number | null>(null)
  const [removing, setRemoving] = useState<Attachment | null>(null)
  // 이 문서의 그림 — 그 문서 자리(`owner_field` = 문서 키)의 첨부.
  const photos = useResource(
    () => attachmentApi.list('objects', objectId, def.key),
    [objectId, def.key],
  )
  const upload = useAttachmentUpload(
    { ownerTable: 'objects', ownerId: objectId, ownerField: def.key },
    photos.reload,
  )
  const shelf = photos.data ?? []

  useEffect(() => {
    if (caret.current === null || !editor.current) return
    editor.current.focus()
    editor.current.setSelectionRange(caret.current, caret.current)
    caret.current = null
  })

  /** 커서 자리에 그림 표시를 넣는다 — 줄 중간이면 그 줄을 가른다. */
  function placeAtCaret(ids: string[]) {
    if (ids.length === 0) return
    const at = editor.current?.selectionStart ?? draft.length
    const next = insertPhotos(draft, at, ids)
    caret.current = next.caret
    setDraft(next.text)
  }

  async function addPhotos(files: File[]) {
    const made = await upload.uploadAll(files)
    placeAtCaret(made.map((one) => one.id))
  }

  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    // 글이 함께 오면 글을 붙인다(`pastedFiles`) — 화면 캡처처럼 그림만 올 때만 업로드한다.
    const files = pastedFiles(event)
    if (files.length === 0) return
    event.preventDefault()
    void addPhotos(files)
  }

  function onDrop(event: DragEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.dataTransfer?.files ?? [])
    if (files.length === 0) return
    event.preventDefault()
    // 놓은 자리로 커서가 옮겨 가지 않으므로, 지금 커서 자리에 넣는다.
    void addPhotos(files)
  }

  const data = doc.data
  const body = data?.body ?? ''
  const { hash } = useLocation()
  // 목차는 그려진 제목에서 읽는다 — 마크다운을 따로 풀면 그린 id 와 어긋날 수 있다.
  useEffect(() => {
    if (editing || !article.current) {
      setHeadings([])
      return
    }
    const found = Array.from(article.current.querySelectorAll('h1[id], h2[id], h3[id]')).map(
      (one) => ({ id: one.id, text: one.textContent ?? '', level: Number(one.tagName[1]) }),
    )
    setHeadings(found)
    // 검색의 「뜻이 가까운 것」 이 절을 가리켜 들어왔으면(`#<제목 id>`) 그 절로 옮긴다.
    const wanted = hash ? decodeURIComponent(hash.slice(1)) : ''
    if (wanted) {
      article.current.querySelector(`#${CSS.escape(wanted)}`)?.scrollIntoView({ block: 'start' })
    }
  }, [body, editing, hash])

  function startEditing(text = body, why = '') {
    setDraft(text)
    setBase(data?.revision ?? 0)
    setNote(why)
    setPreview(false)
    setConflict(null)
    setError(null)
    setEditing(true)
  }

  async function save(onto = base) {
    setSaving(true)
    setError(null)
    try {
      await documentApi.put(typeSlug, objectId, def.key, draft, onto, note.trim())
      setEditing(false)
      setConflict(null)
      doc.reload()
      onSaved?.()
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        // 덮지 않는다 — 지금 판을 읽어 나란히 보인다.
        setConflict(await documentApi.get(typeSlug, objectId, def.key))
      } else {
        setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <section aria-label={def.label} className="space-y-3 rounded-md border p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-0.5">
          <h2 className="flex items-center gap-2 text-base font-semibold">
            <FileText className="size-4" />
            {def.label}
          </h2>
          {data && <MetaLine doc={data} />}
        </div>
        {data && !editing && (
          <div className="flex gap-2">
            {data.revision > 0 && (
              <Button size="sm" variant="outline" onClick={() => setHistory(true)}>
                <History className="mr-1 size-4" />판 이력
              </Button>
            )}
            {data.can_edit && (
              <Button size="sm" variant="outline" onClick={() => startEditing()}>
                <Pencil className="mr-1 size-4" />
                {data.revision === 0 ? '작성' : '수정'}
              </Button>
            )}
          </div>
        )}
      </div>

      <ErrorNotice error={error ?? doc.error} />

      {editing ? (
        <div className="space-y-2">
          <div className="flex gap-1" role="tablist">
            <Button
              size="sm"
              variant={preview ? 'ghost' : 'secondary'}
              role="tab"
              aria-selected={!preview}
              onClick={() => setPreview(false)}
            >
              작성
            </Button>
            <Button
              size="sm"
              variant={preview ? 'secondary' : 'ghost'}
              role="tab"
              aria-selected={preview}
              onClick={() => setPreview(true)}
            >
              미리보기
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={saving || preview || Boolean(upload.progress)}
              title="이미지를 업로드해 커서 자리에 추가합니다 — 붙여넣기(화면 캡처) · 끌어다 놓기도 됩니다"
              onClick={() => picker.current?.click()}
            >
              <ImagePlus className="mr-1 size-4" />
              그림 추가
            </Button>
            <input
              ref={picker}
              type="file"
              accept={IMAGE_ACCEPT}
              multiple
              hidden
              aria-label={`${def.label} 그림 선택`}
              onChange={(event) => {
                const files = Array.from(event.target.files ?? [])
                event.target.value = ''
                void addPhotos(files)
              }}
            />
            <span className="text-muted-foreground ml-auto self-center text-xs tabular-nums">
              {upload.progress ??
                `${draft.length.toLocaleString()}자 · 마크다운(# 제목 · - 목록 · | 표 · 그림 폭은 ?w=50)`}
            </span>
          </div>
          <ErrorNotice error={upload.error} />
          {preview ? (
            <div className="min-h-[40vh] rounded-md border p-3">
              <MarkdownView body={draft} photos={shelf} />
            </div>
          ) : (
            <Textarea
              ref={editor}
              aria-label={`${def.label} 본문`}
              className="min-h-[50vh] font-mono text-sm"
              value={draft}
              disabled={saving}
              onChange={(event) => setDraft(event.target.value)}
              onPaste={onPaste}
              onDrop={onDrop}
              onDragOver={(event) => {
                if (event.dataTransfer?.types?.includes('Files')) event.preventDefault()
              }}
            />
          )}
          <PhotoShelf
            photos={shelf}
            draft={draft}
            disabled={saving || preview}
            onAdd={(id) => placeAtCaret([id])}
            onRemove={setRemoving}
          />
          {conflict && (
            <ConflictBox
              by={
                conflict.updated_via ? `토큰 「${conflict.updated_via}」` : conflict.updated_by_name
              }
              at={conflict.updated_at}
              theirs={conflict.body}
              mine={draft}
              onTakeTheirs={() => {
                setDraft(conflict.body)
                setBase(conflict.revision)
                setConflict(null)
              }}
              onKeepMine={() => {
                setBase(conflict.revision)
                void save(conflict.revision)
              }}
            />
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Input
              aria-label="수정 사유"
              placeholder="수정 사유(선택) — 판 이력에 남습니다"
              className="h-8 max-w-sm text-sm"
              value={note}
              disabled={saving}
              onChange={(event) => setNote(event.target.value)}
            />
            <Button
              size="sm"
              disabled={saving || Boolean(conflict) || draft === body}
              onClick={() => save()}
            >
              저장
            </Button>
            <Button size="sm" variant="outline" disabled={saving} onClick={() => setEditing(false)}>
              취소
            </Button>
          </div>
        </div>
      ) : data && data.revision === 0 ? (
        <p className="text-muted-foreground text-sm">
          아직 작성하지 않았습니다.
          {data.can_edit &&
            ' 「작성」 으로 쓰거나, AI 가 MCP(document_write)로 넣습니다 — 원본이 Word 면 마크다운으로 바꿔 넣습니다.'}
        </p>
      ) : data ? (
        <div
          className={cn('grid gap-4', headings.length >= 3 && 'lg:grid-cols-[11rem_minmax(0,1fr)]')}
        >
          {headings.length >= 3 && (
            <nav aria-label={`${def.label} 목차`} className="hidden text-xs lg:block">
              <p className="text-muted-foreground mb-1 font-medium">목차</p>
              <ul className="max-h-[70vh] space-y-1 overflow-y-auto">
                {headings.map((one) => (
                  <li key={one.id} style={{ paddingLeft: `${(one.level - 1) * 0.75}rem` }}>
                    <button
                      type="button"
                      className="text-muted-foreground hover:text-foreground text-left"
                      onClick={() =>
                        article.current
                          ?.querySelector(`#${CSS.escape(one.id)}`)
                          ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                      }
                    >
                      {one.text}
                    </button>
                  </li>
                ))}
              </ul>
            </nav>
          )}
          <div ref={article} className="max-h-[70vh] min-w-0 overflow-y-auto pr-1">
            <MarkdownView body={body} photos={shelf} />
          </div>
        </div>
      ) : null}

      {history && data && (
        <RevisionsDialog
          typeSlug={typeSlug}
          objectId={objectId}
          def={def}
          current={data.revision}
          canEdit={data.can_edit}
          photos={shelf}
          onClose={() => setHistory(false)}
          onRestore={(text, revision) => {
            setHistory(false)
            startEditing(text, `판 ${revision} 으로 복원`)
          }}
        />
      )}

      <ConfirmDialog
        open={removing !== null}
        title="그림 삭제"
        destructive
        confirmLabel="삭제"
        description={
          removing && (
            <>
              「{removing.original_name}」 을(를) 이 문서에서 삭제합니다.{' '}
              {usesOf(draft, removing.id) > 0
                ? `작성 중인 본문의 ${usesOf(draft, removing.id)}곳이 이 그림을 씁니다 — 그 자리에는 「삭제됐거나 이 문서의 그림이 아닙니다」 가 표시됩니다. `
                : '작성 중인 본문은 이 그림을 쓰지 않습니다. '}
              이 그림을 쓰던 옛 판에서도 보이지 않고, 되돌릴 수 없습니다.
            </>
          )
        }
        onConfirm={async () => {
          if (!removing) return
          await attachmentApi.remove(removing.id)
          setRemoving(null)
          photos.reload()
        }}
        onClose={() => setRemoving(null)}
      />
    </section>
  )
}
