/**
 * 장표에서 기록에 하는 일(ADR 0037 2단계) — 값 수정 · 연결 · 연결 해제 · 새 기록 · 글을 기록으로.
 *
 * **모두 객체 API 로 한다.** 장표는 기록을 가리킬 뿐이고, 권한 · 감사 · 알림의 규칙은 기록의 것이다
 * (ADR 0036 — 기록은 만든 사람과 부서 관리자가 수정한다). 장표 서버는 「이 기록을 수정할 수 있나」 ·
 * 「이 영역은 무엇으로 이어지나」 만 알려 준다(`can_edit` · `link` · `ties`).
 */

import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { ObjectCreateDialog } from '@/modules/objects/ObjectCreateDialog'
import { PropertyFields, ObjectRefPicker } from '@/modules/objects/PropertyFields'
import type { PropertyValues } from '@/modules/objects/PropertyFields'
import { objectApi } from '@/modules/objects/api'
import { ontologyApi } from '@/modules/ontology/api'
import type { ObjectType, PropertyDef } from '@/modules/ontology/api'
import { slideApi } from '@/modules/slides/api'
import type { RenderedArea, Slide, SlideRecord } from '@/modules/slides/api'
import type { RecordActions } from '@/modules/slides/SlideGrid'
import { useAuth } from '@/shared/auth/AuthContext'
import { canCreateObject } from '@/shared/auth/roles'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
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
import { useResource } from '@/shared/hooks/useResource'

type TypeWithProps = ObjectType & { properties: PropertyDef[] }

interface Creating {
  area: RenderedArea
  type: TypeWithProps
  values: PropertyValues
  label?: string
  title?: string
  /** 만든 뒤 — 영역에 잇기(선택 · 따라옴) 또는 글 영역에 적기. */
  then: (created: { id: string; label: string }) => Promise<void>
}

function list(raw: unknown): string[] {
  if (raw === null || raw === undefined || raw === '') return []
  return (Array.isArray(raw) ? raw : [raw]).map(String)
}

/** 따라갈 기록 하나와 이 기록을 잇는다 — 영역의 `link` 가 말하는 대로. */
export async function tie(area: RenderedArea, sourceId: string, recordId: string) {
  const link = area.link
  const recordType = area.type_slug
  if (!link || !recordType) return
  if (link.kind === 'relation' && link.relation) {
    // 관계는 출발점을 수정할 수 있는 사람이 맺는다 — 방향대로 출발점에서.
    if (link.direction === 'in') {
      await objectApi.addRelation(link.source_type_slug, sourceId, {
        relation: link.relation,
        dst_object_id: recordId,
      })
    } else {
      await objectApi.addRelation(recordType, recordId, {
        relation: link.relation,
        dst_object_id: sourceId,
      })
    }
    return
  }
  const [owner, ownerId, target] =
    link.kind === 'ref'
      ? [recordType, recordId, sourceId]
      : [link.source_type_slug, sourceId, recordId]
  const key = link.property ?? ''
  const found = await objectApi.profile(owner, ownerId)
  const now = list(found.object.properties[key])
  if (now.includes(target)) return
  await objectApi.update(owner, ownerId, {
    properties: { [key]: link.multi ? [...now, target] : target },
  })
}

/** 이 기록과 따라갈 기록의 연결을 모두 끊는다(`ties`). */
export async function untie(area: RenderedArea, record: SlideRecord) {
  const link = area.link
  const recordType = area.type_slug
  if (!link || !recordType) return
  for (const one of record.ties) {
    if (link.kind === 'relation') {
      if (link.direction === 'in') {
        await objectApi.removeRelation(link.source_type_slug, one.source_id, one.id)
      } else {
        await objectApi.removeRelation(recordType, record.id, one.id)
      }
      continue
    }
    const [owner, ownerId, target] =
      link.kind === 'ref'
        ? [recordType, record.id, one.source_id]
        : [link.source_type_slug, one.source_id, record.id]
    const key = link.property ?? ''
    const found = await objectApi.profile(owner, ownerId)
    const kept = list(found.object.properties[key]).filter((each) => each !== target)
    await objectApi.update(owner, ownerId, {
      properties: { [key]: link.multi ? kept : null },
    })
  }
}

/** 글을 담을 칸 — 긴 글 칸이 먼저, 없으면 글 칸. */
function textField(type: TypeWithProps): PropertyDef | undefined {
  return (
    type.properties.find((one) => one.data_type === 'text_long') ??
    type.properties.find((one) => one.data_type === 'text')
  )
}

