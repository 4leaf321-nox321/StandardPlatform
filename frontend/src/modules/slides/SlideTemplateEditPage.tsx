/**
 * 장표 틀 편집(ADR 0037) — 시스템 관리자.
 *
 * 격자(최대 3행 × 3열)에 영역을 나누고, 영역마다 받는 것을 정한다:
 * - **기록 영역** — 기록 타입 · 표시할 칸 · 채우는 법(주제와 이어지는 칸 + 고정 조건, 또는 장표마다
 *   선택) · 연결 항목 줄의 칸.
 * - **짝 영역** — 다른 기록 영역의 기록을 같은 차례로, 칸만 다르게(AS-IS = 변경 전 · TO-BE = 변경 후).
 * - **글 영역** — 장표마다 사람이 쓰는 글.
 *
 * 저장할 때 서버가 틀을 세워 보고 틀린 것을 영역 이름과 함께 말한다. 저장 전에 「미리 보기」 로
 * 주제 하나를 넣어 볼 수 있다.
 */

import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Plus, Trash2 } from 'lucide-react'

import { ConditionBar } from '@/modules/objects/ConditionBar'
import { objectApi } from '@/modules/objects/api'
import type { Condition } from '@/modules/objects/api'
import { ObjectRefPicker } from '@/modules/objects/PropertyFields'
import { ontologyApi } from '@/modules/ontology/api'
import type { ObjectType, PropertyDef } from '@/modules/ontology/api'
import { blankArea, slideApi } from '@/modules/slides/api'
import type { AreaDef, Rendered, TemplateBody } from '@/modules/slides/api'
import { SlideGrid } from '@/modules/slides/SlideGrid'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { Textarea } from '@/shared/components/ui/textarea'
import { useResource } from '@/shared/hooks/useResource'

type TypeWithProps = ObjectType & { properties: PropertyDef[] }

const GRID = [1, 2, 3]
const NONE = '__none__'
const FIXED_FIELDS = [
  { key: 'label', label: '이름' },
  { key: 'key', label: '식별자' },
]

const EMPTY: TemplateBody = {
  name: '',
  description: '',
  subject_type_slug: '',
  rows: 2,
  cols: 2,
  areas: [],
}

