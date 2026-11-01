/**
 * 계보(ADR 0041) — 이 객체가 **무엇에서 만들어졌나**(위, 원천 사슬)와 **이것으로 만든 것**(아래,
 * 파생물 사슬), 줄마다 고정 개정과 지금 개정, 낡았으면 그 까닭.
 *
 * 원천이 고정 뒤에 바뀌면 파생물은 **낡음**(바로 낡음)이고, 원천 중 하나가 낡았으면 **위에서
 * 낡음**이다. 원천이 바뀌었지만 영향이 없거나 새 원천으로 다시 만들었으면 「다시 고정」 으로 고정을
 * 원천의 지금 값으로 바꾼다 — 원천 객체는 안 바뀌므로 위에서 낡음은 그 원천을 먼저 다시 고정해야
 * 풀린다.
 *
 * **접어 두고 펼칠 때 읽는다.** 사슬은 깊이 20까지 따라가므로 상세를 열 때마다 읽으면 그만큼
 * 기다린다(BOM 전개와 같다).
 */

import { useState } from 'react'
import { ChevronRight, GitFork, Loader2, Pin } from 'lucide-react'
import { Link } from 'react-router-dom'

import { objectApi } from '@/modules/objects/api'
import type {
  LineageEnd,
  LineageLine,
  LineageOut,
  LineageRepinOut,
  RepinBasis,
} from '@/modules/objects/api'
import type { PropertyDef } from '@/modules/ontology/api'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Label } from '@/shared/components/ui/label'
import { Textarea } from '@/shared/components/ui/textarea'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'
import { cn } from '@/shared/lib/utils'

/** 다시 고정의 근거 — 감사 · 이력의 까닭이 된다. */
const BASES: { value: RepinBasis; label: string; hint: string }[] = [
  {
    value: 'checked',
    label: '영향 없음 확인',
    hint: '원천이 바뀌었지만 이 파생물에는 영향이 없습니다.',
  },
  {
    value: 'rebuilt',
    label: '다시 만들었음',
    hint: '바뀐 원천으로 이 파생물을 다시 만들었습니다.',
  },
]

interface Props {
  typeSlug: string
  objectId: string
  /** 이 타입의 속성 — 개정 칸의 이름을 적는다. 모르면 키 그대로. */
  defs?: PropertyDef[]
  /**
   * 바뀌면 다시 읽는다 — 상세에서 이 객체(개정)나 관계를 수정한 것. 다시 고정은 응답이 새 계보를
   * 싣고 오므로 이것을 바꾸지 않아도 된다.
   */
  reloadKey?: unknown
  /** 다시 고정한 뒤 — 상세가 이력을 다시 읽게 한다(양 끝의 이력에 남는다). */
  onRepinned?: () => void
}

export function LineagePanel(props: Props) {
  const [open, setOpen] = useState(false)
  return (
    <section className="space-y-3" aria-label="계보">
      <h2 className="text-base font-semibold">
        <button
          type="button"
          aria-expanded={open}
          className="flex items-center gap-2 text-left"
          onClick={() => setOpen((now) => !now)}
        >
          <ChevronRight className={cn('size-4 transition-transform', open && 'rotate-90')} />
          <GitFork className="size-4" />
          계보
          <span className="text-muted-foreground text-sm font-normal">
            무엇에서 만들어졌나 · 이것으로 만든 것 — 펼칠 때 읽습니다
          </span>
        </button>
      </h2>
      {open && <LineageBody {...props} />}
    </section>
  )
}

