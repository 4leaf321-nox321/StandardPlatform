/**
 * 장표 목록(ADR 0037) — 새로 고친 것부터. 누구나 만든다.
 *
 * 장표는 틀 하나 + 주제 하나다. 「새 장표」 는 틀을 고르면 그 틀의 주제 타입에서 주제를 고르게 한다
 * — 다른 타입을 고르면 서버가 거절하므로 처음부터 그 타입만 내놓는다(화면 문구는 「선택」).
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ObjectRefPicker } from '@/modules/objects/PropertyFields'
import { slideApi } from '@/modules/slides/api'
import type { Template } from '@/modules/slides/api'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Pagination } from '@/shared/components/Pagination'
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

export default function SlidesPage() {
  const [draft, setDraft] = useState('')
  const [q, setQ] = useState('')
  const [mine, setMine] = useState(false)
  const [offset, setOffset] = useState(0)
  const [creating, setCreating] = useState(false)
  const page = useResource(() => slideApi.list({ q, mine, offset }), [q, mine, offset])
  const templates = useResource(() => slideApi.templates(), [])
  const narrowed = q !== '' || mine

  function search(event: FormEvent) {
    event.preventDefault()
    setOffset(0)
    setQ(draft.trim())
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="장표"
        description="여러 타입의 기록을 틀 한 장에 모아 봅니다. 값은 열 때마다 지금의 기록에서 읽습니다."
        actions={
          <Button
            size="sm"
            onClick={() => setCreating(true)}
            disabled={(templates.data ?? []).length === 0}
          >
            새 장표
          </Button>
        }
      />
      {templates.data && templates.data.length === 0 && (
        <p className="text-muted-foreground text-sm">
          아직 틀이 없습니다 — 틀은 시스템 관리자가 관리 › 장표 틀에서 생성합니다.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <form onSubmit={search} className="flex items-center gap-2">
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="제목 검색"
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
          내 장표
        </label>
      </div>
      <ErrorNotice error={page.error ?? templates.error} />
      {page.data && page.data.items.length === 0 ? (
        <EmptyState
          title={narrowed ? '조건에 맞는 장표가 없습니다' : '아직 장표가 없습니다'}
          hint={
            narrowed
              ? '검색어를 바꾸거나 「내 장표」 를 해제해 보세요.'
              : '「새 장표」 에서 틀과 주제를 선택하면 그 주제의 기록이 틀에 모입니다.'
          }
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>제목</TableHead>
              <TableHead className="w-48">틀</TableHead>
              <TableHead className="w-48">주제</TableHead>
              <TableHead className="w-32">작성자</TableHead>
              <TableHead className="w-44">수정</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(page.data?.items ?? []).map((one) => (
              <TableRow key={one.id}>
                <TableCell>
                  <Link to={`/slides/${one.id}`} className="font-medium hover:underline">
                    {one.title}
                  </Link>
                </TableCell>
                <TableCell className="text-sm">{one.template_name}</TableCell>
                <TableCell className="text-sm">
                  {one.subject ? (
                    <Link
                      to={`/o/${one.subject.type_slug}/${one.subject.id}`}
                      className="hover:underline"
                    >
                      {one.subject.label}
                      {one.subject.deleted && ' (지워짐)'}
                    </Link>
                  ) : (
                    <span className="text-muted-foreground">(없음)</span>
                  )}
                </TableCell>
                <TableCell className="text-sm">{one.author_name}</TableCell>
                <TableCell className="text-muted-foreground text-xs">
                  {shownDateTime(one.edited_at ?? one.created_at)}
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
          unit="장"
        />
      )}
      {creating && templates.data && (
        <NewSlideDialog templates={templates.data} onClose={() => setCreating(false)} />
      )}
    </div>
  )
}

function NewSlideDialog({ templates, onClose }: { templates: Template[]; onClose: () => void }) {
  const navigate = useNavigate()
  const [templateId, setTemplateId] = useState(templates[0]?.id ?? '')
  const [subject, setSubject] = useState<string | null>(null)
  const [title, setTitle] = useState('')
  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  const template = templates.find((one) => one.id === templateId)

  async function submit() {
    if (!template || !subject) return
    setSaving(true)
    setError(null)
    try {
      const made = await slideApi.create({
        template_id: template.id,
        subject_id: subject,
        title: title.trim() || undefined,
      })
      navigate(`/slides/${made.id}`)
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>새 장표</DialogTitle>
          <DialogDescription>
            틀과 주제를 선택하면 그 주제의 기록이 틀의 영역에 모입니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="slide-template">틀</Label>
            <Select
              value={templateId}
              onValueChange={(next) => {
                setTemplateId(next)
                setSubject(null)
              }}
            >
              <SelectTrigger id="slide-template" className="w-full">
                <SelectValue placeholder="틀을 선택하세요" />
              </SelectTrigger>
              <SelectContent>
                {templates.map((one) => (
                  <SelectItem key={one.id} value={one.id}>
                    {one.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {template?.description && (
              <p className="text-muted-foreground text-xs">{template.description}</p>
            )}
          </div>
          {template && (
            <div className="space-y-1.5">
              <Label>주제 — {template.subject_type_label}</Label>
              <ObjectRefPicker
                typeSlug={template.subject_type_slug}
                value={subject}
                onChange={setSubject}
              />
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="slide-title">제목</Label>
            <Input
              id="slide-title"
              value={title}
              maxLength={200}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="비워 두면 「주제 이름 + 틀 이름」"
            />
          </div>
          <ErrorNotice error={error} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            취소
          </Button>
          <Button onClick={submit} disabled={saving || !template || !subject}>
            생성
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
