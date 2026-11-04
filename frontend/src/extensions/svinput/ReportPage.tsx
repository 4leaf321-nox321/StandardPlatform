/**
 * 포스트모텀 한 장 — 둘째 층. 머리(제목 · 과제 · 부서) + 결과요약(지표 · 판정 집계) + 문제점 줄.
 * 문제점 줄은 확장의 「문서 한 장」 API 가 하위 기록 수까지 한 번에 준다.
 *
 * **문서는 문제점을 참조해 구성한다**(ADR 0051 7절) — 「문제점 끌어오기」 로 이미 관리되는 문제점을 잇고,
 * 「새 문제점」 은 만들자마자 잇는다. 잇는 길은 설정의 연결(참조 칸 또는 관계)대로, 코어 API 로.
 */

import { Link2, Plus } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ObjectCreateDialog } from '@/modules/objects/ObjectCreateDialog'
import { ObjectRefPicker } from '@/modules/objects/PropertyFields'
import { objectApi } from '@/modules/objects/api'
import { useAuth } from '@/shared/auth/AuthContext'
import { canCreateObject } from '@/shared/auth/roles'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Badge } from '@/shared/components/ui/badge'
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'

import { NotReady } from './NotReady'
import { paths, svApi, type IssueLine } from './api'
import { attach, refText, useSetup } from './useSetup'

/** 판정 요약 — 「통과 2 · 실패 1 / 3」. 판정이 없으면 「—」. */
export function verdictText(counts: IssueLine['counts']): string {
  if (counts.stage_result === 0) return '—'
  return `통과 ${counts.passed} · 실패 ${counts.failed} / ${counts.stage_result}`
}

