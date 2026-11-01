/**
 * 글 칸 — 글과 사진이 **차례대로** 서고, 사진은 고른 폭으로(칸 너비의 %). 사용자 요청 「사진 크기
 * 조정 하거나, 글 사이에서 위치 이동을 할 수 있게」(2026-10-31).
 *
 * 저장은 칸 글 한 덩이다 — 사진의 자리 · 폭은 글 안의 한 줄 표시(`cellText` — `![](photo:<id>?w=50)`).
 * 그래서 자동 저장 · 덮어쓰기 보호(본 값) · 충돌 상자 · 자동 반영이 1단계의 글 칸과 같다. 사진은
 * **그 칸의 첨부가 정본**이라, 표시가 없는 사진은 끝에 서고 모르는 id 의 표시는 그리지 않는다.
 *
 * - 고칠 때는 글 조각(입력 칸)과 사진이 번갈아 선다 — 사진 앞 · 사이 · 뒤에 빈 조각이 있어 그 자리에도
 *   쓴다. 사진은 끌어 다른 조각의 위 · 아래로 옮기고(↑ ↓ 단추는 한 줄씩), 오른쪽 아래 모서리를 끌어
 *   크기를 바꾼다(10% 단위로 붙는다). 빠른 폭 25 · 50 · 75 · 100%.
 * - 붙여넣기 · 끌어다 놓기 · 「사진」 단추는 **커서 자리에** 넣는다(줄 중간이면 그 줄을 가른다).
 * - 사진 삭제는 내 글에서 그 표시 줄을 먼저 빼 저장하고 첨부를 뗀다(서버도 빼지만, 순서를 지키면 내
 *   저장과 서버의 고침이 부딪치지 않는다).
 * - 볼 때 · 인쇄할 때는 글 문단과 사진이 차례대로, 이어진 사진은 한 줄에 나란히.
 */

import { useEffect, useId, useRef, useState } from 'react'
import type { ClipboardEvent, DragEvent, PointerEvent as ReactPointerEvent, ReactNode } from 'react'
import { ArrowDown, ArrowUp, ImageIcon, ImagePlus, Trash2 } from 'lucide-react'

import {
  QUICK_WIDTHS,
  blocksOf,
  caretIn,
  insertedPhotos,
  movedPhoto,
  placedPhoto,
  readableText,
  resizedPhoto,
  snapWidth,
  withTextBlock,
  withoutPhoto,
} from '@/extensions/jajuverify/cellText'
import type { Block, Caret } from '@/extensions/jajuverify/cellText'
import { OWNER_TABLE } from '@/extensions/jajuverify/api'
import type { Photo } from '@/extensions/jajuverify/api'
import { attachmentApi, attachmentPaths } from '@/modules/files/api'
import type { Attachment } from '@/modules/files/api'
import { IMAGE_ACCEPT } from '@/modules/files/AttachmentList'
import { ImageViewer } from '@/modules/files/ImageViewer'
import { useAttachmentUpload } from '@/modules/files/useAttachmentUpload'
import { pastedFiles } from '@/shared/components/AutoSaveText'
import { ConflictBox } from '@/shared/components/ConflictBox'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { SaveState } from '@/shared/components/SaveState'
import { useAutoSave } from '@/shared/hooks/useAutoSave'
import { useBlobUrl } from '@/shared/hooks/useBlobUrl'
import { cn } from '@/shared/lib/utils'

/** 끌어 옮기는 사진의 표 — 파일 끌기와 가른다. */
const PHOTO_DRAG = 'application/x-jv-photo'

function asError(caught: unknown): Error {
  return caught instanceof Error ? caught : new Error('알 수 없는 오류')
}

/** 사진 한 장 — 폭이 크면 원본을, 작으면 미리보기(긴 변 320px)를 받는다. */
function CellImage({
  photo,
  width,
  onOpen,
  className,
}: {
  photo: Photo
  /** 칸 너비 대비 % — 50 을 넘으면 원본을 받는다. */
  width: number
  onOpen: () => void
  className?: string
}) {
  const ratio = photo.width && photo.height ? `${photo.width} / ${photo.height}` : '4 / 3'
  return (
    <button
      type="button"
      onClick={onOpen}
      title={photo.name}
      aria-label={`${photo.name} 크게 보기`}
      style={{ aspectRatio: ratio }}
      className={cn('bg-muted/30 block w-full overflow-hidden rounded-sm border', className)}
    >
      {/* 칸 폭의 절반을 넘게 그리면 미리보기(320px)로는 흐리다 — 그때만 원본을 받는다. */}
      {width > 50 ? <LargeBody photo={photo} /> : <ImageBody photo={photo} />}
    </button>
  )
}

