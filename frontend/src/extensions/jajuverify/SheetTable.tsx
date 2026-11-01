/**
 * 회의 표 — 항목 · 문제점 · 개선대책 · 일정 · 비고(열의 정의는 서버의 `cells.COLUMNS`). **문서처럼**
 * 그 자리에서 고치고, **여럿이 나눠 쓴다**(2026-10-31 사용자 결정 — 줄은 따로 고치되 묶음으로 하나로
 * 묶인다). 화면 폭 전체를 쓴다.
 *
 * - 글 칸은 글과 사진이 차례대로 선다(`PhotoTextCell` — 사진은 글 사이에서 옮기고 폭을 고른다). 멈추거나
 *   떠나면 자동 저장한다. 그사이 남이 같은 칸을 저장했으면 칸 아래 상자가 묻는다(상대 글 사용 · 내
 *   글로 덮어쓰기). 남이 지금 그 칸을 수정하고 있으면 「김OO 수정 중」.
 * - 일정 칸은 단계 목록(`ScheduleCell` — 날짜 · 이름).
 * - **묶음** — 묶음 없는 줄이 맨 위(머리줄 없이 — 묶음이 하나도 없으면 1단계의 표와 같다), 그 아래
 *   묶음이 차례대로. 묶음 머리줄에 이름(그 자리에서 수정) · 「줄 4 · 작성 완료 3」 · 접기(화면에서만) ·
 *   위로 · 아래로 · 줄 추가 · 묶음 삭제. 줄 앞의 체크 상자로 여럿을 고르면 「새 묶음」 · 「묶음으로
 *   이동」 · 「묶음에서 제외」 막대가 선다. 옮기기는 상대 동작이라 남의 줄이 늘고 줄어도 부딪치지 않는다.
 * - 줄의 왼쪽에 번호 · 작성 완료 · 위로 · 아래로(묶음 안에서) · 아래에 줄 추가 · 묶음으로 이동 · 삭제.
 * - 수정할 수 없으면(그 부서의 구성원이 아니거나 확정된 회의면) 글과 사진만 서고, 표 아래에 그
 *   까닭을 적는다.
 * - 인쇄하면 단추 · 체크 상자 · 상태 꼬리표가 빠지고 글 · 사진만 한 장 보고서처럼 선다(`print:`). 묶음
 *   머리줄은 띠로, 접힌 묶음도 인쇄에서는 편다. 사진은 고른 폭 그대로.
 */

import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  CircleCheck,
  FolderInput,
  FolderPlus,
  Plus,
  Trash2,
  X,
} from 'lucide-react'

import { CELL_KEYS } from '@/extensions/jajuverify/api'
import type { CellKey, ColumnKey, Group, Presence, Row, Sheet } from '@/extensions/jajuverify/api'
import { plainText } from '@/extensions/jajuverify/cellText'
import { GroupNameDialog, GroupRemoveDialog } from '@/extensions/jajuverify/GroupDialogs'
import { PhotoTextCell } from '@/extensions/jajuverify/PhotoTextCell'
import { ScheduleCell } from '@/extensions/jajuverify/ScheduleCell'
import { editorsAt } from '@/extensions/jajuverify/useMeetingSheet'
import type { SheetActions } from '@/extensions/jajuverify/useMeetingSheet'
import { AutoSaveText } from '@/shared/components/AutoSaveText'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { Button } from '@/shared/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import { cn } from '@/shared/lib/utils'

/** 열의 너비 — 번호 칸 다음 다섯. 화면 폭 전체를 쓰고(사용자 요청, 2026-10-31), 일정은 값이라 좁다. */
const WIDTHS: Record<ColumnKey, string> = {
  topic: '14%',
  problem: '28%',
  action: '28%',
  schedule: '12%',
  remark: '18%',
}

function rowName(row: Row, index: number): string {
  const head = plainText(row.topic).split('\n')[0]
  return head ? `「${head.slice(0, 40)}」` : `${index + 1}번 줄`
}

function photoCount(row: Row): number {
  return CELL_KEYS.reduce((sum, key) => sum + row.photos[key].length, 0)
}

