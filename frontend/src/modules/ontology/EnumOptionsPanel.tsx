/**
 * 고를 값 다루기 — **이름을 바꾸면 저장된 값도 따라오고, 코드표로 승격할 수 있다.**
 *
 * 위의 「고를 값 (쉼표로)」 칸은 목록을 통째로 다시 적는 자리라, 「스틸」 을 「강」 으로
 * 고치면 정의만 바뀌고 저장된 7개는 「스틸」 로 남는다. 여기서 바꾸면 **함께** 간다.
 *
 * 승격은 목록을 속성 밖 참조 타입으로 꺼내는 것 — 값에 설명·사용 중지·정렬이 생기고,
 * 다른 타입도 같은 코드표를 가리킬 수 있다. 되돌릴 자리(스냅샷)를 남기고 한다.
 */

import { useState } from 'react'
import { ArrowRight, BookMarked, Loader2, Pencil } from 'lucide-react'

import { ontologyApi } from '@/modules/ontology/api'
import type { ObjectType, PromoteOut, PropertyDef, RenameOptionOut } from '@/modules/ontology/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Badge } from '@/shared/components/ui/badge'
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

interface EnumOptionsPanelProps {
  type: ObjectType
  property: PropertyDef
  /** 코드표로 쓸 수 있는 타입(kind_class=reference). */
  types: ObjectType[]
  /** 이름을 바꾸거나 승격한 뒤 — 정의를 다시 읽는다. */
  onChanged: () => void
  /** 승격이 끝나면 이 창(속성 편집)을 닫는다 — 속성이 더는 enum 이 아니다. */
  onPromoted: () => void
}

export function EnumOptionsPanel({ type, property, types, onChanged, onPromoted }: EnumOptionsPanelProps) {
  const [renaming, setRenaming] = useState<string | null>(null)
  const [promoting, setPromoting] = useState(false)
  const options = property.enum_options ?? []

  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">저장된 고를 값 다루기</span>
        <Button size="xs" variant="outline" onClick={() => setPromoting(true)} disabled={options.length === 0}>
          <BookMarked className="mr-1 size-3" />
          코드표로 승격…
        </Button>
      </div>
      <p className="text-muted-foreground text-xs">
        여기서 이름을 바꾸면 <b>이미 저장된 값도 함께</b> 바뀝니다. 위 칸에서 고치면 정의만 바뀝니다.
      </p>
      <ul className="space-y-1">
        {options.map((value) => (
          <li key={value} className="flex items-center gap-2 text-sm">
            <span className="flex-1 truncate">{value}</span>
            <Button size="xs" variant="ghost" onClick={() => setRenaming(value)}>
              <Pencil className="mr-1 size-3" />
              이름 바꾸기
            </Button>
          </li>
        ))}
      </ul>

      {renaming !== null && (
        <RenameDialog
          typeSlug={type.slug}
          propertyKey={property.key}
          from={renaming}
          onClose={() => setRenaming(null)}
          onDone={() => {
            setRenaming(null)
            onChanged()
          }}
        />
      )}
      {promoting && (
        <PromoteDialog
          type={type}
          property={property}
          books={types.filter((one) => one.kind_class === 'reference' && one.is_active)}
          onClose={() => setPromoting(false)}
          onDone={() => {
            setPromoting(false)
            onPromoted()
          }}
        />
      )}
    </div>
  )
}

interface RenameDialogProps {
  typeSlug: string
  propertyKey: string
  from: string
  onClose: () => void
  onDone: () => void
}

