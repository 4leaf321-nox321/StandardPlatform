/**
 * 뜻으로 찾기(ADR 0052 · 0054) — **어느 타입의 어느 칸을 계산할지 고르는 자리.** 서버 화면의 한
 * 칸이다(시스템 관리자).
 *
 * - 축은 일괄로 대상이 된다(칸은 「자동」 — 이름 · 글 · 선택 · 참조 · 문서 전부).
 * - **기록은 칸을 골라야** 대상이 된다 — 시장 서비스 13.6만 건을 전부 계산하면 GPU 없는 서버에서
 *   끝나지 않는다. 고를 수 있는 것은 이름 · 글 · 여러 줄 글 · 선택 · 참조 · 문서(문서는 절마다).
 * - 줄마다 객체 · 맞춘 수 · 조각 · 대기와 **어림**(조각 수 · 용량 · 이 서버의 지난 속도로 잰 시간).
 *   같은 글은 다시 계산하지 않으므로 실제는 대개 어림보다 짧다.
 * - 계산이 돌거나 기다리는 동안은 3초마다 다시 읽어 진행을 보인다.
 */

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Pencil } from 'lucide-react'

import { semanticApi } from '@/modules/semantic/api'
import type { SemanticJob, SemanticState, SemanticType } from '@/modules/semantic/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { TypeIcon } from '@/shared/components/TypeIcon'
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

/** 고른 칸 가운데 「이름」. */
const LABEL_FIELD = 'label'

/** 칸 고르기에 적는 종류 — 증상 · 내역 같은 「여러 줄 글」 이 뜻으로 찾기에 가장 좋다. */
const KIND_LABEL: Record<string, string> = {
  text: '글',
  text_long: '여러 줄 글',
  enum: '선택',
  object_ref: '참조',
  document: '문서 — 절마다',
}

/** 대상 — 타입 slug → 고른 칸(null 이면 자동). 없는 타입은 대상이 아니다. */
type Targets = Record<string, string[] | null>

/** 「약 3.8MB」 · 「약 1.9GB」 — 어림이라 「약」 을 붙인다. */
export function sizeText(bytes: number): string {
  if (bytes <= 0) return '0'
  if (bytes >= 1024 ** 3) return `약 ${(bytes / 1024 ** 3).toFixed(1)}GB`
  if (bytes >= 1024 ** 2) return `약 ${(bytes / 1024 ** 2).toFixed(1)}MB`
  return `약 ${Math.max(1, Math.round(bytes / 1024))}KB`
}

/** 「약 26분」 · 「1분 안」 — 속도를 모르면 null. */
export function timeText(pieces: number, perSecond: number | null): string | null {
  if (!perSecond || pieces <= 0) return null
  const minutes = pieces / perSecond / 60
  if (minutes < 1) return '1분 안'
  if (minutes < 90) return `약 ${Math.round(minutes)}분`
  return `약 ${(minutes / 60).toFixed(1)}시간`
}

function same(a: Targets, b: Targets): boolean {
  const keys = Object.keys(a)
  return (
    keys.length === Object.keys(b).length &&
    keys.every((key) => key in b && JSON.stringify(a[key]) === JSON.stringify(b[key]))
  )
}

function fieldsText(one: SemanticType, fields: string[] | null): string {
  if (fields === null) return '자동(전부)'
  const labels = new Map(one.options.map((option) => [option.key, option.label]))
  return fields.map((key) => (key === LABEL_FIELD ? '이름' : (labels.get(key) ?? key))).join(' · ')
}

function JobLine({ job }: { job: SemanticJob }) {
  const link = (
    <Link to={`/jobs?job=${job.id}`} className="ml-1 underline-offset-4 hover:underline">
      작업 화면에서 보기
    </Link>
  )
  if (job.status === 'queued') {
    return <p className="text-muted-foreground text-sm">계산 대기 중입니다 —{link}</p>
  }
  if (job.status === 'running') {
    const { stage, done = 0, total = 0 } = job.progress
    return (
      <p className="text-muted-foreground text-sm tabular-nums">
        계산 중 — {stage ?? '시작'} {done.toLocaleString()} / {total.toLocaleString()}
        {link}
      </p>
    )
  }
  if (job.status === 'failed') {
    return (
      <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
        마지막 계산이 실패했습니다 — {job.error ?? '사유가 기록되지 않았습니다'}. 한 시간 뒤에 다시
        실행합니다.{link}
      </p>
    )
  }
  const result = job.result ?? {}
  return (
    <p className="text-muted-foreground text-sm tabular-nums">
      마지막 계산 {job.finished_at ? shownDateTime(job.finished_at) : ''} — 새로 계산{' '}
      {(result.embedded ?? 0).toLocaleString()} · 같은 글 재사용{' '}
      {(result.reused ?? 0).toLocaleString()} · 변경 없음 {(result.unchanged ?? 0).toLocaleString()}
      {(result.removed ?? 0) > 0 && ` · 삭제 ${(result.removed ?? 0).toLocaleString()}`}
      {link}
    </p>
  )
}

