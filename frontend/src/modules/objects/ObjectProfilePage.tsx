/**
 * 객체 하나 — **연결된 것이 여기 모인다.**
 *
 * 속성·첨부가 한 화면에 있고, 2단계에서 관계와 관계도가 여기 붙는다.
 */

import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Bell, BellOff, Pencil, Trash2, Waypoints } from 'lucide-react'

import { AttachmentList } from '@/modules/files/AttachmentList'
import { GraphPanel } from '@/modules/graph/GraphPanel'
import { ontologyApi } from '@/modules/ontology/api'
import { ObjectYears } from '@/modules/objects/ObjectYears'
import { AliasesPanel } from '@/modules/objects/AliasesPanel'
import { RelatedObjects } from '@/modules/objects/RelatedObjects'
import { RollupPanel } from '@/modules/objects/RollupPanel'
import type { PropertyDef, SectionView } from '@/modules/ontology/api'
import { objectApi } from '@/modules/objects/api'
import { PropertyFields, groupBySection, propertyText } from '@/modules/objects/PropertyFields'
import type { PropertyValues } from '@/modules/objects/PropertyFields'
import { ObjectDeleteDialog } from '@/modules/objects/ObjectDeleteDialog'
import { ObjectHistory } from '@/modules/objects/ObjectHistory'
import { ApiError } from '@/shared/api/client'
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
  const [watchBusy, setWatchBusy] = useState(false)
  const [saving, setSaving] = useState(false)
  const [confirming, setConfirming] = useState(false)

  // 매 렌더 새 배열이면 관계도가 글자 하나 칠 때마다 다시 흔들린다 — 정의가 바뀔 때만.
  const typeSlugs = useMemo(() => (schema.data?.types ?? []).map((one) => one.slug), [schema.data])
  const row = profile.data?.object
  const defs = profile.data?.properties_schema ?? []
  /** 원 표를 비추는 객체 — 속성·첨부·이력·연도가 없고, 고치는 곳은 그 표의 화면이다. */
  const isSystem = objectType?.kind_class === 'system'
  /** 허브가 내려준 객체 — 값은 허브에서 고치고, 여기서는 관계로 가리키기만 한다. */
  const managed = Boolean(objectType?.managed_by)

  // 편집을 열 때 지금 값을 담는다. **화면 상태를 서버 값과 따로 두면** 저장을
  // 취소했을 때 어느 쪽이 진짜인지 알 수 없다.
  useEffect(() => {
    if (row && editing) {
      setLabel(row.label)
      setDescription(row.description)
      setValues(row.properties as PropertyValues)
    }
  }, [row, editing])

  // 합쳐져서 지워진 것이면 **이긴 쪽으로 간다** — 옛 링크가 404 로 끝나지 않게.
  const mergedInto =
    profile.error instanceof ApiError && typeof profile.error.details.merged_into === 'string'
      ? profile.error.details.merged_into
      : null
  useEffect(() => {
    if (mergedInto) navigate(`/o/${typeSlug}/${mergedInto}`, { replace: true })
  }, [mergedInto, navigate, typeSlug])

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

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title={row.label}
        description={row.key ? `식별자 ${row.key}` : undefined}
        back={{ to: `/o/${typeSlug}`, label: profile.data.type_label }}
        actions={
          <div className="flex gap-2">
            {/* **관계 목록은 한 단계만 보여 준다.** 그 너머는 그래프에서 — 누구나.
                원 표의 객체(부서 등)에서도 출발한다. */}
            <Button asChild size="sm" variant="outline">
              <Link to={`/graph?focus=${row.id}`}>
                <Waypoints className="mr-1 size-4" />
                그래프에서 보기
              </Link>
            </Button>
            {/* **알림 구독.** 「내가 보던 그게 아직 그대로인가」 를 알 방법이 목록을
                다시 여는 것뿐이면, 사람은 안 열고 옛 값을 들고 회의에 들어간다.
                투영 타입에는 행이 없어 지켜볼 것도 없다. */}
            {!isSystem && (
              <Button
                size="sm"
                variant={profile.data.watching ? 'secondary' : 'outline'}
                aria-pressed={profile.data.watching}
                title={
                  profile.data.watching
                    ? '바뀌면 알림이 옵니다 — 내가 수정한 것은 빼고'
                    : '이것이 바뀌면 알림을 받습니다'
                }
                disabled={watchBusy}
                onClick={() => {
                  setWatchBusy(true)
                  setError(null)
                  objectApi
                    .setWatch(typeSlug, row.id, !profile.data?.watching)
                    .then(() => profile.reload())
                    .catch((caught: unknown) =>
                      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류')),
                    )
                    .finally(() => setWatchBusy(false))
                }}
              >
                {profile.data.watching ? (
                  <Bell className="mr-1 size-4" />
                ) : (
                  <BellOff className="mr-1 size-4" />
                )}
                {profile.data.watching ? '지켜보는 중' : '알림 구독'}
                {profile.data.watcher_count > 1 && (
                  <span className="text-muted-foreground ml-1 text-xs">
                    {profile.data.watcher_count}
                  </span>
                )}
              </Button>
            )}
            {profile.data.can_edit && !editing && (
              <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
                <Pencil className="mr-1 size-4" />
                수정
              </Button>
            )}
            {profile.data.can_edit && (
              <Button size="sm" variant="outline" onClick={() => setConfirming(true)}>
                <Trash2 className="mr-1 size-4" />
                삭제
              </Button>
            )}
          </div>
        }
      />

      {error && <ErrorNotice error={error} />}

      {managed && (
        <p className="text-muted-foreground text-sm">
          {objectType?.managed_by === 'hub' ? '허브' : objectType?.managed_by}가 내려준
          기준정보입니다 — 값은 허브에서 수정한 뒤 받습니다. 이 설치의 관계(산출 문서 · 참여 등)는
          여기서 잇습니다.
        </p>
      )}

      <section className="space-y-3 rounded-md border p-4">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <StatusBadge kind="object" value={row.status} />
          {!isSystem && (
            <span className="text-muted-foreground">
              {/* **NULL 은 전역이다.** 빈 칸으로 두면 「부서가 없다」 로 읽힌다. */}
              {row.owner_workspace_slug ? `${row.owner_workspace_slug} 부서` : '전역'}
            </span>
          )}
          {!isSystem && (
            <span className="text-muted-foreground">수정한 때 {shownDateTime(row.updated_at)}</span>
          )}
        </div>

        {isSystem ? (
          <p className="text-muted-foreground text-sm">{row.description || '원 표의 행입니다.'}</p>
        ) : editing ? (
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

      {isSystem && (
        <p className="text-muted-foreground text-sm">
          이 객체는 다른 표(원 표)를 비춥니다. 이름과 내용은 그 표의 화면에서 수정합니다 — 여기서는
          이것을 가리키는 관계만 봅니다.
        </p>
      )}

      {/* **연도를 쓰는 축에서만 나온다.** 없는 것을 있는 척하지 않는다. */}
      {objectType?.temporal_kind === 'yearly' && (
        <ObjectYears typeSlug={typeSlug} objectId={objectId} canEdit={profile.data.can_edit} />
      )}

      {/* 다른 이름 — 원 표의 객체에는 없다(그 표가 이름을 갖는다). */}
      {!isSystem && (
        <AliasesPanel
          typeSlug={typeSlug}
          objectId={objectId}
          aliases={row.aliases ?? []}
          externalIds={row.external_ids ?? {}}
          canEdit={profile.data.can_edit}
          onChanged={profile.reload}
        />
      )}

      {/* 롤업 — 트리와 롤업 정의가 있는 타입에서만. 정의가 없으면 빈 목록이라 안 뜬다. */}
      {objectType?.list_view?.tree?.relation && (objectType.list_view.rollups?.length ?? 0) > 0 && (
        <RollupPanel typeSlug={typeSlug} objectId={objectId} reloadKey={profile.data} />
      )}

      <RelatedObjects
        typeSlug={typeSlug}
        objectId={objectId}
        objectLabel={row.label}
        objectTypeSlug={row.type_slug}
        related={profile.data.related}
        relationTypes={schema.data?.relation_types ?? []}
        canEdit={profile.data.can_link ?? profile.data.can_edit}
        onChanged={profile.reload}
      />

      {/* 상세를 떠나지 않고 보는 관계도 — 관계가 없으면 안 그린다. */}
      <GraphPanel objectId={objectId} typeSlugs={typeSlugs} />

      {/* 이 값이 어디서 왔나 — 상세가 다시 읽힐 때마다 이력도 다시(관계 변경은 updated_at 을
          안 건드리므로 응답 객체 자체를 키로 쓴다). 원 표의 객체는 이력이 그 표에 있다. */}
      {!isSystem && (
        <ObjectHistory
          typeSlug={typeSlug}
          objectId={objectId}
          defs={defs}
          current={{
            key: row.key,
            label: row.label,
            status: row.status,
            properties: row.properties,
            description: row.description,
            valid_from_year: row.valid_from_year,
            valid_to_year: row.valid_to_year,
          }}
          refLabels={row.ref_labels}
          canEdit={profile.data.can_edit}
          onRestored={profile.reload}
          reloadKey={profile.data}
        />
      )}

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

      {/* 속성으로 정해지지 않은 첨부. 자리를 안 나눠 쓰는 경우다. 원 표의 객체에는 없다. */}
      {!isSystem && (
        <AttachmentList
          ownerTable="objects"
          ownerId={row.id}
          workspaceSlug={row.owner_workspace_slug}
          canEdit={profile.data.can_edit}
          title={fileDefs.length > 0 ? '그 밖의 첨부' : '첨부'}
        />
      )}

      {confirming && (
        <ObjectDeleteDialog
          typeSlug={typeSlug}
          typeLabel={profile.data.type_label}
          object={row}
          onClose={() => setConfirming(false)}
          onDone={(into) => {
            setConfirming(false)
            navigate(into ? `/o/${typeSlug}/${into}` : `/o/${typeSlug}`, { replace: true })
          }}
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
