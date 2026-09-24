/**
 * 디지털 트윈 › 역량 — **연계(시험 항목과 시뮬레이션의 짝)를 잇는 자리.**
 *
 * 1단계는 여기까지다: 기준 정보가 준비됐나 · 지금 어떤 짝이 있나 · 잇기 · 끊기.
 * 축 다섯을 매기는 오른쪽 판은 2단계에서 이 화면에 붙는다.
 *
 * ⚠️ **시험 칸은 같은 것끼리 합친다.** 한 시험을 여러 시뮬레이션이 보는 일이 흔해서, 안
 *    합치면 같은 이름이 열 줄 반복되고 「이 시험에 몇 개가 걸렸나」 가 안 읽힌다.
 * ⚠️ **끊으면 평가·이력이 같이 간다**(2단계부터). 확인 문구가 그 사실을 말한다.
 */

import { Link2, Unlink } from 'lucide-react'
import { useState } from 'react'

import { objectApi } from '@/modules/objects/api'
import { useAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin } from '@/shared/auth/roles'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
import { Button } from '@/shared/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'

import { dtApi, type Pair } from './api'

/** 같은 시험 항목끼리 묶는다 — 표에서 첫 줄만 이름을 들고 나머지는 합쳐진다. */
function groupBySubject(rows: Pair[]): { subject: string; rows: Pair[] }[] {
  const out: { subject: string; rows: Pair[] }[] = []
  for (const row of rows) {
    const last = out[out.length - 1]
    if (last && last.subject === row.subject_label) last.rows.push(row)
    else out.push({ subject: row.subject_label, rows: [row] })
  }
  return out
}