function RenameDialog({ typeSlug, propertyKey, from, onClose, onDone }: RenameDialogProps) {
  const [to, setTo] = useState(from)
  const [plan, setPlan] = useState<RenameOptionOut | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  async function run(apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      const result = await ontologyApi.renameOption(typeSlug, propertyKey, { from, to: to.trim(), apply })
      setPlan(result)
      if (result.applied) onDone()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const changed = to.trim() !== '' && to.trim() !== from
  const previewed = plan !== null && plan.to_value === to.trim() && plan.errors.length === 0

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>「{from}」 이름 바꾸기</DialogTitle>
          <DialogDescription>정의의 고를 값과, 이 값을 가진 객체의 저장값이 함께 바뀝니다.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="rename-to">새 이름</Label>
            <Input
              id="rename-to"
              autoFocus
              value={to}
              onChange={(event) => {
                setTo(event.target.value)
                setPlan(null)
              }}
            />
          </div>
          {plan && plan.errors.length > 0 && (
            <ul className="text-destructive space-y-0.5 text-sm">
              {plan.errors.map((one) => (
                <li key={one}>{one}</li>
              ))}
            </ul>
          )}
          {previewed && (
            <p className="text-sm">
              <Badge variant="secondary">저장된 값 {plan.objects_with_value}개</Badge> 가 「{plan.to_value}」 으로
              함께 바뀝니다. 객체마다 변경 이력에 남습니다.
            </p>
          )}
          {error && <ErrorNotice error={error} />}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            취소
          </Button>
          {!previewed ? (
            <Button disabled={!changed || busy} onClick={() => void run(false)}>
              {busy && <Loader2 className="mr-1 size-3.5 animate-spin" />}
              몇 개가 바뀌는지 보기
            </Button>
          ) : (
            <Button disabled={busy} onClick={() => void run(true)}>
              {busy && <Loader2 className="mr-1 size-3.5 animate-spin" />}
              바꾸기 — 저장값 {plan.objects_with_value}개 포함
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface PromoteDialogProps {
  type: ObjectType
  property: PropertyDef
  books: ObjectType[]
  onClose: () => void
  onDone: () => void
}

/** 코드표로 승격 — 새로 만들거나 있는 것에 붙인다. 계획을 보고 누른다. */
function PromoteDialog({ type, property, books, onClose, onDone }: PromoteDialogProps) {
  const [mode, setMode] = useState<'new' | 'existing'>(books.length > 0 ? 'existing' : 'new')
  const [target, setTarget] = useState(books[0]?.slug ?? '')
  const [newSlug, setNewSlug] = useState(`${property.key}_book`)
  const [newLabel, setNewLabel] = useState(property.label)
  const [plan, setPlan] = useState<PromoteOut | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const body = (apply: boolean) =>
    mode === 'existing'
      ? { target_type_slug: target, apply }
      : { new_slug: newSlug.trim(), new_label: newLabel.trim(), apply }

  async function run(apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      const result = await ontologyApi.promoteProperty(type.slug, property.key, body(apply))
      setPlan(result)
      if (result.applied) onDone()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const ready = mode === 'existing' ? Boolean(target) : Boolean(newSlug.trim() && newLabel.trim())
  const canApply = plan !== null && !plan.applied && plan.errors.length === 0
  const moved = plan ? plan.options.reduce((sum, one) => sum + one.objects_with_value, 0) : 0

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>「{property.label}」 을 코드표로 승격</DialogTitle>
          <DialogDescription>
            고를 값마다 객체가 생기고, 저장된 문자열이 그 객체를 가리키게 바뀝니다. 이 속성은
            「객체 참조」 가 됩니다. 정의는 스냅샷으로 되돌릴 수 있지만 <b>옮긴 값은 안 돌아옵니다</b>
            — 계획을 보고 누르세요.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm">
          <div className="flex gap-2">
            <label className="flex cursor-pointer items-center gap-1.5">
              <input type="radio" checked={mode === 'existing'} disabled={books.length === 0} onChange={() => { setMode('existing'); setPlan(null) }} />
              있는 코드표에 붙이기
            </label>
            <label className="flex cursor-pointer items-center gap-1.5">
              <input type="radio" checked={mode === 'new'} onChange={() => { setMode('new'); setPlan(null) }} />
              새 코드표 만들기
            </label>
          </div>
          {mode === 'existing' ? (
            <Select value={target} onValueChange={(next) => { setTarget(next); setPlan(null) }}>
              <SelectTrigger size="sm" className="w-full">
                <SelectValue placeholder="코드표 고르기" />
              </SelectTrigger>
              <SelectContent>
                {books.map((one) => (
                  <SelectItem key={one.slug} value={one.slug}>
                    {one.label} <span className="text-muted-foreground">({one.object_count})</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label htmlFor="promote-slug">slug</Label>
                <Input id="promote-slug" value={newSlug} onChange={(event) => { setNewSlug(event.target.value); setPlan(null) }} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="promote-label">이름</Label>
                <Input id="promote-label" value={newLabel} onChange={(event) => { setNewLabel(event.target.value); setPlan(null) }} />
              </div>
            </div>
          )}

          {plan && (
            <div className="space-y-2">
              <p>
                {plan.target_new ? '새 코드표' : '있는 코드표'} <b>{plan.target_label}</b> ({plan.target_slug})
              </p>
              <table className="w-full text-xs">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1 text-left font-normal">값</th>
                    <th className="py-1 text-left font-normal">코드표에</th>
                    <th className="py-1 text-right font-normal">옮길 저장값</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.options.map((one) => (
                    <tr key={one.value} className="border-t">
                      <td className="py-1">{one.value}</td>
                      <td className="py-1">
                        <Badge variant={one.action === 'create' ? 'default' : 'outline'}>
                          {one.action === 'create' ? '새로 만듦' : '있는 것에 붙임'}
                        </Badge>
                      </td>
                      <td className="py-1 text-right tabular-nums">{one.objects_with_value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {plan.errors.length > 0 && (
                <ul className="text-destructive space-y-0.5">
                  {plan.errors.map((one) => (
                    <li key={one}>{one}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {error && <ErrorNotice error={error} />}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            취소
          </Button>
          {!canApply ? (
            <Button disabled={!ready || busy} onClick={() => void run(false)}>
              {busy && <Loader2 className="mr-1 size-3.5 animate-spin" />}
              계획 보기
            </Button>
          ) : (
            <Button disabled={busy} onClick={() => void run(true)}>
              {busy ? <Loader2 className="mr-1 size-3.5 animate-spin" /> : <ArrowRight className="mr-1 size-3.5" />}
              승격 — 저장값 {moved}개 옮김
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
