/**
 * 게시판 목록(ADR 0033) — **고정한 글이 먼저, 그다음 새 글부터.**
 *
 * 공지는 운영자가 전하는 말이고, 여기는 누구나 쓰는 자리다. 줄마다 댓글 · 첨부 수를 보여 —
 * 열어 보기 전에 「답이 달렸나 · 파일이 있나」 를 안다.
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { MessageSquare, Paperclip, Pin } from 'lucide-react'

import { boardApi } from '@/modules/board/api'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Pagination } from '@/shared/components/Pagination'
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
import { shownDateTime } from '@/shared/lib/datetime'

export default function BoardPage() {
  // 통합 검색의 「게시판에서 모두 보기」 가 `?q=` 로 들어온다 — 그 말로 거른 목록부터.
  const [params] = useSearchParams()
  const [draft, setDraft] = useState(params.get('q') ?? '')
  const [q, setQ] = useState(params.get('q')?.trim() ?? '')
  const [mine, setMine] = useState(false)
  const [offset, setOffset] = useState(0)
  const page = useResource(() => boardApi.list({ q, mine, offset }), [q, mine, offset])
  const narrowed = q !== '' || mine

  function search(event: FormEvent) {
    event.preventDefault()
    setOffset(0)
    setQ(draft.trim())
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="게시판"
        description="설치 전체가 함께 쓰는 글 목록입니다. 누구나 글과 댓글을 작성하고 파일을 첨부할 수 있습니다 — 운영 안내는 「공지」 에 있습니다."
        actions={
          <Button asChild size="sm">
            <Link to="/board/new">글 작성</Link>
          </Button>
        }
      />
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <form onSubmit={search} className="flex items-center gap-2">
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="제목 · 본문 검색"
            aria-label="검색어"
            className="h-8 w-60"
          />
          <Button type="submit" size="sm" variant="outline">
            검색
          </Button>
        </form>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={mine}
            onChange={(event) => {
              setOffset(0)
              setMine(event.target.checked)
            }}
          />
          내 글
        </label>
      </div>
      <ErrorNotice error={page.error} />
      {page.data && page.data.items.length === 0 ? (
        narrowed ? (
          <EmptyState
            title="조건에 맞는 글이 없습니다"
            hint="검색어를 바꾸거나 「내 글」 을 해제해 보세요."
          />
        ) : (
          <EmptyState
            title="아직 글이 없습니다"
            hint="첫 글을 작성해 보세요 — 물음 · 자료 공유 · 안내 무엇이든 됩니다."
          />
        )
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>제목</TableHead>
              <TableHead className="w-40">작성자</TableHead>
              <TableHead className="w-44">작성</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(page.data?.items ?? []).map((one) => (
              <TableRow key={one.id}>
                <TableCell>
                  <div className="flex flex-wrap items-center gap-2">
                    {one.pinned && (
                      <Badge variant="secondary" className="gap-1">
                        <Pin className="size-3" />
                        고정
                      </Badge>
                    )}
                    <Link to={`/board/${one.id}`} className="font-medium hover:underline">
                      {one.title}
                    </Link>
                    {one.comment_count > 0 && (
                      <span
                        className="text-muted-foreground inline-flex items-center gap-1 text-xs"
                        title="댓글"
                      >
                        <MessageSquare className="size-3" />
                        {one.comment_count}
                      </span>
                    )}
                    {one.attachment_count > 0 && (
                      <span
                        className="text-muted-foreground inline-flex items-center gap-1 text-xs"
                        title="첨부"
                      >
                        <Paperclip className="size-3" />
                        {one.attachment_count}
                      </span>
                    )}
                  </div>
                </TableCell>
                <TableCell className="text-sm">{one.author_name}</TableCell>
                <TableCell className="text-muted-foreground text-xs">
                  {shownDateTime(one.created_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {page.data && (
        <Pagination
          total={page.data.total}
          limit={page.data.limit}
          offset={page.data.offset}
          onChange={setOffset}
          unit="개"
        />
      )}
    </div>
  )
}
