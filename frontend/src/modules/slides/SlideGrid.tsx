/**
 * 장표 한 장을 그린다(ADR 0037) — 틀의 격자 그대로, 영역마다 표.
 *
 * - **기록 영역**: 기록마다 연결 항목 줄(「모델: A · 부품: 커넥터」) 한 줄, 그 아래 칸 표.
 * - **짝 영역**(AS-IS / TO-BE): 짝끼리 **표 하나**로 그린다 — 머리글 줄, 기록마다 연결 항목 줄 한 번,
 *   그 아래 왼쪽 칸 | 오른쪽 칸. 따로 그리면 머리글과 구분선 · 좌우의 가로선이 어긋난다.
 * - **글 영역**: 사람이 쓴 글. 기록에서 온 값과 눈으로 갈리게 머리에 「작성한 글」 을 단다.
 *
 * 값은 서버가 열 때 센 것이다(살아 있는 값). 영역 하나가 고장 나면 그 영역만 까닭을 말한다.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, FilePlus2, Link2, ListFilter, Pencil, Plus, Unlink } from 'lucide-react'

import type { RenderedArea, SlideRecord } from '@/modules/slides/api'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Textarea } from '@/shared/components/ui/textarea'

/** 장표에서 기록에 하는 일 — 값 수정 · 연결 · 해제 · 새 기록 · 글을 기록으로. 모두 **기록의 권한**을
 *  따른다(장표의 작성자인지와 다른 축). 장표 화면이 객체 API 로 한다. */
export interface RecordActions {
  onEditValue?: (
    area: RenderedArea,
    record: SlideRecord,
    field: { key: string; label: string },
  ) => void
  onLink?: (area: RenderedArea) => void
  onUnlink?: (area: RenderedArea, record: SlideRecord) => void
  /** 이 영역에 새 기록을 만들 수 있나 — 타입의 용도와 내 소속으로(`canCreateObject`). */
  canCreate?: (area: RenderedArea) => boolean
  onCreate?: (area: RenderedArea) => void
  onMakeRecord?: (area: RenderedArea) => void
}

interface Props extends RecordActions {
  rows: number
  cols: number
  areas: RenderedArea[]
  /** 글 영역 · 고르기 영역을 고칠 수 있나 — 작성자와 시스템 관리자. */
  editable?: boolean
  onSaveText?: (key: string, text: string) => Promise<void>
  onPick?: (area: RenderedArea) => void
}

/** 이 기록과 따라갈 기록의 연결을 끊을 수 있나 — 따라갈 기록 쪽을 고치는 연결이면 그쪽 권한. */
export function canUnlink(area: RenderedArea, record: SlideRecord): boolean {
  const link = area.link
  if (!link || record.ties.length === 0) return false
  const sourceSide =
    link.kind === 'backref' || (link.kind === 'relation' && link.direction === 'in')
  return sourceSide ? link.can_link : record.can_edit
}

/**
 * 덩이의 원본 — 짝이 아닌 영역. 덩이는 열 차례로 놓여 짝이 왼쪽에 올 수 있다 — 그때 첫 영역을
 * 원본으로 읽으면 짝의 채우는 법 · 연결로 그려 「기록 선택」 · 「연결」 이 사라졌다(2026-10-24 점검).
 */
export function primaryOf(group: RenderedArea[]): RenderedArea {
  return group.find((one) => !one.pair_of) ?? group[0]
}

/** 짝끼리 묶은 덩이 — 짝이 없으면 영역 하나가 한 덩이. 왼쪽부터. */
export function blocksOf(areas: RenderedArea[]): RenderedArea[][] {
  const out: RenderedArea[][] = []
  for (const area of areas) {
    if (area.pair_of) continue
    const group = [area, ...areas.filter((one) => one.pair_of === area.key)]
    out.push(group.sort((a, b) => a.col - b.col))
  }
  return out
}