/** 「줄 4 · 작성 완료 3」 — 회의 머리 · 묶음 머리가 함께 쓴다. */
export function progressText(rows: Row[]): string {
  return `줄 ${rows.length} · 작성 완료 ${rows.filter((one) => one.done).length}`
}

/** 「김OO 수정 중」 꼬리표 — 인쇄에는 안 나온다. */
export function EditingTag({ names, className }: { names: string[]; className?: string }) {
  if (names.length === 0) return null
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded bg-sky-500/10 px-1.5 py-0.5 text-[11px] font-medium text-sky-700 print:hidden dark:text-sky-400',
        className,
      )}
    >
      <span className="size-1.5 animate-pulse rounded-full bg-sky-500" />
      {names.join(', ')} 수정 중
    </span>
  )
}

/** 남이 수정하는 칸에 들어갔을 때의 안내 — 막지는 않는다. */
function editingNotice(names: string[]): string | undefined {
  return names.length ? `${names.join(', ')} 님이 이 칸을 수정하는 중입니다.` : undefined
}

/** 칸 하나 — 글 열은 글 + 사진(`PhotoTextCell`), 일정 열은 단계 목록(`ScheduleCell`). */
function SheetCell({
  row,
  index,
  cell,
  canWrite,
  actions,
  autoFocus,
  presence,
}: {
  row: Row
  index: number
  cell: Sheet['cells'][number]
  canWrite: boolean
  actions: SheetActions
  autoFocus: boolean
  presence: Presence[]
}) {
  const name = `${index + 1}번 줄 ${cell.label}`
  const editors = editorsAt(presence, { row_id: row.id, field: cell.key })
  const focus = (focused: boolean) =>
    focused ? actions.focus({ row_id: row.id, field: cell.key }) : actions.blur()
  return (
    <td className="border-border border p-1.5 align-top">
      <div className="flex flex-col gap-1.5">
        {editors.length > 0 && <EditingTag names={editors} className="self-start" />}
        {cell.kind === 'schedule' ? (
          <ScheduleCell
            value={row.schedule}
            label={name}
            canWrite={canWrite}
            notice={editingNotice(editors)}
            onFocusChange={focus}
            onSave={(next, base) => actions.saveSchedule(row, next, base)}
          />
        ) : (
          <PhotoTextCell
            value={row[cell.key as CellKey]}
            photos={row.photos[cell.key as CellKey]}
            ownerId={row.id}
            field={cell.key}
            label={name}
            canWrite={canWrite}
            placeholder={canWrite ? cell.label : undefined}
            autoFocus={autoFocus}
            notice={editingNotice(editors)}
            onFocusChange={focus}
            onSave={(next, base) => actions.saveCell(row, cell.key as CellKey, next, base)}
            onPhotosChanged={actions.photosChanged}
          />
        )}
      </div>
    </td>
  )
}

function Control({
  label,
  onClick,
  disabled,
  children,
  danger,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  children: ReactNode
  danger?: boolean
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'text-muted-foreground rounded p-0.5 disabled:opacity-30',
        danger ? 'hover:text-destructive' : 'hover:text-foreground hover:bg-muted',
      )}
    >
      {children}
    </button>
  )
}

