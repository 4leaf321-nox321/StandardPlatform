/**
 * 「관련 객체」 — **한 객체에서 사방을 본다.**
 *
 * 트리가 관계 **한 종류**로 구조를 훑는 도구라면, 여기는 이 객체에 걸린 것을
 * 종류별로 모아 보여 준다. 둘은 경쟁이 아니라 짝이다 — 트리로는 「이 부품을
 * 공급하는 곳」 이 안 보이고, 여기서는 「이 어셈블리 전체」 가 안 보인다.
 *
 * **양방향을 함께 보여 준다.** 「이것이 가리키는 것」 만 보이면 그 부품을 쓰는
 * 어셈블리를 못 찾고, 그러면 지워도 되는지 알 수 없다.
 *
 * **기준일에 유효한 관계만** 온다(ADR 0039, 비우면 오늘). 유효 기간이 지났거나 아직 시작하지 않은
 * 관계는 「지난 · 예정 관계 포함」 을 선택해야 보이고, 줄마다 배지로 그것을 말한다 — 안 말하면
 * 지난 구성이 지금 구성으로 읽힌다.
 *
 * **열린 제안도 함께 보인다**(ADR 0042) — 흐리게, 「제안」 배지와 근거 · 확신도 · 출처로. 제안은
 * 관계가 아니라서 트리 · 롤업에는 안 나오지만, 이 객체를 보는 사람은 「누가 이것을 이으려 한다」
 * 를 알아야 같은 것을 또 제안하지 않는다. 출발 객체를 수정할 수 있으면 「확정」 · 「거절」, 제안한
 * 사람이면 「철회」. 수정할 수 없는 사람의 단추는 「연결」 대신 「제안으로 추가」 다.
 */

import { useState } from 'react'
import { ArrowLeft, ArrowRight, Link2, Pencil, Plus, X } from 'lucide-react'
import { Link } from 'react-router-dom'

import type { RelationType } from '@/modules/ontology/api'
import { objectApi } from '@/modules/objects/api'
import type { RelatedObject, RelationProposal } from '@/modules/objects/api'
import { AsOfPicker } from '@/modules/objects/AsOfPicker'
import { hasPeriod, periodText } from '@/modules/objects/periods'
import { confidenceText, originText, otherEnd } from '@/modules/objects/proposals'
import { RelationAddDialog } from '@/modules/objects/RelationAddDialog'
import { RelationEditDialog } from '@/modules/objects/RelationEditDialog'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
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
import { useResource } from '@/shared/hooks/useResource'

interface Props {
  typeSlug: string
  objectId: string
  objectLabel: string
  objectTypeSlug: string
  related: RelatedObject[]
  relationTypes: RelationType[]
  canEdit: boolean
  onChanged: () => void
  /** 기준일(`YYYY-MM-DD`, 빈 글자 = 오늘) — 상세가 들고 있다(롤업도 같은 날을 쓴다). */
  asOf?: string
  onAsOf?: (next: string) => void
  /** 지난 · 예정 관계까지 모두 보나(`all_periods`). */
  allPeriods?: boolean
  onAllPeriods?: (next: boolean) => void
  /**
   * 제안된 관계(ADR 0042)를 읽고 「제안으로 추가」 를 세우나 — 원 표를 비추는 타입(부서 · 계정)은
   * 제안의 끝이 되지 않아 끈다. 기본은 켬.
   */
  proposals?: boolean
  /**
   * **이 객체의 참조 칸이 가리키는 묶음은 접어 둔다** — 그 값은 상세의 기본 정보에 링크로 이미 있다.
   * 안 접으면 시장 서비스 한 건이 칸 다섯(모델 · 국가 · 센터 · 불량 · 부품)을 한 줄짜리 상자 다섯으로
   * 또 보여, 정작 이 객체를 가리키는 쪽(역방향)이 화면 아래로 밀린다(2026-11-05).
   */
  foldOwnFields?: boolean
}

