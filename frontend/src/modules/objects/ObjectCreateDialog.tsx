/**
 * 인스턴스 만들기.
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
import { useAuth } from '@/shared/auth/AuthContext'

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
   * 어느 부서 것으로 만들 것인가.
   *
   * **기본은 내 부서다.** 전역은 여러 부서가 함께 쓰므로 시스템 관리자만 만들고,
   * 기본값으로 두면 아무나 전역을 만들려다 403 을 본다.
   */
  const myWorkspace = user?.home_workspace_slug ?? user?.memberships[0]?.slug ?? null

  async function submit() {
    setError(null)
    setSaving(true)
    try {
      await objectApi.create(type.slug, {
        key: type.key_policy === 'none' ? null : key || null,
        label,
        description,
        properties: values,
        workspace_slug: myWorkspace,
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
          <DialogTitle>{type.label} 만들기</DialogTitle>
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

          <PropertyFields defs={defs} values={values} onChange={setValues} disabled={saving} />

          <p className="text-muted-foreground text-xs">
            {myWorkspace
              ? `${myWorkspace} 부서의 것으로 만듭니다.`
              : '소속 부서가 없어 전역으로 만듭니다 — 시스템 관리자만 됩니다.'}
          </p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            취소
          </Button>
          <Button onClick={submit} disabled={saving || !label.trim()}>
            만들기
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
