/**
 * 디지털 트윈 › 인력 — **역량을 떠받치는 조건 가운데 사람.**
 *
 * 축은 전부 결과를 잰다. 그 결과를 만든 조건이 옆에 서야 「전담 0.5 FTE 라 여기까지」 가
 * 자료로 말해지고, 낮은 수준이 변명이 아니라 설명이 된다.
 *
 * ⚠️ **투입률을 입력하지 않는다.** 한 사람은 1.0 이고 담당 해석이 n 개면 각 1/n 이다 —
 *    퍼센트를 사람이 적으면 정의가 흔들리고 합이 사람 수를 넘는다. 셈은 서버가 한다.
 * ⚠️ **표에 서는 것은 가명이다**(담당 A · B). 실명은 그 부서를 고칠 수 있는 사람에게만
 *    보인다 — 사람을 세는 자리이지 사람을 평가하는 자리가 아니다.
 */

import { Pencil, Plus, Trash2, Users } from 'lucide-react'
import { useState } from 'react'

import { objectApi } from '@/modules/objects/api'
import { workspaceApi } from '@/modules/workspaces/api'
import { useAuth } from '@/shared/auth/AuthContext'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
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

import { dtApi, type Staff } from './api'

export default function StaffPage() {
  const { user } = useAuth()
  const home = user?.home_workspace_slug ?? user?.memberships[0]?.slug ?? ''
  const setup = useResource(() => dtApi.setupStatus(), [])
  const rows = useResource(() => dtApi.staff(), [])
  const summary = useResource(() => dtApi.staffSummary(), [])
  const workspaces = useResource(() => workspaceApi.list(true), [])
  const agentSlug = setup.data?.agent_type_slug ?? null
  const agents = useResource(
    () => (agentSlug ? objectApi.list(agentSlug, { limit: 200 }) : Promise.resolve(null)),
    [agentSlug],
  )

  const [editing, setEditing] = useState<Staff | null>(null)
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<Staff | null>(null)
  const [failed, setFailed] = useState<Error | null>(null)

  function reload() {
    rows.reload()
    summary.reload()
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="인력"
        description="담당 인원과 환산 인원(FTE)입니다. 투입률은 입력하지 않습니다 — 1인은 1.0 이며 담당 해석이 n 개면 각 1/n 으로 산정됩니다."
      />

      <ErrorNotice error={failed ?? rows.error ?? summary.error} />

      <section className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-md border p-3">
          <p className="text-muted-foreground text-xs">인원</p>
          <p className="text-2xl font-semibold tabular-nums">{summary.data?.head_count ?? 0}</p>
        </div>
        <div className="rounded-md border p-3">
          <p className="text-muted-foreground text-xs">FTE 합</p>
          <p className="text-2xl font-semibold tabular-nums">{summary.data?.fte ?? 0}</p>
          <p className="text-muted-foreground text-xs">인원 수를 넘지 않습니다</p>
        </div>
        <div className="rounded-md border p-3">
          <p className="text-muted-foreground text-xs">역량 분야로만 센 인원</p>
          <p className="text-2xl font-semibold tabular-nums">
            {(summary.data?.by_kind ?? []).reduce((sum, one) => sum + one.people, 0)}
          </p>
          <p className="text-muted-foreground text-xs">담당 해석이 없는 인력 — FTE 로 세지 않습니다</p>
        </div>
      </section>

      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold">인력 목록</h2>
        <Button size="sm" className="ml-auto" onClick={() => setAdding(true)}>
          <Plus className="size-4" /> 인력 등록
        </Button>
      </div>

      {(rows.data ?? []).length === 0 ? (
        <EmptyState
          title="등록된 인력이 없습니다"
          hint="담당 해석을 고르면 FTE 가 1/n 으로 산정됩니다. 실명은 입력하되 표에는 가명으로 표시됩니다."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>담당</TableHead>
              <TableHead>담당 해석</TableHead>
              <TableHead>역량 분야</TableHead>
              <TableHead>부서</TableHead>
              <TableHead className="text-right">몫</TableHead>
              <TableHead className="text-right">FTE</TableHead>
              <TableHead className="w-28" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(rows.data ?? []).map((one) => (
              <TableRow key={one.id}>
                <TableCell>
                  <span className="font-medium">{one.alias}</span>
                  {/* 실명은 고칠 수 있는 사람에게만 온다 — 없으면 칸도 비어 있다. */}
                  {one.name && (
                    <span className="text-muted-foreground ml-2 text-xs">{one.name}</span>
                  )}
                  {one.outside && (
                    <span className="text-muted-foreground ml-2 text-xs">· 조사 밖 업무 있음</span>
                  )}
                </TableCell>
                <TableCell className="text-xs">
                  {one.agents.map((agent) => agent.label).join(' · ') || '—'}
                </TableCell>
                <TableCell className="text-muted-foreground text-xs">
                  {one.skill_kinds.join(' · ') || '—'}
                </TableCell>
                <TableCell className="text-muted-foreground text-xs">
                  {one.workspace_name}
                </TableCell>
                <TableCell className="text-right text-xs tabular-nums">{one.share}</TableCell>
                <TableCell className="text-right tabular-nums">{one.fte}</TableCell>
                <TableCell className="space-x-1 text-right whitespace-nowrap">
                  <Button variant="ghost" size="sm" onClick={() => setEditing(one)}>
                    <Pencil className="size-4" />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setRemoving(one)}>
                    <Trash2 className="size-4" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {/* 해석마다 뒤에 몇 명이 있나 — 「이 해석을 누가 들고 있나」 의 답이다. */}
      {(summary.data?.by_agent ?? []).length > 0 && (
        <section className="space-y-2">
          <h2 className="flex items-center gap-1 text-base font-semibold">
            <Users className="size-4" /> 해석별 FTE
          </h2>
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {(summary.data?.by_agent ?? []).map((one) => (
              <li key={one.id} className="flex items-baseline gap-2 rounded-md border p-2 text-sm">
                <span className="min-w-0 flex-1 truncate">{one.label}</span>
                <span className="tabular-nums">{one.fte}</span>
                <span className="text-muted-foreground text-xs tabular-nums">{one.people}명</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <StaffDialog
        open={adding || editing !== null}
        current={editing}
        home={home}
        workspaces={(workspaces.data ?? []).map((one) => ({ slug: one.slug, name: one.name }))}
        agents={(agents.data?.items ?? []).map((one) => ({ id: one.id, label: one.label }))}
        onClose={() => {
          setAdding(false)
          setEditing(null)
        }}
        onSaved={() => {
          setAdding(false)
          setEditing(null)
          reload()
        }}
        onFailed={setFailed}
      />

      <ConfirmDialog
        open={removing !== null}
        title="인력을 삭제합니까?"
        description={`${removing?.alias ?? ''} 줄이 사라지고 담당 해석의 FTE 가 다시 셈됩니다.`}
        destructive
        confirmLabel="삭제"
        onConfirm={async () => {
          if (!removing) return
          await dtApi.staffDelete(removing.id)
          setRemoving(null)
          reload()
        }}
        onClose={() => setRemoving(null)}
      />
    </div>
  )
}

function StaffDialog({
  open,
  current,
  home,
  workspaces,
  agents,
  onClose,
  onSaved,
  onFailed,
}: {
  open: boolean
  current: Staff | null
  home: string
  workspaces: { slug: string; name: string }[]
  agents: { id: string; label: string }[]
  onClose: () => void
  onSaved: () => void
  onFailed: (error: Error) => void
}) {
  const [name, setName] = useState(current?.name ?? '')
  const [target, setTarget] = useState(
    current ? (workspaces.find((one) => one.name === current.workspace_name)?.slug ?? home) : home,
  )
  const [picked, setPicked] = useState<string[]>(current?.agents.map((one) => one.id) ?? [])
  const [kinds, setKinds] = useState(current?.skill_kinds.join(', ') ?? '')
  const [outside, setOutside] = useState(current?.outside ?? false)
  const [note, setNote] = useState(current?.note ?? '')
  const [busy, setBusy] = useState(false)

  // 창이 열릴 때마다 고른 줄의 값으로 채운다 — 같은 창을 다시 쓰므로.
  const key = `${open}-${current?.id ?? 'new'}`
  const [seen, setSeen] = useState(key)
  if (seen !== key) {
    setSeen(key)
    setName(current?.name ?? '')
    setTarget(
      current ? (workspaces.find((one) => one.name === current.workspace_name)?.slug ?? home) : home,
    )
    setPicked(current?.agents.map((one) => one.id) ?? [])
    setKinds(current?.skill_kinds.join(', ') ?? '')
    setOutside(current?.outside ?? false)
    setNote(current?.note ?? '')
  }

  const parts = picked.length + (outside ? 1 : 0)
  const share = picked.length === 0 ? 0 : Math.round((1 / parts) * 10000) / 10000

  async function save() {
    setBusy(true)
    try {
      const body = {
        workspace_slug: target,
        name,
        agents: picked,
        skill_kinds: kinds
          .split(',')
          .map((one) => one.trim())
          .filter(Boolean),
        outside,
        note,
      }
      if (current) await dtApi.staffUpdate(current.id, body)
      else await dtApi.staffCreate(body)
      onSaved()
    } catch (caught) {
      onFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(on) => !on && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{current ? '인력 수정' : '인력 등록'}</DialogTitle>
          <DialogDescription>
            투입률은 입력하지 않습니다 — 담당 해석 수로 몫이 갈립니다.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <span className="text-muted-foreground text-xs">이름 (화면에는 가명으로 표시)</span>
              <Input value={name} onChange={(event) => setName(event.target.value)} />
            </div>
            <div className="space-y-1">
              <span className="text-muted-foreground text-xs">부서</span>
              <Select value={target} onValueChange={setTarget}>
                <SelectTrigger>
                  <SelectValue placeholder="부서 선택" />
                </SelectTrigger>
                <SelectContent>
                  {workspaces.map((one) => (
                    <SelectItem key={one.slug} value={one.slug}>
                      {one.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1">
            <span className="text-muted-foreground text-xs">
              담당 해석 — 고른 수만큼 몫이 갈립니다
            </span>
            <ul className="max-h-48 space-y-1 overflow-y-auto rounded-md border p-2">
              {agents.length === 0 && (
                <li className="text-muted-foreground p-2 text-sm">
                  시뮬레이션 해석이 없습니다 — 해당 타입 화면에서 먼저 등록합니다.
                </li>
              )}
              {agents.map((one) => (
                <li key={one.id}>
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={picked.includes(one.id)}
                      onChange={(event) =>
                        setPicked((was) =>
                          event.target.checked
                            ? [...was, one.id]
                            : was.filter((id) => id !== one.id),
                        )
                      }
                    />
                    {one.label}
                  </label>
                </li>
              ))}
            </ul>
          </div>

          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={outside}
              onChange={(event) => setOutside(event.target.checked)}
            />
            <span>
              이 조사 밖 업무가 있습니다
              <span className="text-muted-foreground ml-1 text-xs">
                몫을 담당 수 + 1 로 나눕니다 — 없는 일까지 이 조사에 넣지 않습니다.
              </span>
            </span>
          </label>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <span className="text-muted-foreground text-xs">
                역량 분야 (담당 해석이 없을 때, 쉼표로)
              </span>
              <Input value={kinds} onChange={(event) => setKinds(event.target.value)} />
            </div>
            <div className="space-y-1">
              <span className="text-muted-foreground text-xs">메모</span>
              <Input value={note} onChange={(event) => setNote(event.target.value)} />
            </div>
          </div>

          <p className="text-muted-foreground text-sm">
            담당 하나에 주는 몫: <b className="text-foreground tabular-nums">{share}</b>
            {picked.length > 0 && ` (${picked.length}담당${outside ? ' + 조사 밖 업무' : ''})`}
          </p>
        </div>

        <DialogFooter>
          <Button onClick={() => void save()} disabled={busy || !name.trim() || !target}>
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
