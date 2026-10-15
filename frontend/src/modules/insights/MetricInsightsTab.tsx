/**
 * 지표 상세의 「눈여겨볼 것」 — 이 지표에서 플랫폼이 찾은 결론(ADR 0032).
 *
 * 시스템 관리자는 여기서 이 지표의 훑기를 끄고 켠다 — 결론이 잡음뿐인 지표. 끈 지표의 결론은 지난
 * 것으로 남는다.
 */

import { useState } from 'react'

import { InsightList } from '@/modules/insights/InsightList'
import { insightsApi } from '@/modules/insights/api'
import type { Insight } from '@/modules/insights/api'
import type { Metric } from '@/modules/metrics/api'
import { useAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin } from '@/shared/auth/roles'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

export function MetricInsightsTab({
  metric,
  onChanged,
}: {
  metric: Metric
  /** 훑기를 끄고 켜면 지표를 다시 읽는다. */
  onChanged: () => void
}) {
  const { user } = useAuth()
  const admin = isSystemAdmin(user)
  const list = useResource(
    () => insightsApi.list({ metric: metric.slug, include_past: true, limit: 200 }),
    [metric.slug],
  )
  const [busy, setBusy] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [failed, setFailed] = useState<Error | null>(null)

  async function setOff(off: boolean) {
    setBusy(true)
    setFailed(null)
    try {
      await insightsApi.setMetric(metric.slug, off)
      onChanged()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  async function toggle(one: Insight) {
    setBusyId(one.id)
    setFailed(null)
    try {
      await insightsApi.dismiss(one.id, !one.dismissed)
      list.reload()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <p className="text-muted-foreground">
          {metric.insights_off
            ? '이 지표는 훑지 않습니다.'
            : metric.insights_scanned_at
              ? `마지막 훑기 ${shownDateTime(metric.insights_scanned_at)} — 지표를 다시 센 뒤 저절로 훑습니다.`
              : '아직 훑지 않았습니다 — 지표를 다시 센 뒤 저절로 훑습니다.'}
        </p>
        {admin && (
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            disabled={busy}
            onClick={() => void setOff(!metric.insights_off)}
          >
            {metric.insights_off ? '훑기 켜기' : '훑기 끄기'}
          </Button>
        )}
      </div>
      <ErrorNotice error={failed ?? list.error} />
      {list.data && list.data.items.length === 0 ? (
        <p className="text-muted-foreground text-sm">이 지표에서 찾은 것이 없습니다.</p>
      ) : (
        <InsightList
          items={list.data?.items ?? []}
          showMetric={false}
          onDismiss={admin ? (one) => void toggle(one) : undefined}
          busyId={busyId}
        />
      )}
    </div>
  )
}
