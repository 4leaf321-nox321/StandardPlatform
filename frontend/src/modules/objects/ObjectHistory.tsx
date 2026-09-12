/**
 * 변경 이력 — **「작년엔 뭐였지」 에 이 화면에서 답한다.**
 *
 * 언제·누가·어느 칸을 무엇에서 무엇으로. 관계가 걸리고 끊긴 것도 같은 줄에 선다.
 * 값 기록을 누르면 **그 시점의 값 전체**가 지금 값과 나란히 뜨고, 고칠 수 있는
 * 사람에게는 「이 값으로 되돌리기」 가 선다 — 저장과 같은 검증을 거치므로, 그때
 * 가리키던 것이 지워졌으면 서버가 막고 이유를 말한다. 그 말은 창 안에 그대로 뜬다.
 */

import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, History, Layers, Loader2, RotateCcw } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import { BulkUndoDialog } from '@/modules/objects/BulkUndoDialog'
import type { HistoryEntry, Snapshot } from '@/modules/objects/api'
import { propertyText } from '@/modules/objects/PropertyFields'
import type { PropertyDef } from '@/modules/ontology/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

interface ObjectHistoryProps {
  typeSlug: string
  objectId: string
  defs: PropertyDef[]
  /** 지금 값 — 「그때」 와 나란히 놓는다. */
  current: Snapshot
  refLabels: Record<string, string>
  canEdit: boolean
  /** 되돌린 뒤 — 상세를 다시 읽는다. */
  onRestored: () => void
  /** 상세가 다시 읽힐 때마다 바뀌는 값(응답 객체) — 이력도 같이 다시 읽는다. */
  reloadKey: unknown
}

const ACTION_LABEL: Record<string, string> = {
  'object.create': '만듦',
  'object.update': '고침',
  'object.delete': '지움',
  'object.merge': '합침',
  'object.relation.add': '관계 맺음',
  'object.relation.update': '관계 고침',
  'object.relation.remove': '관계 끊음',
}

const FIXED_LABEL: Record<string, string> = {
  key: '식별자',
  label: '이름',
  status: '상태',
}

/** 처음에 보여 줄 줄 수. 그 위는 「더 보기」. */
const FIRST_PAGE = 10