export function SlideGrid({
  rows,
  cols,
  areas,
  editable = false,
  onSaveText,
  onPick,
  ...actions
}: Props) {
  return (
    <div
      className="grid gap-px overflow-hidden rounded-md border bg-border"
      style={{
        gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
        gridTemplateRows: `repeat(${rows}, auto)`,
      }}
    >
      {blocksOf(areas).map((group) => {
        const first = group[0]
        const primary = primaryOf(group)
        const last = group[group.length - 1]
        return (
          <section
            key={first.key}
            aria-label={group.map((one) => one.title).join(' · ')}
            className="bg-background min-w-0 p-3"
            style={{
              gridRow: `${first.row + 1} / span ${first.row_span}`,
              gridColumn: `${first.col + 1} / ${last.col + last.col_span + 1}`,
            }}
          >
            {first.kind === 'text' ? (
              <TextArea
                area={first}
                editable={editable}
                onSave={onSaveText ? (text) => onSaveText(first.key, text) : undefined}
                onMakeRecord={
                  editable && actions.onMakeRecord ? () => actions.onMakeRecord?.(first) : undefined
                }
              />
            ) : (
              <RecordsBlock
                group={group}
                editable={editable}
                onPick={onPick ? () => onPick(primary) : undefined}
                actions={actions}
              />
            )}
          </section>
        )
      })}
    </div>
  )
}

