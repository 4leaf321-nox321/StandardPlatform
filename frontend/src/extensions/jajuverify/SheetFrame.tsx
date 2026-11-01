/**
 * 회의 한 장의 틀 — 읽기 · 오류 · 돌아갈 곳 · 인쇄 · 확정 · 회의 삭제, 그리고 머리(제목 · 일자 · 부서 ·
 * 작성자 · 「줄 8 · 작성 완료 5」 · 확정). 표와 머리의 배치는 **종류마다의 화면**(심사회 · 포스트모텀 ·
 * 설명회)이 정한다 — 지금은 같은 표를 쓰지만 종류마다 따로 바꿀 수 있게 나눠 둔다(사용자 결정).
 *
 * 제목 · 일자 · 회의 삭제 · 확정은 관리(`can_manage` — 만든 사람 · 부서 관리자 · 시스템 관리자)다.
 * **확정되면** 머리에 「확정 · 날짜 · 이름」 이 서고 아무도 수정하지 못한다 — 확정 해제까지.
 */

import { useId, useState } from 'react'
import type { ReactNode } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import { Lock, LockOpen, Printer, Trash2 } from 'lucide-react'

import { KINDS, paths } from '@/extensions/jajuverify/api'
import type { MeetingKind, Presence, Sheet } from '@/extensions/jajuverify/api'
import { EditingTag, progressText } from '@/extensions/jajuverify/SheetTable'
import { editorsAt, useMeetingSheet } from '@/extensions/jajuverify/useMeetingSheet'
import type { SheetActions } from '@/extensions/jajuverify/useMeetingSheet'
import { AutoSaveText } from '@/shared/components/AutoSaveText'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ConflictBox } from '@/shared/components/ConflictBox'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { SaveState } from '@/shared/components/SaveState'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { useAutoSave } from '@/shared/hooks/useAutoSave'
import { shownDate } from '@/shared/lib/datetime'

export interface SheetView {
  sheet: Sheet
  actions: SheetActions
  fresh: string | null
  /** 지금 칸을 수정하는 다른 사람(30초 안). */
  presence: Presence[]
}

/** 머리를 수정할 수 있나 — 관리이고 확정 전. */
function headEditable(sheet: Sheet): boolean {
  return sheet.can_manage && !sheet.meeting.confirmed_at
}

