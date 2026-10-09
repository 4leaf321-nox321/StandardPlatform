/**
 * 객체 생성.
 *
 * **`window.prompt` 를 쓰지 않는다** — 서버가 거절했을 때 그 말을 보여 줄 자리가
 * 없어서다. 여기서는 오류가 폼 안에 그대로 선다.
 *
 * ## 파일 칸 — 만든 뒤에 올린다(2026-10-08)
 *
 * 첨부는 객체 id 에 매달린다. 그래서 파일 칸에서 고른 파일은 들고 있다가, 저장할 때 **객체를
 * 만든 뒤** 상세 화면과 같은 업로드 API 로 한 장씩 올린다(임시 표를 따로 두지 않는다 — 업로드
 * 길이 둘이 되면 검사도 둘이 된다). 객체는 만들어졌는데 파일이 실패하면 **창을 닫지 않고
 * 그 사실을 적는다** — 객체는 남고, 상세에서 다시 올리라고 안내한다. 거절되거나 끊긴 업로드의
 * 파일은 아무도 안 가리키고, 고아 정리(`files/gc.py`)가 하루 뒤에 지운다.
 */

import { useState } from 'react'

import { attachmentApi, ATTACHMENT_MAX_BYTES } from '@/modules/files/api'
import type { ObjectType, PropertyDef } from '@/modules/ontology/api'
import { objectApi } from '@/modules/objects/api'
import { PropertyFields } from '@/modules/objects/PropertyFields'
import type { PickedFiles, PropertyValues } from '@/modules/objects/PropertyFields'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert'
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
  /** 만든 객체의 상세로 — 파일 업로드가 실패했을 때 「상세 열기」 가 쓴다. */
  onOpen?: (id: string) => void
}

/** 객체는 만들었는데 파일 일부가 실패한 상태 — 창을 닫지 않고 알린다. */
interface PartialFailure {
  id: string
  label: string
  failed: string[]
}

export function ObjectCreateDialog({ type, defs, onClose, onCreated, onOpen }: Props) {
  const { user } = useAuth()
  const [key, setKey] = useState('')
  const [label, setLabel] = useState('')
  const [description, setDescription] = useState('')
  const [values, setValues] = useState<PropertyValues>({})
  /** 파일 칸에서 고른 파일 — 객체를 만든 뒤 올린다. */
  const [files, setFiles] = useState<PickedFiles>({})
  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  const [progress, setProgress] = useState<string | null>(null)
  const [partial, setPartial] = useState<PartialFailure | null>(null)

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

  const pending = Object.entries(files).flatMap(([field, list]) =>
    list.map((file) => ({ field, file })),
  )

  async function submit() {
    setError(null)
    // **만들기 전에** 크기를 본다 — 만든 뒤에 거절되면 객체만 남는다(서버도 같은 상한으로 막는다).
    const tooBig = pending.filter((one) => one.file.size > ATTACHMENT_MAX_BYTES)
    if (tooBig.length > 0) {
      setError(
        new Error(
          `한 파일은 ${ATTACHMENT_MAX_BYTES / 1024 / 1024}MB 까지입니다 — ` +
            tooBig.map((one) => one.file.name).join(', '),
        ),
      )
      return
    }
    setSaving(true)
    let created: { id: string; label: string }
    try {
      created = await objectApi.create(type.slug, {
        key: type.key_policy === 'none' ? null : key || null,
        label,
        description,
        properties: values,
        workspace_slug: owner,
      })
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      setSaving(false)
      return
    }
    // 객체는 만들어졌다 — 이제 파일. 한 장씩 차례로, 실패한 것은 이름과 까닭을 남기고 계속한다.
    const failed: string[] = []
    for (const [index, one] of pending.entries()) {
      setProgress(`파일 ${pending.length}개 중 ${index + 1}번째 업로드 중…`)
      try {
        await attachmentApi.upload({
          ownerTable: 'objects',
          ownerId: created.id,
          ownerField: one.field,
          workspaceSlug: owner,
          file: one.file,
        })
      } catch (caught) {
        failed.push(
          `${one.file.name}: ${caught instanceof Error ? caught.message : '알 수 없는 오류'}`,
        )
      }
    }
    setProgress(null)
    setSaving(false)
    if (failed.length === 0) {
      onCreated()
      return
    }
    setPartial({ id: created.id, label: created.label, failed })
  }

  if (partial) {
    // 객체는 있다 — 닫으면 목록을 다시 읽는다(onCreated). 다시 「생성」 을 누를 자리는 없다
    // (누르면 같은 것이 둘이 된다).
    return (
      <Dialog open onOpenChange={(open) => !open && onCreated()}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{type.label} 생성</DialogTitle>
          </DialogHeader>
          <Alert variant="destructive">
            <AlertTitle>
              「{partial.label}」 은(는) 만들었지만 파일 {partial.failed.length}개를 업로드하지
              못했습니다
            </AlertTitle>
            <AlertDescription>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">
                {partial.failed.map((one) => (
                  <li key={one}>{one}</li>
                ))}
              </ul>
              <p className="mt-2">
                객체는 그대로 있습니다 — 상세 화면의 그 칸에서 다시 업로드하세요.
              </p>
            </AlertDescription>
          </Alert>
          <DialogFooter>
            <Button variant="outline" onClick={onCreated}>
              닫기
            </Button>
            {onOpen && (
              <Button
                onClick={() => {
                  onCreated()
                  onOpen(partial.id)
                }}
              >
                상세 열기
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    )
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !saving && onClose()}>
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
            files={{
              picked: files,
              onChange: (field, list) => setFiles((before) => ({ ...before, [field]: list })),
            }}
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
          {progress && (
            <span className="text-muted-foreground mr-auto self-center text-xs" role="status">
              {progress}
            </span>
          )}
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