function ImageBody({ photo }: { photo: Photo }) {
  const blob = useBlobUrl(attachmentPaths.thumbnail(photo.id))
  return blob.url ? (
    <img src={blob.url} alt={photo.name} className="size-full object-contain" />
  ) : (
    <span className="text-muted-foreground flex size-full items-center justify-center">
      <ImageIcon className="size-5" />
    </span>
  )
}

function LargeBody({ photo }: { photo: Photo }) {
  const blob = useBlobUrl(attachmentPaths.content(photo.id))
  return blob.url ? (
    <img src={blob.url} alt={photo.name} className="size-full object-contain" />
  ) : (
    <ImageBody photo={photo} />
  )
}

function asAttachment(one: Photo, ownerId: string, field: string): Attachment {
  return {
    id: one.id,
    owner_table: OWNER_TABLE,
    owner_id: ownerId,
    owner_field: field,
    original_name: one.name,
    content_type: '',
    size_bytes: one.size_bytes,
    sha256: '',
    created_at: '',
    is_image: one.is_image,
    width: one.width,
    height: one.height,
  }
}

/** 볼 때 · 인쇄할 때 — 글 문단과 사진이 차례대로, 이어진 사진은 나란히. */
function ReadBlocks({
  blocks,
  photos,
  onOpen,
  className,
}: {
  blocks: Block[]
  photos: Map<string, Photo>
  onOpen: (id: string) => void
  className?: string
}) {
  // 이어진 사진은 한 줄에 — 50% 둘이면 나란히 선다.
  const runs: (Block | Block[])[] = []
  for (const one of blocks) {
    const last = runs[runs.length - 1]
    if (one.kind === 'photo') {
      if (Array.isArray(last)) last.push(one)
      else runs.push([one])
    } else if (one.text !== '') runs.push(one)
  }
  return (
    <div className={cn('space-y-1', className)}>
      {runs.map((run, index) =>
        Array.isArray(run) ? (
          <div key={`p${index}`} className="flex flex-wrap items-start gap-1">
            {run.map((one) => {
              if (one.kind !== 'photo') return null
              const photo = photos.get(one.id)
              if (!photo) return null
              return (
                <div
                  key={one.id}
                  className="break-inside-avoid"
                  style={{ width: `calc(${one.width}% - 0.25rem)` }}
                >
                  <CellImage photo={photo} width={one.width} onOpen={() => onOpen(one.id)} />
                </div>
              )
            })}
          </div>
        ) : (
          run.kind === 'text' && (
            <div key={`t${index}`} className="text-sm break-words whitespace-pre-wrap">
              {run.text}
            </div>
          )
        ),
      )}
    </div>
  )
}

