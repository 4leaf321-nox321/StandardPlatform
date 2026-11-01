/**
 * 관계 하나의 수정 — 근거와 **유효 기간**(ADR 0039).
 *
 * 양끝과 종류는 못 바꾼다 — 그것은 다른 관계다(해제하고 새로 연결한다). **보낸 것만 바뀐다**:
 * 바꾼 칸만 실어 보내고, 비운 기간 칸은 `null`(그쪽 끝이 열린다)로 보낸다. 안 바꾼 칸을 함께
 * 보내면 그 사이 다른 사람이 수정한 값을 이 창의 옛 값이 덮는다.
 */

import { useState } from 'react'

import type { RelationType } from '@/modules/ontology/api'
import { objectApi } from '@/modules/objects/api'
import type { RelatedObject } from '@/modules/objects/api'
import { PeriodFields } from '@/modules/objects/PeriodFields'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'

interface Props {
  typeSlug: string
  objectId: string
  row: RelatedObject
  kind: RelationType | null
  onClose: () => void
  onSaved: () => void
}

export function RelationEditDialog({ typeSlug, objectId, row, kind, onClose, onSaved }: Props) {
  const [note, setNote] = useState(row.evidence_note ?? '')
  const [validFrom, setValidFrom] = useState(row.valid_from ?? '')
  const [validTo, setValidTo] = useState(row.valid_to ?? '')
  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  /** 기간 칸은 유효 기간을 받는 종류에서만 — 이미 기간이 든 관계면(종류를 끈 뒤) 보여는 준다. */
  const periodic = Boolean(kind?.effectivity) || Boolean(row.valid_from || row.valid_to)

  const body: Record<string, unknown> = {}
  if (note !== (row.evidence_note ?? '')) body.evidence_note = note
  if (periodic && validFrom !== (row.valid_from ?? '')) body.valid_from = validFrom || null
  if (periodic && validTo !== (row.valid_to ?? '')) body.valid_to = validTo || null
  const changed = Object.keys(body).length > 0

  async function save() {
    setError(null)
    setSaving(true)
    try {
      await objectApi.updateRelation(typeSlug, objectId, row.relation_id, body)
      onSaved()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {row.label} · {row.object_label} 수정
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {error && <ErrorNotice error={error} />}

          {periodic && (
            <PeriodFields
              from={validFrom}
              to={validTo}
              onFrom={setValidFrom}
              onTo={setValidTo}
              idPrefix="rel-edit"
            />
          )}

          <div className="space-y-1.5">
            <Label htmlFor="rel-edit-note">근거</Label>
            <Input
              id="rel-edit-note"
              value={note}
              placeholder="왜 이렇게 연결했는지 (BOM 기준, 도면 확인 …)"
              onChange={(event) => setNote(event.target.value)}
            />
          </div>
          <p className="text-muted-foreground text-xs">
            연결한 두 객체와 관계 종류는 수정할 수 없습니다 — 다른 관계이므로 해제하고 새로
            연결합니다.
          </p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            취소
          </Button>
          <Button onClick={save} disabled={saving || !changed}>
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
