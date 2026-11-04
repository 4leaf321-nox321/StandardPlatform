/**
 * 문제점 목록 — **첫 메뉴.** 관리 단위는 문서가 아니라 문제점이다(ADR 0051 7절). 문서와 상관없이
 * 횡단으로 보고, 설정된 축(일정 상태 · 고르기 칸)으로 거른다. 모든 거르기(부품 · 고장모드 · PLM 번호 …)는
 * 코어 목록 화면(`/o/<타입>`)이 더 넓게 한다 — 여기는 자주 쓰는 것만.
 */

import { ExternalLink, Plus, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ObjectCreateDialog } from '@/modules/objects/ObjectCreateDialog'
import { objectApi } from '@/modules/objects/api'
import { useAuth } from '@/shared/auth/AuthContext'
import { canCreateObject } from '@/shared/auth/roles'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Pagination } from '@/shared/components/Pagination'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
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
const ALL = '__all__'

export default function IssueListPage() {
  const { user } = useAuth()
  const setup = useSetup()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [asked, setAsked] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [offset, setOffset] = useState(0)
  const [creating, setCreating] = useState(false)

  const issueType = setup.typeOf('issue')
  const statusKey = setup.fieldOf('issue', 'status')
  const slug = issueType?.slug ?? null
  /** 거르기 칸 — 고르기(enum) 칸 전부. 상태 칸이 고르기면 여기 함께 든다. */
  const enumDefs = useMemo(
    () => (issueType?.properties ?? []).filter((def) => def.data_type === 'enum' && !def.multi),
    [issueType],
  )
  const columns = useMemo(
    () =>
      (issueType?.properties ?? []).filter(
        (def) =>
          (def.data_type === 'enum' || def.data_type === 'object_ref' || def.key === statusKey) &&
          !def.multi,
      ),
    [issueType, statusKey],
  )
  const list = useResource(
    () =>
      slug
        ? objectApi.list(slug, {
            q: asked || undefined,
            properties: filters,
            limit: LIMIT,
            offset,
          })
        : Promise.resolve(null),
    [slug, asked, JSON.stringify(filters), offset],
  )

  if (setup.error) return <ErrorNotice error={setup.error} />
  if (!setup.loading && !setup.ready) return <NotReady status={setup.status} />

  const rows = list.data?.items ?? []
  const canCreate = issueType ? canCreateObject(user, issueType.usage) : false

  return (
    <div className="space-y-4">
      <PageHeader
        title="문제점"
        description="관리 단위입니다 — 포스트모텀 · 심사회는 여기 있는 문제점을 참조해 구성합니다. 현상 · 대책 · 단계별 판정 · 부품 · 고장모드 · PLM 번호로 추적합니다."
        actions={
          <div className="flex gap-2">
            {slug && (
              <Button asChild variant="outline">
                <Link to={`/o/${slug}`}>
                  <ExternalLink className="size-4" />
                  모든 거르기 · 통계
                </Link>
              </Button>
            )}
            {canCreate && issueType && (
              <Button onClick={() => setCreating(true)}>
                <Plus className="size-4" />새 문제점
              </Button>
            )}
          </div>
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
            placeholder="현상 · 제목 · PLM 번호로 찾기"
            aria-label="찾기"
            className="w-72"
          />
          <Button type="submit" variant="outline">
            <Search className="size-4" />
            찾기
          </Button>
        </form>
        {enumDefs.map((def) => (
          <label key={def.key} className="space-y-1 text-sm">
            <span className="text-muted-foreground text-xs">{def.label}</span>
            <Select
              value={filters[def.key] ?? ALL}
              onValueChange={(value) => {
                setOffset(0)
                setFilters((prev) => {
                  const next = { ...prev }
                  if (value === ALL) delete next[def.key]
                  else next[def.key] = value
                  return next
                })
              }}
            >
              <SelectTrigger className="w-40" aria-label={def.label}>
                <SelectValue placeholder="전체" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>전체</SelectItem>
                {(def.enum_options ?? []).map((option) => (
                  <SelectItem key={option} value={option}>
                    {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
        ))}
      </div>

      {list.error && <ErrorNotice error={list.error} />}
      {list.data && rows.length === 0 && (
        <EmptyState
          title={
            asked || Object.keys(filters).length
              ? '조건에 맞는 문제점이 없습니다'
              : '아직 문제점이 없습니다'
          }
          hint={
            asked || Object.keys(filters).length
              ? '다른 말로 찾거나 거르기를 비워 보세요.'
              : '「새 문제점」 으로 만들거나, 허브의 적재(묶음 · 데이터 소스 · MCP)를 기다립니다.'
          }
        />
      )}
      {rows.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>문제점</TableHead>
              {columns.map((def) => (
                <TableHead key={def.key}>{def.label}</TableHead>
              ))}
              <TableHead>바뀐 때</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.id}>
                <TableCell>
                  <Link to={paths.issue(row.id)} className="font-medium hover:underline">
                    {row.label}
                  </Link>
                </TableCell>
                {columns.map((def) => {
                  const value = row.properties[def.key]
                  const text =
                    def.data_type === 'object_ref'
                      ? refText(value, row.ref_labels)
                      : value == null
                        ? ''
                        : String(value)
                  return (
                    <TableCell key={def.key} className="text-sm">
                      {def.key === statusKey && text ? (
                        <Badge variant="outline">{text}</Badge>
                      ) : (
                        text || '—'
                      )}
                    </TableCell>
                  )
                })}
                <TableCell className="text-muted-foreground text-sm">
                  {row.updated_at.slice(0, 10)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {list.data && (
        <Pagination
          total={list.data.total}
          limit={list.data.limit}
          offset={list.data.offset}
          onChange={setOffset}
        />
      )}

      {creating && issueType && (
        <ObjectCreateDialog
          type={issueType}
          defs={issueType.properties}
          title="새 문제점"
          onClose={() => setCreating(false)}
          onCreated={(made) => {
            setCreating(false)
            navigate(paths.issue(made.id))
          }}
          onOpen={(id) => navigate(paths.issue(id))}
        />
      )}
    </div>
  )
}
