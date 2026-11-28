/**
 * 객체를 다른 타입으로 옮기기 · 타입 합치기(ADR 0094).
 *
 * 같은 개념이 두 타입에 갈렸을 때(협력사 · 공급사) 이 타입의 객체 전부를 대상 타입으로 옮긴다.
 * 객체 id 가 그대로라 참조 · 관계 · 별칭 · 이력이 따라온다. 계획이 **칸 대응**을 줄마다 보인다 —
 * 같은 키 · 다른 키 · 새로 만들기 · 버림. 사람이 줄마다 바꿀 수 있고(버림은 사람이 고를 때만),
 * 모양이 다른 칸 · 겹치는 객체는 계획이 막는 까닭을 적는다.
 *
 * 옮길 객체가 많으면(2만 건 넘음) 적용이 ONTOLOGY-0106 으로 돌아온다 — 작업으로 계획을 다시 세우고
 * 다음 「옮기기」 가 그 작업을 적용한다(키 변경 · 종류 변경과 같은 무늬).
 */

import { useState } from 'react'
import { ArrowRight, Loader2 } from 'lucide-react'

import { jobsApi } from '@/modules/jobs/api'
import type { Job, JobProgress } from '@/modules/jobs/api'
import { ontologyApi } from '@/modules/ontology/api'
import type { MoveField, MoveOut, MoveRequest } from '@/modules/ontology/api'
import { ApiError } from '@/shared/api/client'
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
import { Label } from '@/shared/components/ui/label'

const SELECT = 'border-input bg-background h-8 rounded-md border px-2 text-sm'

/** 칸 대응의 말 — 「작업」 화면의 옮기기 계획도 같은 말로 읽는다. */
export const MOVE_ACTION_LABEL: Record<MoveField['action'], string> = {
  same: '같은 키',
  map: '다른 키로',
  create: '대상에 새로 생성',
  drop: '버림',
}

interface MoveDialogProps {
  type: { slug: string; label: string }
  /** 옮겨 갈 수 있는 타입(자기 자신 · 원 표 타입은 부르는 쪽이 뺀다). */
  targets: { slug: string; label: string }[]
  onDone: () => void
  onClose: () => void
}

