/**
 * 장표 한 장(ADR 0037) — 열 때마다 지금의 기록을 읽는다(살아 있는 값).
 *
 * 작성자와 시스템 관리자는 제목 · 글 영역의 글 · 고르기 영역의 기록을 수정한다. 기록 자체는 원래
 * 자리(객체 상세)에서 수정한다 — 장표는 기록을 가리킬 뿐이다.
 */

import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Pencil, RefreshCw, Trash2, X } from 'lucide-react'

import { slideApi } from '@/modules/slides/api'
import type { RenderedArea, Slide } from '@/modules/slides/api'
import { SlideGrid } from '@/modules/slides/SlideGrid'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
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
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

export default function SlidePage() {
  const { slideId = '' } = useParams()
  const navigate = useNavigate()
  const loaded = useResource(() => slideApi.get(slideId), [slideId])
  const [fresh, setFresh] = useState<Slide | null>(null)
  const slide = fresh ?? loaded.data
  const [error, setError] = useState<Error | null>(null)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [picking, setPicking] = useState<RenderedArea | null>(null)
  const [confirming, setConfirming] = useState(false)

  async function patch(body: Parameters<typeof slideApi.update>[1]) {
    setError(null)
    try {
      setFresh(await slideApi.update(slideId, body))
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      throw caught
    }
  }

  async function reload() {
    setError(null)
    try {
      setFresh(await slideApi.get(slideId))
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  async function remove() {
    await slideApi.remove(slideId)
    navigate('/slides')
  }

  if (!slide) return <ErrorNotice error={loaded.error} />

  return (
    <div className="space-y-4">
      <PageHeader
        title={slide.title}
        description={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>틀: {slide.template_name}</span>
            {slide.subject ? (
              <span>
                주제:{' '}
                <Link
                  to={`/o/${slide.subject.type_slug}/${slide.subject.id}`}
                  className="hover:underline"
                >
                  {slide.subject.label}
                </Link>
                {slide.subject.deleted && ' (삭제됨)'}
              </span>
            ) : (
              <span>주제: (없음)</span>
            )}
            <span>작성자: {slide.author_name}</span>
            <span>기준 시각: {shownDateTime(slide.rendered_at)}</span>
          </span>
        }
        actions={
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={reload} title="지금의 기록으로 다시 읽기">
              <RefreshCw className="mr-1 size-4" />
              새로 고침
            </Button>
            {slide.can_edit && renaming === null && (
              <Button size="sm" variant="outline" onClick={() => setRenaming(slide.title)}>
                <Pencil className="mr-1 size-4" />
                제목 수정
              </Button>
            )}
            {slide.can_edit && (
              <Button size="sm" variant="outline" onClick={() => setConfirming(true)}>
                <Trash2 className="mr-1 size-4" />
                삭제
              </Button>
            )}
          </div>
        }
      />
      {renaming !== null && (
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            if (renaming.trim()) {
              void patch({ title: renaming.trim() }).then(() => setRenaming(null))
            }
          }}
        >
          <Input
            aria-label="제목"
            value={renaming}
            maxLength={200}
            onChange={(event) => setRenaming(event.target.value)}
            className="h-8 w-96"
            autoFocus
          />
          <Button type="submit" size="sm">
            저장
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setRenaming(null)}>
            <X className="size-4" />
          </Button>
        </form>
      )}
      <ErrorNotice error={error ?? loaded.error} />
      <SlideGrid
        rows={slide.rows}
        cols={slide.cols}
        areas={slide.areas}
        editable={slide.can_edit}
        onSaveText={(key, text) => patch({ texts: { [key]: text } })}
        onPick={setPicking}
      />
      <p className="text-muted-foreground text-xs">
        기록의 값은 원래 자리에서 수정합니다 — 장표는 열 때마다 지금의 기록을 읽습니다.
      </p>
      {picking && (
        <PickDialog
          slideId={slideId}
          area={picking}
          onClose={() => setPicking(null)}
          onSave={(ids) => patch({ picks: { [picking.key]: ids } }).then(() => setPicking(null))}
        />
      )}
      {confirming && (
        <ConfirmDialog
          open
          title="장표 삭제"
          description="장표를 목록에서 삭제합니다. 장표가 가리키던 기록은 그대로 남습니다."
          confirmLabel="삭제"
          destructive
          onConfirm={remove}
          onClose={() => setConfirming(false)}
        />
      )}
    </div>
  )
}

