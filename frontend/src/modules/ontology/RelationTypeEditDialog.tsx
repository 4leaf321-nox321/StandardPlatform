/**
 * 관계 종류 하나 — 생성과 수정을 같은 창이 한다.
 *
 * **여기서 정하는 것이 관계의 의미다.** 코드에 암묵이면 화면도 MCP 도 그 뜻을
 * 알 방법이 없다 — 어느 쪽으로 읽는지, 재귀로 펼치는지, 무엇과 무엇을 이을 수
 * 있는지를 데이터가 들고 있어야 한다.
 */

import { useState } from 'react'

import { ontologyApi } from '@/modules/ontology/api'
import type { Cardinality, ObjectType, RelationType } from '@/modules/ontology/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'

export const CARDINALITY_LABELS: Record<Cardinality, string> = {
  one_to_one: '1 : 1',
  one_to_many: '1 : N',
  many_to_one: 'N : 1',
  many_to_many: 'N : N',
}

const CARDINALITY_HINTS: Record<Cardinality, string> = {
  one_to_one: '양쪽 다 하나씩. 이미 맺힌 것이 있으면 새로 못 맺습니다.',
  one_to_many: '출발 하나가 여럿을 가집니다 — 어셈블리 → 부품.',
  many_to_one: '여럿이 하나를 가리킵니다 — 부품 → 공급사.',
  many_to_many: '제약 없음.',
}

interface Props {
  relation?: RelationType | null
  types: ObjectType[]
  onClose: () => void
  onChanged: () => void
}