export default function ReportPage() {
  const { reportId = '' } = useParams()
  const { user } = useAuth()
  const setup = useSetup()
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)
  const [picking, setPicking] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<Error | null>(null)
  const sheet = useResource(() => svApi.sheet(reportId), [reportId])

  const issueType = setup.typeOf('issue')
  const link = setup.linkOf('issue', 'report_ref')
  const modelKey = setup.fieldOf('report', 'model')
  const numKey = setup.fieldOf('kpi', 'numerator')
  const denKey = setup.fieldOf('kpi', 'denominator')

  if (setup.error) return <ErrorNotice error={setup.error} />
  if (!setup.loading && !setup.ready) return <NotReady status={setup.status} />
  if (sheet.error) return <ErrorNotice error={sheet.error} />
  if (!sheet.data) return null

  const { report, issues, kpis } = sheet.data
  const reportTypeSlug = sheet.data.report_type_slug
  const stages = issues.reduce(
    (acc, one) => ({
      total: acc.total + one.counts.stage_result,
      passed: acc.passed + one.counts.passed,
      failed: acc.failed + one.counts.failed,
    }),
    { total: 0, passed: 0, failed: 0 },
  )
  const byStatus = new Map<string, number>()
  for (const one of issues) {
    const key = one.plan_status ?? '(없음)'
    byStatus.set(key, (byStatus.get(key) ?? 0) + 1)
  }
  const canCreate = issueType ? canCreateObject(user, issueType.usage) : false
  const canLink = Boolean(issueType && link && (sheet.data.can_edit || link.kind === 'property'))

  /** 문제점을 이 문서에 잇는다 — 새로 만든 것이든 고른 것이든 같은 길. */
  async function linkIssue(issueId: string) {
    if (!issueType || !link) return
    setBusy(true)
    setFailed(null)
    try {
      const row = await objectApi.profile(issueType.slug, issueId)
      await attach(
        link,
        { typeSlug: reportTypeSlug, id: report.id },
        { typeSlug: issueType.slug, id: issueId, properties: row.object.properties },
        issueType.properties,
      )
      setPicking(false)
      setPicked(null)
      sheet.reload()
    } catch (caught) {
      setFailed(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={report.label}
        description={
          modelKey
            ? `과제 · 모델: ${refText(report.properties[modelKey], report.ref_labels) || '—'}`
            : undefined
        }
        back={{ to: paths.reports(), label: '포스트모텀 목록' }}
        actions={
          <div className="flex gap-2">
            <Button asChild variant="outline">
              <Link to={`/o/${reportTypeSlug}/${report.id}`}>문서 상세</Link>
            </Button>
            {canLink && (
              <Button variant="outline" onClick={() => setPicking(true)}>
                <Link2 className="size-4" />
                문제점 끌어오기
              </Button>
            )}
            {canCreate && issueType && link && (
              <Button onClick={() => setCreating(true)}>
                <Plus className="size-4" />새 문제점
              </Button>
            )}
          </div>
        }
      />
      {failed && <ErrorNotice error={failed} />}

      <section className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-md border p-3">
          <p className="text-muted-foreground text-xs">문제점</p>
          <p className="text-2xl font-semibold">{issues.length}</p>
          <p className="text-muted-foreground text-xs">
            {[...byStatus.entries()].map(([k, n]) => `${k} ${n}`).join(' · ') || '—'}
          </p>
        </div>
        <div className="rounded-md border p-3">
          <p className="text-muted-foreground text-xs">단계별 판정</p>
          <p className="text-2xl font-semibold">{stages.total}</p>
          <p className="text-muted-foreground text-xs">
            통과 {stages.passed} · 실패 {stages.failed}
          </p>
        </div>
        <div className="rounded-md border p-3">
          <p className="text-muted-foreground text-xs">결과요약 지표</p>
          {kpis.length === 0 ? (
            <p className="text-muted-foreground text-sm">지표 기록 없음</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {kpis.map((kpi) => {
                const num = numKey ? kpi.properties[numKey] : undefined
                const den = denKey ? kpi.properties[denKey] : undefined
                const ratio =
                  typeof num === 'number' && typeof den === 'number' && den > 0
                    ? ` ${Math.round((num / den) * 1000) / 10}% (${num}/${den})`
                    : ''
                return (
                  <li key={kpi.id}>
                    <span className="font-medium">{kpi.label}</span>
                    {ratio}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </section>

      {issues.length === 0 ? (
        <EmptyState
          title="이 문서에 실린 문제점이 없습니다"
          hint="「문제점 끌어오기」 로 이미 관리되는 문제점을 잇거나 「새 문제점」 으로 만듭니다. 아무것도 안 보이면 설정의 연결 칸(참조 칸 · 관계)을 확인하세요."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">#</TableHead>
              <TableHead>문제점</TableHead>
              <TableHead>일정 상태</TableHead>
              <TableHead>단계별 판정</TableHead>
              <TableHead className="text-right">원인</TableHead>
              <TableHead className="text-right">재발방지</TableHead>
              <TableHead className="text-right">그림</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {issues.map((one, index) => (
              <TableRow key={one.id}>
                <TableCell className="text-muted-foreground">{index + 1}</TableCell>
                <TableCell>
                  <Link to={paths.issue(one.id)} className="font-medium hover:underline">
                    {one.label}
                  </Link>
                </TableCell>
                <TableCell>
                  {one.plan_status ? <Badge variant="outline">{one.plan_status}</Badge> : '—'}
                </TableCell>
                <TableCell className="text-sm">{verdictText(one.counts)}</TableCell>
                <TableCell className="text-right">{one.counts.cause}</TableCell>
                <TableCell className="text-right">{one.counts.prevention}</TableCell>
                <TableCell className="text-right">{one.counts.figure}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {creating && issueType && link && (
        <ObjectCreateDialog
          type={issueType}
          defs={issueType.properties}
          title="새 문제점"
          // 참조 칸이면 만들 때부터 문서를 가리키게 — 관계면 만든 뒤 선을 긋는다.
          initial={link.kind === 'property' ? { values: { [link.key]: report.id } } : undefined}
          onClose={() => setCreating(false)}
          onCreated={(made) => {
            setCreating(false)
            if (link.kind === 'relation') void linkIssue(made.id)
            else sheet.reload()
          }}
          onOpen={(id) => navigate(paths.issue(id))}
        />
      )}

      {picking && issueType && (
        <Dialog open onOpenChange={(open) => !open && !busy && setPicking(false)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>문제점 끌어오기</DialogTitle>
              <DialogDescription>
                이미 관리되는 문제점을 이 문서에 잇습니다 — 같은 문제점이 심사회 · 포스트모텀에 함께
                실릴 수 있습니다. 문제점 자체는 바뀌지 않습니다.
              </DialogDescription>
            </DialogHeader>
            <ObjectRefPicker typeSlug={issueType.slug} value={picked} onChange={setPicked} />
            <DialogFooter>
              <Button variant="outline" onClick={() => setPicking(false)} disabled={busy}>
                취소
              </Button>
              <Button onClick={() => picked && linkIssue(picked)} disabled={!picked || busy}>
                {busy ? '잇는 중…' : '잇기'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  )
}