/**
 * 선택 영역의 기록 — 후보는 **서버가 틀의 조건으로 좁힌 것**(주제의 변경점 등)이다. 차례가 장표의
 * 차례다. 선택할 수 있는 건수가 1 이면 새로 선택한 것이 앞의 것을 바꾼다(기록 하나 = 한 장).
 */
function PickDialog({
  slideId,
  area,
  onClose,
  onSave,
}: {
  slideId: string
  area: RenderedArea
  onClose: () => void
  onSave: (ids: string[]) => Promise<void>
}) {
  const [chosen, setChosen] = useState(() =>
    area.records.map((one) => ({ id: one.id, label: one.label })),
  )
  const [q, setQ] = useState('')
  const [found, setFound] = useState<{ items: { id: string; label: string }[]; total: number }>()
  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  const taken = useMemo(() => new Set(chosen.map((one) => one.id)), [chosen])
  const single = area.limit === 1
  const full = !single && chosen.length >= area.limit

  useEffect(() => {
    let cancelled = false
    const timer = window.setTimeout(() => {
      slideApi
        .candidates(slideId, area.key, q.trim())
        .then((next) => {
          if (!cancelled) setFound(next)
        })
        .catch((caught: unknown) => {
          if (!cancelled) setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
        })
    }, 200)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [slideId, area.key, q])

  function choose(one: { id: string; label: string }) {
    setChosen((before) => (single ? [one] : [...before, one]))
  }

  async function save() {
    setSaving(true)
    try {
      await onSave(chosen.map((one) => one.id))
    } catch {
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{area.title} — 기록 선택</DialogTitle>
          <DialogDescription>
            {single
              ? `${area.type_label} 기록 하나를 선택합니다.`
              : `${area.type_label} 기록을 ${area.limit}건까지 선택합니다. 선택한 차례대로 표시됩니다.`}
          </DialogDescription>
        </DialogHeader>
        <ol className="space-y-1 text-sm">
          {chosen.map((one, index) => (
            <li key={one.id} className="flex items-center gap-2 rounded border px-2 py-1">
              {!single && <span className="text-muted-foreground w-5 text-right">{index + 1}</span>}
              <span className="flex-1 truncate">{one.label}</span>
              <Button
                size="sm"
                variant="ghost"
                className="h-6"
                aria-label={`${one.label} 제외`}
                onClick={() => setChosen((before) => before.filter((each) => each.id !== one.id))}
              >
                <X className="size-3" />
              </Button>
            </li>
          ))}
          {chosen.length === 0 && (
            <li className="text-muted-foreground">선택한 기록이 없습니다.</li>
          )}
        </ol>
        <Input
          aria-label="후보 검색"
          placeholder="이름 · 식별자로 검색"
          value={q}
          onChange={(event) => setQ(event.target.value)}
          className="h-8"
        />
        <ErrorNotice error={error} />
        <ul className="max-h-64 space-y-1 overflow-y-auto text-sm" aria-label="후보">
          {(found?.items ?? []).map((one) => (
            <li key={one.id}>
              <button
                type="button"
                className="hover:bg-muted w-full rounded px-2 py-1 text-left disabled:opacity-50"
                disabled={taken.has(one.id) || full}
                onClick={() => choose(one)}
              >
                {one.label}
              </button>
            </li>
          ))}
          {found && found.items.length === 0 && (
            <li className="text-muted-foreground px-2">
              후보가 없습니다 — 이 장표의 주제와 이어진 기록이 없거나 조건에 맞지 않습니다.
            </li>
          )}
        </ul>
        {found && found.total > found.items.length && (
          <p className="text-muted-foreground text-xs">
            후보 {found.total}건 중 {found.items.length}건을 표시합니다 — 검색으로 좁히세요.
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            취소
          </Button>
          <Button onClick={save} disabled={saving}>
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