/** 줄을 다른 묶음으로 — 줄 메뉴 · 선택 막대가 함께 쓴다. */
function MoveMenu({
  label,
  groups,
  from,
  onMove,
  children,
}: {
  label: string
  groups: Group[]
  /** 지금 묶음 — 목록에서 뺀다(undefined 면 여럿이라 다 보인다). */
  from?: string | null
  onMove: (groupId: string | null) => void
  children: ReactNode
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>{label}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {groups
          .filter((one) => one.id !== from)
          .map((one, index) => (
            <DropdownMenuItem key={one.id} onSelect={() => onMove(one.id)}>
              {one.title || `${index + 1}번 묶음`}
            </DropdownMenuItem>
          ))}
        {from !== null && (
          <DropdownMenuItem onSelect={() => onMove(null)}>묶음 없음</DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** 표의 한 줄 — 번호 칸(선택 · 작성 완료 · 단추)과 칸 넷. */
function RowLine({
  row,
  index,
  sheet,
  actions,
  autoFocus,
  presence,
  first,
  last,
  selected,
  onSelect,
  adding,
  full,
  onAdd,
  onRemove,
  hiddenOnScreen,
}: {
  row: Row
  index: number
  sheet: Sheet
  actions: SheetActions
  autoFocus: boolean
  presence: Presence[]
  first: boolean
  last: boolean
  selected: boolean
  onSelect: (on: boolean) => void
  adding: boolean
  full: boolean
  onAdd: (after: Row) => void
  onRemove: () => void
  /** 접힌 묶음의 줄 — 화면에서는 숨고 인쇄에는 나온다. */
  hiddenOnScreen: boolean
}) {
  const canWrite = sheet.can_write
  const n = index + 1
  const doneTitle = row.done
    ? `작성 완료${row.done_by_name ? ` — ${row.done_by_name}` : ''}`
    : '작성 중'
  return (
    <tr
      className={cn('group break-inside-avoid', hiddenOnScreen && 'hidden print:table-row')}
      data-done={row.done || undefined}
    >
      <td
        className={cn(
          'border-border border px-0.5 py-1.5 text-center align-top',
          row.done && 'bg-emerald-500/5 print:bg-transparent',
        )}
      >
        {canWrite && (
          <input
            type="checkbox"
            aria-label={`${n}번 줄 선택`}
            checked={selected}
            onChange={(event) => onSelect(event.target.checked)}
            className="accent-primary mb-0.5 size-3.5 print:hidden"
          />
        )}
        <div
          className="text-muted-foreground text-xs tabular-nums"
          title={row.updated_by_name ? `마지막 수정 — ${row.updated_by_name}` : undefined}
        >
          {n}
        </div>
        {canWrite ? (
          <button
            type="button"
            aria-label={`${n}번 줄 작성 완료`}
            aria-pressed={row.done}
            title={doneTitle}
            onClick={() => void actions.setDone(row, !row.done)}
            className={cn(
              'mt-0.5 rounded p-0.5 print:hidden',
              row.done ? 'text-emerald-600' : 'text-muted-foreground/50 hover:text-emerald-600',
            )}
          >
            <CircleCheck className="size-4" />
          </button>
        ) : (
          row.done && (
            <span title={doneTitle} className="mt-0.5 inline-block text-emerald-600 print:hidden">
              <CircleCheck className="size-4" aria-label={`${n}번 줄 작성 완료`} />
            </span>
          )
        )}
        {canWrite && (
          <div className="mt-1 flex flex-col items-center gap-0.5 opacity-40 group-focus-within:opacity-100 group-hover:opacity-100 print:hidden">
            <Control
              label={`${n}번 줄 위로`}
              disabled={first}
              onClick={() => void actions.move(row, 'up')}
            >
              <ArrowUp className="size-3.5" />
            </Control>
            <Control
              label={`${n}번 줄 아래로`}
              disabled={last}
              onClick={() => void actions.move(row, 'down')}
            >
              <ArrowDown className="size-3.5" />
            </Control>
            <Control
              label={`${n}번 줄 아래에 줄 추가`}
              disabled={adding || full}
              onClick={() => onAdd(row)}
            >
              <Plus className="size-3.5" />
            </Control>
            {sheet.groups.length > 0 && (
              <MoveMenu
                label={`${n}번 줄을 옮길 묶음`}
                groups={sheet.groups}
                from={row.group_id}
                onMove={(groupId) => void actions.moveRows([row.id], groupId)}
              >
                <button
                  type="button"
                  aria-label={`${n}번 줄 묶음으로 이동`}
                  title="묶음으로 이동"
                  className="text-muted-foreground hover:text-foreground hover:bg-muted rounded p-0.5"
                >
                  <FolderInput className="size-3.5" />
                </button>
              </MoveMenu>
            )}
            <Control label={`${n}번 줄 삭제`} danger onClick={onRemove}>
              <Trash2 className="size-3.5" />
            </Control>
          </div>
        )}
      </td>
      {sheet.cells.map((cell) => (
        <SheetCell
          key={cell.key}
          row={row}
          index={index}
          cell={cell}
          canWrite={canWrite}
          actions={actions}
          autoFocus={autoFocus && cell.key === 'topic'}
          presence={presence}
        />
      ))}
    </tr>
  )
}

/** 묶음 머리줄 — 이름 · 진행 · 접기 · 위로 · 아래로 · 줄 추가 · 묶음 삭제. 인쇄에는 띠로. */
function GroupHead({
  group,
  index,
  rows,
  sheet,
  actions,
  presence,
  collapsed,
  onToggle,
  first,
  last,
  adding,
  full,
  onAdd,
  onRemove,
}: {
  group: Group
  index: number
  rows: Row[]
  sheet: Sheet
  actions: SheetActions
  presence: Presence[]
  collapsed: boolean
  onToggle: () => void
  first: boolean
  last: boolean
  adding: boolean
  full: boolean
  onAdd: () => void
  onRemove: () => void
}) {
  const canWrite = sheet.can_write
  const n = index + 1
  const editors = editorsAt(presence, { group_id: group.id, field: 'title' })
  return (
    <tr className="bg-muted/50 break-inside-avoid print:bg-transparent">
      <td
        colSpan={sheet.cells.length + 1}
        className="border-border border border-y-2 px-2 py-1.5 print:border-y-2 print:border-black"
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <button
            type="button"
            aria-label={`${n}번 묶음 ${collapsed ? '펼치기' : '접기'}`}
            aria-expanded={!collapsed}
            title={collapsed ? '펼치기' : '접기'}
            onClick={onToggle}
            className="text-muted-foreground hover:text-foreground rounded p-0.5 print:hidden"
          >
            {collapsed ? <ChevronRight className="size-4" /> : <ChevronDown className="size-4" />}
          </button>
          <div className="min-w-[12rem] flex-1">
            <AutoSaveText
              value={group.title}
              label={`${n}번 묶음 이름`}
              singleLine
              required
              readOnly={!canWrite}
              placeholder="묶음 이름"
              className="text-sm font-semibold"
              notice={editingNotice(editors)}
              onFocusChange={(focused) =>
                focused ? actions.focus({ group_id: group.id, field: 'title' }) : actions.blur()
              }
              onSave={(next, base) => actions.renameGroup(group, next, base)}
            />
          </div>
          <EditingTag names={editors} />
          <span className="text-muted-foreground text-xs tabular-nums print:hidden">
            {progressText(rows)}
          </span>
          {canWrite && (
            <span className="flex items-center gap-0.5 print:hidden">
              <Control
                label={`${n}번 묶음 위로`}
                disabled={first}
                onClick={() => void actions.moveGroup(group, 'up')}
              >
                <ArrowUp className="size-3.5" />
              </Control>
              <Control
                label={`${n}번 묶음 아래로`}
                disabled={last}
                onClick={() => void actions.moveGroup(group, 'down')}
              >
                <ArrowDown className="size-3.5" />
              </Control>
              <Control label={`${n}번 묶음에 줄 추가`} disabled={adding || full} onClick={onAdd}>
                <Plus className="size-3.5" />
              </Control>
              <Control label={`${n}번 묶음 삭제`} danger onClick={onRemove}>
                <Trash2 className="size-3.5" />
              </Control>
            </span>
          )}
        </div>
      </td>
    </tr>
  )
}

export function SheetTable({
  sheet,
  actions,
  fresh,
  presence = [],
}: {
  sheet: Sheet
  actions: SheetActions
  /** 방금 더한 줄 — 그 줄의 항목 칸에 들어간다. */
  fresh: string | null
  /** 지금 칸을 수정하는 다른 사람. */
  presence?: Presence[]
}) {
  const [removing, setRemoving] = useState<{ row: Row; index: number } | null>(null)
  const [removingGroup, setRemovingGroup] = useState<{ group: Group; rows: Row[] } | null>(null)
  const [naming, setNaming] = useState<string[] | null>(null)
  const [adding, setAdding] = useState(false)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const canWrite = sheet.can_write
  const full = sheet.rows.length >= sheet.max_rows

  // 묶음 없는 줄이 맨 위, 그 아래 묶음이 차례대로 — 번호는 표 전체로 이어 매긴다.
  const sections = useMemo(() => {
    const live = new Set(sheet.groups.map((one) => one.id))
    const loose = sheet.rows.filter((one) => !one.group_id || !live.has(one.group_id))
    return [
      { group: null as Group | null, rows: loose },
      ...sheet.groups.map((group) => ({
        group: group as Group | null,
        rows: sheet.rows.filter((one) => one.group_id === group.id),
      })),
    ]
  }, [sheet.rows, sheet.groups])
  const numbered = useMemo(() => {
    const out = new Map<string, number>()
    sections.flatMap((one) => one.rows).forEach((row, index) => out.set(row.id, index))
    return out
  }, [sections])
  // 그사이 사라진 줄은 선택에서 뺀다(남이 지웠거나 옮겼다).
  const chosen = sheet.rows.filter((one) => picked.has(one.id)).map((one) => one.id)

  async function add(after: Row | null, groupId?: string | null) {
    setAdding(true)
    try {
      await actions.addRow(after, groupId)
    } finally {
      setAdding(false)
    }
  }

  function select(rowId: string, on: boolean) {
    setPicked((now) => {
      const next = new Set(now)
      if (on) next.add(rowId)
      else next.delete(rowId)
      return next
    })
  }

  function toggle(groupId: string) {
    setCollapsed((now) => {
      const next = new Set(now)
      if (next.has(groupId)) next.delete(groupId)
      else next.add(groupId)
      return next
    })
  }

  async function moveChosen(groupId: string | null) {
    await actions.moveRows(chosen, groupId)
    setPicked(new Set())
  }

  const empty = sheet.rows.length === 0 && sheet.groups.length === 0

  return (
    <div className="space-y-2">
      {canWrite && chosen.length > 0 && (
        <div
          role="toolbar"
          aria-label="선택한 줄"
          className="bg-muted/60 sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-md border px-3 py-1.5 text-sm print:hidden"
        >
          <span className="font-medium">{chosen.length}줄 선택됨</span>
          <Button size="sm" variant="outline" className="h-7" onClick={() => setNaming(chosen)}>
            <FolderPlus className="size-3.5" />새 묶음
          </Button>
          {sheet.groups.length > 0 && (
            <MoveMenu
              label="선택한 줄을 옮길 묶음"
              groups={sheet.groups}
              onMove={(groupId) => void moveChosen(groupId)}
            >
              <Button size="sm" variant="outline" className="h-7">
                <FolderInput className="size-3.5" />
                묶음으로 이동
              </Button>
            </MoveMenu>
          )}
          {chosen.some((id) => sheet.rows.find((one) => one.id === id)?.group_id) && (
            <Button
              size="sm"
              variant="outline"
              className="h-7"
              onClick={() => void moveChosen(null)}
            >
              묶음에서 제외
            </Button>
          )}
          <Button size="sm" variant="ghost" className="h-7" onClick={() => setPicked(new Set())}>
            <X className="size-3.5" />
            선택 해제
          </Button>
        </div>
      )}

      <table className="w-full table-fixed border-collapse text-sm print:text-[10pt]">
        <colgroup>
          <col className="w-11" />
          {sheet.cells.map((cell) => (
            <col key={cell.key} style={{ width: WIDTHS[cell.key] }} />
          ))}
        </colgroup>
        <thead>
          <tr className="bg-muted/60 print:bg-transparent">
            <th className="border-border border px-1 py-1.5 text-xs font-medium">
              <span className="sr-only">번호</span>
            </th>
            {sheet.cells.map((cell) => (
              <th
                key={cell.key}
                scope="col"
                className="border-border border px-2 py-1.5 text-left text-sm font-semibold"
              >
                {cell.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {empty && (
            <tr>
              <td
                colSpan={sheet.cells.length + 1}
                className="border-border text-muted-foreground border px-3 py-8 text-center text-sm"
              >
                {canWrite
                  ? '아직 줄이 없습니다 — 아래 「줄 추가」 로 시작합니다.'
                  : '아직 줄이 없습니다.'}
              </td>
            </tr>
          )}
          {/* 한 목록으로 그린다(묶음마다 감싸지 않는다) — 남이 줄을 다른 묶음으로 옮겨도 그 줄이 다시
              만들어지지 않게(치고 있던 칸의 커서 · 글이 그대로 남는다). */}
          {sections.flatMap(({ group, rows }) => {
            const groupIndex = group ? sheet.groups.indexOf(group) : -1
            const folded = group ? collapsed.has(group.id) : false
            const head = group
              ? [
                  <GroupHead
                    key={`group-${group.id}`}
                    group={group}
                    index={groupIndex}
                    rows={rows}
                    sheet={sheet}
                    actions={actions}
                    presence={presence}
                    collapsed={folded}
                    onToggle={() => toggle(group.id)}
                    first={groupIndex === 0}
                    last={groupIndex === sheet.groups.length - 1}
                    adding={adding}
                    full={full}
                    onAdd={() => void add(null, group.id)}
                    onRemove={() => setRemovingGroup({ group, rows })}
                  />,
                ]
              : []
            const blank =
              group && rows.length === 0
                ? [
                    <tr
                      key={`empty-${group.id}`}
                      className={cn('print:hidden', folded && 'hidden')}
                    >
                      <td
                        colSpan={sheet.cells.length + 1}
                        className="border-border text-muted-foreground border px-3 py-3 text-center text-xs"
                      >
                        이 묶음에 줄이 없습니다
                        {canWrite &&
                          ' — 머리줄의 「+」 로 줄을 추가하거나, 줄을 선택해 「묶음으로 이동」 합니다.'}
                      </td>
                    </tr>,
                  ]
                : []
            return [
              ...head,
              ...blank,
              ...rows.map((row, at) => {
                const index = numbered.get(row.id) ?? 0
                return (
                  <RowLine
                    key={row.id}
                    row={row}
                    index={index}
                    sheet={sheet}
                    actions={actions}
                    autoFocus={fresh === row.id}
                    presence={presence}
                    first={at === 0}
                    last={at === rows.length - 1}
                    selected={picked.has(row.id)}
                    onSelect={(on) => select(row.id, on)}
                    adding={adding}
                    full={full}
                    onAdd={(after) => void add(after)}
                    onRemove={() => setRemoving({ row, index })}
                    hiddenOnScreen={folded}
                  />
                )
              }),
            ]
          })}
        </tbody>
      </table>

      <div className="flex flex-wrap items-center gap-3 print:hidden">
        {canWrite ? (
          <>
            <Button
              variant="outline"
              size="sm"
              disabled={adding || full}
              onClick={() => void add(null)}
            >
              <Plus className="size-4" />줄 추가
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={sheet.groups.length >= sheet.max_groups}
              onClick={() => setNaming([])}
            >
              <FolderPlus className="size-4" />
              묶음 추가
            </Button>
            <p className="text-muted-foreground text-xs">
              {full
                ? `한 장의 표는 ${sheet.max_rows}줄까지입니다 — 회의를 나누세요.`
                : '칸을 클릭하면 바로 수정하고 자동 저장합니다. 사진은 칸의 글을 클릭한 뒤 붙여넣기(Ctrl+V)하거나 칸으로 끌어다 놓습니다. 줄 앞의 체크 상자로 여러 줄을 선택해 묶음으로 묶습니다.'}
            </p>
          </>
        ) : (
          sheet.write_refusal && (
            <p className="text-muted-foreground text-xs">{sheet.write_refusal}</p>
          )
        )}
      </div>

      {removing && (
        <ConfirmDialog
          open
          title="줄 삭제"
          description={`${rowName(removing.row, removing.index)} 줄을 삭제합니다 — 이 줄의 글 네 칸${
            photoCount(removing.row) ? `과 사진 ${photoCount(removing.row)}장` : ''
          }이 표에서 사라집니다. 사진 파일은 지우지 않고 남깁니다.`}
          confirmLabel="삭제"
          destructive
          onConfirm={() => actions.removeRow(removing.row)}
          onClose={() => setRemoving(null)}
        />
      )}
      {removingGroup && (
        <GroupRemoveDialog
          group={removingGroup.group}
          rows={removingGroup.rows.length}
          photos={removingGroup.rows.reduce((sum, one) => sum + photoCount(one), 0)}
          onRemove={(withRows) => actions.removeGroup(removingGroup.group, withRows)}
          onClose={() => setRemovingGroup(null)}
        />
      )}
      {naming && (
        <GroupNameDialog
          rows={naming.length}
          onCreate={async (title) => {
            await actions.addGroup(title, naming)
            setPicked(new Set())
          }}
          onClose={() => setNaming(null)}
        />
      )}
    </div>
  )
}
