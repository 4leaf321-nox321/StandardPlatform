/**
 * 객체 둘을 잇는다.
 *
 * **고를 수 있는 것만 보여 준다.** 관계 종류가 허용 타입을 정해 두었으면 그
 * 타입의 객체만 고르게 한다 — 아무거나 고르게 해 놓고 저장할 때 거절하면,
 * 사람은 무엇이 잘못됐는지 찾느라 목록을 다시 훑는다. 허용 타입에 **인터페이스**가 적혔으면
 * 그것을 구현한 타입들이 후보다(서버의 끝 검사와 같은 규칙 — `ontology/interfaces.ts`).
 *
 * 관계 종류가 **유효 기간을 받으면** 시작일 · 끝날 칸이 선다(ADR 0039) — 안 받는 종류에 세우면
 * 적어도 서버가 거절하고, 그 거절은 「왜 칸이 있었지」 를 묻게 한다.
 *
 * **제안으로 추가**(ADR 0042) — 출발 객체를 수정할 수 없는 사람은 관계를 바로 만들지 않고 제안을
 * 남긴다(양 끝을 볼 수만 있으면 된다). 수정할 수 있는 사람도 「제안으로 추가」 를 선택할 수 있다
 * (확신이 없어 다른 사람의 검토를 받을 때). 서버는 만들지 않은 까닭(이미 연결됨 · 이미 열린 제안 ·
 * 거절된 적 있음)도 결과로 준다 — 창을 닫지 않고 그것을 보인다. 거절된 적 있으면 「다시 제안」.
 */

import { useEffect, useMemo, useState } from 'react'

import { ontologyApi } from '@/modules/ontology/api'
import type { ObjectType, RelationType } from '@/modules/ontology/api'
import { endAllows, endLabel, endTypeSlugs } from '@/modules/ontology/interfaces'
import { objectApi } from '@/modules/objects/api'
import type { RelationProposalResult } from '@/modules/objects/api'
import { PeriodFields } from '@/modules/objects/PeriodFields'
import { useObjectOptions } from '@/modules/objects/useObjectOptions'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
import { StatusBadge } from '@/shared/components/StatusBadge'
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
  /**
   * 출발 객체를 수정할 수 있나 — 거짓이면 **제안으로만** 추가한다(서버가 같은 판정으로 거절하는 것을
   * 미리 말한다). 기본은 참.
   */
  canLink?: boolean
  /** 제안을 남긴(또는 이미 열린 것을 확인한) 뒤 — 창은 결과를 보이며 열려 있다. 목록만 다시 읽는다. */
  onProposed?: () => void
}

/** 확신도 칸 — 비면 「모름」, 아니면 0 ~ 1. 틀리면 저장 전에 말한다. */
function parseConfidence(raw: string): { value: number | null; problem: string | null } {
  const text = raw.trim()
  if (!text) return { value: null, problem: null }
  const value = Number(text)
  if (!Number.isFinite(value) || value < 0 || value > 1) {
    return { value: null, problem: '확신도는 0 ~ 1 사이의 수입니다(비우면 「모름」).' }
  }
  return { value, problem: null }
}

