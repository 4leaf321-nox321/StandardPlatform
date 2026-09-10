/**
 * 객체 하나 — **연결된 것이 여기 모인다.**
 *
 * 속성·첨부가 한 화면에 있고, 2단계에서 관계와 관계도가 여기 붙는다.
 */

import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Pencil, Trash2 } from 'lucide-react'

import { AttachmentList } from '@/modules/files/AttachmentList'
import { ontologyApi } from '@/modules/ontology/api'
import { ObjectYears } from '@/modules/objects/ObjectYears'
import { RelatedObjects } from '@/modules/objects/RelatedObjects'
import type { PropertyDef, SectionView } from '@/modules/ontology/api'
import { objectApi } from '@/modules/objects/api'
import { PropertyFields, groupBySection, propertyText } from '@/modules/objects/PropertyFields'
import type { PropertyValues } from '@/modules/objects/PropertyFields'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import { Textarea } from '@/shared/components/ui/textarea'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

export default function ObjectProfilePage() {
  const { typeSlug = '', objectId = '' } = useParams()
  const navigate = useNavigate()
  const profile = useResource(() => objectApi.profile(typeSlug, objectId), [typeSlug, objectId])
  // 관계 종류는 스키마에서 온다 — **어떤 관계를 맺을 수 있는지 화면이 알아야
  // 고를 것을 걸러 줄 수 있다.**
  const schema = useResource(() => ontologyApi.schema(), [])
  const objectType = schema.data?.types.find((one) => one.slug === typeSlug)

  const [editing, setEditing] = useState(false)
  const [label, setLabel] = useState('')
  const [description, setDescription] = useState('')
  const [values, setValues] = useState<PropertyValues>({})
  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const row = profile.data?.object
  const defs = profile.data?.properties_schema ?? []

  // 편집을 열 때 지금 값을 담는다. **화면 상태를 서버 값과 따로 두면** 저장을
  // 취소했을 때 어느 쪽이 진짜인지 알 수 없다.
  useEffect(() => {
    if (row && editing) {
      setLabel(row.label)
      setDescription(row.description)
      setValues(row.properties as PropertyValues)
    }
  }, [row, editing])

  if (profile.error) return <ErrorNotice error={profile.error} />
  if (!profile.data || !row) {
    return profile.loading ? null : <EmptyState title="객체를 찾을 수 없습니다" />
  }

  const fileDefs = defs.filter((def) => def.data_type === 'file')
  const valueDefs = defs.filter((def) => def.data_type !== 'file')

  async function save() {
    setError(null)
    setSaving(true)
    try {
      await objectApi.update(typeSlug, objectId, { label, description, properties: values })
      setEditing(false)
      profile.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    await objectApi.remove(typeSlug, objectId)
    navigate(`/o/${typeSlug}`)
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title={row.label}
        description={row.key ? `식별자 ${row.key}` : undefined}
        back={{ to: `/o/${typeSlug}`, label: profile.data.type_label }}
        actions={
          profile.data.can_edit && (
            <div className="flex gap-2">
              {!editing && (
                <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
                  <Pencil className="mr-1 size-4" />
                  고치기
                </Button>
              )}
              <Button size="sm" variant="outline" onClick={() => setConfirming(true)}>
                <Trash2 className="mr-1 size-4" />
                지우기
              </Button>
            </div>
          )
        }
      />

      {error && <ErrorNotice error={error} />}

      <section className="space-y-3 rounded-md border p-4">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <StatusBadge kind="object" value={row.status} />
          <span className="text-muted-foreground">
            {/* **NULL 은 전역이다.** 빈 칸으로 두면 「부서가 없다」 로 읽힌다. */}
            {row.owner_workspace_slug ? `${row.owner_workspace_slug} 부서` : '전역'}
          </span>
          <span className="text-muted-foreground">고친 때 {shownDateTime(row.updated_at)}</span>
        </div>

        {editing ? (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="edit-label">이름</Label>
              <Input
                id="edit-label"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="edit-description">설명</Label>
              <Textarea
                id="edit-description"
                rows={2}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>

            <PropertyFields
              defs={valueDefs}
              values={values}
              onChange={setValues}
              disabled={saving}
              view={objectType?.form_view}
            />

            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setEditing(false)} disabled={saving}>
                취소
              </Button>
              <Button onClick={save} disabled={saving || !label.trim()}>
                저장
              </Button>
            </div>
          </div>
        ) : (
          <ReadOnlyProperties
            defs={valueDefs}
            values={row.properties}
            refLabels={row.ref_labels}
            note={row.description}
            view={objectType?.detail_view}
          />
        )}
      </section>

      {/* **연도를 쓰는 축에서만 나온다.** 없는 것을 있는 척하지 않는다. */}
      {objectType?.temporal_kind === 'yearly' && (
        <ObjectYears typeSlug={typeSlug} objectId={objectId} canEdit={profile.data.can_edit} />
      )}

      <RelatedObjects
        typeSlug={typeSlug}
        objectId={objectId}
        objectLabel={row.label}
        objectTypeSlug={row.type_slug}
        related={profile.data.related}
        relationTypes={schema.data?.relation_types ?? []}
        canEdit={profile.data.can_edit}
        onChanged={profile.reload}
      />

      {/* **속성이 둘이면 첨부 목록도 둘이다.** 안 가르면 「도면」 칸에
          「시험성적서」 가 섞여 보이고, 그 목록은 무엇도 말해 주지 못한다. */}
      {fileDefs.map((def) => (
        <AttachmentList
          key={def.key}
          ownerTable="objects"
          ownerId={row.id}
          ownerField={def.key}
          title={def.label}
          workspaceSlug={row.owner_workspace_slug}
          canEdit={profile.data?.can_edit ?? false}
        />
      ))}

      {/* 속성으로 정해지지 않은 첨부. 자리를 안 나눠 쓰는 경우다. */}
      <AttachmentList
        ownerTable="objects"
        ownerId={row.id}
        workspaceSlug={row.owner_workspace_slug}
        canEdit={profile.data.can_edit}
        title={fileDefs.length > 0 ? '그 밖의 첨부' : '첨부'}
      />

      {confirming && (
        <ConfirmDialog
          open
          destructive
          title={`${row.label} 을(를) 지웁니다`}
          description={
            <>
              목록에서 사라집니다. <b>기록은 남습니다</b> — 이 객체를 가리키는 첨부와
              (앞으로 생길) 관계가 밖에 있어서, 지운 흔적까지 없애면 그것들이 무엇을
              가리키는지 설명할 수 없게 됩니다.
            </>
          }
          confirmLabel="지우기"
          onConfirm={remove}
          onClose={() => setConfirming(false)}
        />
      )}
    </div>
  )
}

