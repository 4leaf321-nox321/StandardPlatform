/**
 * 디지털 트윈 › 역량 — **연계(시험 항목과 시뮬레이션의 조합)를 등록하고 평가하는 화면.**
 *
 * 1단계의 범위: 기준 정보 준비 상태 · 연계 목록 · 연계 등록 · 연계 해제.
 * 축 다섯을 매기는 오른쪽 판은 2단계에서 이 화면에 붙는다.
 *
 * ⚠️ **시험 칸은 같은 것끼리 합친다.** 한 시험을 여러 시뮬레이션이 보는 일이 흔해서, 안
 *    합치면 같은 이름이 열 줄 반복되고 「이 시험에 몇 개가 걸렸나」 가 안 읽힌다.
 * ⚠️ **연계를 해제하면 평가·이력이 함께 삭제된다**(2단계부터). 확인 문구가 그 사실을 말한다.
 */

import { ExternalLink, Link2, Pencil, Search, Unlink } from 'lucide-react'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { objectApi } from '@/modules/objects/api'
import { workspaceApi } from '@/modules/workspaces/api'
import { useAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin } from '@/shared/auth/roles'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'

import { AssessmentPanel } from './AssessmentPanel'
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
  const home = user?.home_workspace_slug ?? user?.memberships[0]?.slug ?? null
  const [target, setTarget] = useState<string>(home ?? '')
  const setup = useResource(() => dtApi.setupStatus(), [])
  const defs = useResource(() => dtApi.defs(), [])
  const workspaces = useResource(() => workspaceApi.list(true), [])
  // **목록은 부서로 걸지 않는다.** 홈 부서로 걸어 두었더니 다른 부서에 등록한 연계가
  // 화면에서 사라졌고, 사람은 그것을 「저장이 안 됐다」 로 읽었다(실측 2026-09-24).
  // 볼 수 있는 범위는 서버가 판정한다 — 화면이 한 번 더 좁히면 그 사실이 어디에도 안 적힌다.
  const pairs = useResource(() => dtApi.pairs(), [])
  const [failed, setFailed] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)
  const [subject, setSubject] = useState<string | null>(null)
  const [agent, setAgent] = useState<string | null>(null)
  const [unlinking, setUnlinking] = useState<Pair | null>(null)
  // 대시보드의 액자를 누르면 `?pair=…` 로 온다 — **그 줄이 골라진 채로 열린다.**
  // 안 그러면 사람이 방금 누른 것을 목록에서 다시 찾아야 한다.
  const [params] = useSearchParams()
  const [picked, setPicked] = useState<string | null>(params.get('pair'))
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<Pair | null>(null)
  const [editTarget, setEditTarget] = useState<string>('')
  const [query, setQuery] = useState('')
  const [chosen, setChosen] = useState<string[]>([])
  const [bulkTarget, setBulkTarget] = useState('')
  const [bulkUnlinking, setBulkUnlinking] = useState(false)

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

  async function moveChosen() {
    if (!bulkTarget || chosen.length === 0) return
    setBusy(true)
    setFailed(null)
    try {
      await dtApi.bulkMove(chosen, bulkTarget)
      setChosen([])
      setBulkTarget('')
      pairs.reload()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  async function moveTo() {
    if (!editing || !editTarget) return
    setBusy(true)
    setFailed(null)
    try {
      await dtApi.move(editing.id, editTarget)
      setEditing(null)
      pairs.reload()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  async function link() {
    if (!subject || !agent || !target) return
    setBusy(true)
    setFailed(null)
    try {
      await dtApi.link({ subject_id: subject, agent_id: agent, workspace_slug: target })
      setSubject(null)
      setAgent(null)
      setAdding(false)
      pairs.reload()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  // **찾는 것은 이름으로 찾는다.** 스무 건이 넘어가면 눈으로 훑는 것이 가장 느리다.
  const needle = query.trim().toLowerCase()
  const shown = (pairs.data ?? []).filter((one) =>
    needle
      ? `${one.subject_label} ${one.agent_label} ${one.agent_tools.join(' ')} ${
          one.agent_dept ?? ''
        } ${one.workspace_name}`
          .toLowerCase()
          .includes(needle)
      : true,
  )
  const groups = groupBySubject(shown)
  const current = (pairs.data ?? []).find((one) => one.id === picked) ?? null
  // **불량 유형은 시험 항목이 든 목록이다.** 수단에 두면 같은 시험인데 도구마다 목록이
  // 갈려 「이 시험의 불량 중 아직 아무 데서도 재현 안 되는 것」 을 셀 수 없다.
  const defectTypes = current
    ? (((subjects.data?.items ?? []).find((one) => one.id === current.subject_id)?.properties
        ?.defect_types as string[] | undefined) ?? [])
    : []
  const subjectLabel = defs.data?.subject_label ?? '시험 항목'
  const agentLabel = defs.data?.agent_label ?? '시뮬레이션'

  return (
    <div className="space-y-6">
      <PageHeader
        title="역량"
        description={`${subjectLabel} · ${agentLabel} 연계별로 수준을 평가합니다. 평가 축은 ${
          defs.data?.axes.map((one) => one.label).join(' · ') ?? '…'
        } 입니다.`}
      />

      <ErrorNotice error={failed ?? setup.error ?? pairs.error} />

      {ready && (
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-4 -translate-y-1/2" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="시험 항목 · 해석 · 도구 · 부서로 검색"
              className="w-72 pl-8"
            />
          </div>
          <span className="text-muted-foreground text-sm">
            {needle ? `${shown.length} / ${pairs.data?.length ?? 0}` : `연계 ${shown.length}`}건
          </span>

          {/* **고른 것이 있으면 할 수 있는 일을 옆에 둔다.** 서른 건을 고른 사람에게
              서른 번을 누르게 하지 않는다. */}
          {chosen.length > 0 && (
            <div className="ml-auto flex flex-wrap items-center gap-2">
              <span className="text-sm">{chosen.length}건 선택</span>
              <Select value={bulkTarget} onValueChange={setBulkTarget}>
                <SelectTrigger className="w-44">
                  <SelectValue placeholder="부서 옮기기" />
                </SelectTrigger>
                <SelectContent>
                  {(workspaces.data ?? []).map((one) => (
                    <SelectItem key={one.slug} value={one.slug}>
                      {one.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                variant="outline"
                size="sm"
                disabled={busy || !bulkTarget}
                onClick={() => void moveChosen()}
              >
                옮기기
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => setBulkUnlinking(true)}
              >
                <Unlink className="size-4" /> 해제
              </Button>
            </div>
          )}
        </div>
      )}

      {setup.data && !ready && (
        <div className="space-y-3 rounded-md border p-4">
          <p className="text-sm">
            <b>기준 정보 설정</b>이 필요합니다 — {subjectLabel} · {agentLabel} 으로 사용할
            온톨로지 타입을 지정합니다. 생성 후에는 해당 타입의 목록 · 일괄 입력 · 내보내기
            화면을 즉시 사용할 수 있습니다.
          </p>
          {isSystemAdmin(user) ? (
            <Button onClick={() => void makeSetup()} disabled={busy}>
              기준 정보 생성
            </Button>
          ) : (
            <p className="text-muted-foreground text-sm">
              시스템 관리자의 1회 생성이 필요합니다 — 온톨로지 정의 변경에 해당합니다.
            </p>
          )}
        </div>
      )}

      {ready && (
        <>
          {/* **기준 정보를 넣는 자리는 타입 화면이다.** 목록 · 상세 · 일괄 입력 ·
              내보내기가 거기 이미 있어 확장이 다시 만들지 않는다. 다만 여기서 한 걸음에
              갈 수 있어야 한다 — 없으면 주소를 외워야 한다. */}
          <section className="flex flex-wrap items-center gap-3 rounded-md border p-4 text-sm">
            <span className="text-muted-foreground">기준 정보 관리</span>
            <Link
              to={`/o/${setup.data?.subject_type_slug ?? ''}`}
              className="inline-flex items-center gap-1 hover:underline"
            >
              {subjectLabel} <ExternalLink className="size-3.5" />
            </Link>
            <Link
              to={`/o/${setup.data?.agent_type_slug ?? ''}`}
              className="inline-flex items-center gap-1 hover:underline"
            >
              {agentLabel} <ExternalLink className="size-3.5" />
            </Link>
            <Button size="sm" className="ml-auto" onClick={() => setAdding(true)}>
              <Link2 className="size-4" /> 연계 등록
            </Button>
          </section>
          <p className="text-muted-foreground text-xs">
            시험 항목 · 시뮬레이션 해석의 추가 · 수정 · 일괄 입력 · 내보내기는 해당 타입
            화면에서 수행합니다.
          </p>

          <Dialog open={adding} onOpenChange={setAdding}>
            <DialogContent className="sm:max-w-2xl">
              <DialogHeader>
                <DialogTitle>연계 등록</DialogTitle>
                <DialogDescription>
                  {subjectLabel}과 {agentLabel}을 선택합니다. 평가는 연계 단위로 수행합니다.
                </DialogDescription>
              </DialogHeader>
              <div className="grid gap-3 sm:grid-cols-2">
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
                    placeholder={`${subjectLabel} 선택`}
                    emptyText={`${subjectLabel} 이 없습니다 — 해당 타입의 목록 화면에서 먼저 등록합니다.`}
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
                    placeholder={`${agentLabel} 선택`}
                    emptyText={`${agentLabel} 이 없습니다 — 해당 타입의 목록 화면에서 먼저 등록합니다.`}
                  />
                </div>
                <div className="space-y-1">
                  <span className="text-muted-foreground text-xs">소속 부서</span>
                  <Select value={target} onValueChange={setTarget}>
                    <SelectTrigger>
                      <SelectValue placeholder="부서 선택" />
                    </SelectTrigger>
                    <SelectContent>
                      {(workspaces.data ?? []).map((one) => (
                        <SelectItem key={one.slug} value={one.slug}>
                          {one.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <DialogFooter>
                <Button
                  onClick={() => void link()}
                  disabled={busy || !subject || !agent || !target}
                >
                  <Link2 className="size-4" /> 등록
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          {groups.length === 0 ? (
            <EmptyState
              title="등록된 연계가 없습니다"
              hint={`${subjectLabel}과 ${agentLabel}을 선택하여 연계를 등록합니다. 평가는 연계 단위로 수행합니다.`}
            />
          ) : (
            <div className="grid gap-4 xl:grid-cols-[minmax(28rem,1fr)_minmax(0,1.1fr)]">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8">
                    <input
                      type="checkbox"
                      aria-label="모두 선택"
                      checked={shown.length > 0 && chosen.length === shown.length}
                      onChange={(event) =>
                        setChosen(event.target.checked ? shown.map((one) => one.id) : [])
                      }
                    />
                  </TableHead>
                  <TableHead>{subjectLabel}</TableHead>
                  <TableHead>{agentLabel}</TableHead>
                  <TableHead>사용 도구</TableHead>
                  <TableHead>담당 부서</TableHead>
                  <TableHead>소속 부서</TableHead>
                  <TableHead className="text-right">평가</TableHead>
                  <TableHead className="w-36" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {groups.map((group) =>
                  group.rows.map((row, index) => (
                    <TableRow
                      key={row.id}
                      onClick={() => setPicked(row.id)}
                      className={
                        picked === row.id ? 'bg-muted/60 cursor-pointer' : 'cursor-pointer'
                      }
                    >
                      <TableCell onClick={(event) => event.stopPropagation()}>
                        <input
                          type="checkbox"
                          aria-label={`${row.subject_label} · ${row.agent_label} 선택`}
                          checked={chosen.includes(row.id)}
                          onChange={(event) =>
                            setChosen((was) =>
                              event.target.checked
                                ? [...was, row.id]
                                : was.filter((id) => id !== row.id),
                            )
                          }
                        />
                      </TableCell>
                      {index === 0 && (
                        <TableCell rowSpan={group.rows.length} className="align-top font-medium">
                          {group.subject}
                          <span className="text-muted-foreground ml-2 text-xs">
                            {group.rows.length}개
                          </span>
                        </TableCell>
                      )}
                      <TableCell>{row.agent_label}</TableCell>
                      <TableCell className="text-muted-foreground text-xs">
                        {row.agent_tools.filter(Boolean).join(' · ') || '—'}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-xs">
                        {row.agent_dept ?? '—'}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-xs">
                        {row.workspace_name}
                      </TableCell>
                      {/* **어디까지 채웠나.** 다섯 축 중 몇 개를 매겼는지가 다음 할 일이다. */}
                      <TableCell className="text-right text-xs tabular-nums">
                        {row.assessed} / {defs.data?.axes.length ?? 5}
                      </TableCell>
                      <TableCell className="space-x-1 text-right whitespace-nowrap">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(event) => {
                            event.stopPropagation()
                            setEditing(row)
                            setEditTarget(
                              (workspaces.data ?? []).find(
                                (one) => one.name === row.workspace_name,
                              )?.slug ?? '',
                            )
                          }}
                        >
                          <Pencil className="size-4" /> 수정
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(event) => {
                            event.stopPropagation()
                            setUnlinking(row)
                          }}
                        >
                          <Unlink className="size-4" /> 해제
                        </Button>
                      </TableCell>
                    </TableRow>
                  )),
                )}
              </TableBody>
            </Table>

            {/* **오른쪽은 고른 연계의 평가다.** 모달로 띄우면 표와 값을 나란히 못 본다 —
                한 시험의 해석 셋을 견주는 일이 이 화면의 일이다. */}
            <div className="rounded-md border p-4">
              {current && defs.data ? (
                <AssessmentPanel
                  pair={current}
                  defs={defs.data}
                  defectTypes={defectTypes}
                  onSaved={() => pairs.reload()}
                />
              ) : (
                <p className="text-muted-foreground text-sm">
                  왼쪽 표에서 연계를 선택하면 축별 평가를 입력할 수 있습니다.
                </p>
              )}
            </div>
            </div>
          )}
        </>
      )}

      <Dialog open={editing !== null} onOpenChange={(on) => !on && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>연계 수정</DialogTitle>
            <DialogDescription>
              {editing?.subject_label} · {editing?.agent_label}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <span className="text-muted-foreground text-xs">소속 부서</span>
              <Select value={editTarget} onValueChange={setEditTarget}>
                <SelectTrigger>
                  <SelectValue placeholder="부서 선택" />
                </SelectTrigger>
                <SelectContent>
                  {(workspaces.data ?? []).map((one) => (
                    <SelectItem key={one.slug} value={one.slug}>
                      {one.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {/* **대상 · 수단은 못 바꾼다.** 바꾸면 다른 연계이고, 이미 매긴 평가가 엉뚱한
                대상의 평가로 남는다 — 그 손실은 숫자에서만 드러난다. */}
            <p className="text-muted-foreground text-sm">
              {subjectLabel} · {agentLabel} 을 바꾸려면 이 연계를 <b>해제</b>하고 다시
              등록합니다 — 다른 연계이기 때문입니다. 해제하면 이 연계의 평가와 이력도 함께
              삭제됩니다.
            </p>
          </div>
          <DialogFooter>
            <Button
              onClick={() => void moveTo()}
              disabled={busy || !editTarget || editTarget === ''}
            >
              저장
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={bulkUnlinking}
        title={`연계 ${chosen.length}건을 해제합니까?`}
        description="고른 연계와 그 평가 · 변경 이력이 함께 삭제됩니다."
        destructive
        confirmLabel="해제"
        onConfirm={async () => {
          await dtApi.bulkUnlink(chosen)
          setChosen([])
          setBulkUnlinking(false)
          pairs.reload()
        }}
        onClose={() => setBulkUnlinking(false)}
      />

      <ConfirmDialog
        open={unlinking !== null}
        title="연계를 해제하시겠습니까?"
        description={
          unlinking ? (
            <>
              <b>
                {unlinking.subject_label} · {unlinking.agent_label}
              </b>{' '}
              연계를 해제합니다. 해당 연계의 평가와 변경 이력도 함께 삭제됩니다.
            </>
          ) : (
            ''
          )
        }
        destructive
        confirmLabel="해제"
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