export function ObjectHistory({
  typeSlug,
  objectId,
  defs,
  current,
  refLabels,
  canEdit,
  onRestored,
  reloadKey,
}: ObjectHistoryProps) {
  const history = useResource(
    () => objectApi.history(typeSlug, objectId),
    [typeSlug, objectId, reloadKey],
  )
  const [expanded, setExpanded] = useState(false)
  const [picked, setPicked] = useState<HistoryEntry | null>(null)
  const [undoBatch, setUndoBatch] = useState<string | null>(null)
  const byKey = useMemo(() => new Map(defs.map((def) => [def.key, def])), [defs])

  const labelOf = (field: string): string => {
    if (field.startsWith('properties.')) {
      const key = field.slice('properties.'.length)
      return byKey.get(key)?.label ?? key
    }
    return FIXED_LABEL[field] ?? field
  }
  const textOf = (field: string, value: unknown): string => {
    if (value === null || value === undefined || value === '') return '—'
    if (field.startsWith('properties.')) {
      const def = byKey.get(field.slice('properties.'.length))
      if (def) return propertyText(def, value, refLabels)
    }
    return String(value)
  }

  if (history.error) return <ErrorNotice error={history.error} />
  const entries = history.data ?? []
  const shown = expanded ? entries : entries.slice(0, FIRST_PAGE)

  return (
    <section className="space-y-2">
      <h2 className="flex items-center gap-2 text-sm font-medium">
        <History className="size-4" />
        변경 이력
        <span className="text-muted-foreground font-normal">{entries.length}</span>
      </h2>
      {history.loading && entries.length === 0 && (
        <p className="text-muted-foreground text-sm">읽는 중…</p>
      )}
      {!history.loading && entries.length === 0 && (
        <p className="text-muted-foreground text-sm">기록이 없습니다.</p>
      )}
      {shown.length > 0 && (
        <ol className="divide-y rounded-md border text-sm">
          {shown.map((one) => {
            const fields = Object.keys(one.changes)
            const clickable = one.kind === 'object'
            return (
              <li key={one.id}>
                <button
                  type="button"
                  disabled={!clickable}
                  onClick={() => setPicked(one)}
                  className="hover:bg-muted/60 flex w-full items-start gap-3 px-3 py-2 text-left disabled:cursor-default disabled:hover:bg-transparent"
                  title={clickable ? '그 시점의 값 보기' : undefined}
                >
                  <span className="text-muted-foreground w-32 shrink-0 text-xs tabular-nums">
                    {shownDateTime(one.at)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-1.5">
                      <Badge variant={one.kind === 'relation' ? 'outline' : 'secondary'}>
                        {ACTION_LABEL[one.action] ?? one.action}
                      </Badge>
                      <span className="text-muted-foreground text-xs">{one.actor_label}</span>
                      {one.reason && (
                        <span className="text-muted-foreground text-xs">· {one.reason}</span>
                      )}
                    </span>
                    {one.relation && (
                      <span className="mt-0.5 block text-xs">
                        {one.relation.outgoing ? '→' : '←'}{' '}
                        {one.relation.other_label || '(이름 없음)'}{' '}
                        <span className="text-muted-foreground">({one.relation.relation})</span>
                      </span>
                    )}
                    {fields.length > 0 && (
                      <span className="mt-0.5 block space-y-0.5 text-xs">
                        {fields.slice(0, 4).map((field) => (
                          <span key={field} className="block truncate">
                            <span className="text-muted-foreground">{labelOf(field)}</span>{' '}
                            <span className="line-through opacity-60">
                              {textOf(field, one.changes[field].before)}
                            </span>{' '}
                            → {textOf(field, one.changes[field].after)}
                          </span>
                        ))}
                        {fields.length > 4 && (
                          <span className="text-muted-foreground block">
                            … 외 {fields.length - 4}칸
                          </span>
                        )}
                      </span>
                    )}
                  </span>
                  {clickable && (
                    <ChevronRight className="text-muted-foreground mt-1 size-4 shrink-0" />
                  )}
                </button>
              </li>
            )
          })}
        </ol>
      )}
      {entries.length > FIRST_PAGE && !expanded && (
        <Button size="sm" variant="ghost" onClick={() => setExpanded(true)}>
          <ChevronDown className="mr-1 size-3.5" />
          {entries.length - FIRST_PAGE}개 더 보기
        </Button>
      )}

      {picked?.snapshot && (
        <SnapshotDialog
          entry={picked}
          snapshot={picked.snapshot}
          current={current}
          defs={defs}
          refLabels={refLabels}
          canEdit={canEdit}
          isLatest={entries[0]?.id === picked.id}
          onClose={() => setPicked(null)}
          onUndoBatch={(batchId) => {
            setPicked(null)
            setUndoBatch(batchId)
          }}
          onRestore={async () => {
            await objectApi.restore(typeSlug, objectId, picked.id)
            setPicked(null)
            onRestored()
          }}
        />
      )}

      {undoBatch && (
        <BulkUndoDialog
          typeSlug={typeSlug}
          batchId={undoBatch}
          onClose={() => setUndoBatch(null)}
          onApplied={onRestored}
        />
      )}
    </section>
  )
}

interface SnapshotDialogProps {
  entry: HistoryEntry
  snapshot: Snapshot
  current: Snapshot
  defs: PropertyDef[]
  refLabels: Record<string, string>
  canEdit: boolean
  /** 가장 최근 기록 = 지금 값. 되돌릴 것이 없다. */
  isLatest: boolean
  onClose: () => void
  onRestore: () => Promise<void>
  /** 여럿 골라 고치기로 같이 바뀐 기록이면 — 그 묶음을 통째로 되돌린다. */
  onUndoBatch: (batchId: string) => void
}

/** 그때와 지금을 나란히 — 다른 칸만 진하게. */
function SnapshotDialog({
  entry,
  snapshot,
  current,
  defs,
  refLabels,
  canEdit,
  isLatest,
  onClose,
  onRestore,
  onUndoBatch,
}: SnapshotDialogProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const rows = useMemo(() => {
    const out: { label: string; was: string; now: string; differs: boolean }[] = []
    const fixed: [string, string | null, string | null][] = [
      ['식별자', snapshot.key, current.key],
      ['이름', snapshot.label, current.label],
      ['상태', snapshot.status, current.status],
    ]
    for (const [label, then, now] of fixed) {
      out.push({ label, was: then ?? '—', now: now ?? '—', differs: then !== now })
    }
    for (const def of defs) {
      if (def.data_type === 'file') continue
      const then = propertyText(def, snapshot.properties[def.key], refLabels)
      const now = propertyText(def, current.properties[def.key], refLabels)
      out.push({ label: def.label, was: then, now, differs: then !== now })
    }
    // 지금은 정의가 없는 칸 — 되돌릴 때 빠진다고 말한다.
    for (const key of Object.keys(snapshot.properties)) {
      if (!defs.some((def) => def.key === key)) {
        out.push({
          label: `${key} (지금은 없는 속성)`,
          was: String(snapshot.properties[key]),
          now: '—',
          differs: true,
        })
      }
    }
    return out
  }, [snapshot, current, defs, refLabels])

  const changed = rows.filter((one) => one.differs).length

  async function run() {
    setBusy(true)
    setError(null)
    try {
      await onRestore()
    } catch (caught) {
      // **창을 닫지 않는다.** 서버가 막은 이유가 여기 떠야 사람이 무엇을 고칠지 안다.
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{shownDateTime(entry.at)} 시점의 값</DialogTitle>
          <DialogDescription>
            {entry.actor_label}이(가) {ACTION_LABEL[entry.action] ?? entry.action} 직후의 값입니다.
            {isLatest
              ? ' 가장 최근 기록이라 지금 값과 같습니다.'
              : changed > 0
                ? ` 지금과 다른 칸 ${changed}개.`
                : ' 지금 값과 같습니다.'}
          </DialogDescription>
        </DialogHeader>

        <table className="w-full text-sm">
          <thead className="text-muted-foreground text-xs">
            <tr>
              <th className="py-1 text-left font-normal">칸</th>
              <th className="py-1 text-left font-normal">그때</th>
              <th className="py-1 text-left font-normal">지금</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((one) => (
              <tr
                key={one.label}
                className={`border-t ${one.differs ? 'font-medium' : 'text-muted-foreground'}`}
              >
                <td className="py-1 pr-3 whitespace-nowrap">{one.label}</td>
                <td className="max-w-48 truncate py-1 pr-3" title={one.was}>
                  {one.was}
                </td>
                <td className="max-w-48 truncate py-1" title={one.now}>
                  {one.now}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {error && <ErrorNotice error={error} />}

        {/* **한 건만 되돌리면 나머지는 틀린 값으로 남는다.** 여럿 골라 고친 기록이면
            그때 같이 바뀐 것을 한 번에 돌리는 길을 여기 둔다 — 틀린 값을 발견하는
            자리가 대개 이 이력이다. */}
        {canEdit && entry.batch && (
          <div className="bg-muted/50 flex flex-wrap items-center justify-between gap-2 rounded-md px-3 py-2 text-sm">
            <span>
              여럿 골라 고치기로 <strong>{entry.batch.size}건</strong>이 함께 바뀐 기록입니다.
            </span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => onUndoBatch((entry.batch as { id: string }).id)}
            >
              <Layers className="mr-1 size-3.5" />
              함께 바뀐 {entry.batch.size}건 되돌리기
            </Button>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            닫기
          </Button>
          {canEdit && (
            <Button
              disabled={busy || isLatest || changed === 0}
              onClick={() => void run()}
              title={
                isLatest || changed === 0 ? '지금 값과 같습니다' : '저장과 같은 검증을 거칩니다'
              }
            >
              {busy ? (
                <Loader2 className="mr-1 size-3.5 animate-spin" />
              ) : (
                <RotateCcw className="mr-1 size-3.5" />
              )}
              이 값으로 되돌리기
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