/** 칸 고르기 — 기록은 하나 이상, 축은 모두 해제하면 「자동」. */
function FieldsDialog({
  type,
  current,
  onClose,
  onPick,
}: {
  type: SemanticType
  current: string[] | null
  onClose: () => void
  onPick: (fields: string[] | null) => void
}) {
  const [checked, setChecked] = useState<string[]>(current ?? [])
  const isLog = type.usage === 'log'
  const choices = [{ key: LABEL_FIELD, label: '이름', data_type: 'label' }, ...type.options]
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{type.type_label} — 뜻으로 찾을 칸</DialogTitle>
          <DialogDescription>
            {isLog
              ? '기록은 칸을 하나 이상 선택합니다 — 증상 · 내역처럼 사람마다 다르게 적는 글이 좋습니다. 같은 글은 한 번만 계산합니다.'
              : '선택하지 않으면 자동(이름 · 글 · 선택 · 참조 · 문서 전부)입니다.'}{' '}
            문서 칸은 제목으로 나눈 절마다 계산합니다.
          </DialogDescription>
        </DialogHeader>
        <ul className="space-y-1.5">
          {choices.map((one) => (
            <li key={one.key}>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="size-4"
                  checked={checked.includes(one.key)}
                  onChange={() =>
                    setChecked(
                      checked.includes(one.key)
                        ? checked.filter((key) => key !== one.key)
                        : [...checked, one.key],
                    )
                  }
                />
                {one.label}
                {KIND_LABEL[one.data_type] && (
                  <span className="text-muted-foreground text-xs">{KIND_LABEL[one.data_type]}</span>
                )}
              </label>
            </li>
          ))}
        </ul>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            취소
          </Button>
          <Button
            disabled={isLog && checked.length === 0}
            onClick={() => onPick(checked.length === 0 ? null : checked)}
          >
            확인
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function SemanticPanel() {
  const state = useResource(() => semanticApi.get(), [])
  /** 고르는 중인 대상 — null 이면 저장된 그대로. */
  const [picked, setPicked] = useState<Targets | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<Error | null>(null)
  const [shown, setShown] = useState<SemanticState | null>(null)
  const [choosing, setChoosing] = useState<SemanticType | null>(null)

  const data = shown ?? state.data
  const running = Boolean(data?.job && ['queued', 'running'].includes(data.job.status))
  const reload = state.reload
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => {
      setShown(null)
      reload()
    }, 3000)
    return () => window.clearInterval(timer)
  }, [running, reload])

  if (!data) {
    return (
      <section className="space-y-2">
        <h2 className="text-base font-semibold">뜻으로 찾기</h2>
        <ErrorNotice error={state.error} />
      </section>
    )
  }

  const saved: Targets = Object.fromEntries(
    data.types.filter((one) => one.enabled).map((one) => [one.type_slug, one.fields]),
  )
  const chosen = picked ?? saved
  const axes = data.types.filter((one) => !one.why_not && one.usage !== 'log')
  const dirty = picked !== null && !same(picked, saved)
  /** 그 줄의 어림이 지금 고른 것과 같은가 — 칸을 바꿨으면 저장해야 다시 어림한다. */
  const estimated = (one: SemanticType) =>
    !(one.type_slug in chosen) ||
    JSON.stringify(chosen[one.type_slug]) === JSON.stringify(one.fields)
  const chosenRows = data.types.filter((one) => one.type_slug in chosen)
  const chosenPieces = chosenRows.reduce((sum, one) => sum + one.estimate_pieces, 0)
  const chosenBytes = chosenRows.reduce((sum, one) => sum + one.estimate_bytes, 0)
  const chosenTime = timeText(chosenPieces, data.per_second)

  async function act(run: () => Promise<SemanticState>) {
    setBusy(true)
    setFailed(null)
    try {
      setShown(await run())
      setPicked(null)
    } catch (caught) {
      setFailed(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  function toggle(one: SemanticType) {
    if (one.type_slug in chosen) {
      const next = { ...chosen }
      delete next[one.type_slug]
      setPicked(next)
    } else if (one.usage === 'log') {
      // 기록은 칸을 먼저 고른다 — 고르면 대상이 된다.
      setChoosing(one)
    } else {
      setPicked({ ...chosen, [one.type_slug]: null })
    }
  }

  return (
    <section className="space-y-3" aria-label="뜻으로 찾기">
      <h2 className="text-base font-semibold">뜻으로 찾기</h2>
      <p className="text-muted-foreground text-sm">
        글자가 달라도 뜻이 가까운 객체를 찾습니다 — 「뒷면 커버」 로 검색하면 「후면 커버」,
        「battery」 로 검색하면 「배터리」 가 표시됩니다. 대상 타입의 객체를 서버 안의 모델(
        <code>{data.model}</code>)로 계산해 두고, 검색 화면이 「뜻이 가까운 것」 으로 함께
        표시합니다. 축 타입은 그대로 대상으로 선택하고(칸은 자동), <b>기록은 칸을 선택</b>합니다 —
        건수가 많아 전부를 계산하면 서버가 감당하지 못합니다. 문서 칸은 제목으로 나눈 절마다
        계산하고, 같은 글은 한 번만 계산합니다.
      </p>

      {data.state === 'off' && (
        <p className="text-muted-foreground text-sm">
          이 설치는 뜻으로 찾기가 비활성입니다(<code>EMBEDDING_BACKEND=off</code>) — 대상을 선택해도
          계산하지 않습니다.
        </p>
      )}
      {data.state === 'problem' && (
        <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          지금은 계산할 수 없습니다 — {data.problem}
        </p>
      )}

      <ErrorNotice error={failed ?? state.error} />

      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm">
          <thead className="text-muted-foreground text-xs">
            <tr className="border-b">
              <th className="w-12 px-3 py-2 text-center font-normal">대상</th>
              <th className="px-3 py-2 text-left font-normal">타입</th>
              <th className="px-3 py-2 text-left font-normal">칸</th>
              <th className="px-3 py-2 text-right font-normal">객체</th>
              <th className="px-3 py-2 text-right font-normal">계산 완료</th>
              <th className="px-3 py-2 text-right font-normal">조각</th>
              <th className="px-3 py-2 text-right font-normal">대기</th>
              <th className="px-3 py-2 text-right font-normal">예상</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {data.types.map((one) => {
              const on = one.type_slug in chosen
              const time = timeText(one.estimate_pieces, data.per_second)
              return (
                <tr key={one.type_slug} className={one.why_not ? 'text-muted-foreground' : ''}>
                  <td className="px-3 py-2 text-center">
                    <input
                      type="checkbox"
                      className="size-4"
                      aria-label={`${one.type_label} 대상`}
                      checked={on}
                      disabled={Boolean(one.why_not) || busy}
                      onChange={() => toggle(one)}
                    />
                  </td>
                  <td className="px-3 py-2">
                    <span className="flex items-center gap-2">
                      <TypeIcon name={one.icon} className="shrink-0" />
                      {one.type_label}
                      {one.usage === 'log' && (
                        <span className="rounded border px-1 text-[10px]">기록</span>
                      )}
                    </span>
                    {one.why_not && <span className="block text-xs">{one.why_not}</span>}
                  </td>
                  <td className="px-3 py-2">
                    <span className="flex items-center gap-1">
                      <span className="text-xs">
                        {on
                          ? fieldsText(one, chosen[one.type_slug])
                          : one.usage === 'log'
                            ? '선택 필요'
                            : '—'}
                      </span>
                      {!one.why_not && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="size-6"
                          aria-label={`${one.type_label} 칸 선택`}
                          disabled={busy}
                          onClick={() => setChoosing(one)}
                        >
                          <Pencil className="size-3.5" />
                        </Button>
                      )}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {one.objects.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {one.synced.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {one.pieces.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {one.pending === null ? '—' : one.pending.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right text-xs tabular-nums">
                    {estimated(one) ? (
                      <>
                        조각 {one.estimate_pieces.toLocaleString()} · {sizeText(one.estimate_bytes)}
                        {time && <span className="block">{time}</span>}
                      </>
                    ) : (
                      '저장하면 다시 어림'
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy || axes.every((one) => one.type_slug in chosen)}
          onClick={() =>
            setPicked({
              ...chosen,
              ...Object.fromEntries(
                axes
                  .filter((one) => !(one.type_slug in chosen))
                  .map((one) => [one.type_slug, null]),
              ),
            })
          }
        >
          축 타입 전체 선택
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={busy || !dirty}
          onClick={() => act(() => semanticApi.setTargets(chosen))}
        >
          저장
        </Button>
        {dirty && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => setPicked(null)}
          >
            취소
          </Button>
        )}
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy || running || Object.keys(saved).length === 0}
          onClick={() => act(() => semanticApi.sync())}
        >
          재계산
        </Button>
        <span className="text-muted-foreground text-xs tabular-nums">
          대상 타입 {chosenRows.length}개 · 조각 약 {chosenPieces.toLocaleString()}개 ·{' '}
          {sizeText(chosenBytes)}
          {chosenTime
            ? ` · 처음 계산 ${chosenTime}(지난 속도 초당 ${data.per_second}개)`
            : ' · 시간은 첫 계산 뒤에 어림합니다'}
          {dirty && ' (저장 전)'}
        </span>
      </div>

      {data.job && <JobLine job={data.job} />}

      {choosing && (
        <FieldsDialog
          type={choosing}
          current={choosing.type_slug in chosen ? chosen[choosing.type_slug] : null}
          onClose={() => setChoosing(null)}
          onPick={(fields) => {
            setPicked({ ...chosen, [choosing.type_slug]: fields })
            setChoosing(null)
          }}
        />
      )}
    </section>
  )
}