export default function PairsPage() {
  const { user } = useAuth()
  const workspace = user?.home_workspace_slug ?? user?.memberships[0]?.slug ?? null
  const setup = useResource(() => dtApi.setupStatus(), [])
  const defs = useResource(() => dtApi.defs(), [])
  const pairs = useResource(() => dtApi.pairs(workspace ?? undefined), [workspace])
  const [failed, setFailed] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)
  const [subject, setSubject] = useState<string | null>(null)
  const [agent, setAgent] = useState<string | null>(null)
  const [unlinking, setUnlinking] = useState<Pair | null>(null)

  const ready = setup.data?.ready ?? false
  const subjectSlug = setup.data?.subject_type_slug ?? null
  const agentSlug = setup.data?.agent_type_slug ?? null

  // 기준 정보가 정해진 뒤에만 후보를 받는다 — 어느 타입인지 모르면 물을 데가 없다.
  const subjects = useResource(
    () => (subjectSlug ? objectApi.list(subjectSlug, { limit: 200 }) : Promise.resolve(null)),
    [subjectSlug],
  )
  const agents = useResource(
    () => (agentSlug ? objectApi.list(agentSlug, { limit: 200 }) : Promise.resolve(null)),
    [agentSlug],
  )

  async function makeSetup() {
    setBusy(true)
    setFailed(null)
    try {
      await dtApi.setup()
      setup.reload()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  async function link() {
    if (!subject || !agent || !workspace) return
    setBusy(true)
    setFailed(null)
    try {
      await dtApi.link({ subject_id: subject, agent_id: agent, workspace_slug: workspace })
      setSubject(null)
      setAgent(null)
      pairs.reload()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  const groups = groupBySubject(pairs.data ?? [])
  const subjectLabel = defs.data?.subject_label ?? '시험 항목'
  const agentLabel = defs.data?.agent_label ?? '시뮬레이션'

  return (
    <div className="space-y-6">
      <PageHeader
        title="역량"
        description={`${subjectLabel}과 ${agentLabel}의 짝(연계)마다 수준을 매깁니다. 축은 ${
          defs.data?.axes.map((one) => one.label).join(' · ') ?? '…'
        } 입니다.`}
      />

      <ErrorNotice error={failed ?? setup.error ?? pairs.error} />

      {setup.data && !ready && (
        <div className="space-y-3 rounded-md border p-4">
          <p className="text-sm">
            먼저 <b>기준 정보</b>를 정합니다 — 어느 온톨로지 타입을 {subjectLabel} ·{' '}
            {agentLabel} 으로 쓸지입니다. 만들면 그 타입의 목록 · 일괄 입력 · 내보내기 화면이
            바로 생깁니다.
          </p>
          {isSystemAdmin(user) ? (
            <Button onClick={() => void makeSetup()} disabled={busy}>
              기준 정보 만들기
            </Button>
          ) : (
            <p className="text-muted-foreground text-sm">
              시스템 관리자가 한 번 만들어야 합니다 — 온톨로지 정의를 바꾸는 일입니다.
            </p>
          )}
        </div>
      )}

      {ready && (
        <>
          <section className="space-y-3 rounded-md border p-4">
            <h2 className="text-sm font-semibold">잇기</h2>
            <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
              <div className="space-y-1">
                <span className="text-muted-foreground text-xs">{subjectLabel}</span>
                <SearchablePicker
                  options={(subjects.data?.items ?? []).map((one) => ({
                    value: one.id,
                    label: one.label,
                    hint: one.key ?? undefined,
                  }))}
                  value={subject}
                  onChange={setSubject}
                  placeholder={`${subjectLabel} 고르기`}
                  emptyText={`${subjectLabel} 이 없습니다 — 먼저 목록 화면에서 넣습니다.`}
                />
              </div>
              <div className="space-y-1">
                <span className="text-muted-foreground text-xs">{agentLabel}</span>
                <SearchablePicker
                  options={(agents.data?.items ?? []).map((one) => ({
                    value: one.id,
                    label: one.label,
                    hint: one.key ?? undefined,
                  }))}
                  value={agent}
                  onChange={setAgent}
                  placeholder={`${agentLabel} 고르기`}
                  emptyText={`${agentLabel} 이 없습니다 — 먼저 목록 화면에서 넣습니다.`}
                />
              </div>
              <Button onClick={() => void link()} disabled={busy || !subject || !agent}>
                <Link2 className="size-4" /> 잇기
              </Button>
            </div>
          </section>

          {groups.length === 0 ? (
            <EmptyState
              title="이어진 연계가 없습니다"
              hint={`${subjectLabel}과 ${agentLabel}을 골라 잇습니다. 평가는 연계마다 매깁니다.`}
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{subjectLabel}</TableHead>
                  <TableHead>{agentLabel}</TableHead>
                  <TableHead className="w-24" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {groups.map((group) =>
                  group.rows.map((row, index) => (
                    <TableRow key={row.id}>
                      {index === 0 && (
                        <TableCell rowSpan={group.rows.length} className="align-top font-medium">
                          {group.subject}
                          <span className="text-muted-foreground ml-2 text-xs">
                            {group.rows.length}개
                          </span>
                        </TableCell>
                      )}
                      <TableCell>{row.agent_label}</TableCell>
                      <TableCell className="text-right">
                        <Button variant="ghost" size="sm" onClick={() => setUnlinking(row)}>
                          <Unlink className="size-4" /> 끊기
                        </Button>
                      </TableCell>
                    </TableRow>
                  )),
                )}
              </TableBody>
            </Table>
          )}
        </>
      )}

      <ConfirmDialog
        open={unlinking !== null}
        title="연계를 끊습니까?"
        description={
          unlinking ? (
            <>
              <b>
                {unlinking.subject_label} · {unlinking.agent_label}
              </b>{' '}
              의 연결을 끊습니다. 이 연계에 매긴 평가와 이력도 함께 사라집니다.
            </>
          ) : (
            ''
          )
        }
        destructive
        confirmLabel="끊기"
        onConfirm={async () => {
          if (!unlinking) return
          await dtApi.unlink(unlinking.id)
          setUnlinking(null)
          pairs.reload()
        }}
        onClose={() => setUnlinking(null)}
      />
    </div>
  )
}
