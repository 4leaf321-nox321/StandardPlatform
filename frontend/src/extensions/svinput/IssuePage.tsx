/**
 * 문제점 한 페이지 — 셋째 층. PPT 한 장처럼: 머리(제목 · 상태 · 문서) · 칸(현상 · 대책 · 재발방지 … —
 * **타입의 속성 정의대로**, `PropertyFields` 가 그린다) · 사진(공통 첨부) · 하위 기록(단계별 판정 ·
 * 원인 · 재발방지 · 그림).
 *
 * 고치기 · 하위 기록 만들기는 **코어 객체 API** 로 간다 — 검증 · 권한 · 감사가 그대로다.
 */

import { Pencil, Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { AttachmentList } from '@/modules/files/AttachmentList'
import { ObjectCreateDialog } from '@/modules/objects/ObjectCreateDialog'
import { PropertyFields, propertyText } from '@/modules/objects/PropertyFields'
import type { PropertyValues } from '@/modules/objects/PropertyFields'
import { objectApi } from '@/modules/objects/api'
import { useAuth } from '@/shared/auth/AuthContext'
import { canCreateObject } from '@/shared/auth/roles'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'

import { NotReady } from './NotReady'
import { paths } from './api'
import { refText, useSetup, type Setup, type TypeWithDefs } from './useSetup'

/** 하위 기록의 역할 — 차례가 페이지의 차례다. */
const CHILD_ROLES: { key: string; label: string }[] = [
  { key: 'stage_result', label: '단계별 판정' },
  { key: 'cause', label: '원인' },
  { key: 'prevention', label: '재발방지' },
  { key: 'figure', label: '장표 그림' },
]

function ChildSection({
  setup,
  role,
  label,
  issueId,
}: {
  setup: Setup
  role: string
  label: string
  issueId: string
}) {
  const { user } = useAuth()
  const type = setup.typeOf(role)
  const refKey = setup.fieldOf(role, 'issue_ref')
  const [creating, setCreating] = useState(false)
  const slug = type?.slug ?? null
  const list = useResource(
    () =>
      slug && refKey
        ? objectApi.list(slug, { properties: { [refKey]: issueId }, limit: 200 })
        : Promise.resolve(null),
    [slug, refKey, issueId],
  )
  if (!type || !refKey) return null
  const rows = list.data?.items ?? []
  // 참조 칸 · 글 칸은 표의 열로 — 짧은 칸만. 긴 글(text_long)은 상세에서.
  const columns = type.properties.filter(
    (def) => def.key !== refKey && def.data_type !== 'file' && def.data_type !== 'text_long',
  )
  const canCreate = canCreateObject(user, type.usage)
  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">
          {label} <span className="text-muted-foreground font-normal">{rows.length}</span>
        </h2>
        {canCreate && (
          <Button size="sm" variant="outline" onClick={() => setCreating(true)}>
            <Plus className="size-4" />
            {label} 추가
          </Button>
        )}
      </div>
      {list.error && <ErrorNotice error={list.error} />}
      {rows.length === 0 ? (
        <p className="text-muted-foreground text-sm">아직 없습니다.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>이름</TableHead>
              {columns.map((def) => (
                <TableHead key={def.key}>{def.label}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.id}>
                <TableCell>
                  <Link to={`/o/${type.slug}/${row.id}`} className="hover:underline">
                    {row.label}
                  </Link>
                </TableCell>
                {columns.map((def) => (
                  <TableCell key={def.key} className="text-sm">
                    {def.data_type === 'object_ref'
                      ? refText(row.properties[def.key], row.ref_labels)
                      : propertyText(def, row.properties[def.key])}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {creating && (
        <ObjectCreateDialog
          type={type}
          defs={type.properties}
          title={`${label} 추가`}
          initial={{ values: { [refKey]: issueId } }}
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false)
            list.reload()
          }}
        />
      )}
    </section>
  )
}

export default function IssuePage() {
  const { issueId = '' } = useParams()
  const setup = useSetup()
  const issueType: TypeWithDefs | undefined = setup.typeOf('issue')
  const slug = issueType?.slug ?? null
  const profile = useResource(
    () => (slug ? objectApi.profile(slug, issueId) : Promise.resolve(null)),
    [slug, issueId],
  )
  const [editing, setEditing] = useState(false)
  const [label, setLabel] = useState('')
  const [values, setValues] = useState<PropertyValues>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const row = profile.data?.object
  useEffect(() => {
    if (!row) return
    setLabel(row.label)
    setValues(row.properties)
  }, [row])

  if (setup.error) return <ErrorNotice error={setup.error} />
  if (!setup.loading && !setup.ready) return <NotReady status={setup.status} />
  if (profile.error) return <ErrorNotice error={profile.error} />
  if (!profile.data || !row || !issueType || !slug) return null

  const defs = profile.data.properties_schema
  const reportRef = setup.fieldOf('issue', 'report_ref')
  const statusKey = setup.fieldOf('issue', 'status')
  const reportId = reportRef ? row.properties[reportRef] : null
  const reportIdText = Array.isArray(reportId) ? String(reportId[0] ?? '') : String(reportId ?? '')
  const status = statusKey ? row.properties[statusKey] : null
  const valueDefs = defs.filter((def) => def.data_type !== 'file')

  async function save() {
    setError(null)
    setSaving(true)
    try {
      await objectApi.update(slug!, issueId, { label, properties: values })
      setEditing(false)
      profile.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            {row.label}
            {status != null && status !== '' && <Badge variant="outline">{String(status)}</Badge>}
          </span>
        }
        description={
          reportIdText
            ? `문서: ${row.ref_labels[reportIdText] ?? reportIdText}`
            : '문서 참조가 비어 있습니다 — 목록에 보이지 않습니다.'
        }
        back={
          reportIdText
            ? { to: paths.report(reportIdText), label: '문제점 목록' }
            : { to: paths.reports(), label: '포스트모텀 목록' }
        }
        actions={
          <div className="flex gap-2">
            <Button asChild variant="outline">
              <Link to={`/o/${slug}/${issueId}`}>객체 상세</Link>
            </Button>
            {profile.data.can_edit && !editing && (
              <Button onClick={() => setEditing(true)}>
                <Pencil className="size-4" />
                수정
              </Button>
            )}
          </div>
        }
      />

      {error && <ErrorNotice error={error} />}

      {editing ? (
        <div className="space-y-4 rounded-md border p-4">
          <div className="space-y-1">
            <label className="text-sm font-medium" htmlFor="svinput-label">
              제목
            </label>
            <Input
              id="svinput-label"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              disabled={saving}
            />
          </div>
          <PropertyFields
            defs={valueDefs}
            values={values}
            onChange={setValues}
            disabled={saving}
            view={issueType.form_view}
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setEditing(false)} disabled={saving}>
              취소
            </Button>
            <Button onClick={save} disabled={saving}>
              {saving ? '저장 중…' : '저장'}
            </Button>
          </div>
        </div>
      ) : (
        <dl className="grid gap-x-6 gap-y-3 rounded-md border p-4 sm:grid-cols-2">
          {valueDefs.map((def) => {
            const value = row.properties[def.key]
            const text =
              def.data_type === 'object_ref'
                ? refText(value, row.ref_labels)
                : propertyText(def, value)
            const long = def.data_type === 'text_long'
            return (
              <div key={def.key} className={long ? 'sm:col-span-2' : undefined}>
                <dt className="text-muted-foreground text-xs">{def.label}</dt>
                <dd className={long ? 'text-sm whitespace-pre-wrap' : 'text-sm'}>
                  {text || <span className="text-muted-foreground">—</span>}
                </dd>
              </div>
            )
          })}
        </dl>
      )}

      <section className="space-y-2">
        <h2 className="text-sm font-semibold">사진 · 첨부</h2>
        <AttachmentList
          ownerTable="objects"
          ownerId={issueId}
          workspaceSlug={row.owner_workspace_slug}
          canEdit={profile.data.can_edit}
        />
      </section>

      {CHILD_ROLES.map((one) => (
        <ChildSection key={one.key} setup={setup} role={one.key} label={one.label} issueId={issueId} />
      ))}
    </div>
  )
}
