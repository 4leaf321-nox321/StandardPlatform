/**
 * 사진 일괄 업로드 — zip 하나의 사진을 **파일 이름으로 객체를 찾아** 파일 속성에 업로드한다
 * (ADR 0012, 2026-10-08).
 *
 * 일괄 입력과 같은 무늬다 — 보내면 작업이 되고, 워커가 파일마다 무엇이 될지(업로드 · 못 찾음 ·
 * 여럿에 맞음 · 이미지 아님 · 권한 없음 …)를 세우고, **사람이 읽고 적용한다.** 창을 닫아도 작업
 * 화면에서 같은 계획을 보고 적용한다(`PhotoPlanTable` 을 함께 쓴다).
 *
 * 다른 점 하나 — 못 붙는 파일이 있어도 **붙는 것은 붙는다.** 사진 300장 중 5장의 이름이
 * 틀렸다고 295장을 막으면 사람은 300장을 다시 고른다. 못 붙은 5장은 계획에 까닭과 함께 남는다.
 */

import { useRef, useState } from 'react'
import { Images, Loader2 } from 'lucide-react'

import { jobsApi, STUCK_MS } from '@/modules/jobs/api'
import type { Job } from '@/modules/jobs/api'
import { objectApi } from '@/modules/objects/api'
import type { PhotoPlan } from '@/modules/objects/api'
import { PhotoPlanTable, photoPlanOf, photoPlanWrites } from '@/modules/objects/PhotoPlanTable'
import type { ObjectType, PropertyDef } from '@/modules/ontology/api'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'

type Existing = 'skip' | 'replace' | 'add'

/** 칸에 이미 사진이 있을 때 — 기본은 건너뜀(이미 고른 사진을 조용히 바꾸지 않는다). */
const EXISTING: { value: Existing; label: string; help: string }[] = [
  { value: 'skip', label: '건너뜀', help: '이미 있는 칸은 그대로 둡니다' },
  { value: 'replace', label: '교체', help: '있던 파일을 해제하고 업로드합니다' },
  { value: 'add', label: '추가', help: '있던 파일 곁에 업로드합니다' },
]

interface Props {
  type: ObjectType
  /** 그 타입의 속성 — 파일 속성이 고를 칸이 된다. */
  defs: PropertyDef[]
  onClose: () => void
  /** 적용이 끝났을 때 — 목록을 다시 읽는다(목록의 미리보기가 바뀐다). */
  onApplied: () => void
}

