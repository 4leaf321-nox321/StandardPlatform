/**
 * 「경보 저장」 — 지금 보는 분석을 그 인자 그대로 저장한다(ADR 0016).
 *
 * 저장하면 서버가 한 번 돌려 **지금 있는 결과는 「처음부터 있던 것」 으로 적고 알리지 않는다** —
 * 만들자마자 옛 신호 열두 개가 종에 쌓이면 그 종은 잡음이 된다. 다음 계산부터 처음 보는 결과만
 * 내 알림으로 온다. 경보는 내 눈(내가 보는 부서)으로 돌고 나에게만 온다.
 */

import { useState } from 'react'
import { BellPlus } from 'lucide-react'

import type { AlertRecipe, AlertSaved, Metric } from '@/modules/metrics/api'
import { metricsApi } from '@/modules/metrics/api'
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

const RECIPE_LABELS: Record<AlertRecipe, string> = {
  sprt: '순차 검정',
  control: '관리도',
  changes: '변화점',
}

export interface AlertSaveProps {
  metric: Metric
  recipe: AlertRecipe
  /** 분석 요청의 쿼리 그대로 — `analysisQuery` 로 짓는다. */
  params: Record<string, string>
  disabled?: boolean
}

export function AlertSave({ metric, recipe, params, disabled }: AlertSaveProps) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [launched, setLaunched] = useState(false)
  const [within, setWithin] = useState('6')
  const [notifyGood, setNotifyGood] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [saved, setSaved] = useState<AlertSaved | null>(null)
  // 새 모델 훑기는 모델마다 전작이 다르므로 「전작을 가리키는 칸」 으로만 된다.
  const canScan = recipe === 'sprt' && Boolean(params.reference_via) && !params.reference

  const start = () => {
    setName(`${metric.label} · ${RECIPE_LABELS[recipe]}`)
    setLaunched(false)
    setNotifyGood(false)
    setError(null)
    setSaved(null)
    setOpen(true)
  }

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const body: Record<string, string> = { ...params }
      if (recipe === 'sprt') {
        if (launched) {
          delete body.target
          body.launched_within = within
        }
        if (notifyGood) body.notify_not_worse = 'true'
      }
      setSaved(await metricsApi.createAlert(metric.slug, { name: name.trim(), recipe, params: body }))
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button size="sm" variant="outline" disabled={disabled} onClick={start}>
        <BellPlus className="mr-1 size-4" aria-hidden />
        경보 저장
      </Button>
      {open && (
        <Dialog open onOpenChange={(next) => !next && setOpen(false)}>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle>경보 저장</DialogTitle>
              <DialogDescription>
                지표를 다시 계산할 때마다 이 분석을 같은 인자로 돌려, 처음 보는 결과만 내 알림으로
                보냅니다. 내가 보는 부서의 기록으로 돌고 나에게만 옵니다.
              </DialogDescription>
            </DialogHeader>
            <ErrorNotice error={error} />
            {saved ? (
              <div className="space-y-2 text-sm" aria-label="저장한 경보">
                <p>
                  저장했습니다. 지금 있는 결과{' '}
                  <strong>{saved.baseline?.findings.length ?? 0}건</strong>은 알리지 않습니다 —
                  다음 계산부터 새로 나온 것만 알립니다.
                </p>
                {(saved.baseline?.findings.length ?? 0) > 0 && (
                  <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-xs">
                    {saved.baseline?.findings.map((one) => <li key={one.key}>{one.title}</li>)}
                  </ul>
                )}
                {saved.baseline?.notes.map((note) => (
                  <p key={note} className="text-muted-foreground text-xs">
                    {note}
                  </p>
                ))}
                <p className="text-muted-foreground text-xs">
                  내 경보는 이 지표의 「경보」 탭에서 끄고 켜거나 삭제합니다.
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="space-y-1">
                  <Label htmlFor="alert-name">이름</Label>
                  <Input
                    id="alert-name"
                    value={name}
                    maxLength={120}
                    onChange={(event) => setName(event.target.value)}
                  />
                </div>
                {canScan && (
                  <div className="space-y-1">
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={launched}
                        onChange={(event) => setLaunched(event.target.checked)}
                      />
                      이 모델 대신 최근 출시 모델 전부
                    </label>
                    {launched && (
                      <div className="flex items-center gap-2 pl-6 text-sm">
                        <Input
                          aria-label="최근 몇 기간"
                          type="number"
                          min={1}
                          max={120}
                          className="w-20"
                          value={within}
                          onChange={(event) => setWithin(event.target.value)}
                        />
                        기간 안에 처음 팔린 모델 — 여럿을 한꺼번에 보므로 「나쁨」 의 기준이
                        모델 수만큼 엄격해집니다.
                      </div>
                    )}
                  </div>
                )}
                {recipe === 'sprt' && (
                  <label className="text-muted-foreground flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={notifyGood}
                      onChange={(event) => setNotifyGood(event.target.checked)}
                    />
                    「나쁘지 않음」 도 알림 — 끄면 「나쁨」 만 알립니다.
                  </label>
                )}
                {recipe === 'control' && (
                  <p className="text-muted-foreground text-xs">
                    끝에서 닫힌 부분군 하나의 신호만 새로 봅니다 — 한계는 자료가 늘면 움직여서
                    지난 부분군의 신호를 다시 알리면 잡음이 됩니다.
                  </p>
                )}
                {recipe === 'changes' && (
                  <p className="text-muted-foreground text-xs">
                    끝에서 여섯 기간 안의 변화점만 새로 봅니다. 같은 방향의 변화점이 두 기간 안에서
                    움직이면 같은 것으로 봅니다.
                  </p>
                )}
              </div>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => setOpen(false)}>
                닫기
              </Button>
              {!saved && (
                <Button disabled={busy || !name.trim()} onClick={() => void save()}>
                  {busy ? '저장하는 중…' : '저장'}
                </Button>
              )}
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </>
  )
}