/** 회의 일자 — 고르면 곧 저장한다. */
function HeldOn({ sheet, actions }: { sheet: Sheet; actions: SheetActions }) {
  const id = useId()
  const meeting = sheet.meeting
  const [focused, setFocused] = useState(false)
  const auto = useAutoSave<string>(
    meeting.held_on ?? '',
    (next, base) => actions.saveHeldOn(next, base),
    { delay: 300, hold: focused },
  )
  if (!headEditable(sheet)) return <span>{meeting.held_on ?? '—'}</span>
  return (
    <span className="inline-flex flex-col gap-1">
      <span className="inline-flex flex-wrap items-center gap-1.5">
        <label htmlFor={id} className="sr-only">
          일자
        </label>
        <Input
          id={id}
          type="date"
          value={auto.draft}
          onChange={(event) => auto.change(event.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => {
            setFocused(false)
            void auto.flush()
          }}
          className="h-7 w-auto px-1.5 text-sm print:hidden"
        />
        <span className="hidden print:inline">{auto.draft || '—'}</span>
        <SaveState status={auto.status} error={auto.error} onRetry={() => void auto.flush()} />
      </span>
      {auto.conflict && (
        <ConflictBox
          noun="값"
          by={auto.conflict.by}
          at={auto.conflict.at}
          theirs={auto.conflict.current || '—'}
          mine={auto.draft || '—'}
          onTakeTheirs={auto.takeTheirs}
          onKeepMine={() => void auto.keepMine()}
        />
      )}
    </span>
  )
}

/**
 * 머리 — 「자주검증 심사회」 띠, 제목(그 자리에서 수정), 일자 · 부서 · 작성자 · 진행 · 확정. `heading`
 * 은 종류마다의 화면이 주는 표의 제목(「심사 결과」 …).
 */
export function SheetHead({ sheet, actions, presence, heading }: SheetView & { heading: string }) {
  const meeting = sheet.meeting
  const editors = editorsAt(presence, { field: 'title' })
  return (
    <header className="mb-4 space-y-2 border-b pb-3 print:mb-2 print:pb-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">자주검증 {meeting.kind_label}</Badge>
        <span className="text-muted-foreground text-xs">{heading}</span>
        {meeting.confirmed_at && (
          <span className="inline-flex items-center gap-1.5 text-xs">
            <StatusBadge kind="sheet" value="confirmed" />
            <span className="text-muted-foreground">
              {shownDate(meeting.confirmed_at)}
              {meeting.confirmed_by_name ? ` · ${meeting.confirmed_by_name}` : ''}
            </span>
          </span>
        )}
        <EditingTag names={editors} />
      </div>
      <AutoSaveText
        value={meeting.title}
        label="제목"
        singleLine
        required
        readOnly={!headEditable(sheet)}
        className="text-2xl font-semibold tracking-tight"
        notice={editors.length ? `${editors.join(', ')} 님이 제목을 수정하는 중입니다.` : undefined}
        onFocusChange={(focused) => (focused ? actions.focus({ field: 'title' }) : actions.blur())}
        onSave={(next, base) => actions.saveTitle(next, base)}
      />
      <dl className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
        <div className="flex items-center gap-2">
          <dt className="text-muted-foreground">일자</dt>
          <dd>
            <HeldOn sheet={sheet} actions={actions} />
          </dd>
        </div>
        <div className="flex items-center gap-2">
          <dt className="text-muted-foreground">부서</dt>
          <dd>{meeting.workspace_name ?? '—'}</dd>
        </div>
        <div className="flex items-center gap-2">
          <dt className="text-muted-foreground">작성자</dt>
          <dd>{meeting.author_name ?? '—'}</dd>
        </div>
        <div className="flex items-center gap-2 print:hidden">
          <dt className="text-muted-foreground">진행</dt>
          <dd className="tabular-nums">{progressText(sheet.rows)}</dd>
        </div>
      </dl>
    </header>
  )
}

export function useMeetingId(): string {
  return useParams<{ meetingId: string }>().meetingId ?? ''
}

/** 한 장의 틀 — 종류가 주소와 다르면 맞는 주소로 보낸다(즐겨찾기한 주소가 죽지 않게). */
export function SheetFrame({
  kind,
  children,
}: {
  kind: MeetingKind
  children: (view: SheetView) => ReactNode
}) {
  const meetingId = useMeetingId()
  const { sheet, loading, error, notice, fresh, presence, actions } = useMeetingSheet(meetingId)
  const [removing, setRemoving] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [failed, setFailed] = useState<Error | null>(null)
  const navigate = useNavigate()

  if (loading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }
  if (!sheet) {
    return (
      <div className="space-y-4">
        <PageHeader
          title={`자주검증 ${KINDS[kind]}`}
          back={{ to: paths.list(kind), label: `${KINDS[kind]} 목록` }}
        />
        <ErrorNotice error={error} />
      </div>
    )
  }
  if (sheet.meeting.kind !== kind) {
    return <Navigate to={paths.sheet(sheet.meeting.kind, sheet.meeting.id)} replace />
  }

  const confirmed = Boolean(sheet.meeting.confirmed_at)
  const writing = sheet.rows.filter((one) => !one.done).length

  async function settle(job: () => Promise<void>) {
    setFailed(null)
    try {
      await job()
    } catch (caught) {
      setFailed(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  return (
    // 본문 폭 전체를 쓴다(사용자 요청, 2026-10-31) — 다섯 열에 글과 사진이 함께 서는 표다.
    <div className="w-full">
      {/* 인쇄는 가로 한 장 — 이 화면에 있는 동안만 쓰는 용지 설정이다. */}
      <style>{'@page { size: A4 landscape; margin: 12mm; }'}</style>
      <div className="print:hidden">
        <PageHeader
          title={`자주검증 ${KINDS[kind]}`}
          back={{ to: paths.list(kind), label: `${KINDS[kind]} 목록` }}
          actions={
            <>
              <Button variant="outline" size="sm" onClick={() => window.print()}>
                <Printer className="size-4" />
                인쇄
              </Button>
              {sheet.can_manage &&
                (confirmed ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void settle(actions.unconfirm)}
                  >
                    <LockOpen className="size-4" />
                    확정 해제
                  </Button>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      writing > 0 ? setConfirming(true) : void settle(actions.confirm)
                    }
                  >
                    <Lock className="size-4" />
                    확정
                  </Button>
                ))}
              {sheet.can_manage && !confirmed && (
                <Button variant="outline" size="sm" onClick={() => setRemoving(true)}>
                  <Trash2 className="size-4" />
                  회의 삭제
                </Button>
              )}
            </>
          }
        />
      </div>
      <ErrorNotice error={failed ?? error} className="mb-3 print:hidden" />
      {notice && (
        <p role="status" className="text-muted-foreground mb-3 text-xs print:hidden">
          {notice}
        </p>
      )}
      {children({ sheet, actions, fresh, presence })}
      {confirming && (
        <ConfirmDialog
          open
          title="회의 확정"
          description={`작성 중 ${writing}개가 남았습니다 — 그래도 확정할까요? 확정하면 확정을 해제할 때까지 아무도 표 · 묶음 · 사진 · 제목 · 일자를 수정할 수 없습니다.`}
          confirmLabel="확정"
          onConfirm={actions.confirm}
          onClose={() => setConfirming(false)}
        />
      )}
      {removing && (
        <ConfirmDialog
          open
          title="회의 삭제"
          description={`「${sheet.meeting.title}」 을(를) 삭제합니다 — 묶음 ${sheet.groups.length}개와 표의 줄 ${sheet.rows.length}개, 그 사진이 목록과 화면에서 사라집니다. 사진 파일은 지우지 않고 남깁니다.`}
          confirmLabel="삭제"
          destructive
          onConfirm={async () => {
            await actions.removeMeeting()
            navigate(paths.list(kind))
          }}
          onClose={() => setRemoving(false)}
        />
      )}
    </div>
  )
}