function LineageBody({ typeSlug, objectId, defs = [], reloadKey, onRepinned }: Props) {
  const found = useResource(
    () => objectApi.lineage(typeSlug, objectId),
    [typeSlug, objectId, reloadKey],
  )
  /**
   * 다시 고정의 응답이 싣고 온 새 계보 — **그 응답을 부른 때의 계보 위에서만** 쓴다. 그 뒤에 다시
   * 읽었으면(상세에서 수정) 새로 읽은 것이 맞다.
   */
  const [repinned, setRepinned] = useState<{
    base: LineageOut | null
    result: LineageRepinOut
  } | null>(null)
  const [repinning, setRepinning] = useState(false)

  const afterRepin = repinned && repinned.base === found.data ? repinned.result : null
  // 오류가 나면 앞 응답이 남아 있다 — 그대로 그리면 그것이 지금 계보로 읽힌다.
  const data = afterRepin?.lineage ?? (found.error && found.stale ? null : found.data)
  const direct = (data?.up ?? []).filter((one) => one.depth === 1)
  const revisionLabel = data?.revision_key
    ? (defs.find((one) => one.key === data.revision_key)?.label ?? data.revision_key)
    : ''

  return (
    <div className="space-y-3">
      <ErrorNotice error={found.error} />

      {data &&
        (data.kinds.length === 0 ? (
          <EmptyState title="계보 관계 종류가 없습니다" hint={data.note} />
        ) : (
          <div className={cn('space-y-3', found.loading && 'opacity-60')}>
            <div className="space-y-2 rounded-md border p-3 text-sm">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                <StatusBadge kind="lineage" value={data.state} />
                {data.revision_key ? (
                  <span className="text-muted-foreground">
                    지금 개정({revisionLabel}){' '}
                    <b className="text-foreground">{data.revision ?? '(비어 있음)'}</b>
                  </span>
                ) : (
                  <span className="text-muted-foreground">개정 칸 없음 — 수정 시각으로 판정</span>
                )}
                <span>
                  <span className="text-muted-foreground">영향 범위</span> 이것으로 만든 것{' '}
                  <b>{data.impact.derived.toLocaleString()}개</b>(낡음{' '}
                  {data.impact.stale.toLocaleString()}개)
                </span>
                {data.can_repin && direct.length > 0 && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="ml-auto"
                    onClick={() => setRepinning(true)}
                  >
                    <Pin className="mr-1 size-4" />
                    다시 고정
                  </Button>
                )}
              </div>
              {data.reasons.length > 0 && (
                <ul className="list-disc space-y-0.5 pl-5 text-xs" aria-label="낡은 까닭">
                  {data.reasons.map((one, index) => (
                    <li key={index}>{one}</li>
                  ))}
                </ul>
              )}
            </div>

            {afterRepin && <RepinNotice result={afterRepin} />}

            {data.up.length === 0 && data.down.length === 0 ? (
              <EmptyState
                title="이어진 원천 · 파생물이 없습니다"
                hint={data.note || '이 객체는 계보 관계로 이어진 원천도 파생물도 없습니다.'}
              />
            ) : (
              <>
                <Chain side="up" lines={data.up} total={data.up_total} />
                <Chain side="down" lines={data.down} total={data.down_total} />
              </>
            )}
          </div>
        ))}
      {!data && found.loading && <p className="text-muted-foreground text-sm">읽는 중…</p>}

      {repinning && data && (
        <RepinDialog
          typeSlug={typeSlug}
          objectId={objectId}
          lines={direct}
          onClose={() => setRepinning(false)}
          onDone={(result) => {
            setRepinned({ base: found.data, result })
            setRepinning(false)
            onRepinned?.()
          }}
        />
      )}
    </div>
  )
}

/** 다시 고정의 결과 — 몇 개가 바뀌었고, 무엇이 아직 낡았나. */
function RepinNotice({ result }: { result: LineageRepinOut }) {
  return (
    <p className="bg-muted/50 rounded-md px-3 py-2 text-sm" role="status">
      다시 고정했습니다 — 바뀐 관계 {result.changed.length.toLocaleString()}개 · 그대로{' '}
      {result.unchanged.toLocaleString()}개.
      {result.lineage.state === 'upstream' &&
        ' 위에서 낡음은 그대로입니다 — 낡은 원천을 먼저 다시 고정해야 풀립니다.'}
    </p>
  )
}

