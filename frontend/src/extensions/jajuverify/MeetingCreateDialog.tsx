/**
 * 회의 만들기 — **종류 · 제목 · 일자 · 부서만** 묻는 짧은 창. 만들면 그 한 장으로 바로 간다(표는
 * 거기서 채운다). 부서는 소속이 하나면 묻지 않는다.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { KINDS, jvApi, paths } from '@/extensions/jajuverify/api'
import type { MeetingKind } from '@/extensions/jajuverify/api'
import { OwnerWorkspacePicker } from '@/modules/workspaces/OwnerWorkspacePicker'
import { useAuth } from '@/shared/auth/AuthContext'
import { defaultOwnerWorkspace, isSystemAdmin, ownableMemberships } from '@/shared/auth/roles'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
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
import { Label } from '@/shared/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'

/** 오늘(사용자의 시계) — `YYYY-MM-DD`. */
export function today(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

export function MeetingCreateDialog({ kind, onClose }: { kind: MeetingKind; onClose: () => void }) {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [chosen, setChosen] = useState<MeetingKind>(kind)
  const [title, setTitle] = useState('')
  const [heldOn, setHeldOn] = useState(today())
  // 회의는 구성원이 만든다(기록과 같다) — 부서는 소속 중에서.
  const [workspace, setWorkspace] = useState<string | null>(
    defaultOwnerWorkspace(user, null, 'log'),
  )
  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  const askWorkspace = isSystemAdmin(user) || ownableMemberships(user, 'log').length > 1

  async function create() {
    if (!title.trim() || !workspace) return
    setSaving(true)
    setError(null)
    try {
      const made = await jvApi.create({
        kind: chosen,
        title: title.trim(),
        held_on: heldOn || null,
        workspace_slug: workspace,
      })
      navigate(paths.sheet(made.kind, made.id))
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>새 회의</DialogTitle>
          <DialogDescription>
            만들면 그 회의의 표로 바로 갑니다. 표의 줄 · 사진은 거기서 추가합니다.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            void create()
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="jv-new-kind">종류</Label>
            <Select value={chosen} onValueChange={(value) => setChosen(value as MeetingKind)}>
              <SelectTrigger id="jv-new-kind" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(KINDS) as MeetingKind[]).map((one) => (
                  <SelectItem key={one} value={one}>
                    {KINDS[one]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="jv-new-title">제목</Label>
            <Input
              id="jv-new-title"
              value={title}
              maxLength={200}
              autoFocus
              placeholder="예: 모델 A 1차 심사회"
              onChange={(event) => setTitle(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="jv-new-date">일자</Label>
            <Input
              id="jv-new-date"
              type="date"
              value={heldOn}
              onChange={(event) => setHeldOn(event.target.value)}
              className="w-auto"
            />
          </div>
          {askWorkspace && (
            <div className="space-y-1.5">
              <Label htmlFor="jv-new-workspace">부서</Label>
              <OwnerWorkspacePicker
                id="jv-new-workspace"
                usage="log"
                value={workspace}
                onChange={setWorkspace}
              />
            </div>
          )}
          {!workspace && (
            <p className="text-destructive text-sm">
              소속 부서가 없어 회의를 만들 수 없습니다 — 부서 관리자에게 소속을 요청하세요.
            </p>
          )}
          <ErrorNotice error={error} />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={saving}>
              취소
            </Button>
            <Button type="submit" disabled={saving || !title.trim() || !workspace}>
              생성
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
