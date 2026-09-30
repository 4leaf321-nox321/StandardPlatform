/**
 * 사이드바 묶음 하나를 고친다 — **행을 누르면 여기가 뜬다.**
 *
 * 타입과 같은 손놀림이어야 한다. 한쪽만 행을 눌러 고쳐지면 다른 쪽에서도
 * 눌러 보게 되고, 아무 일도 안 일어나는 것은 고장으로 읽힌다.
 */

import { useState } from 'react'

import { ontologyApi } from '@/modules/ontology/api'
import type { NavGroupRow } from '@/modules/ontology/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
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

/** 「맨 위」 를 고르는 값 — 빈 문자열은 Select 가 값 없음으로 읽는다. */
const TOP = '__top__'

interface Props {
  group: NavGroupRow
  /** 이 묶음에 걸린 타입 이름들. **삭제 전에 무엇이 걸렸는지 말한다.** */
  attached: string[]
  /** 상위로 고를 수 있는 묶음들 — **두 단계까지라** 이미 상위가 있는 것과 내 자식은 빠진다. */
  groups?: NavGroupRow[]
  onClose: () => void
  onChanged: () => void
}

export function GroupEditDialog({ group, attached, groups = [], onClose, onChanged }: Props) {
  const [label, setLabel] = useState(group.label)
  const [audience, setAudience] = useState(group.audience)
  const [parent, setParent] = useState(group.parent_slug || TOP)
  const [sortOrder, setSortOrder] = useState(String(group.sort_order))
  const [isActive, setIsActive] = useState(group.is_active)

  // 내 아래에 묶음이 있으면 나는 남의 아래로 못 간다(3단계가 된다) — 그때는 고르지 못하게 한다.
  const hasChildren = groups.some((one) => one.parent_slug === group.slug)
  const candidates = groups.filter(
    (one) => one.slug !== group.slug && !one.parent_slug && !hasChildren,
  )

  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  const [removing, setRemoving] = useState(false)

  async function save() {
    setError(null)
    setSaving(true)
    try {
      await ontologyApi.updateGroup(group.slug, {
        label,
        audience,
        // 빈 문자열이 「맨 위로」 다 — null 은 서버에서 「안 보냄」 과 구별되지 않는다.
        parent_slug: parent === TOP ? '' : parent,
        sort_order: Number(sortOrder) || 0,
        is_active: isActive,
      })
      onChanged()
      onClose()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <Dialog open onOpenChange={(open) => !open && onClose()}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{group.label} 수정</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            {error && <ErrorNotice error={error} />}

            <div className="space-y-1.5">
              <Label>slug</Label>
              <Input value={group.slug} readOnly disabled className="font-mono" />
              <p className="text-muted-foreground text-xs">
                <b>바꿀 수 없습니다.</b> 사람과 문서가 이 이름으로 가리킵니다.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="group-edit-label">이름</Label>
              <Input
                id="group-edit-label"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="group-edit-audience">보이는 대상</Label>
                <Select value={audience} onValueChange={setAudience}>
                  <SelectTrigger id="group-edit-audience">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="everyone">모두</SelectItem>
                    <SelectItem value="manager">부서 관리자</SelectItem>
                    <SelectItem value="system_admin">시스템 관리자</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="group-edit-sort">순서</Label>
                <Input
                  id="group-edit-sort"
                  type="number"
                  value={sortOrder}
                  onChange={(event) => setSortOrder(event.target.value)}
                />
              </div>
            </div>

            <p className="text-muted-foreground text-xs">
              보이는 대상은 <b>표시일 뿐 권한이 아닙니다.</b> 권한은 서버가 판정합니다.
            </p>

            <div className="space-y-1.5">
              <Label htmlFor="group-edit-parent">상위 묶음</Label>
              <Select value={parent} onValueChange={setParent} disabled={hasChildren}>
                <SelectTrigger id="group-edit-parent">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={TOP}>(맨 위)</SelectItem>
                  {candidates.map((one) => (
                    <SelectItem key={one.slug} value={one.slug}>
                      {one.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">
                {hasChildren ? (
                  <>
                    이 묶음 <b>아래에 이미 묶음이 있어</b> 상위로 옮길 수 없습니다 — 묶음은 두
                    단계까지입니다(상위 묶음 › 묶음 › 타입).
                  </>
                ) : (
                  <>
                    사이드바를 <b>두 단계</b>로 세웁니다. 이미 상위가 있는 묶음은 후보에서
                    빠집니다 — 세 단계는 만들지 않습니다.
                  </>
                )}
              </p>
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4"
                checked={isActive}
                onChange={(event) => setIsActive(event.target.checked)}
              />
              사용함
              <span className="text-muted-foreground text-xs">
                끄면 사이드바에서 이 묶음이 통째로 제외됩니다.
              </span>
            </label>
          </div>

          <DialogFooter className="justify-between sm:justify-between">
            <Button variant="ghost" onClick={() => setRemoving(true)} disabled={saving}>
              삭제
            </Button>
            <div className="flex gap-2">
              <Button variant="outline" onClick={onClose} disabled={saving}>
                취소
              </Button>
              <Button onClick={save} disabled={saving || !label.trim()}>
                저장
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {removing && (
        <ConfirmDialog
          open
          destructive
          title={`${group.label} 묶음을 삭제합니다`}
          description={
            attached.length > 0 ? (
              <>
                <b>{attached.join(', ')}</b> 이(가) 걸려 있어 <b>지울 수 없습니다.</b> 그 타입들의
                묶음을 먼저 바꾸세요 — 안 그러면 사이드바에서 조용히 사라집니다.
              </>
            ) : (
              <>소속 타입이 없어 삭제할 수 있습니다.</>
            )
          }
          confirmLabel="삭제"
          onConfirm={async () => {
            await ontologyApi.removeGroup(group.slug)
            onChanged()
            onClose()
          }}
          onClose={() => setRemoving(false)}
        />
      )}
    </>
  )
}
