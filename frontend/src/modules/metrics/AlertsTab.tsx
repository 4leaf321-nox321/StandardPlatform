/**
 * 「경보」 탭 — 이 지표에 건 **내** 경보(ADR 0016).
 *
 * 경보는 분석 탭의 「경보 저장」 으로 만든다(본 것을 그대로 저장하게). 여기서는 끄고 켜고 ·
 * 지금 확인하고 · 발생을 보고 · 삭제한다. 남의 경보는 보이지 않는다 — 경보는 그 사람의 눈으로
 * 돌고 그 사람에게만 간다.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'

import type { AlertCheck, AlertEvent, Metric, MetricAlert } from '@/modules/metrics/api'
import { metricsApi } from '@/modules/metrics/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

function when(value: string | null): string {
  return value ? shownDateTime(value) : '아직'
}

function AlertRow({
  metric,
  alert,
  onChanged,
}: {
  metric: Metric
  alert: MetricAlert
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [checked, setChecked] = useState<AlertCheck | null>(null)
  const [events, setEvents] = useState<AlertEvent[] | null>(null)
  const [removing, setRemoving] = useState(false)

  const run = async (work: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await work()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="space-y-2 rounded-md border p-3" aria-label={alert.name}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{alert.name}</span>
        <Badge variant="outline">{alert.recipe_label}</Badge>
        {!alert.is_active && <Badge variant="secondary">꺼짐</Badge>}
        {alert.last_status === 'failed' && <Badge variant="destructive">확인 실패</Badge>}
        <span className="text-muted-foreground text-xs">
          마지막 확인 {when(alert.last_checked_at)} · 발생 {alert.events}건
        </span>
        <div className="ml-auto flex flex-wrap gap-1">
          <Button size="sm" variant="outline" asChild>
            <Link to={alert.link}>분석 열기</Link>
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() =>
              void run(async () => setChecked(await metricsApi.checkAlert(metric.slug, alert.id)))
            }
          >
            지금 확인
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() =>
              void run(async () =>
                setEvents(events ? null : await metricsApi.alertEvents(metric.slug, alert.id)),
              )
            }
          >
            {events ? '발생 닫기' : '발생'}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await metricsApi.updateAlert(metric.slug, alert.id, {
                  is_active: !alert.is_active,
                })
                onChanged()
              })
            }
          >
            {alert.is_active ? '끄기' : '켜기'}
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => setRemoving(true)}>
            삭제
          </Button>
        </div>
      </div>
      <ConfirmDialog
        open={removing}
        title={`경보 「${alert.name}」 를 삭제합니다`}
        description={
          <>
            경보와 그 발생 기록 {alert.events}건이 함께 사라집니다. 이미 보낸 알림은 남습니다.
          </>
        }
        confirmLabel="삭제"
        destructive
        onConfirm={async () => {
          await metricsApi.removeAlert(metric.slug, alert.id)
          onChanged()
        }}
        onClose={() => setRemoving(false)}
      />
      {alert.last_status === 'failed' && alert.last_error && (
        <p className="text-destructive text-xs">{alert.last_error}</p>
      )}
      <ErrorNotice error={error} />
      {checked && (
        <div className="space-y-1 text-sm" aria-label="지금 확인">
          <p className="text-muted-foreground text-xs">
            지금 셀에서 보이는 결과 {checked.findings.length}건 — 「새것」 은 다음 계산 뒤 알림이
            됩니다. 확인은 적지도 알리지도 않습니다.
          </p>
          <ul className="list-disc space-y-0.5 pl-5 text-xs">
            {checked.findings.map((one) => (
              <li key={one.key}>
                {one.new && <Badge className="mr-1">새것</Badge>}
                {one.title}
              </li>
            ))}
          </ul>
          {checked.notes.map((note) => (
            <p key={note} className="text-muted-foreground text-xs">
              {note}
            </p>
          ))}
        </div>
      )}
      {events && (
        <ul className="space-y-0.5 text-xs" aria-label="발생">
          {events.length === 0 && <li className="text-muted-foreground">발생이 없습니다.</li>}
          {events.map((one) => (
            <li key={one.id}>
              <span className="text-muted-foreground mr-2">{when(one.created_at)}</span>
              <Link to={one.link} className="underline-offset-2 hover:underline">
                {one.title}
              </Link>
              {one.baseline && (
                <span className="text-muted-foreground ml-1">(만들 때부터 있던 것)</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

export function AlertsTab({ metric }: { metric: Metric }) {
  const [version, setVersion] = useState(0)
  const alerts = useResource(() => metricsApi.alerts(metric.slug), [metric.slug, version])
  return (
    <div className="space-y-3">
      <p className="text-muted-foreground text-sm">
        지표를 다시 계산할 때마다 저장한 분석을 같은 인자로 돌려, 처음 보는 결과만 내 알림으로
        보냅니다. 경보는 「분석」 탭의 「경보 저장」 으로 만듭니다.
      </p>
      {alerts.error && <ErrorNotice error={alerts.error} />}
      {alerts.data && alerts.data.length === 0 && (
        <p className="text-muted-foreground text-sm">이 지표에 건 내 경보가 없습니다.</p>
      )}
      {alerts.data && alerts.data.length > 0 && (
        <ul className="space-y-2">
          {alerts.data.map((one) => (
            <AlertRow
              key={one.id}
              metric={metric}
              alert={one}
              onChanged={() => setVersion((value) => value + 1)}
            />
          ))}
        </ul>
      )}
    </div>
  )
}