export function PhotoImportDialog({ type, defs, onClose, onApplied }: Props) {
  // 사진만 받는 칸을 앞에 — 「사진」 칸이 「성적서」 칸보다 먼저 골라져 있어야 한다.
  const fields = defs.filter((def) => def.data_type === 'file').sort(
    (a, b) => Number(b.accept === 'image') - Number(a.accept === 'image'),
  )
  const [field, setField] = useState(fields[0]?.key ?? '')
  const [existing, setExisting] = useState<Existing>('skip')
  const [file, setFile] = useState<File | null>(null)
  const [plan, setPlan] = useState<PhotoPlan | null>(null)
  /** 계획을 세운 작업 — 적용은 이 작업의 파일 · 지문으로 간다(zip 을 다시 올리지 않는다). */
  const [planJob, setPlanJob] = useState<Job | null>(null)
  const [running, setRunning] = useState<Job | null>(null)
  const [stuck, setStuck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  /** 고른 것이 바뀌면 앞의 계획은 버린다 — 그 계획은 다른 것을 보고 세운 것이다. */
  const reset = () => {
    setPlan(null)
    setPlanJob(null)
    setError(null)
  }

  const settle = async (started: Job): Promise<PhotoPlan | null> => {
    setRunning(started)
    setStuck(false)
    const since = Date.now()
    const finished = await jobsApi.waitFor(started.id, (job) => {
      setRunning(job)
      setStuck(job.status === 'queued' && Date.now() - since > STUCK_MS)
    })
    setRunning(null)
    setStuck(false)
    const result = photoPlanOf(finished.result)
    if (finished.status !== 'done' || !result) {
      setError(
        new Error(
          finished.error ??
            (finished.status === 'cancelled' ? '취소됐습니다.' : '작업이 끝나지 않았습니다.'),
        ),
      )
      return null
    }
    return result
  }

  const run = async (apply: boolean) => {
    setBusy(true)
    setError(null)
    try {
      if (apply) {
        if (!planJob) return
        const result = await settle(await jobsApi.apply(planJob.id))
        if (result) {
          setPlan(result)
          if (result.applied) onApplied()
        }
        return
      }
      if (!file || !field) return
      // 같은 파일을 다시 고를 수 있게 비운다 — 안 비우면 change 가 안 난다.
      if (inputRef.current) inputRef.current.value = ''
      const started = await objectApi.importPhotos(type.slug, file, { field, existing })
      setPlanJob(started)
      const result = await settle(started)
      if (result) setPlan(result)
    } catch (caught) {
      setRunning(null)
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const writes = plan ? photoPlanWrites(plan) : 0

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{type.label} 사진 일괄 업로드</DialogTitle>
          <DialogDescription>
            zip 하나에 사진을 담아 업로드하면 <strong>파일 이름으로 객체를 찾아</strong>(식별자 ·
            별칭 · 이름) 선택한 칸에 업로드합니다. 먼저 무엇이 될지 보여 주고, 적용은 확인한 뒤에
            합니다. 찾지 못한 파일이 있어도 찾은 것은 업로드됩니다.
          </DialogDescription>
        </DialogHeader>

        <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-xs">
          <li>
            파일 이름이 식별자나 이름이면 됩니다 — <code>P-100.jpg</code>. 한 객체에 여러 장이면{' '}
            <code>P-100_2.jpg</code> · <code>P-100 (2).jpg</code> 처럼 뒤에 번호를 붙이거나{' '}
            <code>P-100</code> 폴더에 담습니다.
          </li>
          <li>
            사진(PNG · JPEG · GIF · WebP)만 업로드합니다. 한 장은 50MB, zip 하나는 서버의 작업
            파일 상한(기본 100MB) · 2,000개까지 — 넘으면 나눠 업로드하세요.
          </li>
          <li>
            관리하는 부서의 객체에만 업로드됩니다. 수정할 수 없는 객체는 계획에 「권한 없음」 으로
            표시됩니다.
          </li>
        </ul>

        <div className="flex flex-wrap items-end gap-4 text-sm">
          {fields.length > 1 && (
            <div className="space-y-1.5">
              <label htmlFor="photo-field" className="text-muted-foreground block text-xs">
                업로드할 칸
              </label>
              <Select
                value={field}
                onValueChange={(next) => {
                  setField(next)
                  reset()
                }}
              >
                <SelectTrigger id="photo-field" className="h-9 w-48" aria-label="업로드할 칸">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {fields.map((def) => (
                    <SelectItem key={def.key} value={def.key}>
                      {def.label}
                      {def.accept === 'image' ? ' (사진만)' : ''}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <fieldset className="space-y-1.5">
            <legend className="text-muted-foreground text-xs">이미 파일이 있는 칸</legend>
            <div className="flex flex-wrap gap-3">
              {EXISTING.map((one) => (
                <label key={one.value} className="flex items-center gap-1.5" title={one.help}>
                  <input
                    type="radio"
                    name="photo-existing"
                    value={one.value}
                    checked={existing === one.value}
                    onChange={() => {
                      setExisting(one.value)
                      reset()
                    }}
                  />
                  {one.label}
                </label>
              ))}
            </div>
          </fieldset>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={inputRef}
            type="file"
            accept=".zip,application/zip,application/x-zip-compressed"
            aria-label="사진 zip 파일"
            className="text-sm"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null)
              reset()
            }}
          />
          <Button size="sm" disabled={!file || !field || busy} onClick={() => void run(false)}>
            {busy && !plan ? (
              <Loader2 className="mr-1 size-3.5 animate-spin" />
            ) : (
              <Images className="mr-1 size-3.5" />
            )}
            미리 보기
          </Button>
        </div>

        {running && (
          <p className="text-muted-foreground flex items-center gap-2 text-xs" role="status">
            <Loader2 className="size-3.5 animate-spin" />
            {running.status === 'queued'
              ? stuck
                ? '아직 아무도 집어 가지 않았습니다 — 작업 워커가 꺼져 있을 수 있습니다. 「내 활동 › 작업」 에서 확인하세요(파일은 이미 올라가 있습니다).'
                : '워커를 기다리는 중 — 작업 화면에서도 볼 수 있습니다.'
              : running.progress.total > 0
                ? `${running.progress.stage} ${running.progress.done.toLocaleString()} / ${running.progress.total.toLocaleString()}개`
                : `${running.progress.stage || '진행 중'}…`}
          </p>
        )}

        {error && <ErrorNotice error={error} />}

        {plan && <PhotoPlanTable plan={plan} />}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {plan?.applied ? '닫기' : '취소'}
          </Button>
          {plan && !plan.applied && (
            <Button
              disabled={writes === 0 || busy}
              onClick={() => void run(true)}
              title={writes === 0 ? '업로드할 사진이 없습니다' : undefined}
            >
              {busy && <Loader2 className="mr-1 size-3.5 animate-spin" />}
              적용 — 업로드 {writes}장
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
