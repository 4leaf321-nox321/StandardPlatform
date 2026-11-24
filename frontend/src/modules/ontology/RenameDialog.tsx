/**
 * 키 · slug 변경(ADR 0093) — **바뀌는 자리를 세어 보이고 한 번에.**
 *
 * 키 · slug 는 보이는 이름이 아니라 화면 주소 · MCP 인자 · 내보내기에 박히는 이름이다. 그래서
 * 저장으로 바꾸지 않고 이 창에서 계획을 먼저 본다 — 바뀌는 자리(객체 값 · 화면 설정 · 저장된 뷰 ·
 * 지표 …)와 건수, 바꾸지 않는 바깥 자리(정제 키트 · 원천 레시피 · 수신 시스템), 막는 것.
 *
 * 값이 많은 키(2만 건 넘음)는 적용이 ONTOLOGY-0105 로 돌아온다 — 그때 작업으로 계획을 다시 세우고,
 * 다음 「변경」 은 그 작업을 적용한다(지문이 「미리 본 것과 같은가」 를 지킨다). 종류 변경과 같은
 * 무늬다(`RetypeDialog`).
 */

import { useState } from 'react'
import { ArrowRight, Loader2 } from 'lucide-react'

import { jobsApi } from '@/modules/jobs/api'
import type { Job, JobProgress } from '@/modules/jobs/api'
import { ontologyApi } from '@/modules/ontology/api'
import type { RenameOut, RenameRequest } from '@/modules/ontology/api'
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
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'

interface RenameDialogProps {
  kind: 'property' | 'type'
  /** 타입 slug — 키 변경이면 그 속성의 주인. */
  slug: string
  /** 키 변경이면 지금 키. */
  propertyKey?: string
  /** 사람이 읽는 이름(「부품 · 무게」). */
  label: string
  /** 바꿨다 — 새 이름을 준다. 부르는 쪽은 옛 이름을 쥔 창을 닫고 다시 읽는다. */
  onDone: (to: string) => void
  onClose: () => void
}

export function RenameDialog({
  kind,
  slug,
  propertyKey,
  label,
  onDone,
  onClose,
}: RenameDialogProps) {
  const current = kind === 'property' ? (propertyKey ?? '') : slug
  const what = kind === 'property' ? '키' : 'slug'
  const [to, setTo] = useState('')
  const [acceptCore, setAcceptCore] = useState(false)
  const [plan, setPlan] = useState<RenameOut | null>(null)
  /** 계획이 작업으로 섰으면 그 작업 — 적용은 그 작업을 적용한다. */
  const [planJob, setPlanJob] = useState<string | null>(null)
  const [progress, setProgress] = useState<JobProgress | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  /** 무엇이든 고치면 계획은 옛것이다 — 다시 보게 한다. */
  const stale = () => {
    setPlan(null)
    setPlanJob(null)
  }

  function body(apply: boolean): RenameRequest {
    return { to: to.trim(), apply, accept_core: acceptCore }
  }

  async function follow(started: Job): Promise<RenameOut> {
    const done = await jobsApi.waitFor(started.id, (job) => setProgress(job.progress))
    setProgress(null)
    if (done.status !== 'done' || !done.result) {
      throw new Error(done.error ?? '작업이 끝나지 않았습니다.')
    }
    return done.result as unknown as RenameOut
  }

  async function call(apply: boolean): Promise<RenameOut> {
    if (kind === 'type') return ontologyApi.renameType(slug, body(apply))
    try {
      return await ontologyApi.renameKey(slug, current, body(apply))
    } catch (caught) {
      // 값이 있는 객체가 많다 — 요청 안에서 안 끝나 작업으로 계획한다(적용은 그 작업으로).
      if (!(caught instanceof ApiError && caught.code.endsWith('ONTOLOGY-0105'))) throw caught
      const started = await ontologyApi.renameKeyJob(slug, current, body(false))
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
      if (result.applied) onDone(result.to)
    } catch (caught) {
      setProgress(null)
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const needsCore = Boolean(plan && plan.core_consumers.length > 0)
  const canApply =
    plan !== null && !plan.applied && plan.errors.length === 0 && (!needsCore || acceptCore)
  const ready = to.trim() !== '' && to.trim() !== current

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            「{label}」 {what} 변경
          </DialogTitle>
          <DialogDescription>
            {what}는 화면 주소 · MCP 인자 · 내보내기에 쓰이는 이름입니다(보이는 이름은 수정 창에서
            변경합니다). 계획에서 바뀌는 자리와 바꾸지 않는 바깥 자리를 확인한 뒤 변경합니다.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 text-sm">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>지금 {what}</Label>
              <Input value={current} readOnly disabled className="font-mono" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="rename-to">새 {what}</Label>
              <Input
                id="rename-to"
                value={to}
                className="font-mono"
                placeholder={kind === 'property' ? 'weight' : 'component'}
                onChange={(event) => {
                  setTo(event.target.value)
                  stale()
                }}
              />
            </div>
          </div>

          {plan && (
            <div className="space-y-3">
              {plan.applied && <p className="font-medium">변경했습니다 — {plan.to}</p>}
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
                  <p className="font-medium">지금은 변경할 수 없습니다</p>
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
              값이 많아 작업으로 진행합니다 — {progress.stage || '대기'}{' '}
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
            <Button disabled={!ready || busy} onClick={() => void run(false)}>
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
              {what} 변경
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
