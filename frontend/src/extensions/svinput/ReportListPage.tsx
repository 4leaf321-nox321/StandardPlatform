/**
 * 포스트모텀 목록 — 문서는 **스파인(코어의 과제 · 모델)** 으로 묶인다. 스파인은 코어 타입이라 확장은
 * 가리키기만 한다(만들지도 고치지도 않는다) — 여기서는 과제를 골라 거르고, 과제 상세는 코어 화면으로.
 * 「새 포스트모텀」 은 코어의 만들기 창(`ObjectCreateDialog`) — 폼은 타입의 속성 정의대로 선다.
 */

import { Plus, Search } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ObjectCreateDialog } from '@/modules/objects/ObjectCreateDialog'
import { ObjectRefPicker } from '@/modules/objects/PropertyFields'
import { objectApi } from '@/modules/objects/api'
import { useAuth } from '@/shared/auth/AuthContext'
import { canCreateObject } from '@/shared/auth/roles'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Pagination } from '@/shared/components/Pagination'
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
import { refText, useSetup } from './useSetup'

const LIMIT = 50

export default function ReportListPage() {
  const { user } = useAuth()
  const setup = useSetup()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [asked, setAsked] = useState('')
  const [spineId, setSpineId] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [creating, setCreating] = useState(false)

  const reportType = setup.typeOf('report')
  const spineType = setup.typeOf('spine')
  const modelKey = setup.fieldOf('report', 'model')
  const modelDef = reportType?.properties.find((def) => def.key === modelKey)
  /** 과제를 고를 수 있나 — 스파인 타입이 정해졌고 문서의 과제 칸이 그 타입을 가리키는 참조 칸일 때. */
  const spineFilter = Boolean(
    spineType && modelKey && modelDef?.data_type === 'object_ref' && modelDef.ref_type_slug === spineType.slug,
  )
  const slug = reportType?.slug ?? null
  const list = useResource(
    () =>
      slug
        ? objectApi.list(slug, {
            q: asked || undefined,
            properties: spineFilter && spineId && modelKey ? { [modelKey]: spineId } : {},
            limit: LIMIT,
            offset,
          })
        : Promise.resolve(null),
    [slug, asked, spineFilter, spineId, modelKey, offset],
  )

  if (setup.error) return <ErrorNotice error={setup.error} />
  if (!setup.loading && !setup.ready) return <NotReady status={setup.status} />

  const rows = list.data?.items ?? []
  const canCreate = canCreateObject(user, reportType?.usage ?? 'axis')

  return (
    <div className="space-y-4">
      <PageHeader
        title="포스트모텀"
        description="과제 · 모델마다 한 장 — 열면 문제점 목록, 문제점을 열면 한 페이지입니다. 결과요약은 자동으로 집계됩니다."
        actions={
          canCreate &&
          reportType && (
            <Button onClick={() => setCreating(true)}>
              <Plus className="size-4" />새 포스트모텀
            </Button>
          )
        }
      />
      <div className="flex flex-wrap items-end gap-2">
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            setOffset(0)
            setAsked(query.trim())
          }}
        >
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="제목으로 찾기"
            aria-label="찾기"
            className="w-64"
          />
          <Button type="submit" variant="outline">
            <Search className="size-4" />
            찾기
          </Button>
        </form>
        {spineFilter && spineType && (
          <div className="w-72 space-y-1 text-sm">
            <span className="text-muted-foreground text-xs">{spineType.label}(스파인)으로 거르기</span>
            <ObjectRefPicker
              typeSlug={spineType.slug}
              value={spineId}
              onChange={(next) => {
                setOffset(0)
                setSpineId(next || null)
              }}
            />
          </div>
        )}
        {spineId && (
          <Button variant="ghost" size="sm" onClick={() => setSpineId(null)}>
            거르기 해제
          </Button>
        )}
      </div>

      {list.error && <ErrorNotice error={list.error} />}
      {list.data && rows.length === 0 && (
        <EmptyState
          title={
            asked || spineId ? '조건에 맞는 포스트모텀이 없습니다' : '아직 포스트모텀이 없습니다'
          }
          hint={
            asked || spineId
              ? '다른 말로 찾거나 거르기를 비워 보세요.'
              : '「새 포스트모텀」 으로 만들거나, 허브의 적재(묶음 · 데이터 소스)를 기다립니다.'
          }
        />
      )}
      {rows.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>제목</TableHead>
              {modelKey && <TableHead>{modelDef?.label ?? '과제 · 모델'}</TableHead>}
              <TableHead>부서</TableHead>
              <TableHead>바뀐 때</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => {
              const modelId = modelKey ? row.properties[modelKey] : null
              const modelText = modelKey ? refText(modelId, row.ref_labels) : ''
              return (
                <TableRow key={row.id}>
                  <TableCell>
                    <Link to={paths.report(row.id)} className="font-medium hover:underline">
                      {row.label}
                    </Link>
                  </TableCell>
                  {modelKey && (
                    <TableCell>
                      {spineFilter && spineType && typeof modelId === 'string' && modelId ? (
                        // 스파인은 코어의 것 — 상세는 코어 화면에서.
                        <Link to={`/o/${spineType.slug}/${modelId}`} className="hover:underline">
                          {modelText}
                        </Link>
                      ) : (
                        modelText || '—'
                      )}
                    </TableCell>
                  )}
                  <TableCell>{row.owner_workspace_name ?? '전역'}</TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {row.updated_at.slice(0, 10)}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
      {list.data && (
        <Pagination
          total={list.data.total}
          limit={list.data.limit}
          offset={list.data.offset}
          onChange={setOffset}
          unit="장"
        />
      )}

      {creating && reportType && (
        <ObjectCreateDialog
          type={reportType}
          defs={reportType.properties}
          title="새 포스트모텀"
          // 과제를 골라 둔 채 만들면 그 과제의 문서로 시작한다 — 스파인은 고르기만 한다.
          initial={spineFilter && spineId && modelKey ? { values: { [modelKey]: spineId } } : undefined}
          onClose={() => setCreating(false)}
          onCreated={(made) => {
            setCreating(false)
            navigate(paths.report(made.id))
          }}
          onOpen={(id) => navigate(paths.report(id))}
        />
      )}
    </div>
  )
}