export default function SlideTemplateEditPage() {
  const { templateId = 'new' } = useParams()
  const creating = templateId === 'new'
  const navigate = useNavigate()
  const schema = useResource(() => ontologyApi.schema(), [])
  const existing = useResource(
    () => (creating ? Promise.resolve(null) : slideApi.template(templateId)),
    [templateId],
  )
  const [body, setBody] = useState<TemplateBody | null>(creating ? EMPTY : null)
  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [subject, setSubject] = useState<string | null>(null)
  const [preview, setPreview] = useState<Rendered | null>(null)

  useEffect(() => {
    if (existing.data) {
      const { name, description, subject_type_slug, rows, cols, areas } = existing.data
      setBody({ name, description, subject_type_slug, rows, cols, areas })
    }
  }, [existing.data])

  const types = useMemo(
    () =>
      (schema.data?.types ?? []).filter((one) => one.kind_class !== 'system') as TypeWithProps[],
    [schema.data],
  )
  const typeBySlug = useMemo(() => new Map(types.map((one) => [one.slug, one])), [types])
  const treeTypes = useMemo(
    () => new Set(types.filter((one) => one.list_view?.tree?.relation).map((one) => one.slug)),
    [types],
  )
  const locked = !creating && (existing.data?.slides ?? 0) > 0

  if (!body) return <ErrorNotice error={existing.error ?? schema.error} />

  function set(patch: Partial<TemplateBody>) {
    setBody((before) => (before ? { ...before, ...patch } : before))
    setPreview(null)
  }

  /**
   * 영역은 **자리(차례)로** 고친다 — 키로 찾던 때는 키 칸을 칠 때마다 편집기가 새로 서서 입력이
   * 끊겼고, 잠깐 다른 영역과 키가 같아지면 다음 입력이 두 영역을 함께 덮었다(2026-10-24 점검).
   * 키가 바뀌면 그 영역을 가리키던 짝 · 따라갈 영역도 새 키로 옮긴다.
   */
  function setArea(index: number, next: AreaDef) {
    const before = body!.areas[index]
    const renamed = before.key !== next.key
    set({
      areas: body!.areas.map((one, at) => {
        if (at === index) return next
        if (!renamed) return one
        return {
          ...one,
          pair_of: one.pair_of === before.key ? next.key : one.pair_of,
          follow_area: one.follow_area === before.key ? next.key : one.follow_area,
        }
      }),
    })
  }

  function addArea(row: number, col: number) {
    const taken = new Set(body!.areas.map((one) => one.key))
    let index = body!.areas.length + 1
    while (taken.has(`area${index}`)) index += 1
    const area = { ...blankArea(`area${index}`, row, col), title: `영역 ${index}` }
    set({ areas: [...body!.areas, area] })
  }

  function removeArea(index: number) {
    const key = body!.areas[index].key
    set({
      areas: body!.areas
        .filter((_, at) => at !== index)
        .map((one) => ({
          ...one,
          pair_of: one.pair_of === key ? null : one.pair_of,
          follow_area: one.follow_area === key ? null : one.follow_area,
        })),
    })
  }

  async function save() {
    setSaving(true)
    setError(null)
    try {
      if (creating) await slideApi.createTemplate(body!)
      else await slideApi.updateTemplate(templateId, body!)
      navigate('/admin/slide-templates')
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      setSaving(false)
    }
  }

  async function runPreview() {
    if (!subject) return
    setError(null)
    try {
      setPreview(await slideApi.preview(body!, subject))
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={creating ? '새 틀' : `틀 — ${existing.data?.name ?? ''}`}
        description="영역의 칸 · 조건은 저장할 때 서버가 확인합니다. 틀린 것은 영역 이름과 함께 알려 줍니다."
        actions={
          <>
            <Button asChild size="sm" variant="outline">
              <Link to="/admin/slide-templates">목록</Link>
            </Button>
            {!creating && (
              <Button
                size="sm"
                variant="outline"
                disabled={locked}
                title={locked ? '이 틀로 만든 장표가 있어 삭제할 수 없습니다' : undefined}
                onClick={() => setConfirming(true)}
              >
                <Trash2 className="mr-1 size-4" />
                삭제
              </Button>
            )}
            <Button size="sm" onClick={save} disabled={saving || !body.name.trim()}>
              저장
            </Button>
          </>
        }
      />
      <ErrorNotice error={error ?? schema.error} />

      <section className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="template-name">이름</Label>
          <Input
            id="template-name"
            value={body.name}
            maxLength={100}
            onChange={(event) => set({ name: event.target.value })}
            placeholder="예: 변경점 · 잠재리스크 검토"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="template-subject">주제 타입</Label>
          <Select
            value={body.subject_type_slug || undefined}
            onValueChange={(next) => set({ subject_type_slug: next })}
            disabled={locked}
          >
            <SelectTrigger id="template-subject" className="w-full">
              <SelectValue placeholder="장표 한 장이 무엇에 대한 것인지 선택하세요" />
            </SelectTrigger>
            <SelectContent>
              {types.map((one) => (
                <SelectItem key={one.slug} value={one.slug}>
                  {one.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {locked && (
            <p className="text-muted-foreground text-xs">
              이 틀로 만든 장표가 있어 주제 타입을 변경할 수 없습니다.
            </p>
          )}
        </div>
        <div className="space-y-1.5 md:col-span-2">
          <Label htmlFor="template-description">설명</Label>
          <Textarea
            id="template-description"
            value={body.description}
            rows={2}
            maxLength={2000}
            onChange={(event) => set({ description: event.target.value })}
          />
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-semibold">격자</h2>
          <GridSize label="행" value={body.rows} onChange={(rows) => set({ rows })} />
          <GridSize label="열" value={body.cols} onChange={(cols) => set({ cols })} />
          <span className="text-muted-foreground text-xs">
            빈 자리의 「+」 를 클릭하면 그 자리에 영역을 추가합니다.
          </span>
        </div>
        <GridMap body={body} onAdd={addArea} />
      </section>

      <section className="space-y-3">
        <h2 className="font-semibold">영역</h2>
        {body.areas.length === 0 && (
          <p className="text-muted-foreground text-sm">아직 영역이 없습니다.</p>
        )}
        {body.areas.map((area, index) => (
          <AreaEditor
            key={index}
            area={area}
            body={body}
            typeBySlug={typeBySlug}
            types={types}
            treeTypes={treeTypes}
            onChange={(next) => setArea(index, next)}
            onRemove={() => removeArea(index)}
          />
        ))}
      </section>

      <section className="space-y-3">
        <h2 className="font-semibold">미리 보기</h2>
        {body.subject_type_slug ? (
          <div className="flex flex-wrap items-center gap-2">
            <div className="w-80">
              <ObjectRefPicker
                typeSlug={body.subject_type_slug}
                value={subject}
                onChange={setSubject}
              />
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={runPreview}
              disabled={!subject || body.areas.length === 0}
            >
              미리 보기
            </Button>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">주제 타입을 먼저 선택하세요.</p>
        )}
        {preview && <SlideGrid rows={preview.rows} cols={preview.cols} areas={preview.areas} />}
      </section>

      {confirming && (
        <ConfirmDialog
          open
          title="틀 삭제"
          description="이 틀을 삭제합니다. 이 틀로 만든 장표가 없을 때만 삭제됩니다."
          confirmLabel="삭제"
          destructive
          onConfirm={async () => {
            await slideApi.removeTemplate(templateId)
            navigate('/admin/slide-templates')
          }}
          onClose={() => setConfirming(false)}
        />
      )}
    </div>
  )
}

function GridSize({
  label,
  value,
  onChange,
}: {
  label: string
  value: number
  onChange: (next: number) => void
}) {
  return (
    <label className="flex items-center gap-1 text-sm">
      <Select value={String(value)} onValueChange={(next) => onChange(Number(next))}>
        <SelectTrigger className="h-8 w-16" aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {GRID.map((one) => (
            <SelectItem key={one} value={String(one)}>
              {one}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {label}
    </label>
  )
}

/** 격자의 어느 자리를 어느 영역이 차지하나 — 겹치면 서버가 거절한다. */
function GridMap({
  body,
  onAdd,
}: {
  body: TemplateBody
  onAdd: (row: number, col: number) => void
}) {
  const owner = new Map<string, AreaDef>()
  for (const area of body.areas) {
    for (let r = area.row; r < area.row + area.row_span; r += 1) {
      for (let c = area.col; c < area.col + area.col_span; c += 1) owner.set(`${r}:${c}`, area)
    }
  }
  return (
    <div
      className="grid max-w-xl gap-1"
      style={{ gridTemplateColumns: `repeat(${body.cols}, minmax(0, 1fr))` }}
    >
      {Array.from({ length: body.rows * body.cols }, (_, index) => {
        const row = Math.floor(index / body.cols)
        const col = index % body.cols
        const area = owner.get(`${row}:${col}`)
        return area ? (
          <div
            key={index}
            className="bg-muted truncate rounded border px-2 py-3 text-center text-xs"
            title={area.key}
          >
            {area.title || area.key}
            {area.pair_of && ' (짝)'}
          </div>
        ) : (
          <button
            key={index}
            type="button"
            className="text-muted-foreground hover:bg-muted rounded border border-dashed px-2 py-3 text-xs"
            onClick={() => onAdd(row, col)}
            aria-label={`${row + 1}행 ${col + 1}열에 영역 추가`}
          >
            <Plus className="mx-auto size-4" />
          </button>
        )
      })}
    </div>
  )
}

function AreaEditor({
  area,
  body,
  types,
  typeBySlug,
  treeTypes,
  onChange,
  onRemove,
}: {
  area: AreaDef
  body: TemplateBody
  types: TypeWithProps[]
  typeBySlug: Map<string, TypeWithProps>
  treeTypes: ReadonlySet<string>
  onChange: (next: AreaDef) => void
  onRemove: () => void
}) {
  const set = (patch: Partial<AreaDef>) => onChange({ ...area, ...patch })
  const primaries = body.areas.filter(
    (one) => one.kind === 'records' && !one.pair_of && one.key !== area.key,
  )
  const primary = area.pair_of ? body.areas.find((one) => one.key === area.pair_of) : area
  const recordType = primary?.type_slug ? typeBySlug.get(primary.type_slug) : undefined
  const props = recordType?.properties ?? []
  const shownFields = [
    ...FIXED_FIELDS,
    ...props
      .filter((one) => one.data_type !== 'file')
      .map((one) => ({ key: one.key, label: one.label })),
  ]

  return (
    <div className="space-y-3 rounded-md border p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor={`${area.key}-title`}>머리글</Label>
          <Input
            id={`${area.key}-title`}
            value={area.title}
            maxLength={60}
            onChange={(event) => set({ title: event.target.value })}
            className="h-8 w-48"
            placeholder="예: AS-IS"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor={`${area.key}-key`}>키</Label>
          <Input
            id={`${area.key}-key`}
            value={area.key}
            maxLength={40}
            onChange={(event) => set({ key: event.target.value.toLowerCase() })}
            className="h-8 w-32 font-mono"
          />
        </div>
        <Place label="행" value={area.row} max={body.rows} onChange={(row) => set({ row })} />
        <Place label="열" value={area.col} max={body.cols} onChange={(col) => set({ col })} />
        <Span
          label="높이"
          value={area.row_span}
          max={body.rows - area.row}
          onChange={(row_span) => set({ row_span })}
        />
        <Span
          label="너비"
          value={area.col_span}
          max={body.cols - area.col}
          onChange={(col_span) => set({ col_span })}
        />
        <div className="space-y-1">
          <Label>종류</Label>
          <Select
            value={area.kind}
            onValueChange={(next) => set({ kind: next as AreaDef['kind'], pair_of: null })}
          >
            <SelectTrigger className="h-8 w-28" aria-label="종류">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="records">기록</SelectItem>
              <SelectItem value="text">글</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onRemove}>
          <Trash2 className="mr-1 size-4" />
          삭제
        </Button>
      </div>

      {area.kind === 'records' && (
        <>
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <Label>짝</Label>
              <Select
                value={area.pair_of ?? NONE}
                onValueChange={(next) =>
                  set({ pair_of: next === NONE ? null : next, type_slug: null, fields: [] })
                }
              >
                <SelectTrigger className="h-8 w-48" aria-label="짝">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>없음</SelectItem>
                  {primaries.map((one) => (
                    <SelectItem key={one.key} value={one.key}>
                      {one.title || one.key}의 기록을 같은 차례로
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {!area.pair_of && (
              <div className="space-y-1">
                <Label>기록 타입</Label>
                <Select
                  value={area.type_slug ?? undefined}
                  onValueChange={(next) =>
                    set({
                      type_slug: next,
                      fields: [],
                      ref_line: [],
                      conditions: [],
                      subject_field: null,
                    })
                  }
                >
                  <SelectTrigger className="h-8 w-48" aria-label="기록 타입">
                    <SelectValue placeholder="선택하세요" />
                  </SelectTrigger>
                  <SelectContent>
                    {types.map((one) => (
                      <SelectItem key={one.slug} value={one.slug}>
                        {one.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>

          {recordType && (
            <Checks
              title="표시할 칸"
              options={shownFields}
              chosen={area.fields}
              onChange={(fields) => set({ fields })}
            />
          )}

          {!area.pair_of && recordType && (
            <RecordSource
              area={area}
              recordType={recordType}
              subjectType={body.subject_type_slug}
              treeTypes={treeTypes}
              sources={primaries.map((one) => ({
                key: one.key,
                title: one.title,
                type_slug: one.type_slug,
              }))}
              onChange={set}
            />
          )}
        </>
      )}
    </div>
  )
}

/** 채우는 법 · 연결 항목 줄 · 건수 — 짝이 없는 기록 영역만. */
function RecordSource({
  area,
  recordType,
  subjectType,
  treeTypes,
  sources,
  onChange,
}: {
  area: AreaDef
  recordType: TypeWithProps
  subjectType: string
  treeTypes: ReadonlySet<string>
  /** 따라갈 수 있는 영역 — 짝이 없는 다른 기록 영역과 그 타입. */
  sources: { key: string; title: string; type_slug: string | null }[]
  onChange: (patch: Partial<AreaDef>) => void
}) {
  const linked = useResource(() => objectApi.fields(recordType.slug), [recordType.slug])
  const refs = recordType.properties.filter((one) => one.data_type === 'object_ref')
  /** 이 기록에서 그 타입을 가리키는 칸 — 자기 칸과 걸음 끝의 칸(관계 · 나를 가리키는 것 포함). */
  const pointing = (target: string | null) =>
    target
      ? [
          ...refs
            .filter((one) => one.ref_type_slug === target)
            .map((one) => ({ field: one.key, label: one.label })),
          ...(linked.data ?? [])
            .filter((one) => one.ref_type_slug === target)
            .map((one) => ({ field: one.field, label: `${one.heading} › ${one.label}` })),
        ]
      : []
  const toSubject = pointing(subjectType)
  const source = sources.find((one) => one.key === area.follow_area)
  const toSource = pointing(source?.type_slug ?? null)
  const scoped = area.fill !== 'follow'

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label>채우는 법</Label>
          <Select
            value={area.fill}
            onValueChange={(next) =>
              onChange({
                fill: next as AreaDef['fill'],
                follow_area: null,
                follow_field: null,
                limit: next === 'pick' ? 1 : area.limit,
              })
            }
          >
            <SelectTrigger className="h-8 w-56" aria-label="채우는 법">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="condition">조건에 맞는 것 전부(목록)</SelectItem>
              <SelectItem value="pick">장표마다 선택</SelectItem>
              <SelectItem value="follow">다른 영역을 따라옴</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {scoped && (
          <div className="space-y-1">
            <Label>주제와 이어지는 칸</Label>
            <Select
              value={area.subject_field ?? NONE}
              onValueChange={(next) => onChange({ subject_field: next === NONE ? null : next })}
            >
              <SelectTrigger className="h-8 w-72" aria-label="주제와 이어지는 칸">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>주제로 거르지 않음</SelectItem>
                {toSubject.map((one) => (
                  <SelectItem key={one.field} value={one.field}>
                    {one.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        {scoped && area.subject_field && treeTypes.has(subjectType) && (
          <div className="space-y-1">
            <Label>주제와의 관계</Label>
            <Select
              value={area.subject_op}
              onValueChange={(next) => onChange({ subject_op: next as AreaDef['subject_op'] })}
            >
              <SelectTrigger className="h-8 w-40" aria-label="주제와의 관계">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="eq">주제와 같음</SelectItem>
                <SelectItem value="under">주제의 하위 전체</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
        {area.fill === 'follow' && (
          <>
            <div className="space-y-1">
              <Label>따라갈 영역</Label>
              <Select
                value={area.follow_area ?? undefined}
                onValueChange={(next) => onChange({ follow_area: next, follow_field: null })}
              >
                <SelectTrigger className="h-8 w-48" aria-label="따라갈 영역">
                  <SelectValue placeholder="선택하세요" />
                </SelectTrigger>
                <SelectContent>
                  {sources.map((one) => (
                    <SelectItem key={one.key} value={one.key}>
                      {one.title || one.key}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>그 영역의 기록을 가리키는 칸</Label>
              <Select
                value={area.follow_field ?? undefined}
                onValueChange={(next) => onChange({ follow_field: next })}
                disabled={!source}
              >
                <SelectTrigger className="h-8 w-72" aria-label="그 영역의 기록을 가리키는 칸">
                  <SelectValue placeholder="선택하세요" />
                </SelectTrigger>
                <SelectContent>
                  {toSource.map((one) => (
                    <SelectItem key={one.field} value={one.field}>
                      {one.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {source && toSource.length === 0 && (
                <p className="text-muted-foreground text-xs">
                  이 기록에서 그 영역의 기록으로 이어지는 칸 · 관계가 없습니다.
                </p>
              )}
            </div>
          </>
        )}
        <div className="space-y-1">
          <Label htmlFor={`${area.key}-limit`}>
            {area.fill === 'pick' ? '선택할 수 있는 건수' : '건수 상한'}
          </Label>
          <Input
            id={`${area.key}-limit`}
            type="number"
            min={1}
            max={50}
            value={area.limit}
            onChange={(event) =>
              onChange({ limit: Math.min(50, Math.max(1, Number(event.target.value) || 1)) })
            }
            className="h-8 w-20"
          />
        </div>
        <div className="space-y-1">
          <Label>차례</Label>
          <Select
            value={area.order}
            onValueChange={(next) => onChange({ order: next as AreaDef['order'] })}
          >
            <SelectTrigger className="h-8 w-32" aria-label="차례">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="newest">새것부터</SelectItem>
              <SelectItem value="oldest">오래된 것부터</SelectItem>
              <SelectItem value="label">이름 순</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      {area.fill === 'pick' && (
        <p className="text-muted-foreground text-xs">
          주제와 이어지는 칸 · 고정 조건은 선택 후보를 좁힙니다. 건수가 1 이면 장표 한 장이 기록
          하나에 대한 것입니다.
        </p>
      )}
      <div className="space-y-1">
        <Label>고정 조건</Label>
        <ConditionBar
          defs={recordType.properties}
          conditions={area.conditions as Condition[]}
          onChange={(next) =>
            onChange({
              conditions: next.map((one) => ({ ...one, exclude: one.exclude ?? 0 })),
            })
          }
          linked={linked.data ?? []}
          treeTypes={treeTypes}
        />
      </div>
      {refs.length > 0 && (
        <Checks
          title="연결 항목 줄 — 기록마다 표 위에 한 줄로 표시할 참조 칸"
          options={refs.map((one) => ({ key: one.key, label: one.label }))}
          chosen={area.ref_line}
          onChange={(ref_line) => onChange({ ref_line })}
        />
      )}
    </div>
  )
}

function Checks({
  title,
  options,
  chosen,
  onChange,
}: {
  title: string
  options: { key: string; label: string }[]
  chosen: string[]
  onChange: (next: string[]) => void
}) {
  return (
    <fieldset className="space-y-1">
      <legend className="text-sm font-medium">{title}</legend>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {options.map((one) => (
          <label key={one.key} className="flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={chosen.includes(one.key)}
              onChange={(event) =>
                onChange(
                  event.target.checked
                    ? [...chosen, one.key]
                    : chosen.filter((each) => each !== one.key),
                )
              }
            />
            {one.label}
          </label>
        ))}
      </div>
    </fieldset>
  )
}

function Place({
  label,
  value,
  max,
  onChange,
}: {
  label: string
  value: number
  max: number
  onChange: (next: number) => void
}) {
  return (
    <div className="space-y-1">
      <Label>{label}</Label>
      <Select value={String(value)} onValueChange={(next) => onChange(Number(next))}>
        <SelectTrigger className="h-8 w-16" aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Array.from({ length: Math.max(max, value + 1) }, (_, index) => (
            <SelectItem key={index} value={String(index)}>
              {index + 1}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

function Span({
  label,
  value,
  max,
  onChange,
}: {
  label: string
  value: number
  max: number
  onChange: (next: number) => void
}) {
  return (
    <div className="space-y-1">
      <Label>{label}</Label>
      <Select value={String(value)} onValueChange={(next) => onChange(Number(next))}>
        <SelectTrigger className="h-8 w-16" aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Array.from({ length: Math.max(max, value, 1) }, (_, index) => (
            <SelectItem key={index} value={String(index + 1)}>
              {index + 1}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
