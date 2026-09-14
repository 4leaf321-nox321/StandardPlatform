/**
 * 계정 관리.
 *
 * **승인 대기가 맨 위에 온다.** 관리자가 할 일이 목록 맨 위에 있어야 한다 —
 * 이름순으로 두면 대기 하나를 찾으려고 세 쪽을 넘겨야 하고, 그러면 며칠씩 방치된다.
 */

import { useState } from 'react'
import { Plus } from 'lucide-react'

import { accountApi } from '@/modules/accounts/api'
import { workspaceApi } from '@/modules/workspaces/api'
import { ApiError } from '@/shared/api/client'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'
import { shownDate } from '@/shared/lib/datetime'

export default function AccountsAdminPage() {
  const list = useResource(() => accountApi.list(), [])
  const summary = useResource(() => accountApi.summary(), [])
  const workspaces = useResource(() => workspaceApi.options(), [])
  const [error, setError] = useState<ApiError | Error | null>(null)
  // 임시 비밀번호는 **한 번만** 나온다. 화면이 붙들고 있어야 관리자가 옮겨 적는다.
  const [issued, setIssued] = useState<{ email: string; password: string } | null>(null)
  const [rejecting, setRejecting] = useState<{ id: string; email: string } | null>(null)
  const [note, setNote] = useState('')

  const [newEmail, setNewEmail] = useState('')
  const [newName, setNewName] = useState('')
  const [newWorkspace, setNewWorkspace] = useState('')

  async function act(run: () => Promise<unknown>) {
    setError(null)
    try {
      await run()
      list.reload()
      summary.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  const onlyOneAdmin = (summary.data?.active_system_admins ?? 0) <= 1

  return (
    <div className="space-y-6">
      <PageHeader
        title="계정"
        description="가입 승인과 권한을 다룹니다. 부서 멤버 관리는 부서 화면에서 합니다."
      />

      {/* **관리자가 하나뿐이면 말한다.** 그 사람이 잠기는 순간 복구 경로가 서버
          콘솔뿐이고, 그때는 화면에서 할 수 있는 것이 하나도 없다. */}
      {summary.data && onlyOneAdmin && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          활성 시스템 관리자가 1명입니다. 그 계정이 잠기면 서버 콘솔로만 복구할 수
          있습니다 — 한 명 더 지정해 두세요.
        </div>
      )}

      {issued && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          <p className="font-medium">
            {issued.email} 의 임시 비밀번호입니다. 지금 전달하세요 — 다시 볼 수 없습니다.
          </p>
          <p className="mt-1 font-mono text-xs">{issued.password}</p>
        </div>
      )}

      <form
        className="flex flex-wrap items-end gap-2 rounded-md border p-4"
        onSubmit={(event) => {
          event.preventDefault()
          act(async () => {
            const body = await accountApi.create({
              email: newEmail,
              display_name: newName,
              workspace_slug: newWorkspace,
            })
            setIssued({ email: newEmail, password: body.temporary_password })
            setNewEmail('')
            setNewName('')
          })
        }}
      >
        <div className="space-y-2">
          <Label htmlFor="new-email">아이디</Label>
          <Input
            id="new-email"
            value={newEmail}
            onChange={(event) => setNewEmail(event.target.value)}
            placeholder="hong"
            required
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="new-name">이름</Label>
          <Input
            id="new-name"
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            placeholder="홍길동"
            required
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="new-workspace">부서</Label>
          {/* **Select 가 아니다.** 조직도는 금방 스물을 넘고, 통째로 펼쳐 놓고
              눈으로 찾으라고 하면 못 찾은 사람은 없다고 결론 내린다.
              경로를 함께 보여 준다 — 같은 이름의 팀이 본부마다 있을 수 있다. */}
          <SearchablePicker
            id="new-workspace"
            className="w-64"
            value={newWorkspace || null}
            onChange={setNewWorkspace}
            placeholder="부서를 고르세요"
            searchPlaceholder="부서 이름이나 주소로 검색"
            emptyText="그런 부서가 없습니다"
            options={(workspaces.data ?? []).map((one) => ({
              value: one.slug,
              label: one.name,
              hint: one.path,
              keywords: one.slug,
            }))}
          />
        </div>
        <Button type="submit" disabled={!newWorkspace}>
          <Plus className="size-4" />
          계정 생성
        </Button>
      </form>

      <ErrorNotice error={error ?? list.error} />

      {list.data && list.data.length === 0 ? (
        <EmptyState
          title="계정이 없습니다"
          hint="설치 스크립트로 만든 관리자 계정만 있는 상태일 수 있습니다."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>아이디</TableHead>
              <TableHead>이름</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>소속</TableHead>
              <TableHead>신청/승인</TableHead>
              <TableHead className="text-right">처리</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(list.data ?? []).map((one) => (
              <TableRow key={one.id}>
                <TableCell className="font-mono text-xs">{one.email}</TableCell>
                <TableCell>
                  {one.display_name}
                  {one.is_system_admin && (
                    <span className="text-muted-foreground ml-2 text-xs">시스템 관리자</span>
                  )}
                </TableCell>
                <TableCell>
                  <StatusBadge kind="account" value={one.status} />
                </TableCell>
                <TableCell className="text-sm">
                  {one.memberships.join(', ') || one.requested_workspace_slug || '—'}
                </TableCell>
                <TableCell className="text-sm">
                  {shownDate(one.decided_at ?? one.created_at)}
                </TableCell>
                <TableCell className="space-x-1 text-right">
                  {one.status === 'pending' ? (
                    <>
                      <Button size="sm" onClick={() => act(() => accountApi.approve(one.id, {}))}>
                        승인
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setNote('')
                          setRejecting({ id: one.id, email: one.email })
                        }}
                      >
                        거절
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          act(async () => {
                            const body = await accountApi.resetPassword(one.id)
                            setIssued({ email: one.email, password: body.temporary_password })
                          })
                        }
                      >
                        비밀번호 초기화
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          act(() => accountApi.setSystemAdmin(one.id, !one.is_system_admin))
                        }
                      >
                        {one.is_system_admin ? '관리자 해제' : '관리자 지정'}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          act(() =>
                            one.status === 'active'
                              ? accountApi.suspend(one.id)
                              : accountApi.activate(one.id),
                          )
                        }
                      >
                        {one.status === 'active' ? '정지' : '활성화'}
                      </Button>
                    </>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {/* **사유를 반드시 받는다.** 메일이 없어 통보가 앱 안에서만 되므로, 안 적으면
          신청한 사람은 이유를 영영 모른다. window.prompt 를 쓰지 않는 이유는
          그것이 오류를 보여 줄 자리가 없기 때문이다 — 서버가 거절하면 그 말이
          어디에도 안 뜬다. */}
      <ConfirmDialog
        open={rejecting !== null}
        title="가입 거절"
        description={
          <div className="space-y-2">
            <p>{rejecting?.email} 의 신청을 거절합니다.</p>
            <p className="text-muted-foreground">
              메일 통보가 없으므로 여기 적은 사유가 신청자에게 보이는 전부입니다.
            </p>
            <Input
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="거절 사유"
              autoFocus
            />
          </div>
        }
        confirmLabel="거절"
        destructive
        onConfirm={async () => {
          if (!rejecting) return
          await accountApi.reject(rejecting.id, note)
          list.reload()
          summary.reload()
        }}
        onClose={() => setRejecting(null)}
      />
    </div>
  )
}
