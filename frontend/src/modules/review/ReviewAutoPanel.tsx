/**
 * 검토함의 **자동 확인** — 시스템 관리자가 켠 종류를 밤마다 「추천대로」(ADR 0075).
 *
 * 켜 둔다고 늘 도는 것이 아니다 — 그 확신도 이상에서 **사람이 하나씩 정한 결정**이 충분히 맞아야
 * 돈다(결정 수 · 추천대로 한 몫). 자동으로 정한 것을 누가 복원하면 멈춘다. 그래서 여기에는 켰나만이
 * 아니라 **지금 돌 수 있나와 그 까닭**을 적는다 — 「켰는데 왜 안 돌지」 를 묻지 않게.
 *
 * 처리는 켠 사람의 이름으로 남고(결정 기록 · 감사 기록), 밤마다 묶음 하나라 아래 「최근 적용한
 * 묶음」 에서 복원한다. 부서 관리자는 볼 수만 있다.
 */

import { useState } from 'react'

import { reviewApi } from '@/modules/review/api'
import type { ReviewAuto } from '@/modules/review/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

function evidenceText(one: ReviewAuto): string {
  const rate = one.rate === null ? '—' : `${Math.round(one.rate * 100)}%`
  return `확신도 ${one.threshold.toFixed(2)} 이상에서 사람이 하나씩 정한 결정 ${one.decided.toLocaleString()}건 · 추천대로 ${rate}`
}

function Row({ one, onSaved }: { one: ReviewAuto; onSaved: () => void }) {
  const [threshold, setThreshold] = useState(one.threshold.toFixed(2))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  async function save(enabled: boolean) {
    const value = Number(threshold)
    if (!Number.isFinite(value) || value < 0.9 || value > 1) {
      setError(new Error('자동 확인의 문턱은 0.9 이상 1 이하입니다.'))
      return
    }
    setBusy(true)
    setError(null)
    try {
      await reviewApi.setAuto(one.kind, { enabled, threshold: value })
      onSaved()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="space-y-1 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{one.label}</span>
        {one.enabled ? (
          <Badge variant={one.ready ? 'default' : 'secondary'}>
            {one.ready ? '켜짐 — 다음 차례에 돈다' : '켜짐 — 지금은 안 돈다'}
          </Badge>
        ) : (
          <Badge variant="outline">꺼짐</Badge>
        )}
        {one.can_edit && (
          <span className="ml-auto flex items-center gap-1.5">
            <label className="flex items-center gap-1 text-xs">
              <span className="text-muted-foreground">문턱</span>
              <Input
                aria-label={`${one.label} 자동 확인 문턱`}
                className="h-7 w-16"
                value={threshold}
                disabled={busy}
                onChange={(event) => setThreshold(event.target.value)}
              />
            </label>
            {one.enabled ? (
              <>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => void save(true)}>
                  문턱 저장
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void save(false)}
                >
                  끄기
                </Button>
              </>
            ) : (
              <Button size="sm" disabled={busy} onClick={() => void save(true)}>
                켜기
              </Button>
            )}
          </span>
        )}
      </div>
      <p className="text-muted-foreground text-xs">
        {evidenceText(one)} — 결정 {one.min_decided}건 이상 · 추천대로{' '}
        {Math.round(one.min_rate * 100)}% 이상이어야 돕니다.
      </p>
      {one.enabled && one.why && (
        <p className="text-xs text-amber-700 dark:text-amber-400">{one.why}</p>
      )}
      {one.enabled && (
        <p className="text-muted-foreground text-xs">
          {one.enabled_by_name ?? '—'} 의 이름으로 · 하루 한 번({one.hour}시 이후) · 한 번에{' '}
          {one.max_per_night.toLocaleString()}건까지
          {one.last_run_at && ` · 마지막 ${shownDateTime(one.last_run_at)}`}
          {one.last_note && ` — ${one.last_note}`}
        </p>
      )}
      {error && <ErrorNotice error={error} />}
    </li>
  )
}

export function ReviewAutoPanel() {
  const list = useResource(() => reviewApi.auto(), [])
  const rows = list.data ?? []
  const enabled = rows.filter((one) => one.enabled).length
  return (
    <details className="rounded-md border text-sm">
      <summary className="cursor-pointer px-3 py-2 font-medium">
        자동 확인(밤마다 추천대로){enabled > 0 && ` — ${enabled}종류 켜짐`}
      </summary>
      <div className="space-y-2 border-t px-3 py-2">
        <p className="text-muted-foreground text-xs">
          켠 종류는 밤마다 확신도가 문턱 이상인 것을 「추천대로」 처리합니다 — 켠 시스템 관리자의
          이름으로, 그 구간에서 사람이 하나씩 정한 결정이 충분히 맞을 때만. 밤마다 묶음 하나로 남아
          「최근 적용한 묶음」 에서 복원하고, 자동으로 정한 것을 복원하면 멈춥니다.
        </p>
        {list.error && <ErrorNotice error={list.error} />}
        {list.data && rows.length === 0 && (
          <p className="text-muted-foreground text-xs">처리 길이 있는 종류가 없습니다.</p>
        )}
        <ul className="divide-y rounded-md border">
          {rows.map((one) => (
            <Row key={one.kind} one={one} onSaved={list.reload} />
          ))}
        </ul>
      </div>
    </details>
  )
}
