/**
 * 사이드바 묶음 — **타입보다 먼저 있어야 하는 것.**
 *
 * 묶음이 없으면 타입을 만들어도 사이드바에 서지 않는다. 그래서 두 번째 사이드바의
 * 첫 줄이다.
 */

import { useState } from 'react'

import { GroupEditDialog } from '@/modules/ontology/GroupEditDialog'
import { useOntology } from '@/modules/ontology/OntologyLayout'
import { ontologyApi } from '@/modules/ontology/api'
import type { NavGroupRow } from '@/modules/ontology/api'
import { EmptyState } from '@/shared/components/EmptyState'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'

const AUDIENCE_LABELS: Record<string, string> = {
  everyone: '모두',
  manager: '부서 관리자',
  system_admin: '시스템 관리자',
}

export default function OntologyGroupsPage() {
  const { schema, reload, setError } = useOntology()
  const [editing, setEditing] = useState<string | null>(null)

  const groups = schema?.groups ?? []
  const types = schema?.types ?? []
  const target = groups.find((row) => row.slug === editing) ?? null

  /** 이 묶음에 걸린 타입 이름들. **지우기 전에 무엇이 걸렸는지 말하는 데 쓴다.** */
  function attachedTo(group: NavGroupRow): string[] {
    return types.filter((row) => row.nav_group_slug === group.slug).map((row) => row.label)
  }

  async function create(body: Record<string, unknown>) {
    setError(null)
    try {
      await ontologyApi.createGroup(body)
      reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-muted-foreground text-sm">
        「도메인」 처럼 화면을 묶는 이름입니다. 묶음이 없으면 타입을 만들어도 사이드바에
        서지 않습니다.
      </p>

      <NewGroupForm onSubmit={create} />

      {groups.length === 0 ? (
        <EmptyState
          title="묶음이 없습니다"
          hint="먼저 묶음 하나를 만드세요. 그다음 「타입」 에서 타입을 그 안에 넣습니다."
        />
      ) : (
        <>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>이름</TableHead>
                  <TableHead>slug</TableHead>
                  <TableHead>보이는 대상</TableHead>
                  <TableHead className="text-right">걸린 타입</TableHead>
                  <TableHead className="text-right">순서</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {groups.map((group) => (
                  <TableRow
                    key={group.slug}
                    className="cursor-pointer"
                    onClick={() => setEditing(group.slug)}
                  >
                    <TableCell className="font-medium">
                      {group.label}
                      {!group.is_active && (
                        <span className="text-muted-foreground ml-2 text-xs">사용 안 함</span>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{group.slug}</TableCell>
                    <TableCell>{AUDIENCE_LABELS[group.audience] ?? group.audience}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {attachedTo(group).length}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{group.sort_order}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <p className="text-muted-foreground text-xs">행을 누르면 고치거나 지웁니다.</p>
        </>
      )}

      {target && (
        <GroupEditDialog
          group={target}
          attached={attachedTo(target)}
          onClose={() => setEditing(null)}
          onChanged={reload}
        />
      )}
    </div>
  )
}

function NewGroupForm({ onSubmit }: { onSubmit: (body: Record<string, unknown>) => void }) {
  const [slug, setSlug] = useState('')
  const [label, setLabel] = useState('')

  return (
    <div className="flex flex-wrap items-end gap-3 rounded-md border p-4">
      <div className="space-y-1.5">
        <Label htmlFor="group-slug">slug</Label>
        <Input
          id="group-slug"
          value={slug}
          placeholder="domain"
          className="w-40 font-mono"
          onChange={(event) => setSlug(event.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="group-label">이름</Label>
        <Input
          id="group-label"
          value={label}
          placeholder="도메인"
          className="w-48"
          onChange={(event) => setLabel(event.target.value)}
        />
      </div>
      <Button
        disabled={!slug.trim() || !label.trim()}
        onClick={() => {
          onSubmit({ slug, label })
          setSlug('')
          setLabel('')
        }}
      >
        묶음 만들기
      </Button>
      <p className="text-muted-foreground w-full text-xs">
        slug 는 소문자·숫자·밑줄이고 <b>나중에 바꿀 수 없습니다.</b>
      </p>
    </div>
  )
}
