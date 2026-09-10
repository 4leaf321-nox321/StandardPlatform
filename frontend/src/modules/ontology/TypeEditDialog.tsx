/**
 * 타입 하나를 고친다 — **행을 누르면 여기가 뜬다.**
 *
 * 고칠 자리가 없으면 잘못 넣은 타입을 옮길 방법이 없고, 그러면 사람은 새로
 * 만들고 옛것을 버려 둔다. 버려진 것은 목록에 남아 다음 사람을 헷갈리게 한다.
 *
 * **속성 정의는 여기 없다.** 그것은 그것대로 자기 자리가 있다 — 한 창에 섞으면
 * 「타입의 설정」 과 「그 타입이 담는 값의 모양」 이 같은 것처럼 읽힌다.
 */

import { useState } from 'react'

import { ontologyApi } from '@/modules/ontology/api'
import type { NavGroupRow, ObjectType } from '@/modules/ontology/api'
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
import { Textarea } from '@/shared/components/ui/textarea'

/** 「묶음 없음」. **빈 문자열을 쓸 수 없다** — Select 가 그것을 「고른 것 없음」 으로 본다. */
const NONE = '__none__'

interface Props {
  type: ObjectType
  groups: NavGroupRow[]
  onClose: () => void
  onChanged: () => void
}

export function TypeEditDialog({ type, groups, onClose, onChanged }: Props) {
  const [label, setLabel] = useState(type.label)
  const [description, setDescription] = useState(type.description)
  const [group, setGroup] = useState(type.nav_group_slug ?? NONE)
  const [kindClass, setKindClass] = useState<string>(type.kind_class)
  const [entryPolicy, setEntryPolicy] = useState<string>(type.entry_policy)
  const [keyPolicy, setKeyPolicy] = useState<string>(type.key_policy)
  const [keyScope, setKeyScope] = useState<string>(type.key_scope)
  const [temporalKind, setTemporalKind] = useState<string>(type.temporal_kind)
  const [sortOrder, setSortOrder] = useState(String(type.sort_order))
  const [isActive, setIsActive] = useState(type.is_active)

  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  const [removing, setRemoving] = useState(false)

  async function save() {
    setError(null)
    setSaving(true)
    try {
      // **보낸 것만 바뀐다.** `list_view` 는 여기서 안 만지므로 안 보낸다 —
      // 실어 보내면 이 창에 없는 설정을 이 창이 덮어쓰게 된다.
      await ontologyApi.updateType(type.slug, {
        label,
        description,
        nav_group_slug: group === NONE ? null : group,
        kind_class: kindClass,
        entry_policy: entryPolicy,
        key_policy: keyPolicy,
        key_scope: keyScope,
        temporal_kind: temporalKind,
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
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{type.label} 고치기</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            {error && <ErrorNotice error={error} />}

            <div className="space-y-1.5">
              <Label>slug</Label>
              <Input value={type.slug} readOnly disabled className="font-mono" />
              <p className="text-muted-foreground text-xs">
                <b>바꿀 수 없습니다.</b> 주소(<code>/o/{type.slug}</code>)와 관계·MCP 도구
                이름이 여기 물려 있어, 바꾸면 그 셋이 조용히 어긋납니다.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="type-edit-label">이름</Label>
              <Input
                id="type-edit-label"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="type-edit-description">설명</Label>
              <Textarea
                id="type-edit-description"
                rows={2}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="type-edit-group">사이드바 묶음</Label>
              <Select value={group} onValueChange={setGroup}>
                <SelectTrigger id="type-edit-group">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>사이드바에 안 세움</SelectItem>
                  {groups.map((one) => (
                    <SelectItem key={one.slug} value={one.slug}>
                      {one.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">
                안 세우면 화면은 있지만 메뉴에 안 뜹니다 — 어휘 축은 대개 그렇습니다.
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <Field label="객체 분류" value={kindClass} onChange={setKindClass}
                options={[
                  ['record', '인스턴스'],
                  ['reference', '어휘'],
                  ['system', '투영'],
                ]}
              />
              <Field label="입력 정책" value={entryPolicy} onChange={setEntryPolicy}
                options={[
                  ['open', '누구나 추가'],
                  ['closed', '관리자만'],
                ]}
              />
              <Field label="식별자" value={keyPolicy} onChange={setKeyPolicy}
                options={[
                  ['none', '안 씀'],
                  ['optional', '선택'],
                  ['required', '필수'],
                ]}
              />
              <Field label="식별자 범위" value={keyScope} onChange={setKeyScope}
                options={[
                  ['global', '전사에서 하나'],
                  ['workspace', '부서에서 하나'],
                ]}
              />
              <Field label="시간 정책" value={temporalKind} onChange={setTemporalKind}
                options={[
                  ['evergreen', '연도 무관'],
                  ['lifecycle', '유효 구간'],
                  ['yearly', '연도 배정'],
                  ['derived', '쓰인 데서 추론'],
                ]}
              />
              <div className="space-y-1.5">
                <Label htmlFor="type-edit-sort">순서</Label>
                <Input
                  id="type-edit-sort"
                  type="number"
                  value={sortOrder}
                  onChange={(event) => setSortOrder(event.target.value)}
                />
              </div>
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
                끄면 메뉴와 만들기에서 빠집니다. <b>자료는 그대로 남습니다.</b>
              </span>
            </label>
          </div>

          <DialogFooter className="justify-between sm:justify-between">
            <Button variant="ghost" onClick={() => setRemoving(true)} disabled={saving}>
              지우기
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
          title={`${type.label} 타입을 지웁니다`}
          description={
            type.object_count > 0 ? (
              <>
                지금 <b>{type.object_count}개</b>가 들어 있어 <b>지울 수 없습니다.</b> 그만
                쓰려는 것이면 「사용함」 을 끄세요 — 자료는 남고 화면에서만 빠집니다.
              </>
            ) : (
              <>
                들어 있는 것이 없어 지울 수 있습니다. <b>속성 정의도 함께 사라집니다</b> —
                안 지우면 같은 slug 로 다시 만들 때 옛 속성이 되살아납니다.
              </>
            )
          }
          confirmLabel="지우기"
          onConfirm={async () => {
            await ontologyApi.removeType(type.slug)
            onChanged()
            onClose()
          }}
          onClose={() => setRemoving(false)}
        />
      )}
    </>
  )
}

function Field({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (next: string) => void
  options: [string, string][]
}) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map(([key, text]) => (
            <SelectItem key={key} value={key}>
              {text}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
