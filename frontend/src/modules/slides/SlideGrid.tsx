/**
 * 장표 한 장을 그린다(ADR 0037) — 틀의 격자 그대로, 영역마다 표.
 *
 * - **기록 영역**: 기록마다 연결 항목 줄(「모델: A · 부품: 커넥터」) 한 줄, 그 아래 칸 표.
 * - **짝 영역**(AS-IS / TO-BE): 짝끼리 **한 덩이**로 그린다 — 기록마다 연결 항목 줄 한 번, 그 아래
 *   왼쪽 칸 | 오른쪽 칸. 따로 그리면 칸 길이가 다를 때 줄이 어긋난다.
 * - **글 영역**: 사람이 쓴 글. 기록에서 온 값과 눈으로 갈리게 머리에 「작성한 글」 을 단다.
 *
 * 값은 서버가 열 때 센 것이다(살아 있는 값). 영역 하나가 고장 나면 그 영역만 까닭을 말한다.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, ListFilter, Pencil } from 'lucide-react'

import type { RenderedArea, SlideRecord } from '@/modules/slides/api'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Textarea } from '@/shared/components/ui/textarea'

interface Props {
  rows: number
  cols: number
  areas: RenderedArea[]
  /** 글 영역 · 고르기 영역을 고칠 수 있나 — 작성자와 시스템 관리자. */
  editable?: boolean
  onSaveText?: (key: string, text: string) => Promise<void>
  onPick?: (area: RenderedArea) => void
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

export function SlideGrid({ rows, cols, areas, editable = false, onSaveText, onPick }: Props) {
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
              />
            ) : (
              <RecordsBlock
                group={group}
                editable={editable}
                onPick={onPick ? () => onPick(first) : undefined}
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
}: {
  area: RenderedArea
  editable: boolean
  onSave?: (text: string) => Promise<void>
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
      </header>
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

function RecordsBlock({
  group,
  editable,
  onPick,
}: {
  group: RenderedArea[]
  editable: boolean
  onPick?: () => void
}) {
  const primary = group[0]
  const records = primary.records
  const more = primary.total - records.length
  const listHref =
    primary.type_slug && Object.keys(primary.list_params).length > 0
      ? `/o/${primary.type_slug}?${new URLSearchParams(primary.list_params).toString()}`
      : null

  return (
    <div className="space-y-2">
      <header className="flex flex-wrap items-center gap-2">
        <div
          className="grid min-w-0 flex-1 gap-2"
          style={{ gridTemplateColumns: group.map((one) => `${one.col_span}fr`).join(' ') }}
        >
          {group.map((one) => (
            <h3 key={one.key} className="truncate font-semibold">
              {one.title}
            </h3>
          ))}
        </div>
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
      </header>
      {primary.problem ? (
        <p role="alert" className="text-destructive text-sm">
          이 영역을 표시하지 못했습니다 — {primary.problem}
        </p>
      ) : records.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          {primary.fill === 'pick'
            ? '선택한 기록이 없습니다.'
            : primary.fill === 'follow'
              ? `「${primary.follow_title ?? ''}」 의 기록과 이어진 기록이 없습니다.`
              : '해당하는 기록이 없습니다.'}
        </p>
      ) : (
        <ol className="space-y-2">
          {records.map((record, index) => (
            <li key={record.id} className="rounded-sm border">
              <LinkLine record={record} />
              <div
                className="grid divide-x"
                style={{
                  gridTemplateColumns: group.map((one) => `${one.col_span}fr`).join(' '),
                }}
              >
                {group.map((one) => (
                  <FieldTable key={one.key} area={one} record={one.records[index]} />
                ))}
              </div>
            </li>
          ))}
        </ol>
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

/** 연결 항목 줄 — 기록이 가리키는 온톨로지 항목을 한 줄로. 끝에 그 기록 자신으로 가는 길. */
function LinkLine({ record }: { record: SlideRecord }) {
  const parts = record.links.filter((one) => one.items.length > 0)
  return (
    <div className="bg-muted/60 flex items-center gap-2 border-b px-2 py-1 text-xs">
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

/** 칸 표 — 칸이 하나면 값만, 둘 이상이면 「칸 | 값」. */
function FieldTable({ area, record }: { area: RenderedArea; record?: SlideRecord }) {
  if (!record) return <div />
  if (area.fields.length === 1) {
    const only = area.fields[0]
    return (
      <p className="px-2 py-1.5 text-sm whitespace-pre-wrap">{record.values[only.key] || '—'}</p>
    )
  }
  return (
    <table className="w-full text-sm">
      <tbody>
        {area.fields.map((field) => (
          <tr key={field.key} className="border-b last:border-b-0">
            <th className="text-muted-foreground w-1/3 px-2 py-1 text-left align-top font-normal">
              {field.label}
            </th>
            <td className="px-2 py-1 whitespace-pre-wrap">{record.values[field.key] || '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