export function RelationTypeEditDialog({ relation, types, onClose, onChanged }: Props) {
  const editing = Boolean(relation)

  const [slug, setSlug] = useState(relation?.slug ?? '')
  const [label, setLabel] = useState(relation?.label ?? '')
  const [inverseLabel, setInverseLabel] = useState(relation?.inverse_label ?? '')
  const [description, setDescription] = useState(relation?.description ?? '')
  const [directed, setDirected] = useState(relation?.directed ?? true)
  const [transitive, setTransitive] = useState(relation?.transitive ?? false)
  const [acyclic, setAcyclic] = useState(relation?.acyclic ?? false)
  const [cardinality, setCardinality] = useState<string>(relation?.cardinality ?? 'many_to_many')
  const [src, setSrc] = useState<string[]>(relation?.src_type_slugs ?? [])
  const [dst, setDst] = useState<string[]>(relation?.dst_type_slugs ?? [])

  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  const [removing, setRemoving] = useState(false)

  async function save() {
    setError(null)
    setSaving(true)
    try {
      const body = {
        label,
        inverse_label: inverseLabel,
        description,
        directed,
        transitive,
        // **재귀로 펼치는 관계는 순환을 막아야 한다.** 서버도 막지만, 화면이
        // 먼저 켜 두면 거절당하고 나서 무엇을 고칠지 찾을 일이 없다.
        acyclic: transitive ? true : acyclic,
        cardinality,
        src_type_slugs: src.length > 0 ? src : null,
        dst_type_slugs: dst.length > 0 ? dst : null,
      }
      if (relation) await ontologyApi.updateRelationType(relation.slug, body)
      else await ontologyApi.createRelationType({ slug, ...body })
      onChanged()
      onClose()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <Dialog open onOpenChange={(open) => !open && onClose()}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editing ? `${relation?.label} 수정` : '관계 종류 생성'}</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            {error && <ErrorNotice error={error} />}

            <div className="space-y-1.5">
              <Label htmlFor="rel-slug">slug</Label>
              <Input
                id="rel-slug"
                value={editing ? relation!.slug : slug}
                readOnly={editing}
                disabled={editing}
                placeholder="part_of"
                className="font-mono"
                onChange={(event) => setSlug(event.target.value)}
              />
              {editing && (
                <p className="text-muted-foreground text-xs">
                  <b>바꿀 수 없습니다.</b> 이미 맺힌 관계가 이 값을 문자열로 들고 있어서, 바꾸면 그
                  관계들이 통째로 고아가 됩니다.
                </p>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="rel-label">이름 (→ 방향)</Label>
                <Input
                  id="rel-label"
                  value={label}
                  placeholder="속함"
                  onChange={(event) => setLabel(event.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="rel-inverse">역방향 이름 (← 방향)</Label>
                <Input
                  id="rel-inverse"
                  value={inverseLabel}
                  placeholder="포함"
                  onChange={(event) => setInverseLabel(event.target.value)}
                />
              </div>
            </div>
            <p className="text-muted-foreground text-xs">
              「A가 B에 <b>속함</b>」이면 B쪽 화면에는 「B가 A를 <b>포함</b>」으로 표시됩니다.
              <b> 역방향 이름을 안 적으면</b> 도착 쪽 화면이 말을 못 만들어 slug 를 그대로 보여
              줍니다.
            </p>

            <div className="space-y-1.5">
              <Label htmlFor="rel-desc">설명</Label>
              <Input
                id="rel-desc"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="rel-card">개수 제약</Label>
              <Select value={cardinality} onValueChange={setCardinality}>
                <SelectTrigger id="rel-card">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(CARDINALITY_LABELS) as Cardinality[]).map((one) => (
                    <SelectItem key={one} value={one}>
                      {CARDINALITY_LABELS[one]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">
                {CARDINALITY_HINTS[cardinality as Cardinality]} <b>나중에 조이기는 어렵습니다</b> —
                이미 어긴 데이터가 쌓여 있으면 켤 수가 없습니다.
              </p>
            </div>

            <TypePicker
              title="출발 타입"
              hint="비우면 아무 타입이나 출발점이 됩니다."
              types={types}
              chosen={src}
              onChange={setSrc}
            />
            <TypePicker
              title="도착 타입"
              hint="비우면 아무 타입이나 도착점이 됩니다. 안 정하면 「공급사를 시험함」 같은 말이 안 되는 관계가 남습니다."
              types={types}
              chosen={dst}
              onChange={setDst}
            />

            <div className="space-y-2 border-t pt-3">
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4"
                  checked={directed}
                  onChange={(event) => setDirected(event.target.checked)}
                />
                <span>
                  방향이 있다
                  <span className="text-muted-foreground ml-1 text-xs">
                    끄면 양쪽이 같은 말로 읽힙니다(「비슷함」).
                  </span>
                </span>
              </label>

              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4"
                  checked={transitive}
                  onChange={(event) => {
                    setTransitive(event.target.checked)
                    if (event.target.checked) setAcyclic(true)
                  }}
                />
                <span>
                  재귀로 펼친다 (이행적)
                  <span className="text-muted-foreground ml-1 text-xs">
                    <b>트리와 「아래 것까지 포함」이 이것으로 갈립니다.</b> 「속함」은 참이고
                    「시험함」은 거짓입니다 — 시험의 시험은 시험이 아닙니다.
                  </span>
                </span>
              </label>

              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4"
                  checked={acyclic || transitive}
                  disabled={transitive}
                  onChange={(event) => setAcyclic(event.target.checked)}
                />
                <span>
                  순환 금지
                  <span className="text-muted-foreground ml-1 text-xs">
                    {transitive
                      ? '재귀로 펼치면 반드시 켜야 합니다 — 자기 조상을 자식으로 넣는 순간 트리가 무한히 돕니다.'
                      : '맺을 때 자기 자신으로 돌아오는 길이 생기면 거절합니다.'}
                  </span>
                </span>
              </label>
            </div>
          </div>

          <DialogFooter className="justify-between sm:justify-between">
            {editing ? (
              <Button variant="ghost" onClick={() => setRemoving(true)} disabled={saving}>
                삭제
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button variant="outline" onClick={onClose} disabled={saving}>
                취소
              </Button>
              <Button
                onClick={save}
                disabled={saving || !label.trim() || (!editing && !slug.trim())}
              >
                {editing ? '저장' : '생성'}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {removing && relation && (
        <ConfirmDialog
          open
          destructive
          title={`${relation.label} 관계 종류를 삭제합니다`}
          description={
            <>
              이 종류의 <b>관계 속성 정의도 함께 사라집니다</b> — 안 삭제하면 같은 slug 로 다시 만들
              때 옛 속성이 되살아납니다. 맺힌 관계를 막는 검사는 관계 맺기(2-b)가 생기면 붙습니다.
            </>
          }
          confirmLabel="삭제"
          onConfirm={async () => {
            await ontologyApi.removeRelationType(relation.slug)
            onChanged()
            onClose()
          }}
          onClose={() => setRemoving(false)}
        />
      )}
    </>
  )
}

/** 허용 타입 선택. **비어 있음 = 제약 없음**이라고 말해 준다. */
function TypePicker({
  title,
  hint,
  types,
  chosen,
  onChange,
}: {
  title: string
  hint: string
  types: ObjectType[]
  chosen: string[]
  onChange: (next: string[]) => void
}) {
  return (
    <div className="space-y-1.5">
      <Label>{title}</Label>
      {types.length === 0 ? (
        <p className="text-muted-foreground text-sm">먼저 타입을 만드세요.</p>
      ) : (
        <div className="flex flex-wrap gap-3">
          {types.map((one) => (
            <label key={one.slug} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                className="size-4"
                checked={chosen.includes(one.slug)}
                onChange={() =>
                  onChange(
                    chosen.includes(one.slug)
                      ? chosen.filter((s) => s !== one.slug)
                      : [...chosen, one.slug],
                  )
                }
              />
              {one.label}
            </label>
          ))}
        </div>
      )}
      <p className="text-muted-foreground text-xs">
        {chosen.length === 0 ? <b>제약 없음 — {hint}</b> : hint}
      </p>
    </div>
  )
}