/** 관계 종류 · 방향 하나의 묶음 — 관계 줄과 그 방향의 열린 제안. */
interface Group {
  label: string
  outgoing: boolean
  rows: RelatedObject[]
  proposals: RelationProposal[]
}

export function RelatedObjects({
  typeSlug,
  objectId,
  objectLabel,
  objectTypeSlug,
  related,
  relationTypes,
  canEdit,
  onChanged,
  asOf = '',
  onAsOf,
  allPeriods = false,
  onAllPeriods,
  proposals = true,
  foldOwnFields = false,
}: Props) {
  const [adding, setAdding] = useState(false)
  const [showOwnFields, setShowOwnFields] = useState(false)
  const [editing, setEditing] = useState<RelatedObject | null>(null)
  const [cutting, setCutting] = useState<RelatedObject | null>(null)
  const [rejecting, setRejecting] = useState<RelationProposal | null>(null)
  const [withdrawing, setWithdrawing] = useState<RelationProposal | null>(null)
  const [deciding, setDeciding] = useState<string | null>(null)
  const [error, setError] = useState<Error | null>(null)
  /**
   * 이 객체에 걸린 열린 제안 — 상세가 다시 읽힐 때(`related` 가 바뀔 때)마다 함께 다시 읽는다.
   * 관계를 추가 · 해제 · 합친 뒤에도 제안 목록이 그 상태와 맞아야 한다.
   */
  const proposalList = useResource(
    () =>
      proposals
        ? objectApi.relationProposals(typeSlug, objectId)
        : Promise.resolve([] as RelationProposal[]),
    [typeSlug, objectId, proposals, related],
  )
  const open = proposalList.data ?? []
  const kinds = new Map(relationTypes.map((one) => [one.slug, one]))
  /**
   * 기준일 · 지난 관계 고르개를 세우나 — 유효 기간을 받는 관계 종류가 있을 때만. 없는 설치에
   * 세우면 바꿔도 아무것도 안 바뀌고, 그것은 고장으로 읽힌다.
   */
  const periodic =
    relationTypes.some((one) => one.effectivity) ||
    related.some((one) => hasPeriod(one.valid_from, one.valid_to))

  /** 관계 종류별로 묶는다 — **줄이 스물이면 종류별로 안 묶은 목록은 못 읽는다.** 제안도 같은
   * 묶음(종류 · 방향)에 선다 — 따로 두면 같은 공급사에 걸린 관계와 제안이 멀리 떨어져 읽힌다. */
  const groups = new Map<string, Group>()
  const groupOf = (relation: string, outgoing: boolean, label: string): Group => {
    const key = `${relation}:${outgoing ? 'out' : 'in'}`
    const found = groups.get(key) ?? { label, outgoing, rows: [], proposals: [] }
    groups.set(key, found)
    return found
  }
  for (const row of related) groupOf(row.relation, row.outgoing, row.label).rows.push(row)
  for (const one of open) groupOf(one.relation, one.outgoing, one.label).proposals.push(one)
  /** 이 객체의 참조 칸만으로 이뤄진 묶음 — 제안이 걸려 있으면 접지 않는다(결정할 것이 숨는다). */
  const ownFieldsOnly = (group: Group) =>
    group.outgoing &&
    group.proposals.length === 0 &&
    group.rows.length > 0 &&
    group.rows.every((row) => row.stored_as === 'field')
  const folded = foldOwnFields ? [...groups.values()].filter(ownFieldsOnly) : []
  const foldedCount = folded.reduce((sum, group) => sum + group.rows.length, 0)
  const shownGroups = [...groups.entries()].filter(
    ([, group]) => showOwnFields || !folded.includes(group),
  )

  /** 확정 · 거절 · 철회 뒤 — 제안 목록과 상세(관련 객체)를 다시 읽는다. */
  function decided() {
    proposalList.reload()
    onChanged()
  }

  async function accept(one: RelationProposal) {
    setError(null)
    setDeciding(one.id)
    try {
      await objectApi.acceptProposal(one.id)
      decided()
    } catch (caught) {
      // 개수 제약 · 순환 · 기간 겹침 · 끝 삭제(OBJECTS-0141 · 0143) — 서버의 말 그대로. 제안은 남는다.
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setDeciding(null)
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <Link2 className="size-4" />
          관련 객체
          {related.length > 0 && (
            <span className="text-muted-foreground text-sm font-normal tabular-nums">
              {related.length}
            </span>
          )}
        </h2>
        {canEdit ? (
          <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
            <Plus className="mr-1 size-4" />
            연결
          </Button>
        ) : (
          proposals && (
            <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
              <Plus className="mr-1 size-4" />
              제안으로 추가
            </Button>
          )
        )}
      </div>

      {periodic && (onAsOf || onAllPeriods) && (
        <div className="flex flex-wrap items-center gap-3">
          {onAsOf && <AsOfPicker id="related-as-of" value={asOf} onChange={onAsOf} />}
          {onAllPeriods && (
            <label className="text-muted-foreground flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                className="size-4"
                checked={allPeriods}
                onChange={(event) => onAllPeriods(event.target.checked)}
              />
              지난 · 예정 관계 포함
            </label>
          )}
        </div>
      )}

      {error && <ErrorNotice error={error} />}
      {proposalList.error && <ErrorNotice error={proposalList.error} />}

      {related.length === 0 && open.length === 0 ? (
        <EmptyState
          title="이어진 것이 없습니다"
          hint={
            relationTypes.length === 0
              ? '관계 종류가 아직 정의되지 않았습니다 — 관리 → 온톨로지 → 관계 종류에서 먼저 만드세요.'
              : periodic && !allPeriods
                ? `${asOf ? `기준일 ${asOf}` : '오늘'} 유효한 관계가 없습니다 — 「지난 · 예정 관계 포함」 을 선택하면 기간이 지났거나 아직 시작하지 않은 관계도 표시합니다.`
                : canEdit
                  ? '「연결」 로 다른 객체와 이어 보세요.'
                  : '「제안으로 추가」 로 다른 객체와의 관계를 제안할 수 있습니다.'
          }
        />
      ) : (
        <div className="space-y-3">
          {shownGroups.map(([key, group]) => (
            <div key={key} className="rounded-md border">
              <div className="text-muted-foreground flex items-center gap-1.5 border-b px-3 py-1.5 text-xs">
                {/* **방향을 보여 준다.** 어느 쪽으로 읽는지가 말의 뜻을 바꾼다. */}
                {group.outgoing ? (
                  <ArrowRight className="size-3.5" />
                ) : (
                  <ArrowLeft className="size-3.5" />
                )}
                {group.label}
                <span className="tabular-nums">({group.rows.length})</span>
                {group.proposals.length > 0 && (
                  <span className="tabular-nums">· 제안 {group.proposals.length}</span>
                )}
              </div>
              <ul className="divide-y">
                {group.rows.map((row) => (
                  <li key={row.relation_id} className="flex items-center gap-3 px-3 py-2">
                    <div className="min-w-0 flex-1">
                      <Link
                        className="text-sm font-medium hover:underline"
                        to={`/o/${row.object_type_slug}/${row.object_id}`}
                      >
                        {row.object_label}
                      </Link>
                      <span className="text-muted-foreground ml-2 text-xs">
                        {row.object_type_label}
                        {row.object_key && ` · ${row.object_key}`}
                      </span>
                      {/* 지난 · 예정 관계는 배지로 말한다 — 색은 `StatusBadge` 가 정한다. */}
                      {row.period_state && row.period_state !== 'current' && (
                        <StatusBadge kind="period" value={row.period_state} className="ml-2" />
                      )}
                      {/* 유효 기간 — 끝날은 제외라 「전까지」 로 적는다. */}
                      {hasPeriod(row.valid_from, row.valid_to) && (
                        <p className="text-muted-foreground mt-0.5 text-xs tabular-nums">
                          유효 기간: {periodText(row.valid_from, row.valid_to)}
                        </p>
                      )}
                      {/* **왜 이렇게 이었는가.** 근거 없는 연결은 시간이 지나면
                          아무도 못 믿는다 — 확인하려면 처음부터 다시 조사해야 한다. */}
                      {row.evidence_note && (
                        <p className="text-muted-foreground mt-0.5 text-xs">{row.evidence_note}</p>
                      )}
                    </div>
                    {/* 참조 칸으로 이어진 줄은 끊는 단추가 없다 — 그 칸을 고치는 일이다. */}
                    {row.stored_as === 'field' ? (
                      <span
                        className="text-muted-foreground text-xs"
                        title={
                          row.outgoing
                            ? `이 객체의 「${row.label}」 칸에 적힌 것 — 고치려면 「수정」 에서 그 칸을`
                            : `저 객체의 「${row.field_key ?? ''}」 칸이 이 객체를 가리킴`
                        }
                      >
                        칸
                      </span>
                    ) : null}
                    {canEdit && row.stored_as !== 'field' && (
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`${row.object_label} 관계 수정`}
                        onClick={() => setEditing(row)}
                      >
                        <Pencil className="size-4" />
                      </Button>
                    )}
                    {canEdit && row.stored_as !== 'field' && (
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`${row.object_label} 연결 해제`}
                        onClick={() => setCutting(row)}
                      >
                        <X className="size-4" />
                      </Button>
                    )}
                  </li>
                ))}
                {group.proposals.map((one) => {
                  const other = otherEnd(one)
                  return (
                    <li
                      key={`proposal-${one.id}`}
                      className="flex items-start gap-3 px-3 py-2"
                      data-testid="proposal-row"
                    >
                      {/* **흐리게** — 확정 전의 제안은 관계가 아니다(트리 · 롤업에 안 나온다). */}
                      <div className="min-w-0 flex-1 opacity-60">
                        <Link
                          className="text-sm font-medium hover:underline"
                          to={`/o/${other.typeSlug}/${other.id}`}
                        >
                          {other.label}
                        </Link>
                        <span className="text-muted-foreground ml-2 text-xs">
                          {other.typeLabel}
                          {other.key && ` · ${other.key}`}
                        </span>
                        <StatusBadge kind="proposal" value={one.status} className="ml-2" />
                        {hasPeriod(one.valid_from, one.valid_to) && (
                          <p className="text-muted-foreground mt-0.5 text-xs tabular-nums">
                            유효 기간: {periodText(one.valid_from, one.valid_to)}
                          </p>
                        )}
                        <p className="text-muted-foreground mt-0.5 text-xs">
                          근거: {one.evidence_note || '(적지 않음)'}
                        </p>
                        <p className="text-muted-foreground mt-0.5 text-xs">
                          확신도 {confidenceText(one.confidence)} · 출처: {originText(one)}
                        </p>
                      </div>
                      {one.can_decide && (
                        <>
                          <Button
                            size="sm"
                            variant="outline"
                            aria-label={`${other.label} 제안 확정`}
                            disabled={deciding === one.id}
                            onClick={() => accept(one)}
                          >
                            확정
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={`${other.label} 제안 거절`}
                            disabled={deciding === one.id}
                            onClick={() => setRejecting(one)}
                          >
                            거절
                          </Button>
                        </>
                      )}
                      {one.can_withdraw && (
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`${other.label} 제안 철회`}
                          disabled={deciding === one.id}
                          onClick={() => setWithdrawing(one)}
                        >
                          철회
                        </Button>
                      )}
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
          {foldedCount > 0 && (
            <button
              type="button"
              className="text-muted-foreground hover:text-foreground text-xs hover:underline"
              aria-expanded={showOwnFields}
              onClick={() => setShowOwnFields((shown) => !shown)}
            >
              {showOwnFields
                ? '이 객체의 칸이 가리키는 것 접기'
                : `이 객체의 칸이 가리키는 것 ${foldedCount}건 — 위 기본 정보에 링크로 있습니다 · 펼치기`}
            </button>
          )}
        </div>
      )}

      {adding && (
        <RelationAddDialog
          typeSlug={typeSlug}
          objectId={objectId}
          objectLabel={objectLabel}
          objectTypeSlug={objectTypeSlug}
          relationTypes={relationTypes}
          canLink={canEdit}
          onClose={() => setAdding(false)}
          onAdded={() => {
            setAdding(false)
            onChanged()
          }}
          onProposed={() => proposalList.reload()}
        />
      )}

      {rejecting && (
        <RejectDialog
          proposal={rejecting}
          onClose={() => setRejecting(null)}
          onDone={() => {
            setRejecting(null)
            decided()
          }}
        />
      )}

      {withdrawing && (
        <ConfirmDialog
          open
          destructive
          title={`${otherEnd(withdrawing).label} 와(과)의 관계 제안을 철회합니다`}
          description={
            <>
              「{withdrawing.label}」 제안이 삭제됩니다. 거절과 달리 판단 기록이 남지 않아 같은
              제안을 다시 할 수 있습니다. 철회 기록은 변경 이력에 남습니다.
            </>
          }
          confirmLabel="철회"
          onConfirm={async () => {
            await objectApi.withdrawProposal(withdrawing.id)
            decided()
          }}
          onClose={() => setWithdrawing(null)}
        />
      )}

      {editing && (
        <RelationEditDialog
          typeSlug={typeSlug}
          objectId={objectId}
          row={editing}
          kind={kinds.get(editing.relation) ?? null}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            onChanged()
          }}
        />
      )}

      {cutting && (
        <ConfirmDialog
          open
          destructive
          title={`${cutting.object_label} 와(과)의 관계를 끊습니다`}
          description={
            <>
              「{cutting.label}」 연결만 사라집니다
              {hasPeriod(cutting.valid_from, cutting.valid_to) &&
                ` — 유효 기간 ${periodText(cutting.valid_from, cutting.valid_to)} 의 관계 하나`}
              . <b>양쪽 객체는 그대로 남습니다.</b> 해제 기록은 변경 이력에 남습니다.
            </>
          }
          confirmLabel="연결 해제"
          onConfirm={async () => {
            setError(null)
            try {
              await objectApi.removeRelation(typeSlug, objectId, cutting.relation_id)
              onChanged()
            } catch (caught) {
              setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
              throw caught
            }
          }}
          onClose={() => setCutting(null)}
        />
      )}
    </section>
  )
}

/**
 * 거절 — **까닭을 받는다.** 같은 제안이 다시 오면 서버가 이 까닭을 그대로 돌려준다(「언제 누가
 * 왜」). `window.prompt` 는 서버가 거절할 때 그 말을 보일 자리가 없다.
 */
function RejectDialog({
  proposal,
  onClose,
  onDone,
}: {
  proposal: RelationProposal
  onClose: () => void
  onDone: () => void
}) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      await objectApi.rejectProposal(proposal.id, note)
      onDone()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(next) => !next && !busy && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{otherEnd(proposal).label} 와(과)의 관계 제안 거절</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          {error && <ErrorNotice error={error} />}
          <p className="text-muted-foreground text-sm">
            관계는 추가되지 않습니다. 같은 제안이 다시 오면 만들지 않고 이 까닭을 표시합니다.
          </p>
          <div className="space-y-1.5">
            <Label htmlFor="proposal-reject-note">까닭</Label>
            <Input
              id="proposal-reject-note"
              value={note}
              placeholder="왜 이 관계가 아닌지 (단종된 공급사, 다른 부품 …)"
              onChange={(event) => setNote(event.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            취소
          </Button>
          <Button variant="destructive" onClick={submit} disabled={busy}>
            거절
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