export function useRecordActions({
  slide,
  refresh,
  fail,
}: {
  slide: Slide
  refresh: () => Promise<void>
  fail: (error: unknown) => void
}): { actions: RecordActions; dialogs: ReactNode } {
  const { user } = useAuth()
  const schema = useResource(() => ontologyApi.schema(), [])
  const types = useMemo(
    () => new Map((schema.data?.types ?? []).map((one) => [one.slug, one as TypeWithProps])),
    [schema.data],
  )
  const [editing, setEditing] = useState<{
    area: RenderedArea
    record: SlideRecord
    field: { key: string; label: string }
  } | null>(null)
  const [linking, setLinking] = useState<RenderedArea | null>(null)
  const [unlinking, setUnlinking] = useState<{ area: RenderedArea; record: SlideRecord } | null>(
    null,
  )
  const [creating, setCreating] = useState<Creating | null>(null)
  const [making, setMaking] = useState<RenderedArea | null>(null)

  /** 주제를 가리키는 칸은 주제로 미리 채운다 — 모델 A 의 장표에서 만드는 기록은 모델 A 의 것이다. */
  function subjectValues(type: TypeWithProps): PropertyValues {
    const subject = slide.subject
    const out: PropertyValues = {}
    if (!subject) return out
    for (const one of type.properties) {
      if (one.data_type === 'object_ref' && one.ref_type_slug === subject.type_slug) {
        out[one.key] = one.multi ? [subject.id] : subject.id
      }
    }
    return out
  }

  async function run(work: () => Promise<void>) {
    try {
      await work()
      await refresh()
    } catch (caught) {
      fail(caught)
    }
  }

  const actions: RecordActions = {
    onEditValue: (area, record, field) => setEditing({ area, record, field }),
    onLink: (area) => setLinking(area),
    onUnlink: (area, record) => setUnlinking({ area, record }),
    /**
     * 「새 기록」 은 **만든 기록이 그 영역에 실릴 때만** 세운다 — 만든 뒤 잇는 일(선택에 더하기 · 따라갈
     * 기록에 연결)이 거절되면 기록만 덩그러니 남는다(2026-10-24 점검). 선택 영역은 장표를 수정할 수
     * 있는 사람, 따라오는 영역은 따라갈 기록이 하나이고 연결할 수 있을 때.
     */
    canCreate: (area) => {
      const type = area.type_slug ? types.get(area.type_slug) : undefined
      if (!type || !canCreateObject(user, type.usage)) return false
      if (area.fill === 'pick') return slide.can_edit
      if (area.fill === 'follow') {
        return Boolean(area.link?.can_link && area.link.sources.length === 1)
      }
      return true
    },
    onCreate: (area) => {
      const type = area.type_slug ? types.get(area.type_slug) : undefined
      if (!type) return
      const values = subjectValues(type)
      const link = area.link
      const only = link && link.sources.length === 1 ? link.sources[0].id : null
      if (link?.kind === 'ref' && only && link.property) {
        values[link.property] = link.multi ? [only] : only
      }
      setCreating({
        area,
        type,
        values,
        then: async (created) => {
          if (area.fill === 'pick') {
            const now = area.records.map((one) => one.id)
            const next = area.limit === 1 ? [created.id] : [...now, created.id]
            await slideApi.update(slide.id, { picks: { [area.key]: next } })
          } else if (area.fill === 'follow' && only && link?.kind !== 'ref') {
            await tie(area, only, created.id)
          }
        },
      })
    },
    onMakeRecord: (area) => setMaking(area),
  }

  const recordTypes = [...types.values()].filter(
    (one) => one.usage === 'log' && one.kind_class !== 'system' && canCreateObject(user, 'log'),
  )

  const dialogs = (
    <>
      {editing && (
        <ValueDialog
          {...editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null)
            await refresh()
          }}
        />
      )}
      {linking && linking.link && (
        <LinkDialog
          area={linking}
          onClose={() => setLinking(null)}
          onSave={(sourceId, recordId) =>
            run(async () => {
              await tie(linking, sourceId, recordId)
              setLinking(null)
            })
          }
        />
      )}
      {unlinking && (
        <ConfirmDialog
          open
          title="연결 해제"
          description={`「${unlinking.record.label}」 과 「${unlinking.area.follow_title ?? ''}」 의 기록의 연결을 해제합니다. 기록 자체는 삭제하지 않습니다.`}
          confirmLabel="해제"
          destructive
          onConfirm={async () => {
            await untie(unlinking.area, unlinking.record)
            setUnlinking(null)
            await refresh()
          }}
          onClose={() => setUnlinking(null)}
        />
      )}
      {creating && (
        <ObjectCreateDialog
          type={creating.type}
          defs={creating.type.properties}
          initial={{ label: creating.label, values: creating.values }}
          title={creating.title}
          onClose={() => setCreating(null)}
          onCreated={(created) => {
            const then = creating.then
            setCreating(null)
            void run(() => then(created))
          }}
        />
      )}
      {making && (
        <MakeRecordDialog
          area={making}
          types={recordTypes}
          onClose={() => setMaking(null)}
          onChoose={(type) => {
            const field = textField(type)
            const text = making.text
            const values = subjectValues(type)
            if (field) values[field.key] = text
            const area = making
            setMaking(null)
            setCreating({
              area,
              type,
              values,
              label: text.split('\n')[0].trim().slice(0, 200),
              title: `글을 기록으로 — ${type.label}`,
              then: async (created) => {
                await slideApi.update(slide.id, { made: { [area.key]: created.id } })
              },
            })
          }}
        />
      )}
    </>
  )
  return { actions, dialogs }
}