export function RelationAddDialog({
  typeSlug,
  objectId,
  objectLabel,
  objectTypeSlug,
  relationTypes,
  onClose,
  onAdded,
  canLink = true,
  onProposed,
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
  const [validFrom, setValidFrom] = useState('')
  const [validTo, setValidTo] = useState('')
  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  /** 제안으로 추가하나 — 수정할 수 없으면 늘 그렇다. */
  const [proposing, setProposing] = useState(!canLink)
  const [confidence, setConfidence] = useState('')
  const [result, setResult] = useState<RelationProposalResult | null>(null)
  const asProposal = proposing || !canLink
  const parsedConfidence = parseConfidence(confidence)
  // 정의를 받고 나면 고를 수 있는 관계가 늘 수 있다(끝이 인터페이스인 것) — 고른 것이 목록에
  // 없으면 첫 것으로.
  useEffect(() => {
    if (!usable.some((one) => one.slug === relation)) setRelation(usable[0]?.slug ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [usableKey])

  const kind = usable.find((one) => one.slug === relation) ?? null
  /** 유효 기간을 받는 종류인가 — 아니면 기간 칸을 세우지도 보내지도 않는다. */
  const periodic = Boolean(kind?.effectivity)

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

  async function propose(again = false) {
    if (!target || parsedConfidence.problem) return
    setError(null)
    setSaving(true)
    try {
      const found = await objectApi.proposeRelation(typeSlug, objectId, {
        relation,
        dst_object_id: target.split(':')[1],
        evidence_note: note,
        confidence: parsedConfidence.value,
        origin: 'user',
        again,
        ...(periodic && validFrom ? { valid_from: validFrom } : {}),
        ...(periodic && validTo ? { valid_to: validTo } : {}),
      })
      setResult(found)
      if (found.status === 'created' || found.status === 'open') onProposed?.()
    } catch (caught) {
      setResult(null)
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setSaving(false)
    }
  }

  async function submit() {
    if (!target) return
    if (asProposal) {
      await propose()
      return
    }
    setError(null)
    setSaving(true)
    try {
      await objectApi.addRelation(typeSlug, objectId, {
        relation,
        dst_object_id: target.split(':')[1],
        evidence_note: note,
        // 비운 칸은 안 보낸다 — 열린 끝이다.
        ...(periodic && validFrom ? { valid_from: validFrom } : {}),
        ...(periodic && validTo ? { valid_to: validTo } : {}),
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
          <DialogTitle>
            {asProposal ? `${objectLabel} 에서 관계 제안` : `${objectLabel} 을(를) 잇습니다`}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {error && <ErrorNotice error={error} />}

          {/* 만들지 않은 까닭도 결과다 — 이미 연결됨 · 이미 열린 제안 · 거절된 적 있음. */}
          {result && (
            <div className="space-y-2 rounded-md border p-3 text-sm" role="status">
              <div className="flex items-center gap-2">
                <StatusBadge kind="proposal_result" value={result.status} />
                {result.status === 'open' && result.updated && (
                  <span className="text-muted-foreground text-xs">근거 · 확신도 수정됨</span>
                )}
              </div>
              <p>{result.message}</p>
              {result.status === 'rejected_before' && (
                <Button size="sm" variant="outline" onClick={() => propose(true)} disabled={saving}>
                  다시 제안
                </Button>
              )}
            </div>
          )}

          {canLink && (
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 size-4"
                checked={proposing}
                onChange={(event) => {
                  setProposing(event.target.checked)
                  setResult(null)
                }}
              />
              <span>
                제안으로 추가
                <span className="text-muted-foreground block text-xs">
                  바로 연결하지 않고, 출발 객체를 수정할 수 있는 사람이 확정하도록 남깁니다.
                </span>
              </span>
            </label>
          )}
          {!canLink && (
            <p className="text-muted-foreground text-xs">
              이 객체를 수정할 수 없어 <b>제안</b>으로 추가합니다 — 출발 객체를 수정할 수 있는
              사람이 확정해야 관계가 됩니다. 그 전에는 트리 · 롤업 · 통계에 나오지 않습니다.
            </p>
          )}

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
                  ariaLabel="이을 객체"
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

              {periodic && (
                <PeriodFields
                  from={validFrom}
                  to={validTo}
                  onFrom={setValidFrom}
                  onTo={setValidTo}
                  idPrefix="rel-period"
                />
              )}

              <div className="space-y-1.5">
                <Label htmlFor="rel-note">근거</Label>
                <Input
                  id="rel-note"
                  value={note}
                  placeholder={
                    asProposal
                      ? '어느 문서의 어느 줄 · 왜 그렇게 봤는지'
                      : '왜 이렇게 이었는지 (BOM 기준, 도면 확인 …)'
                  }
                  onChange={(event) => setNote(event.target.value)}
                />
                <p className="text-muted-foreground text-xs">
                  {asProposal ? (
                    <>
                      <b>확정할 사람이 이 근거를 읽고 판단합니다.</b> 확정하면 관계의 근거가 됩니다.
                    </>
                  ) : (
                    <>
                      <b>근거 없는 연결은 시간이 지나면 아무도 못 믿습니다</b> — 맞는지 확인하려면
                      처음부터 다시 조사해야 하기 때문입니다.
                    </>
                  )}
                </p>
              </div>

              {asProposal && (
                <div className="space-y-1.5">
                  <Label htmlFor="rel-confidence">확신도 (선택)</Label>
                  <Input
                    id="rel-confidence"
                    type="number"
                    inputMode="decimal"
                    min={0}
                    max={1}
                    step={0.05}
                    value={confidence}
                    placeholder="0 ~ 1, 비우면 모름"
                    onChange={(event) => setConfidence(event.target.value)}
                  />
                  {parsedConfidence.problem && (
                    <p className="text-destructive text-xs">{parsedConfidence.problem}</p>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            {result ? '닫기' : '취소'}
          </Button>
          <Button
            onClick={submit}
            disabled={
              saving ||
              !target ||
              usable.length === 0 ||
              (asProposal && Boolean(parsedConfidence.problem))
            }
          >
            {asProposal ? '제안으로 추가' : '연결'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
