/**
 * 부서 정보 — 전사 조직도.
 *
 * **트리 순서는 서버가 정한다.** 화면이 평면 목록을 받아 스스로 세우면, 부서
 * 선택기와 이 화면의 순서가 달라진다 — 같은 목록이 화면마다 다르게 보인다.
 *
 * **부서를 지우는 것은 예외다.** 기본은 보관(is_active=false)이고, 삭제는 잘못
 * 만든 부서처럼 자료가 아예 없는 경우를 위한 것이다.
 */

import { useState } from 'react'
import { ChevronDown, ChevronUp, Download, Plus, Trash2 } from 'lucide-react'

import { workspaceApi } from '@/modules/workspaces/api'
import type { Workspace, WorkspaceReference } from '@/modules/workspaces/api'
import { ApiError } from '@/shared/api/client'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'

export default function WorkspacesAdminPage() {
  const list = useResource(() => workspaceApi.list(true), [])
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')
  const [parent, setParent] = useState('')
  const [error, setError] = useState<ApiError | Error | null>(null)

  // 삭제 확인 — **누르기 전에 무엇이 딸려 있는지 보여 준다.**
  const [deleting, setDeleting] = useState<Workspace | null>(null)
  const [references, setReferences] = useState<WorkspaceReference[] | null>(null)

  async function act(run: () => Promise<unknown>) {
    setError(null)
    try {
      await run()
      list.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  async function askDelete(workspace: Workspace) {
    setError(null)
    setReferences(null)
    setDeleting(workspace)
    try {
      setReferences(await workspaceApi.references(workspace.slug))
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="부서 정보"
        description="조직도를 만들고 고칩니다. 부서를 옮겨도 자료는 하나도 움직이지 않습니다."
        actions={
          <Button
            variant="outline"
            onClick={() =>
              act(async () => {
                await workspaceApi.exportCsv()
              })
            }
          >
            <Download className="size-4" />
            CSV 내보내기
          </Button>
        }
      />

      <form
        className="flex flex-wrap items-end gap-2 rounded-md border p-4"
        onSubmit={(event) => {
          event.preventDefault()
          act(async () => {
            await workspaceApi.create({
              slug,
              name,
              parent_slug: parent || null,
            })
            setSlug('')
            setName('')
            setParent('')
          })
        }}
      >
        <div className="space-y-2">
          <Label htmlFor="slug">주소 이름</Label>
          {/* URL 에 들어가므로 소문자·숫자·하이픈만. 한글 이름은 옆 칸이 갖는다. */}
          <Input
            id="slug"
            value={slug}
            onChange={(event) => setSlug(event.target.value)}
            placeholder="material-lab"
            pattern="[a-z0-9][a-z0-9-]{1,49}"
            required
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="name">부서 이름</Label>
          <Input
            id="name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="재료시험팀"
            required
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="parent">상위 부서 (선택)</Label>
          <Input
            id="parent"
            value={parent}
            onChange={(event) => setParent(event.target.value)}
            placeholder="비우면 최상위"
          />
        </div>
        <Button type="submit">
          <Plus className="size-4" />
          부서 추가
        </Button>
      </form>

      <ErrorNotice error={error ?? list.error} />

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>부서</TableHead>
            <TableHead>주소</TableHead>
            <TableHead className="text-right">멤버</TableHead>
            <TableHead>공개</TableHead>
            <TableHead>상태</TableHead>
            <TableHead className="text-right">순서</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {(list.data ?? []).map((one) => (
            <TableRow key={one.id}>
              <TableCell>
                {/* 깊이만큼 들여쓴다 — 경로 문자열을 그대로 쓰면 줄이 길어져 표가 접힌다. */}
                <span style={{ paddingLeft: `${one.depth * 16}px` }}>{one.name}</span>
              </TableCell>
              <TableCell className="font-mono text-xs">{one.slug}</TableCell>
              <TableCell className="text-right">{one.member_count}</TableCell>
              <TableCell>
                {/* **가리는 쪽이 예외다.** 기본은 전원 공개 — 사내 플랫폼의 물음이
                    대개 부서를 가로지르기 때문이다. */}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    act(() => workspaceApi.update(one.slug, { restricted: !one.restricted }))
                  }
                >
                  {one.restricted ? '멤버만' : '전원'}
                </Button>
              </TableCell>
              <TableCell>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    act(() => workspaceApi.update(one.slug, { is_active: !one.is_active }))
                  }
                >
                  <StatusBadge kind="workspace" value={one.is_active ? 'active' : 'archived'} />
                </Button>
              </TableCell>
              <TableCell className="text-right">
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="위로"
                  onClick={() => act(() => workspaceApi.reorder(one.slug, 'up'))}
                >
                  <ChevronUp className="size-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="아래로"
                  onClick={() => act(() => workspaceApi.reorder(one.slug, 'down'))}
                >
                  <ChevronDown className="size-4" />
                </Button>
              </TableCell>
              <TableCell className="text-right">
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="삭제"
                  onClick={() => askDelete(one)}
                >
                  <Trash2 className="size-4" />
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <ConfirmDialog
        open={deleting !== null}
        title={`${deleting?.name ?? ''} 부서 삭제`}
        description={
          <div className="space-y-2">
            {/* **무엇이 사라지는지 적는다.** "정말 삭제하시겠습니까" 만 묻는 창은
                아무도 안 읽고 예를 누른다 — 읽을 것이 없기 때문이다. */}
            {references === null ? (
              <p className="text-muted-foreground">무엇이 걸려 있는지 확인 중…</p>
            ) : references.length === 0 ? (
              <p>이 부서를 가리키는 것이 없습니다. 지워도 잃는 자료가 없습니다.</p>
            ) : (
              <>
                <p>이 부서를 가리키는 것들입니다:</p>
                <ul className="space-y-1">
                  {references.map((one) => (
                    <li key={one.table}>
                      {one.label} {one.count}건
                      {one.blocks_delete && (
                        <span className="text-destructive ml-1">— 먼저 정리해야 합니다</span>
                      )}
                    </li>
                  ))}
                </ul>
              </>
            )}
            <p className="text-muted-foreground">
              지우는 대신 <strong>보관</strong>으로 두면 자료는 남고 새 활동만 막힙니다.
            </p>
          </div>
        }
        confirmLabel="삭제"
        destructive
        onConfirm={async () => {
          if (!deleting) return
          await workspaceApi.remove(deleting.slug)
          list.reload()
        }}
        onClose={() => setDeleting(null)}
      />
    </div>
  )
}