export function PhotoTextCell({
  value,
  photos,
  ownerId,
  field,
  label,
  canWrite,
  placeholder,
  autoFocus = false,
  notice,
  onSave,
  onFocusChange,
  onPhotosChanged,
}: {
  value: string
  /** 그 칸의 첨부(정본). */
  photos: Photo[]
  ownerId: string
  field: string
  /** 칸의 이름 — 첫 글 조각 · 단추 · 시험이 이것으로 찾는다. */
  label: string
  canWrite: boolean
  placeholder?: string
  autoFocus?: boolean
  /** 칸에 들어가 있는 동안의 안내(「김OO 님이 이 칸을 수정하는 중입니다」). */
  notice?: string
  onSave: (next: string, base: string) => Promise<void>
  onFocusChange?: (focused: boolean) => void
  /** 사진을 붙이거나 뗐다 — 한 장을 다시 읽는다. */
  onPhotosChanged: () => void
}) {
  const id = useId()
  const box = useRef<HTMLDivElement>(null)
  const pick = useRef<HTMLInputElement>(null)
  const [focused, setFocused] = useState(false)
  const [viewing, setViewing] = useState<number | null>(null)
  const [failed, setFailed] = useState<Error | null>(null)
  const [dragging, setDragging] = useState(false)
  /** 크기를 끄는 동안의 폭(%) — 놓으면 글에 적는다. */
  const [sizing, setSizing] = useState<{ id: string; width: number } | null>(null)
  /** 마지막 커서 자리 — 「사진」 단추 · 끌어다 놓기가 여기에 넣는다. */
  const caret = useRef<Caret | null>(null)
  const focusRef = useRef(onFocusChange)
  focusRef.current = onFocusChange
  const auto = useAutoSave(value, onSave, { hold: focused })
  const upload = useAttachmentUpload({ ownerTable: OWNER_TABLE, ownerId, ownerField: field })

  const images = photos.filter((one) => one.is_image)
  const files = photos.filter((one) => !one.is_image)
  const byId = new Map(images.map((one) => [one.id.toLowerCase(), one]))
  const known = images.map((one) => one.id)
  const text = canWrite ? auto.draft : value

  useEffect(() => {
    if (!autoFocus || !box.current) return
    const first = box.current.querySelector('textarea')
    first?.focus()
    first?.select()
  }, [autoFocus])

  function write(next: string) {
    auto.change(next)
    return auto.flush()
  }

  async function addFiles(chosen: File[], at: Caret | null) {
    setFailed(null)
    const made = await upload.uploadAll(chosen)
    const shots = made.filter((one) => one.is_image).map((one) => one.id)
    if (shots.length) await write(insertedPhotos(auto.read(), at, shots))
    if (made.length) onPhotosChanged()
  }

  async function remove(photoId: string) {
    setFailed(null)
    const now = auto.read()
    const next = withoutPhoto(now, photoId)
    if (next !== now && !(await write(next))) return // 저장이 안 됐으면 떼지 않는다
    try {
      await attachmentApi.remove(photoId)
      onPhotosChanged()
    } catch (caught) {
      setFailed(asError(caught))
    }
  }

  function startResize(event: ReactPointerEvent, photoId: string, width: number) {
    const area = box.current
    if (!area) return
    event.preventDefault()
    const full = area.getBoundingClientRect().width || 1
    const startX = event.clientX
    let now = width
    const onMove = (move: PointerEvent) => {
      now = snapWidth(width + ((move.clientX - startX) / full) * 100)
      setSizing({ id: photoId, width: now })
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      setSizing(null)
      if (now !== width) void write(resizedPhoto(auto.read(), photoId, now))
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  /** 끌어 온 사진을 그 덩어리의 위 · 아래로. */
  function dropPhoto(event: DragEvent, block: Block, lines: number) {
    if (!event.dataTransfer.types.includes(PHOTO_DRAG)) return
    const photoId = event.dataTransfer.getData(PHOTO_DRAG).toLowerCase()
    // 다른 칸의 사진은 받지 않는다 — 그 칸의 첨부가 아니라 여기서는 그려지지 않는 표시가 된다.
    if (!byId.has(photoId)) return
    event.preventDefault()
    event.stopPropagation()
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
    const below = event.clientY > rect.top + rect.height / 2
    const line =
      block.kind === 'text'
        ? below
          ? block.start + block.count
          : block.start
        : block.placed
          ? block.line + (below ? 1 : 0)
          : lines
    void write(placedPhoto(auto.read(), photoId, line))
  }

  const viewer =
    viewing !== null && images[viewing] ? (
      <ImageViewer
        images={images.map((one) => asAttachment(one, ownerId, field))}
        index={viewing}
        onIndex={setViewing}
        onClose={() => setViewing(null)}
      />
    ) : null
  const open = (photoId: string) =>
    setViewing(images.findIndex((one) => one.id.toLowerCase() === photoId))

  if (!canWrite) {
    const blocks = blocksOf(value, known, false)
    return (
      <div aria-label={label} className="space-y-1">
        {blocks.length === 0 && files.length === 0 ? (
          <span className="text-muted-foreground text-sm print:hidden">—</span>
        ) : (
          <ReadBlocks blocks={blocks} photos={byId} onOpen={open} />
        )}
        {/* 치는 사이에 수정할 수 없게 됐다(회의 확정 …) — 저장 못 한 글을 말없이 버리지 않는다. */}
        {auto.unsaved && (
          <div className="rounded border border-amber-500/50 bg-amber-500/5 p-1.5 text-xs print:hidden">
            <p className="font-medium text-amber-800 dark:text-amber-300">
              저장하지 못한 내 글 — 이제 수정할 수 없는 칸입니다. 필요하면 복사해 둡니다.
            </p>
            <p className="mt-1 break-words whitespace-pre-wrap select-all">{auto.draft}</p>
          </div>
        )}
        <FileLinks files={files} />
        {viewer}
      </div>
    )
  }

  const blocks = blocksOf(text, known, true)
  const lineCount = text === '' ? 0 : text.split('\n').length
  let textIndex = 0

  return (
    <div
      ref={box}
      className={cn(
        'space-y-1 rounded-sm',
        dragging && 'ring-primary/60 bg-primary/5 ring-2 ring-inset',
      )}
      onFocus={() => {
        if (focused) return
        setFocused(true)
        focusRef.current?.(true)
      }}
      onBlur={(event) => {
        if (box.current?.contains(event.relatedTarget as Node | null)) return
        setFocused(false)
        focusRef.current?.(false)
        void auto.flush()
      }}
      onDragOver={(event) => {
        if (!event.dataTransfer.types.includes('Files')) return
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        setDragging(false)
        if (!event.dataTransfer.files?.length) return
        event.preventDefault()
        void addFiles(Array.from(event.dataTransfer.files), focused ? caret.current : null)
      }}
    >
      {/* 인쇄에는 같은 덩어리를 읽는 모양으로 — 입력 칸 대신 글이 서고(쪽이 넘어가면 잘리므로), 빈
          조각은 빠져 이어진 사진이 나란히 선다. 사진을 두 벌 그리지 않는다(원본을 두 번 받는다). */}
      <div className="space-y-1 print:flex print:flex-wrap print:items-start print:gap-1 print:space-y-0">
        {blocks.map((block, index) => {
          if (block.kind === 'text') {
            const first = textIndex === 0
            textIndex += 1
            const name = first ? label : `${label} — ${textIndex}번째 글 조각`
            const empty = block.text === ''
            const rows = Math.max(first ? 2 : 1, block.text.split('\n').length)
            return (
              <div
                key={`t${index}`}
                className={cn('print:basis-full', empty && 'print:hidden')}
                onDragOver={(event) => {
                  if (event.dataTransfer.types.includes(PHOTO_DRAG)) event.preventDefault()
                }}
                onDrop={(event) => dropPhoto(event, block, lineCount)}
              >
                <div
                  aria-hidden
                  className="hidden text-sm break-words whitespace-pre-wrap print:block"
                >
                  {block.text}
                </div>
                <textarea
                  id={first ? id : undefined}
                  aria-label={name}
                  value={block.text}
                  placeholder={first ? placeholder : undefined}
                  rows={empty && !first ? 1 : rows}
                  onChange={(event) =>
                    auto.change(withTextBlock(auto.read(), block, event.target.value))
                  }
                  onSelect={(event) => {
                    caret.current = caretIn(block, event.currentTarget.selectionStart)
                  }}
                  onPaste={(event: ClipboardEvent<HTMLTextAreaElement>) => {
                    const pasted = pastedFiles(event)
                    if (pasted.length === 0) return
                    event.preventDefault()
                    void addFiles(pasted, caretIn(block, event.currentTarget.selectionStart))
                  }}
                  className={cn(
                    'placeholder:text-muted-foreground/60 focus:bg-muted/40 block w-full resize-none print:hidden rounded-sm bg-transparent px-1 py-0.5 text-sm [field-sizing:content] outline-none focus:ring-1 focus:ring-ring/40',
                    empty && !first && 'min-h-0 py-0 leading-3 focus:py-0.5 focus:leading-normal',
                  )}
                />
              </div>
            )
          }
          const photo = byId.get(block.id)
          if (!photo) return null
          const width = sizing?.id === block.id ? sizing.width : block.width
          const name = photo.name
          return (
            <div
              key={`p${block.id}`}
              className="group/photo relative break-inside-avoid"
              style={{ width: `${width}%` }}
              draggable
              onDragStart={(event) => {
                event.dataTransfer.setData(PHOTO_DRAG, block.id)
                event.dataTransfer.effectAllowed = 'move'
              }}
              onDragOver={(event) => {
                if (event.dataTransfer.types.includes(PHOTO_DRAG)) event.preventDefault()
              }}
              onDrop={(event) => dropPhoto(event, block, lineCount)}
            >
              <CellImage photo={photo} width={width} onOpen={() => open(block.id)} />
              <div className="bg-background/90 absolute top-0.5 right-0.5 flex items-center gap-0.5 rounded px-0.5 opacity-0 shadow-sm group-focus-within/photo:opacity-100 group-hover/photo:opacity-100 print:hidden">
                <PhotoButton
                  label={`${name} 위로`}
                  onClick={() => void write(movedPhoto(auto.read(), block.id, 'up'))}
                >
                  <ArrowUp className="size-3.5" />
                </PhotoButton>
                <PhotoButton
                  label={`${name} 아래로`}
                  onClick={() => void write(movedPhoto(auto.read(), block.id, 'down'))}
                >
                  <ArrowDown className="size-3.5" />
                </PhotoButton>
                {QUICK_WIDTHS.map((one) => (
                  <PhotoButton
                    key={one}
                    label={`${name} 폭 ${one}%`}
                    pressed={width === one}
                    onClick={() => void write(resizedPhoto(auto.read(), block.id, one))}
                  >
                    <span className="px-0.5 text-[10px] tabular-nums">{one}</span>
                  </PhotoButton>
                ))}
                <PhotoButton label={`${name} 삭제`} danger onClick={() => void remove(block.id)}>
                  <Trash2 className="size-3.5" />
                </PhotoButton>
              </div>
              <span
                role="slider"
                tabIndex={0}
                aria-label={`${name} 크기`}
                aria-valuemin={10}
                aria-valuemax={100}
                aria-valuenow={width}
                title="끌어서 크기 조정 — 10% 단위"
                onPointerDown={(event) => startResize(event, block.id, width)}
                onKeyDown={(event) => {
                  const step = event.key === 'ArrowRight' ? 10 : event.key === 'ArrowLeft' ? -10 : 0
                  if (!step) return
                  event.preventDefault()
                  void write(resizedPhoto(auto.read(), block.id, snapWidth(width + step)))
                }}
                className="bg-primary/70 absolute right-0 bottom-0 size-3 cursor-se-resize rounded-tl-sm opacity-0 group-focus-within/photo:opacity-100 group-hover/photo:opacity-100 print:hidden"
              />
              {sizing?.id === block.id && (
                <span className="bg-background/90 absolute bottom-1 left-1 rounded px-1 text-[10px] tabular-nums shadow-sm">
                  {width}%
                </span>
              )}
            </div>
          )
        })}
        <div className="flex flex-wrap items-center gap-1.5 print:hidden">
          <input
            ref={pick}
            type="file"
            multiple
            hidden
            accept={IMAGE_ACCEPT}
            aria-label={`${label} 사진 파일`}
            onChange={(event) => {
              const chosen = Array.from(event.target.files ?? [])
              // **값을 비운다** — 같은 파일을 다시 고르면 change 가 안 나 화면이 먹통으로 읽힌다.
              event.target.value = ''
              void addFiles(chosen, caret.current)
            }}
          />
          <button
            type="button"
            onClick={() => pick.current?.click()}
            disabled={upload.progress !== null}
            aria-label={`${label} 사진 업로드`}
            title="사진 — 선택하거나, 칸으로 끌어다 놓거나, 글을 클릭한 뒤 붙여넣기(Ctrl+V). 마지막 커서 자리에 들어갑니다."
            className="text-muted-foreground hover:text-foreground hover:border-foreground/30 inline-flex h-6 items-center gap-1 rounded-sm border border-dashed px-1.5 text-[11px] opacity-60 group-hover:opacity-100 focus:opacity-100"
          >
            <ImagePlus className="size-3.5" />
            {upload.progress ?? '사진'}
          </button>
          <SaveState status={auto.status} error={auto.error} onRetry={() => void auto.flush()} />
        </div>
      </div>
      {focused && notice && (
        <p className="px-1 text-[11px] text-sky-700 print:hidden dark:text-sky-400">{notice}</p>
      )}
      {auto.stale && !auto.conflict && (
        <p className="px-1 text-[11px] text-amber-700 print:hidden dark:text-amber-400">
          다른 사람이 이 칸을 수정했습니다 — 저장할 때 확인합니다.
        </p>
      )}
      {auto.conflict && (
        <ConflictBox
          by={auto.conflict.by}
          at={auto.conflict.at}
          theirs={readableText(
            typeof auto.conflict.current === 'string' ? auto.conflict.current : '',
          )}
          mine={readableText(auto.draft)}
          onTakeTheirs={auto.takeTheirs}
          onKeepMine={() => void auto.keepMine()}
        />
      )}
      <FileLinks files={files} />
      <ErrorNotice error={failed ?? upload.error} className="text-xs print:hidden" />
      {viewer}
    </div>
  )
}

function PhotoButton({
  label,
  onClick,
  children,
  danger,
  pressed,
}: {
  label: string
  onClick: () => void
  children: ReactNode
  danger?: boolean
  pressed?: boolean
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      onClick={onClick}
      className={cn(
        'text-muted-foreground rounded p-0.5',
        danger ? 'hover:text-destructive' : 'hover:text-foreground hover:bg-muted',
        pressed && 'text-foreground bg-muted',
      )}
    >
      {children}
    </button>
  )
}

/** 사진이 아닌 첨부(1단계 이전에 붙은 것) — 이름만, 클릭하면 다운로드. */
function FileLinks({ files }: { files: Photo[] }) {
  if (files.length === 0) return null
  return (
    <ul className="text-muted-foreground space-y-0.5 text-[11px]">
      {files.map((one) => (
        <li key={one.id}>
          <button
            type="button"
            className="hover:text-foreground underline underline-offset-2"
            onClick={() => void attachmentApi.download(one.id, one.name)}
          >
            {one.name}
          </button>
        </li>
      ))}
    </ul>
  )
}
