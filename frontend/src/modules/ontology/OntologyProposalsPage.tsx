/**
 * 정의 제안 — **적용 대신 쌓인 정의**(ADR 0096).
 *
 * AI · 사람이 `ontology_propose` 로 낸 정의를 계획(바뀌는 것 · 경고 · 비슷한 정의)과 함께 본다.
 * 적용 · 거절은 **검토함**에서 한다(결정 기록 · 복원이 거기 있다) — 이 화면은 계획 전부를 읽고,
 * 그새 정의가 바뀌었으면 「다시 셈」, 낸 사람은 「철회」 한다.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Inbox, RefreshCw } from 'lucide-react'

import { ImportPlanView } from '@/modules/ontology/ImportPlanView'
import { ontologyApi } from '@/modules/ontology/api'
import type { OntologyProposal } from '@/modules/ontology/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

const SELECT = 'border-input bg-background h-8 rounded-md border px-2 text-sm'

const STATUS_FILTERS: { value: OntologyProposal['status'] | 'all'; label: string }[] = [
  { value: 'proposed', label: '열린 제안' },
  { value: 'accepted', label: '적용' },
  { value: 'rejected', label: '거절' },
  { value: 'withdrawn', label: '철회' },
  { value: 'all', label: '전체' },
]

export default function OntologyProposalsPage() {
  const [status, setStatus] = useState<OntologyProposal['status'] | 'all'>('proposed')
  const found = useResource(() => ontologyApi.proposals(status), [status])
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [withdrawing, setWithdrawing] = useState<OntologyProposal | null>(null)

  async function replan(one: OntologyProposal) {
    setBusy(one.id)
    setError(null)
    try {
      const next = await ontologyApi.replanProposal(one.id)
      setNotice(
        next.replanned
          ? `「${one.summary}」 의 계획이 달라졌습니다 — 지금은 「${next.summary}」 입니다. 다시 읽고 검토함에서 고르세요.`
          : `「${one.summary}」 의 계획은 그대로입니다.`,
      )
      found.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="text-muted-foreground max-w-3xl text-sm">
          AI · 사람이 <b>적용 대신 제안</b>한 정의입니다. 적용 · 거절은 검토함에서 합니다 — 지금
          정의로 다시 센 계획이 제안 때와 같을 때만 적용되고, 그새 정의가 바뀌었으면 「다시 셈」
          으로 새 계획을 읽은 뒤 고릅니다.
        </p>
        <div className="flex items-center gap-2">
          <label className="text-muted-foreground text-sm" htmlFor="proposal-status">
            상태
          </label>
          <select
            id="proposal-status"
            className={SELECT}
            value={status}
            onChange={(event) => setStatus(event.target.value as typeof status)}
          >
            {STATUS_FILTERS.map((one) => (
              <option key={one.value} value={one.value}>
                {one.label}
              </option>
            ))}
          </select>
          <Button variant="outline" size="sm" asChild>
            <Link to="/review?kind=definition_proposal">
              <Inbox className="mr-1 size-4" />
              검토함에서 처리
            </Link>
          </Button>
        </div>
      </div>

      {notice && <p className="text-sm">{notice}</p>}
      {error && <ErrorNotice error={error} />}
      {found.error && <ErrorNotice error={found.error} />}
      {found.data && found.data.length === 0 && (
        <EmptyState
          title="정의 제안이 없습니다"
          hint="AI 가 MCP 의 ontology_propose 로 내면 여기에 쌓입니다."
        />
      )}

      {(found.data ?? []).map((one) => (
        <section key={one.id} id={one.id} className="space-y-3 rounded-md border p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 space-y-1">
              <h2 className="flex flex-wrap items-center gap-2 text-base font-semibold">
                {one.summary}
                <Badge variant={one.status === 'proposed' ? 'default' : 'secondary'}>
                  {one.status_label}
                </Badge>
              </h2>
              <p className="text-muted-foreground text-xs">
                {one.proposed_by || '(알 수 없음)'}
                {one.tool && ` · AI 도구 ${one.tool}`}
                {!one.tool && one.token_name && ` · 토큰 「${one.token_name}」`} ·{' '}
                {shownDateTime(one.created_at)}
                {one.source && ` · 원천 잠금 ${one.source}`} · 계획을 센 때{' '}
                {shownDateTime(one.planned_at)}
              </p>
            </div>
            {one.status === 'proposed' && (
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy === one.id}
                  onClick={() => void replan(one)}
                >
                  <RefreshCw className="mr-1 size-3.5" />
                  다시 셈
                </Button>
                <Button variant="outline" size="sm" onClick={() => setWithdrawing(one)}>
                  철회
                </Button>
              </div>
            )}
          </div>
          <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[6rem_1fr]">
            <dt className="text-muted-foreground">근거</dt>
            <dd className="whitespace-pre-wrap">{one.note || '(적지 않음)'}</dd>
            {one.origin_ref && (
              <>
                <dt className="text-muted-foreground">근거 문서</dt>
                <dd>{one.origin_ref}</dd>
              </>
            )}
            {one.decided_at && (
              <>
                <dt className="text-muted-foreground">결정</dt>
                <dd>
                  {one.status_label} · {one.decided_by} · {shownDateTime(one.decided_at)}
                  {one.decision_note && ` — ${one.decision_note}`}
                  {one.snapshot_id && (
                    <>
                      {' '}
                      · 적용 직전 정의는{' '}
                      <Link className="underline" to="/admin/ontology/import">
                        가져오기 › 정의 이력
                      </Link>
                      에서 복원합니다
                    </>
                  )}
                </dd>
              </>
            )}
          </dl>
          <details open={one.status === 'proposed'}>
            <summary className="cursor-pointer text-sm font-medium">계획</summary>
            <div className="mt-3 space-y-3">
              <ImportPlanView plan={one.plan} />
            </div>
          </details>
        </section>
      ))}

      <ConfirmDialog
        open={withdrawing !== null}
        title="제안 철회"
        description={
          withdrawing
            ? `「${withdrawing.summary}」 제안을 철회합니다. 검토함에서 빠지고, 같은 정의를 다시 제안할 수 있습니다.`
            : ''
        }
        confirmLabel="철회"
        destructive
        onConfirm={async () => {
          if (!withdrawing) return
          await ontologyApi.withdrawProposal(withdrawing.id)
          setWithdrawing(null)
          found.reload()
        }}
        onClose={() => setWithdrawing(null)}
      />
    </div>
  )
}
