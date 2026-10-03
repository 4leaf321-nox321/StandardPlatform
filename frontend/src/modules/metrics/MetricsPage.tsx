/**
 * 지표 — **기록을 미리 세어 둔 값**(ADR 0013).
 *
 * 목록은 「무엇을 세어 두었나 · 언제 셌나 · 지금 믿어도 되나」 를 한 줄에 말한다. 실패 · 깨짐 ·
 * 오래됨이 배지로 서야 밤의 타이머가 조용히 멎은 것을 아침에 안다. 정의는 시스템 관리자가
 * 만들고, 값은 누구나 **보이는 것만** 더해 읽는다.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Plus, RefreshCw, Trash2 } from 'lucide-react'

import { jobsApi } from '@/modules/jobs/api'
import { MetricDefinitionDialog } from '@/modules/metrics/MetricDefinitionDialog'
import { metricsApi } from '@/modules/metrics/api'
import type { Metric } from '@/modules/metrics/api'
import { GRAIN_LABELS, freshness, shownNumber } from '@/modules/metrics/metricDrill'
import { useAuth } from '@/shared/auth/AuthContext'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

const TONE: Record<'bad' | 'warn' | 'neutral', string> = {
  bad: 'bg-destructive/10 text-destructive',
  warn: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
  neutral: 'bg-muted text-muted-foreground',
}

export default function MetricsPage() {
  const { user } = useAuth()
  const admin = Boolean(user?.is_system_admin)
  const list = useResource(() => metricsApi.list(), [])
  const [editing, setEditing] = useState<Metric | 'new' | null>(null)
  const [removing, setRemoving] = useState<Metric | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<Error | null>(null)

  const metrics = list.data ?? []

  async function recompute(metric: Metric) {
    setError(null)
    setBusy(metric.slug)
    try {
      const job = await metricsApi.recompute(metric.slug)
      const done = await jobsApi.waitFor(job.id)
      if (done.status !== 'done') {
        setError(new Error(done.error ?? `계산이 끝나지 않았습니다 (${done.status})`))
      }
      list.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(null)
    }
  }

  async function remove(metric: Metric) {
    setError(null)
    try {
      await metricsApi.remove(metric.slug)
      setRemoving(null)
      list.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader
        title="지표"
        description="기록을 미리 세어 둔 값입니다. 비율 · 추이 · 코호트를 계산 시각과 함께 봅니다 — 보이는 부서의 기록만 더해집니다."
        actions={
          admin ? (
            <Button size="sm" onClick={() => setEditing('new')}>
              <Plus className="mr-1 size-4" />새 지표
            </Button>
          ) : undefined
        }
      />

      {list.error && <ErrorNotice error={list.error} />}
      {error && <ErrorNotice error={error} />}

      {list.data && metrics.length === 0 ? (
        <EmptyState
          title="지표가 없습니다"
          hint={
            admin
              ? '「새 지표」 로 원천 기록 타입 · 시간 칸 · 기준을 정하면 밤마다 세어 둡니다.'
              : '시스템 관리자가 정의하면 여기에 섭니다.'
          }
        />
      ) : (
        <ul className="divide-y rounded-md border">
          {metrics.map((metric) => {
            const mark = freshness(metric)
            return (
              <li key={metric.id} className="space-y-1 p-4">
                <div className="flex flex-wrap items-center gap-3">
                  <Link to={`/metrics/${metric.slug}`} className="font-medium hover:underline">
                    {metric.label}
                  </Link>
                  <span className="text-muted-foreground text-xs">
                    {metric.source_type_label} · {metric.measure_label}
                    {metric.grain && ` · ${GRAIN_LABELS[metric.grain] ?? metric.grain}별`}
                    {metric.cohort_grain && ' · 코호트'}
                    {metric.spec.denominator && ' · 비율'}
                  </span>
                  {mark && (
                    <span className={`rounded px-1.5 py-0.5 text-xs ${TONE[mark.tone]}`}>
                      {mark.label}
                    </span>
                  )}
                  {metric.overlap && (
                    <span
                      className="text-muted-foreground text-xs"
                      title="한 기록이 여러 셀에 듭니다 — 셀의 합이 기록 수보다 큽니다"
                    >
                      겹침
                    </span>
                  )}
                  {admin && (
                    <span className="ml-auto flex gap-1">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy === metric.slug}
                        onClick={() => recompute(metric)}
                      >
                        <RefreshCw
                          className={`mr-1 size-3.5 ${busy === metric.slug ? 'animate-spin' : ''}`}
                        />
                        지금 다시 계산
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => setEditing(metric)}>
                        수정
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label="지표 삭제"
                        onClick={() => setRemoving(metric)}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </span>
                  )}
                </div>
                <p className="text-muted-foreground text-xs">
                  기준 {metric.dims.map((one) => one.label).join(' · ') || '없음'} · 셀{' '}
                  {shownNumber(metric.cells)}
                  {metric.last_run_at
                    ? ` · 계산 시각 ${shownDateTime(metric.last_run_at)}`
                    : ' · 아직 한 번도 안 셌습니다'}
                  {metric.interval_hours > 0
                    ? ` · ${metric.interval_hours}시간마다`
                    : ' · 손으로만'}
                </p>
                {metric.broken && <p className="text-destructive text-xs">{metric.broken}</p>}
                {metric.last_status === 'failed' && metric.last_error && (
                  <p className="text-destructive text-xs">{metric.last_error}</p>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {editing && (
        <MetricDefinitionDialog
          existing={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            list.reload()
          }}
        />
      )}
      {removing && (
        <ConfirmDialog
          open
          title={`「${removing.label}」 을 지울까요?`}
          description="세어 둔 셀과 계산 기록이 함께 지워집니다. 다른 지표의 분모면 지울 수 없습니다."
          confirmLabel="지우기"
          destructive
          onConfirm={() => remove(removing)}
          onClose={() => setRemoving(null)}
        />
      )}
    </div>
  )
}