function ReadOnlyProperties({
  defs,
  values,
  refLabels,
  note,
  view,
}: {
  defs: PropertyDef[]
  values: Record<string, unknown>
  refLabels: Record<string, string>
  note: string
  /** **폼과 같은 묶음을 쓴다.** 두 벌로 두면 갈리고, 갈린 것은 한쪽만 고쳐진다. */
  view?: SectionView
}) {
  const groups = groupBySection(defs, view)

  return (
    <div className="space-y-4">
      {note && <p className="text-sm whitespace-pre-wrap">{note}</p>}
      {defs.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          이 타입에는 아직 속성이 없습니다. 온톨로지 관리에서 정의하면 여기에 나타납니다.
        </p>
      ) : (
        groups.map((group) => (
          <div key={group.name || '__none__'} className="space-y-1.5">
            {group.name && (
              <p className="text-muted-foreground text-xs font-medium">{group.name}</p>
            )}
            <dl
              className={
                group.columns === 3
                  ? 'grid gap-x-6 gap-y-2 sm:grid-cols-3'
                  : group.columns === 1
                    ? 'grid gap-y-2'
                    : 'grid gap-x-6 gap-y-2 sm:grid-cols-2'
              }
            >
              {group.defs.map((def) => (
                <div key={def.key}>
                  <dt className="text-muted-foreground text-xs">
                    {def.label}
                    {def.unit && ` (${def.unit})`}
                  </dt>
                  <dd className="text-sm break-words">
                    {def.data_type === 'url' && typeof values[def.key] === 'string' ? (
                      <a
                        className="hover:underline"
                        href={values[def.key] as string}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {values[def.key] as string}
                      </a>
                    ) : (
                      propertyText(def, values[def.key], refLabels)
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        ))
      )}
    </div>
  )
}
