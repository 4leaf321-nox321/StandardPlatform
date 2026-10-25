/**
 * 눈여겨볼 것(ADR 0032) — **물어보기 전에 알린다.**
 *
 * 분석은 사람이 지표 · 기준 · 값을 골라 물어야 답한다. 경보는 사람이 고른 곳만 본다. 이 화면은
 * 플랫폼이 스스로 모든 지표를 기준 값마다(증상 x 모델 같은 조합까지) 훑어 찾은 변화 · 차이를
 * 점수 높은 것부터 보인다. 지표를 다시 센 뒤 저절로 훑는다. 알림은 보내지 않는다 — 받고 싶으면
 * 그 분석을 경보로 저장한다.
 */

import { useState } from 'react'

import { InsightList } from '@/modules/insights/InsightList'
import { insightsApi } from '@/modules/insights/api'
import type { Insight, InsightKind } from '@/modules/insights/api'
import { useAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin } from '@/shared/auth/roles'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

const KINDS: { value: InsightKind | ''; label: string }[] = [
  { value: '', label: '전부' },
  { value: 'change', label: '변화점' },
  { value: 'worse', label: '새 모델이 전작보다 나쁨' },
  { value: 'high', label: '다른 집단보다 높음' },
  { value: 'signal', label: '관리도 신호' },
]

export default function InsightsPage() {
  const { user } = useAuth()
  const admin = isSystemAdmin(user)
  const [kind, setKind] = useState<InsightKind | ''>('')
  const [past, setPast] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const list = useResource(
    () =>
      insightsApi.list({
        kind: kind || undefined,
        include_past: past,
        include_dismissed: dismissed,
        limit: 200,
      }),
    [kind, past, dismissed],
  )
  const [busyId, setBusyId] = useState<string | null>(null)
  const [failed, setFailed] = useState<Error | null>(null)
  const [scanned, setScanned] = useState<number | null>(null)

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

  async function scanNow() {
    setFailed(null)
    try {
      setScanned((await insightsApi.scan()).queued)
    } catch (caught) {
      setFailed(caught as Error)
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="눈여겨볼 것"
        description="플랫폼이 스스로 지표를 훑어 찾은 변화 · 차이입니다. 점수(몇 배 · 몇 건)가 높은 것부터 — 지표를 다시 센 뒤 저절로 훑습니다."
        actions={
          admin ? (
            <Button size="sm" variant="outline" onClick={() => void scanNow()}>
              지금 훑기
            </Button>
          ) : undefined
        }
      />
      <ErrorNotice error={failed ?? list.error} />
      {scanned !== null && (
        <p className="text-muted-foreground text-sm">
          훑기 작업 {scanned}개를 넣었습니다 — 작업이 끝나면 여기 섭니다.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-2">
          <span className="text-muted-foreground">종류</span>
          <select
            className="rounded-md border px-2 py-1"
            value={kind}
            onChange={(event) => setKind(event.target.value as InsightKind | '')}
          >
            {KINDS.map((one) => (
              <option key={one.value} value={one.value}>
                {one.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={past}
            onChange={(event) => setPast(event.target.checked)}
          />
          지난 것도
        </label>
        {admin && (
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={dismissed}
              onChange={(event) => setDismissed(event.target.checked)}
            />
            그만 본 것도
          </label>
        )}
        {list.data?.scanned_at && (
          <span className="text-muted-foreground ml-auto text-xs">
            마지막 훑기 {shownDateTime(list.data.scanned_at)}
          </span>
        )}
      </div>
      {list.data && list.data.items.length === 0 ? (
        <EmptyState
          title="눈여겨볼 것이 없습니다"
          hint="아직 훑지 않았거나, 볼 수 있는 지표에서 찾은 변화가 없습니다. 지표의 셀이 든 부서를 모두 볼 수 있어야 그 지표의 결론이 보입니다."
        />
      ) : (
        <InsightList
          items={list.data?.items ?? []}
          onDismiss={admin ? (one) => void toggle(one) : undefined}
          busyId={busyId}
        />
      )}
      {list.data && list.data.total > list.data.items.length && (
        <p className="text-muted-foreground text-xs">
          {list.data.total}개 중 점수 높은 {list.data.items.length}개만 보입니다.
        </p>
      )}
    </div>
  )
}