function TextArea({
  area,
  editable,
  onSave,
  onMakeRecord,
}: {
  area: RenderedArea
  editable: boolean
  onSave?: (text: string) => Promise<void>
  onMakeRecord?: () => void
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  async function save() {
    if (draft === null || !onSave) return
    setSaving(true)
    try {
      await onSave(draft)
      setDraft(null)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-2">
      <header className="flex items-center gap-2">
        <h3 className="font-semibold">{area.title}</h3>
        <Badge variant="outline" className="text-xs font-normal">
          작성한 글
        </Badge>
        {area.ai_draft && (
          <Badge
            variant="secondary"
            className="text-xs font-normal"
            title="AI 가 쓴 초안입니다 — 확인하고 수정해 저장하면 표시가 사라집니다."
          >
            AI 초안
          </Badge>
        )}
        {editable && onSave && draft === null && (
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto h-7"
            onClick={() => setDraft(area.text)}
          >
            <Pencil className="mr-1 size-3" />
            수정
          </Button>
        )}
        {onMakeRecord && draft === null && area.text.trim() && (
          <Button size="sm" variant="ghost" className="h-7" onClick={onMakeRecord}>
            <FilePlus2 className="mr-1 size-3" />
            기록으로 저장
          </Button>
        )}
      </header>
      {area.made && (
        <p className="text-muted-foreground text-xs">
          이 글로 만든 기록:{' '}
          <Link
            to={area.made.type_slug ? `/o/${area.made.type_slug}/${area.made.id}` : '#'}
            className="text-foreground hover:underline"
          >
            {area.made.label}
          </Link>
        </p>
      )}
      {draft === null ? (
        area.text ? (
          <p className="text-sm whitespace-pre-wrap">{area.text}</p>
        ) : (
          <p className="text-muted-foreground text-sm">아직 작성한 글이 없습니다.</p>
        )
      ) : (
        <div className="space-y-2">
          <Textarea
            aria-label={`${area.title} 글`}
            value={draft}
            rows={5}
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="outline" onClick={() => setDraft(null)} disabled={saving}>
              취소
            </Button>
            <Button size="sm" onClick={save} disabled={saving}>
              저장
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

/** 칸이 둘 이상인 영역은 「칸 이름 | 값」 두 열, 하나면 값 한 열. 이름 열의 몫. */
const LABEL_SHARE = 0.32

interface Column {
  area: RenderedArea
  part: 'label' | 'value'
  width: number
}

/** 덩이의 열 — 영역마다 그 너비(`col_span`)의 몫을 나눠 가진다. 머리글 · 연결 항목 줄 · 칸 줄이
 *  **같은 열**을 써서 구분선이 한 자리에 선다. */
export function columnsOf(group: RenderedArea[]): Column[] {
  const span = group.reduce((sum, one) => sum + one.col_span, 0)
  return group.flatMap((one) => {
    const share = (one.col_span / span) * 100
    return one.fields.length > 1
      ? [
          { area: one, part: 'label' as const, width: share * LABEL_SHARE },
          { area: one, part: 'value' as const, width: share * (1 - LABEL_SHARE) },
        ]
      : [{ area: one, part: 'value' as const, width: share }]
  })
}

/**
 * 기록 영역(짝이면 짝까지)을 **표 하나**로 — 머리글 줄, 기록마다 연결 항목 줄과 칸 줄. 짝 영역의 칸
 * 수가 다르면 적은 쪽은 빈 칸으로 채워 가로선도 맞는다. 따로 그리던 때는 머리글 옆의 표시 때문에
 * 머리글 폭이 표보다 좁아 「TO-BE」 가 구분선보다 왼쪽에 섰다(2026-10-23).
 */
function RecordsBlock({
  group,
  editable,
  onPick,
  actions,
}: {
  group: RenderedArea[]
  editable: boolean
  onPick?: () => void
  actions: RecordActions
}) {
  const primary = primaryOf(group)
  const link = primary.link
  const records = primary.records
  const more = primary.total - records.length
  const listHref =
    primary.type_slug && Object.keys(primary.list_params).length > 0
      ? `/o/${primary.type_slug}?${new URLSearchParams(primary.list_params).toString()}`
      : null
  const columns = columnsOf(group)
  const height = Math.max(1, ...group.map((one) => one.fields.length))
  const cell = 'border px-2 py-1 align-top'

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {primary.type_label && (
          <Badge variant="secondary" className="text-xs font-normal">
            {primary.type_label}
          </Badge>
        )}
        {primary.follow_title && (
          <Badge variant="outline" className="text-xs font-normal">
            「{primary.follow_title}」 따라옴
          </Badge>
        )}
        {editable && onPick && primary.fill === 'pick' && (
          <Button size="sm" variant="ghost" className="h-7" onClick={onPick}>
            <Pencil className="mr-1 size-3" />
            기록 선택
          </Button>
        )}
        {link && link.can_link && link.sources.length > 0 && actions.onLink && (
          <Button
            size="sm"
            variant="ghost"
            className="h-7"
            onClick={() => actions.onLink?.(primary)}
          >
            <Link2 className="mr-1 size-3" />
            연결
          </Button>
        )}
        {actions.onCreate && actions.canCreate?.(primary) && (
          <Button
            size="sm"
            variant="ghost"
            className="h-7"
            onClick={() => actions.onCreate?.(primary)}
          >
            <Plus className="mr-1 size-3" />새 {primary.type_label}
          </Button>
        )}
      </div>
      <table className="w-full table-fixed border-collapse text-sm">
        <colgroup>
          {columns.map((one, index) => (
            <col key={index} style={{ width: `${one.width}%` }} />
          ))}
        </colgroup>
        <thead>
          <tr className="bg-muted/70">
            {group.map((one) => (
              <th
                key={one.key}
                colSpan={one.fields.length > 1 ? 2 : 1}
                className={`${cell} truncate py-1.5 text-left font-semibold`}
              >
                {one.title}
              </th>
            ))}
          </tr>
        </thead>
        {records.map((record, index) => (
          <tbody key={record.id} aria-label={record.label}>
            <tr>
              <td colSpan={columns.length} className={`${cell} bg-muted/30 text-xs`}>
                <LinkLine
                  record={record}
                  onUnlink={
                    actions.onUnlink && canUnlink(primary, record)
                      ? () => actions.onUnlink?.(primary, record)
                      : undefined
                  }
                />
              </td>
            </tr>
            {Array.from({ length: height }, (_, row) => (
              <tr key={row}>
                {group.map((one) => {
                  const shown = one.records[index]
                  const field = one.fields[row]
                  const value = field && shown ? shown.values[field.key] || '—' : ''
                  if (one.fields.length > 1) {
                    return [
                      <th
                        key={`${one.key}-label`}
                        scope="row"
                        className={`${cell} text-muted-foreground text-left font-normal`}
                      >
                        {field?.label ?? ''}
                      </th>,
                      <td key={`${one.key}-value`} className={`${cell} whitespace-pre-wrap`}>
                        <Value
                          text={value}
                          label={shown && field ? `${shown.label} ${field.label} 수정` : undefined}
                          onEdit={
                            shown && field && editableField(field.key) && shown.can_edit
                              ? () => actions.onEditValue?.(one, shown, field)
                              : undefined
                          }
                        />
                      </td>,
                    ]
                  }
                  // 칸이 하나면 값 한 칸이 기록의 높이를 다 쓴다.
                  return row === 0 ? (
                    <td key={one.key} rowSpan={height} className={`${cell} whitespace-pre-wrap`}>
                      <Value
                        text={value}
                        label={shown && field ? `${shown.label} ${field.label} 수정` : undefined}
                        onEdit={
                          shown && field && editableField(field.key) && shown.can_edit
                            ? () => actions.onEditValue?.(one, shown, field)
                            : undefined
                        }
                      />
                    </td>
                  ) : null
                })}
              </tr>
            ))}
          </tbody>
        ))}
      </table>
      {primary.problem ? (
        <p role="alert" className="text-destructive text-sm">
          이 영역을 표시하지 못했습니다 — {primary.problem}
        </p>
      ) : (
        records.length === 0 && (
          <p className="text-muted-foreground text-sm">
            {primary.fill === 'pick'
              ? '선택한 기록이 없습니다.'
              : primary.fill === 'follow'
                ? `「${primary.follow_title ?? ''}」 의 기록과 이어진 기록이 없습니다.`
                : '해당하는 기록이 없습니다.'}
          </p>
        )
      )}
      {(more > 0 || listHref) && !primary.problem && (
        <p className="text-muted-foreground flex items-center gap-2 text-xs">
          {more > 0 && <span>{more}건 더 있습니다.</span>}
          {listHref && (
            <Link to={listHref} className="inline-flex items-center gap-1 hover:underline">
              <ListFilter className="size-3" />
              목록에서 보기
            </Link>
          )}
        </p>
      )}
    </div>
  )
}

/** 식별자는 장표에서 수정하지 않는다 — 바꾸면 밖에서 처음 보는 것이 되어 상세에서 이유와 함께 한다. */
function editableField(key: string): boolean {
  return key !== 'key'
}

/** 값 칸 — 수정할 수 있으면 클릭해 그 자리에서 수정한다. */
function Value({ text, label, onEdit }: { text: string; label?: string; onEdit?: () => void }) {
  if (!onEdit) return <>{text}</>
  return (
    <button
      type="button"
      onClick={onEdit}
      aria-label={label}
      title="클릭해 수정"
      className="hover:bg-muted -mx-1 w-[calc(100%+0.5rem)] rounded px-1 text-left whitespace-pre-wrap"
    >
      {text || <span className="text-muted-foreground">—</span>}
    </button>
  )
}

/** 연결 항목 줄 — 기록이 가리키는 온톨로지 항목을 한 줄로. 끝에 그 기록 자신으로 가는 길. */
function LinkLine({ record, onUnlink }: { record: SlideRecord; onUnlink?: () => void }) {
  const parts = record.links.filter((one) => one.items.length > 0)
  return (
    <div className="flex items-center gap-2">
      <span className="min-w-0 flex-1 truncate">
        {parts.length === 0 ? (
          <span className="text-muted-foreground">{record.label}</span>
        ) : (
          parts.map((part, index) => (
            <span key={part.label}>
              {index > 0 && <span className="text-muted-foreground"> · </span>}
              <span className="text-muted-foreground">{part.label}: </span>
              {part.items.map((item, at) => (
                <span key={item.id}>
                  {at > 0 && ', '}
                  {item.type_slug ? (
                    <Link to={`/o/${item.type_slug}/${item.id}`} className="hover:underline">
                      {item.label}
                    </Link>
                  ) : (
                    item.label
                  )}
                </span>
              ))}
            </span>
          ))
        )}
      </span>
      {onUnlink && (
        <button
          type="button"
          onClick={onUnlink}
          className="text-muted-foreground hover:text-destructive inline-flex items-center gap-0.5"
          aria-label={`${record.label} 연결 해제`}
          title="연결 해제"
        >
          <Unlink className="size-3" />
        </button>
      )}
      <Link
        to={`/o/${record.type_slug}/${record.id}`}
        className="text-muted-foreground hover:text-foreground inline-flex items-center"
        title={`${record.label} 열기`}
        aria-label={`${record.label} 열기`}
      >
        <ArrowUpRight className="size-3" />
      </Link>
    </div>
  )
}