/** 위(원천 사슬) · 아래(파생물 사슬) 한쪽 — 얕은 것부터, 깊이만큼 들여 쓴다. */
function Chain({
  side,
  lines,
  total,
}: {
  side: 'up' | 'down'
  lines: LineageLine[]
  total: number
}) {
  const title = side === 'up' ? '위 (원천)' : '아래 (파생물)'
  return (
    <div className="space-y-1.5">
      <h3 className="text-sm font-medium">
        {title}
        <span className="text-muted-foreground ml-2 text-xs font-normal">
          {total.toLocaleString()}줄
        </span>
      </h3>
      {lines.length < total && (
        <p className="flex flex-wrap items-center gap-1.5 text-xs">
          <StatusBadge kind="lineage" value="truncated" />
          전체 {total.toLocaleString()}줄 중 {lines.length.toLocaleString()}줄 표시 — 얕은 것부터
          표시합니다.
        </p>
      )}
      {lines.length === 0 ? (
        <p className="text-muted-foreground text-xs">
          {side === 'up'
            ? '원천이 없습니다 — 이 객체를 만드는 데 쓰인 것이 계보 관계로 이어져 있지 않습니다.'
            : '파생물이 없습니다 — 이것으로 만든 것이 계보 관계로 이어져 있지 않습니다.'}
        </p>
      ) : (
        <ul className="divide-y rounded-md border text-sm" aria-label={title}>
          {lines.map((line, index) => (
            <LineRow key={`${line.relation_id}:${index}`} line={line} side={side} />
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * 줄 하나 — 위에서는 원천을, 아래에서는 파생물을 앞에 세운다. 두 걸음 넘게 떨어진 줄은 이 객체
 * 쪽 끝을 함께 적는다(얕은 것부터 실려 오므로 들여쓰기만으로는 어디에 매달렸는지 안 읽힌다).
 */
function LineRow({ line, side }: { line: LineageLine; side: 'up' | 'down' }) {
  const far = side === 'up' ? line.source : line.derived
  const near = side === 'up' ? line.derived : line.source
  return (
    <li
      className="space-y-0.5 py-2 pr-3"
      style={{ paddingLeft: `${0.75 + (line.depth - 1) * 1.25}rem` }}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-muted-foreground text-xs">
          {line.relation_label || line.relation}
        </span>
        <EndName end={far} />
        {far.type_label && <span className="text-muted-foreground text-xs">{far.type_label}</span>}
        {far.state !== 'fresh' && <StatusBadge kind="lineage" value={far.state} />}
        {line.depth > 1 && (
          <span className="text-muted-foreground text-xs">
            {side === 'up' ? `→ ${endText(near)}` : `← ${endText(near)}`}
          </span>
        )}
      </div>
      <div className="text-muted-foreground flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <PinText line={line} />
        {line.stale && (
          <span className="flex flex-wrap items-center gap-1.5">
            <StatusBadge kind="lineage" value="direct" />
            <span className="text-foreground">{line.reason}</span>
          </span>
        )}
      </div>
    </li>
  )
}

function endText(end: LineageEnd): string {
  return end.id ? end.label : '(볼 수 없는 객체)'
}

/** 끝의 이름 — 보이는 객체는 상세로 가는 링크, 볼 수 없는 객체는 회색 글자(링크 없음). */
function EndName({ end }: { end: LineageEnd }) {
  if (end.id && end.type_slug) {
    return (
      <Link to={`/o/${end.type_slug}/${end.id}`} className="font-medium hover:underline">
        {end.label}
        {end.key && <span className="text-muted-foreground ml-1 font-mono text-xs">{end.key}</span>}
      </Link>
    )
  }
  return <span className="text-muted-foreground">(볼 수 없는 객체)</span>
}

/**
 * 고정한 것과 지금 — 원천 타입에 개정 칸이 있으면 개정으로, 없으면 시각으로(고정 시각 · 원천의
 * 수정 시각). 판정과 같은 기준을 적어야 「왜 낡음인가」 가 읽힌다.
 */
function PinText({ line }: { line: LineageLine }) {
  if (line.revision_key) {
    return (
      <span>
        고정 개정 <b className="text-foreground">{line.pinned_revision ?? '(없음)'}</b> → 지금{' '}
        <b className="text-foreground">{line.revision ?? '(없음)'}</b>
      </span>
    )
  }
  return (
    <span>
      고정 시각 {shownDateTime(line.pinned_at)} · 원천 수정 시각{' '}
      {shownDateTime(line.source_updated_at)}
    </span>
  )
}

/**
 * 다시 고정 — 이 객체에 바로 붙은 원천 관계(기본: 낡은 것)를 골라 고정을 원천의 지금 값으로.
 * **창을 닫지 않고** 서버의 거절을 그 자리에 보인다(권한 · 허브 관리 종류).
 */
function RepinDialog({
  typeSlug,
  objectId,
  lines,
  onClose,
  onDone,
}: {
  typeSlug: string
  objectId: string
  /** 이 객체가 파생물인 선(깊이 1). */
  lines: LineageLine[]
  onClose: () => void
  onDone: (result: LineageRepinOut) => void
}) {
  const [picked, setPicked] = useState<Set<string>>(
    () => new Set(lines.filter((one) => one.stale).map((one) => one.relation_id)),
  )
  const [basis, setBasis] = useState<RepinBasis>('checked')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const toggle = (id: string, on: boolean) =>
    setPicked((before) => {
      const next = new Set(before)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })

  async function run() {
    setBusy(true)
    setError(null)
    try {
      const result = await objectApi.repinLineage(typeSlug, objectId, {
        // 고른 것을 늘 적어 보낸다 — 비우면 서버가 「전부」 로 읽는다.
        relation_ids: lines.map((one) => one.relation_id).filter((id) => picked.has(id)),
        basis,
        ...(note.trim() ? { note: note.trim() } : {}),
      })
      onDone(result)
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>다시 고정</DialogTitle>
          <DialogDescription>
            선택한 원천 관계의 고정을 원천의 지금 값(개정 · 수정 시각)으로 변경합니다. 감사 · 이력에
            앞뒤 개정이 남습니다. <b>원천 객체는 바뀌지 않으므로 위에서 낡음은 풀리지 않습니다</b> —
            원천이 위에서 낡았으면 그 원천을 먼저 다시 고정합니다.
          </DialogDescription>
        </DialogHeader>

        {error && <ErrorNotice error={error} />}

        <fieldset className="space-y-1.5">
          <legend className="mb-1 text-sm font-medium">원천 관계</legend>
          <ul className="divide-y rounded-md border text-sm">
            {lines.map((line) => (
              <li key={line.relation_id}>
                <label className="hover:bg-muted/50 flex cursor-pointer items-start gap-2 px-3 py-2">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4"
                    checked={picked.has(line.relation_id)}
                    onChange={(event) => toggle(line.relation_id, event.target.checked)}
                    aria-label={`${line.relation_label || line.relation} · ${endText(line.source)}`}
                  />
                  <span className="min-w-0 flex-1 space-y-0.5">
                    <span className="flex flex-wrap items-center gap-x-2">
                      <span className="text-muted-foreground text-xs">
                        {line.relation_label || line.relation}
                      </span>
                      <span className="font-medium">{endText(line.source)}</span>
                      {line.source.type_label && (
                        <span className="text-muted-foreground text-xs">
                          {line.source.type_label}
                        </span>
                      )}
                      {line.stale && <StatusBadge kind="lineage" value="direct" />}
                    </span>
                    <span className="text-muted-foreground block text-xs">
                      <PinText line={line} />
                      {line.stale && line.reason && ` — ${line.reason}`}
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
          {!lines.some((one) => one.stale) && (
            <p className="text-muted-foreground text-xs">
              낡은 원천 관계가 없습니다 — 선택한 관계가 이미 지금 값이면 그대로 둡니다.
            </p>
          )}
        </fieldset>

        <fieldset className="space-y-1.5">
          <legend className="mb-1 text-sm font-medium">근거</legend>
          {BASES.map((one) => (
            <label key={one.value} className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="repin-basis"
                className="mt-0.5 size-4"
                value={one.value}
                checked={basis === one.value}
                onChange={() => setBasis(one.value)}
              />
              <span>
                {one.label}
                <span className="text-muted-foreground ml-1 text-xs">{one.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>

        <div className="space-y-1.5">
          <Label htmlFor="repin-note">메모</Label>
          <Textarea
            id="repin-note"
            rows={2}
            maxLength={500}
            value={note}
            placeholder="무엇을 확인했나 · 무엇을 다시 만들었나"
            onChange={(event) => setNote(event.target.value)}
          />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            취소
          </Button>
          <Button onClick={() => void run()} disabled={busy || picked.size === 0}>
            {busy ? (
              <Loader2 className="mr-1 size-4 animate-spin" />
            ) : (
              <Pin className="mr-1 size-4" />
            )}
            {picked.size}개 다시 고정
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
