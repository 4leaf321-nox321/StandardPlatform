/**
 * 객체 생성.
 *
 * **`window.prompt` 를 쓰지 않는다** — 서버가 거절했을 때 그 말을 보여 줄 자리가
 * 없어서다. 여기서는 오류가 폼 안에 그대로 선다.
 */

import { useState } from 'react'

import type { ObjectType, PropertyDef } from '@/modules/ontology/api'
import { objectApi } from '@/modules/objects/api'
import { PropertyFields } from '@/modules/objects/PropertyFields'
import type { PropertyValues } from '@/modules/objects/PropertyFields'
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
import { Textarea } from '@/shared/components/ui/textarea'
import { OwnerWorkspacePicker } from '@/modules/workspaces/OwnerWorkspacePicker'
import { useAuth } from '@/shared/auth/AuthContext'
import { defaultOwnerWorkspace, isSystemAdmin } from '@/shared/auth/roles'

interface Props {
  type: ObjectType
  defs: PropertyDef[]
  onClose: () => void
  onCreated: () => void
}

export function ObjectCreateDialog({ type, defs, onClose, onCreated }: Props) {
  const { user } = useAuth()
  const [key, setKey] = useState('')
  const [label, setLabel] = useState('')
  const [description, setDescription] = useState('')
  const [values, setValues] = useState<PropertyValues>({})
  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)

  /**
   * 어느 부서 것으로 만들 것인가 — **내가 관리자인 부서 중에서 고른다.**
   *
   * 전역은 여러 부서가 함께 쓰므로 시스템 관리자만 만들고, 기본값으로 두면 아무나 전역을
   * 만들려다 403 을 본다. 대표 소속을 고정으로 보내던 때는 B 의 관리자인데 대표 소속 A 에서는
   * 멤버인 사람이 늘 403 을 봤다(2026-10-08) — 기본은 대표 소속이 관리 부서일 때만 그것이다.
   */
  const [owner, setOwner] = useState<string | null>(() => defaultOwnerWorkspace(user))
  /** 부서 없이(전역) 보낼 수 있나 — 서버가 시스템 관리자에게만 받아 준다. */
  const canGlobal = isSystemAdmin(user)

  async function submit() {
    setError(null)
    setSaving(true)
    try {
      await objectApi.create(type.slug, {
        key: type.key_policy === 'none' ? null : key || null,
        label,
        description,
        properties: values,
        workspace_slug: owner,
      })
      onCreated()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{type.label} 생성</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {error && <ErrorNotice error={error} />}

          {type.key_policy !== 'none' && (
            <div className="space-y-1.5">
              <Label htmlFor="object-key">
                식별자
                {type.key_policy === 'required' && <span className="text-destructive ml-1">*</span>}
              </Label>
              <Input
                id="object-key"
                value={key}
                onChange={(event) => setKey(event.target.value)}
                placeholder={type.key_scope === 'global' ? '전사에서 하나' : '이 부서에서 하나'}
              />
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="object-label">
              이름<span className="text-destructive ml-1">*</span>
            </Label>
            <Input
              id="object-label"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="object-description">설명</Label>
            <Textarea
              id="object-description"
              value={description}
              rows={2}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>

          <PropertyFields
            defs={defs}
            values={values}
            onChange={setValues}
            disabled={saving}
            view={type.form_view}
          />

          <div className="space-y-1.5">
            <Label htmlFor="object-owner">소유 부서</Label>
            <OwnerWorkspacePicker
              id="object-owner"
              className="w-full"
              value={owner}
              onChange={setOwner}
            />
            <p className="text-muted-foreground text-xs">
              {owner
                ? '이 부서의 관리자가 수정합니다. 관리하는 부서만 선택할 수 있습니다.'
                : canGlobal
                  ? '부서를 선택하지 않으면 전역으로 만듭니다 — 시스템 관리자만 됩니다.'
                  : '관리하는 부서가 없으면 만들 수 없습니다 — 부서 관리자에게 요청하세요.'}
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            취소
          </Button>
          <Button onClick={submit} disabled={saving || !label.trim() || (!owner && !canGlobal)}>
            생성
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
