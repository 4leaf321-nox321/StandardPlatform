/**
 * 객체 둘을 잇는다.
 *
 * **고를 수 있는 것만 보여 준다.** 관계 종류가 허용 타입을 정해 두었으면 그
 * 타입의 객체만 고르게 한다 — 아무거나 고르게 해 놓고 저장할 때 거절하면,
 * 사람은 무엇이 잘못됐는지 찾느라 목록을 다시 훑는다. 허용 타입에 **인터페이스**가 적혔으면
 * 그것을 구현한 타입들이 후보다(서버의 끝 검사와 같은 규칙 — `ontology/interfaces.ts`).
 */

import { useEffect, useMemo, useState } from 'react'

import { ontologyApi } from '@/modules/ontology/api'
import type { ObjectType, RelationType } from '@/modules/ontology/api'
import { endAllows, endLabel, endTypeSlugs } from '@/modules/ontology/interfaces'
import { objectApi } from '@/modules/objects/api'
import { useObjectOptions } from '@/modules/objects/useObjectOptions'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
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
import { useResource } from '@/shared/hooks/useResource'

interface Props {
  typeSlug: string
  objectId: string
  objectLabel: string
  objectTypeSlug: string
  relationTypes: RelationType[]
  onClose: () => void
  onAdded: () => void
}

export function RelationAddDialog({
  typeSlug,
  objectId,
  objectLabel,
  objectTypeSlug,
  relationTypes,
  onClose,
  onAdded,
}: Props) {
  /**
   * 이 객체가 **출발점이 될 수 있는** 관계만 고르게 한다.
   *
   * 안 거르면 목록에 뜬 것을 골랐다가 저장할 때 거절당하고, 그 거절은 「왜 여기
   * 있었지」 를 묻게 만든다.
   */
  const schema = useResource(() => ontologyApi.schema(), [])
  const allTypes: ObjectType[] = schema.data?.types ?? []
  const interfaces = schema.data?.interfaces ?? []
  const usable = relationTypes.filter(
    (one) => one.is_active && endAllows(one.src_type_slugs, objectTypeSlug, interfaces),
  )
  const usableKey = usable.map((one) => one.slug).join(',')

  const [relation, setRelation] = useState(usable[0]?.slug ?? '')
  const [target, setTarget] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  // 정의를 받고 나면 고를 수 있는 관계가 늘 수 있다(끝이 인터페이스인 것) — 고른 것이 목록에
  // 없으면 첫 것으로.
  useEffect(() => {
    if (!usable.some((one) => one.slug === relation)) setRelation(usable[0]?.slug ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [usableKey])

  const kind = usable.find((one) => one.slug === relation) ?? null

  /** 도착점이 될 수 있는 타입들. 안 정해 뒀으면 전부(원 표는 빼고). */
  const allowed = endTypeSlugs(kind?.dst_type_slugs, interfaces)
  const targetTypes = allowed
    ? allTypes.filter((one) => allowed.includes(one.slug))
    : allTypes.filter((one) => one.kind_class !== 'system')

  // 후보는 **서버가 찾는다** — 도착 타입마다 목록 API 로. 관계를 바꾸면 고른 것을 비운다
  // (다른 타입의 객체가 남아 있으면 「허용 타입」 검사에서 거절되고, 그 이유는 안 보인다).
  const targetKey = targetTypes.map((one) => one.slug).join(',')
  const sources = useMemo(
    () => targetTypes.map((one) => ({ slug: one.slug, label: one.label })),
    // targetTypes 는 매 렌더 새 배열이라 slug 목록으로 비교한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [targetKey],
  )
  const found = useObjectOptions(sources, { exclude: objectId, value: target, composite: true })
  useEffect(() => {
    setTarget(null)
  }, [targetKey])

  async function submit() {
    if (!target) return
    setError(null)
    setSaving(true)
    try {
      await objectApi.addRelation(typeSlug, objectId, {
        relation,
        dst_object_id: target.split(':')[1],
        evidence_note: note,
      })
      onAdded()
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
          <DialogTitle>{objectLabel} 을(를) 잇습니다</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {error && <ErrorNotice error={error} />}

          {!schema.data && !schema.error ? (
            <p className="text-muted-foreground text-sm">불러오는 중…</p>
          ) : usable.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              이 타입에서 출발할 수 있는 관계 종류가 없습니다. 관리 → 온톨로지 → 관계 종류에서
              만들거나, <b>허용 출발 타입</b>에 이 타입을 추가하세요.
            </p>
          ) : (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="rel-kind">관계</Label>
                <Select value={relation} onValueChange={setRelation}>
                  <SelectTrigger id="rel-kind">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {usable.map((one) => (
                      <SelectItem key={one.slug} value={one.slug}>
                        {one.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {kind && (
                  <p className="text-muted-foreground text-xs">
                    {objectLabel} 이(가) 고른 객체에 「{kind.label}」.
                    {kind.inverse_label && ` 저쪽에서는 「${kind.inverse_label}」으로 읽힙니다.`}
                  </p>
                )}
              </div>

              <div className="space-y-1.5">
                <Label>이을 객체</Label>
                <SearchablePicker
                  options={found.options}
                  pinned={found.pinned}
                  total={found.total}
                  loading={found.loading}
                  onQueryChange={found.setQuery}
                  value={target}
                  onChange={setTarget}
                  placeholder="객체를 선택하세요"
                  searchPlaceholder="이름·식별자로 검색"
                  emptyText={
                    found.failed ? '고를 것을 불러오지 못했습니다' : '고를 객체가 없습니다'
                  }
                />
                <p className="text-muted-foreground text-xs">
                  {!allowed
                    ? '아무 타입이나 선택할 수 있습니다.'
                    : targetTypes.length > 0
                      ? `${targetTypes.map((one) => one.label).join('·')} 만 선택할 수 있습니다.`
                      : `도착은 ${(kind?.dst_type_slugs ?? [])
                          .map((one) => endLabel(one, allTypes, interfaces))
                          .join('·')} 인데, 해당하는 타입이 없어 이을 수 없습니다.`}{' '}
                  남의 부서 것은 목록에 없습니다.
                </p>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="rel-note">근거</Label>
                <Input
                  id="rel-note"
                  value={note}
                  placeholder="왜 이렇게 이었는지 (BOM 기준, 도면 확인 …)"
                  onChange={(event) => setNote(event.target.value)}
                />
                <p className="text-muted-foreground text-xs">
                  <b>근거 없는 연결은 시간이 지나면 아무도 못 믿습니다</b> — 맞는지 확인하려면
                  처음부터 다시 조사해야 하기 때문입니다.
                </p>
              </div>
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            취소
          </Button>
          <Button onClick={submit} disabled={saving || !target || usable.length === 0}>
            연결
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