export function MoveDialog({ type, targets, onDone, onClose }: MoveDialogProps) {
  const [to, setTo] = useState('')
  const [mapping, setMapping] = useState<Record<string, string | null>>({})
  const [mergeDuplicates, setMergeDuplicates] = useState(false)
  const [acceptCore, setAcceptCore] = useState(false)
  const [plan, setPlan] = useState<MoveOut | null>(null)
  const [planJob, setPlanJob] = useState<string | null>(null)
  const [progress, setProgress] = useState<JobProgress | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const stale = () => {
    setPlan(null)
    setPlanJob(null)
  }

  function body(apply: boolean): MoveRequest {
    return { to, mapping, merge_duplicates: mergeDuplicates, apply, accept_core: acceptCore }
  }

  async function follow(started: Job): Promise<MoveOut> {
    const done = await jobsApi.waitFor(started.id, (job) => setProgress(job.progress))
    setProgress(null)
    if (done.status !== 'done' || !done.result) {
      throw new Error(done.error ?? '작업이 끝나지 않았습니다.')
    }
    return done.result as unknown as MoveOut
  }

  async function call(apply: boolean): Promise<MoveOut> {
    try {
      return await ontologyApi.moveType(type.slug, body(apply))
    } catch (caught) {
      if (!(caught instanceof ApiError && caught.code.endsWith('ONTOLOGY-0106'))) throw caught
      const started = await ontologyApi.moveTypeJob(type.slug, body(false))
      const planned = await follow(started)
      setPlanJob(started.id)
      return planned
    }
  }

  async function run(apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      const result =
        apply && planJob ? await follow(await jobsApi.apply(planJob)) : await call(apply)
      setPlan(result)
      if (result.applied) onDone()
    } catch (caught) {
      setProgress(null)
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  /** 줄의 고름 — 「계획대로」(서버가 고른 것) · 대상의 키 · 버림. */
  function choose(field: MoveField, value: string) {
    setMapping((now) => {
      const next = { ...now }
      if (value === '') delete next[field.source]
      else if (value === '\u0000drop') next[field.source] = null
      else next[field.source] = value
      return next
    })
    stale()
  }

  /** 대상 키 후보 — 계획에 나온 대상 칸들과 원 키 그대로(새로 생성). */
  const targetKeys = plan
    ? Array.from(
        new Set(
          plan.fields
            .map((one) => one.target)
            .filter((one): one is string => Boolean(one))
            .concat(plan.fields.map((one) => one.source)),
        ),
      ).sort()
    : []
  const needsCore = Boolean(plan && plan.core_consumers.length > 0)
  const canApply =
    plan !== null && !plan.applied && plan.errors.length === 0 && (!needsCore || acceptCore)

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>「{type.label}」 객체를 다른 타입으로 옮기기</DialogTitle>
          <DialogDescription>
            같은 개념이 두 타입에 갈렸을 때 이 타입의 객체 전부를 대상 타입으로 옮깁니다. 객체는
            그대로라 참조 · 관계 · 별칭 · 이력이 따라옵니다. 이 타입은 빈 채로 남습니다.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1.5">
              <Label htmlFor="move-to">대상 타입</Label>
              <select
                id="move-to"
                className={SELECT}
                value={to}
                onChange={(event) => {
                  setTo(event.target.value)
                  setMapping({})
                  stale()
                }}
              >
                <option value="">선택</option>
                {targets.map((one) => (
                  <option key={one.slug} value={one.slug}>
                    {one.label} ({one.slug})
                  </option>
                ))}
              </select>
            </div>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                className="size-4"
                checked={mergeDuplicates}
                onChange={(event) => {
                  setMergeDuplicates(event.target.checked)
                  stale()
                }}
              />
              겹치는 객체 병합(대상에 같은 식별자가 있으면)
            </label>
          </div>

          {plan && (
            <div className="space-y-3">
              {plan.applied && (
                <p className="font-medium">
                  옮겼습니다 — 객체 {plan.moved.toLocaleString()}건
                  {plan.merged > 0 && ` · 병합 ${plan.merged.toLocaleString()}건`}
                </p>
              )}
              <table className="w-full text-left text-xs">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1">원 속성</th>
                    <th className="py-1">대응</th>
                    <th className="py-1 text-right">값</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.fields.map((one) => (
                    <tr key={one.source} className="border-t align-top">
                      <td className="py-1.5">
                        {one.label} <span className="text-muted-foreground">({one.source})</span>
                        {one.note && (
                          <p className={one.blocked ? 'text-destructive' : 'text-muted-foreground'}>
                            {one.note}
                          </p>
                        )}
                      </td>
                      <td className="py-1.5">
                        <select
                          aria-label={`${one.label} 대응`}
                          className={SELECT}
                          value={
                            one.source in mapping
                              ? mapping[one.source] === null
                                ? '\u0000drop'
                                : (mapping[one.source] ?? '')
                              : ''
                          }
                          onChange={(event) => choose(one, event.target.value)}
                        >
                          <option value="">
                            계획대로 — {MOVE_ACTION_LABEL[one.action]}
                            {one.target && one.action !== 'drop' ? ` ${one.target}` : ''}
                          </option>
                          {targetKeys.map((key) => (
                            <option key={key} value={key}>
                              → {key}
                            </option>
                          ))}
                          <option value={'\u0000drop'}>버림(값 삭제)</option>
                        </select>
                      </td>
                      <td className="py-1.5 text-right">{one.values.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {plan.places.length > 0 && (
                <div>
                  <p className="font-medium">바뀌는 자리</p>
                  <ul className="list-disc space-y-0.5 pl-5">
                    {plan.places.map((one) => (
                      <li key={one.what}>
                        {one.what} {one.count.toLocaleString()}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {plan.errors.length > 0 && (
                <div className="text-destructive">
                  <p className="font-medium">지금은 옮길 수 없습니다</p>
                  <ul className="list-disc space-y-0.5 pl-5">
                    {plan.errors.map((one) => (
                      <li key={one}>{one}</li>
                    ))}
                  </ul>
                </div>
              )}
              {plan.warnings.length > 0 && (
                <div>
                  <p className="font-medium">알아 둘 것</p>
                  <ul className="text-muted-foreground list-disc space-y-0.5 pl-5">
                    {plan.warnings.map((one) => (
                      <li key={one}>{one}</li>
                    ))}
                  </ul>
                </div>
              )}
              {needsCore && (
                <label className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4"
                    checked={acceptCore}
                    onChange={(event) => {
                      setAcceptCore(event.target.checked)
                      if (planJob) stale()
                    }}
                  />
                  <span>
                    이 타입은 외부에 공개 중입니다 — 수신 시스템({plan.core_consumers.join(', ')})에
                    통보했습니다.
                  </span>
                </label>
              )}
            </div>
          )}
          {progress && (
            <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
              <Loader2 className="size-3.5 animate-spin" />
              객체가 많아 작업으로 진행합니다 — {progress.stage || '대기'}{' '}
              {progress.total > 0 &&
                `${progress.done.toLocaleString()} / ${progress.total.toLocaleString()}`}
            </p>
          )}
          {error && <ErrorNotice error={error} />}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            취소
          </Button>
          {!canApply ? (
            <Button disabled={!to || busy} onClick={() => void run(false)}>
              {busy && <Loader2 className="mr-1 size-3.5 animate-spin" />}
              계획 보기
            </Button>
          ) : (
            <Button disabled={busy} onClick={() => void run(true)}>
              {busy ? (
                <Loader2 className="mr-1 size-3.5 animate-spin" />
              ) : (
                <ArrowRight className="mr-1 size-3.5" />
              )}
              옮기기 — 객체 {plan.objects.toLocaleString()}건
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
