/**
 * 자주검증 회의 목록 — 종류마다(심사회 · 포스트모텀 · 설명회). 일자가 새것부터, 제목 · 묶음 이름 · 줄의
 * 글로 찾고, 누르면 그 한 장으로. 「새 회의」 는 짧은 창(종류 · 제목 · 일자 · 부서). 상태 열은 「확정」
 * 또는 「작성 중 5/8」(작성 완료한 줄 / 줄).
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Plus, Search } from 'lucide-react'

import { KINDS, jvApi, paths } from '@/extensions/jajuverify/api'
import type { MeetingKind } from '@/extensions/jajuverify/api'
import { MeetingCreateDialog } from '@/extensions/jajuverify/MeetingCreateDialog'
import { useAuth } from '@/shared/auth/AuthContext'
import { canCreateObject } from '@/shared/auth/roles'
import { EmptyState } from '@/shared/components/EmptyState'
import { StatusBadge } from '@/shared/components/StatusBadge'
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

const DESCRIPTIONS: Record<MeetingKind, string> = {
  review: '심사회의 한 장 — 항목마다 문제점 · 개선대책 및 일정 · 비고를 표로 적고 사진을 붙입니다.',
  postmortem: '포스트모텀의 한 장 — 무엇이 문제였고 무엇을 고치는지 표로 적고 사진을 붙입니다.',
  briefing: '설명회의 한 장 — 설명 항목마다 문제점 · 개선대책 · 비고를 표로 적고 사진을 붙입니다.',
}

export function MeetingListPage({ kind }: { kind: MeetingKind }) {
  const { user } = useAuth()
  const [query, setQuery] = useState('')
  const [asked, setAsked] = useState('')
  const [offset, setOffset] = useState(0)
  const [creating, setCreating] = useState(false)
  const list = useResource(() => jvApi.meetings(kind, { q: asked, offset }), [kind, asked, offset])
  // 회의는 구성원이 만든다(기록과 같은 규칙) — 소속이 없으면 단추를 세우지 않는다.
  const canCreate = canCreateObject(user, 'log')
  const rows = list.data?.items ?? []

  return (
    <div className="space-y-4">
      <PageHeader
        title={`자주검증 ${KINDS[kind]}`}
        description={DESCRIPTIONS[kind]}
        actions={
          canCreate && (
            <Button onClick={() => setCreating(true)}>
              <Plus className="size-4" />새 {KINDS[kind]}
            </Button>
          )
        }
      />

      <form
        className="flex max-w-md items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          setOffset(0)
          setAsked(query.trim())
        }}
      >
        <Input
          value={query}
          aria-label="검색"
          placeholder="제목 · 묶음 이름 · 표의 글로 검색"
          onChange={(event) => setQuery(event.target.value)}
        />
        <Button type="submit" variant="outline" size="icon" aria-label="검색 실행">
          <Search className="size-4" />
        </Button>
      </form>

      <ErrorNotice error={list.error} />

      {list.data && rows.length === 0 ? (
        <EmptyState
          title={
            asked
              ? `「${asked}」 에 맞는 ${KINDS[kind]}가 없습니다`
              : `아직 ${KINDS[kind]}가 없습니다`
          }
          hint={
            asked
              ? '검색어를 줄이거나 지우면 전부 보입니다. 제목 · 묶음 이름 · 표의 글(항목 · 문제점 · 개선대책 · 비고)에서 찾습니다.'
              : canCreate
                ? `「새 ${KINDS[kind]}」 로 첫 회의를 만듭니다.`
                : '소속 부서가 없어 회의를 만들 수 없습니다 — 부서 관리자에게 소속을 요청하세요.'
          }
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-28">일자</TableHead>
              <TableHead>제목</TableHead>
              <TableHead className="w-40">부서</TableHead>
              <TableHead className="w-32">작성자</TableHead>
              <TableHead className="w-16 text-right">줄</TableHead>
              <TableHead className="w-32">상태</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((one) => (
              <TableRow key={one.id}>
                <TableCell className="tabular-nums">{one.held_on ?? '—'}</TableCell>
                <TableCell>
                  <Link to={paths.sheet(kind, one.id)} className="font-medium hover:underline">
                    {one.title}
                  </Link>
                </TableCell>
                <TableCell>{one.workspace_name ?? '—'}</TableCell>
                <TableCell>{one.author_name ?? '—'}</TableCell>
                <TableCell className="text-right tabular-nums">{one.row_count}</TableCell>
                <TableCell>
                  {one.confirmed_at ? (
                    <StatusBadge kind="sheet" value="confirmed" />
                  ) : (
                    <span className="inline-flex items-center gap-1.5">
                      <StatusBadge kind="sheet" value="draft" />
                      <span className="text-muted-foreground text-xs tabular-nums">
                        {one.done_count}/{one.row_count}
                      </span>
                    </span>
                  )}
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

      {creating && <MeetingCreateDialog kind={kind} onClose={() => setCreating(false)} />}
    </div>
  )
}

export default MeetingListPage