/** 값 하나를 그 자리에서 수정 — 객체 상세와 같은 칸 입력, 같은 검증(서버). */
function ValueDialog({
  area,
  record,
  field,
  onClose,
  onSaved,
}: {
  area: RenderedArea
  record: SlideRecord
  field: { key: string; label: string }
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const typeSlug = area.type_slug ?? record.type_slug
  const profile = useResource(() => objectApi.profile(typeSlug, record.id), [typeSlug, record.id])
  const [label, setLabel] = useState<string | null>(null)
  const [values, setValues] = useState<PropertyValues | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  const definition = profile.data?.properties_schema.find((one) => one.key === field.key)
  const shownLabel = label ?? profile.data?.object.label ?? ''
  const shownValues = values ?? {
    [field.key]: profile.data?.object.properties[field.key] ?? null,
  }

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await objectApi.update(
        typeSlug,
        record.id,
        field.key === 'label'
          ? { label: shownLabel }
          : { properties: { [field.key]: shownValues[field.key] ?? null } },
      )
      await onSaved()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {record.label} — {field.label} 수정
          </DialogTitle>
          <DialogDescription>
            기록의 값을 수정합니다. 이 기록이 실린 모든 장표에 반영됩니다.
          </DialogDescription>
        </DialogHeader>
        <ErrorNotice error={error ?? profile.error} />
        {profile.data &&
          (field.key === 'label' ? (
            <div className="space-y-1.5">
              <Label htmlFor="slide-value-label">이름</Label>
              <Input
                id="slide-value-label"
                value={shownLabel}
                maxLength={200}
                onChange={(event) => setLabel(event.target.value)}
              />
            </div>
          ) : definition ? (
            <PropertyFields
              defs={[definition]}
              values={shownValues}
              onChange={(next) => setValues(next)}
            />
          ) : (
            <p className="text-muted-foreground text-sm">이 칸은 더 이상 타입에 없습니다.</p>
          ))}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            취소
          </Button>
          <Button
            onClick={save}
            disabled={saving || !profile.data || (field.key === 'label' && !shownLabel.trim())}
          >
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** 따라오는 영역에 기존 기록을 연결 — 따라갈 기록이 여럿이면 어느 것과 이을지 선택한다. */
function LinkDialog({
  area,
  onClose,
  onSave,
}: {
  area: RenderedArea
  onClose: () => void
  onSave: (sourceId: string, recordId: string) => Promise<void>
}) {
  const sources = area.link?.sources ?? []
  const [source, setSource] = useState(sources[0]?.id ?? '')
  const [record, setRecord] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{area.title} — 연결</DialogTitle>
          <DialogDescription>
            기존 {area.type_label} 기록을 「{area.follow_title}」 의 기록에 연결합니다.
          </DialogDescription>
        </DialogHeader>
        {sources.length > 1 && (
          <div className="space-y-1.5">
            <Label htmlFor="slide-link-source">연결할 「{area.follow_title}」 기록</Label>
            <Select value={source} onValueChange={setSource}>
              <SelectTrigger id="slide-link-source" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {sources.map((one) => (
                  <SelectItem key={one.id} value={one.id}>
                    {one.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        <div className="space-y-1.5">
          <Label>{area.type_label} 기록</Label>
          <ObjectRefPicker typeSlug={area.type_slug} value={record} onChange={setRecord} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            취소
          </Button>
          <Button
            disabled={saving || !record || !source}
            onClick={async () => {
              if (!record) return
              setSaving(true)
              await onSave(source, record)
              setSaving(false)
            }}
          >
            연결
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** 글을 기록으로 — 어느 기록 타입으로 만들지 선택하면 글이 미리 채워진 생성 화면이 열린다. */
function MakeRecordDialog({
  area,
  types,
  onClose,
  onChoose,
}: {
  area: RenderedArea
  types: TypeWithProps[]
  onClose: () => void
  onChoose: (type: TypeWithProps) => void
}) {
  const [chosen, setChosen] = useState(types[0]?.slug ?? '')
  const type = types.find((one) => one.slug === chosen)
  const field = type ? textField(type) : undefined

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{area.title} — 기록으로 저장</DialogTitle>
          <DialogDescription>
            글의 첫 줄이 이름, 글 전체가 기록의 글 칸으로 들어간 생성 화면이 열립니다. 나머지 칸은
            그 화면에서 입력합니다.
          </DialogDescription>
        </DialogHeader>
        {types.length === 0 ? (
          <p className="text-muted-foreground text-sm">만들 수 있는 기록 타입이 없습니다.</p>
        ) : (
          <div className="space-y-1.5">
            <Label htmlFor="slide-make-type">기록 타입</Label>
            <Select value={chosen} onValueChange={setChosen}>
              <SelectTrigger id="slide-make-type" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {types.map((one) => (
                  <SelectItem key={one.slug} value={one.slug}>
                    {one.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-xs">
              {field
                ? `글은 「${field.label}」 칸에 들어갑니다.`
                : '이 타입에는 글 칸이 없어 첫 줄만 이름으로 들어갑니다.'}
            </p>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            취소
          </Button>
          <Button disabled={!type} onClick={() => type && onChoose(type)}>
            다음
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
