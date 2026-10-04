/**
 * RA 보고서 소스(ADR 0018)의 설정 — **칸 대응을 적지 않는다.** 보고서 기록 타입의 칸 키가 약속이고,
 * 사람이 고르는 것은 「RA 의 어느 조직인가」 하나다.
 *
 * 차례가 정해져 있다: 타입이 있어야 소스를 저장하고, 조직 목록은 **저장된** 주소 · 토큰으로 RA 에
 * 묻는다(비밀을 화면이 들고 다니지 않게). 그래서 ① 타입 만들기 → ② 저장하고 조직 불러오기 →
 * ③ 조직을 고르고 저장.
 */

import { useState } from 'react'
import { Building2, Wand2 } from 'lucide-react'

import { datasourceApi } from '@/modules/datasources/api'
import type { RaBoard, RaReportType, SourceOptions } from '@/modules/datasources/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
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

/** 피드의 주소 — 종류가 정한다(사람이 적지 않는다). */
export const RA_FEED = '/api/feeds/published-reports'

/**
 * 「보고서 기록 타입 만들기」 — 표준 칸(제목 · 주소 · 보고일 · 작성 부서 · 본문 · 원본 상태 …)과
 * 고른 축의 참조 칸 `ref_<타입>` 을 한 번에. 이미 있으면 모자란 칸만 더한다.
 */
export function RaReportTypeMaker({
  axes,
  onMade,
}: {
  /** 보고서가 가리킬 수 있는 타입 — 이 쌍둥이의 축(개발모델 · 과제 · 부품 …). */
  axes: { slug: string; label: string }[]
  onMade: (slug: string) => void
}) {
  const [slug, setSlug] = useState('ra_report')
  const [label, setLabel] = useState('보고서')
  const [picked, setPicked] = useState<string[]>([])
  const [made, setMade] = useState<RaReportType | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  async function make() {
    setBusy(true)
    setError(null)
    try {
      const done = await datasourceApi.raReportType({
        slug: slug.trim(),
        label: label.trim(),
        axes: picked,
      })
      setMade(done)
      onMade(done.type_slug)
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2 rounded border p-3">
      <p className="text-sm font-medium">보고서 기록 타입 만들기</p>
      {error && <ErrorNotice error={error} />}
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="ra-type-slug" className="text-xs">
            타입 slug
          </Label>
          <Input
            id="ra-type-slug"
            value={slug}
            className="w-40 font-mono"
            onChange={(event) => setSlug(event.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="ra-type-label" className="text-xs">
            이름
          </Label>
          <Input
            id="ra-type-label"
            value={label}
            className="w-40"
            onChange={(event) => setLabel(event.target.value)}
          />
        </div>
      </div>
      {/* 축을 골라 두면 RA 가 SP 에서 받은 태그가 그 타입의 객체에 선으로 걸린다 — 고르지
          않은 축의 태그는 「종류: 값」 글로 남는다(잃지 않는다). */}
      {axes.length > 0 && (
        <fieldset className="space-y-1">
          <legend className="text-xs">
            태그를 선으로 걸 축 (RA 가 이 쌍둥이에서 받은 기준정보)
          </legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {axes.map((one) => (
              <label key={one.slug} className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="size-4"
                  checked={picked.includes(one.slug)}
                  onChange={(event) =>
                    setPicked(
                      event.target.checked
                        ? [...picked, one.slug]
                        : picked.filter((slug) => slug !== one.slug),
                    )
                  }
                />
                {one.label}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={busy || !slug.trim() || !label.trim()}
        onClick={make}
      >
        <Wand2 className="mr-1 size-3.5" />
        {made ? '다시 맞추기 (모자란 칸만 더함)' : '만들기'}
      </Button>
      {made && (
        <p className="text-muted-foreground text-xs">
          {made.created ? '새로 만들었습니다' : '있던 타입에 맞췄습니다'} —{' '}
          <code>{made.type_slug}</code>
          {made.changes.length > 0 ? ` · 바뀐 것 ${made.changes.length}` : ' · 바뀐 것 없음'}. 넣을
          타입으로 골라 두었습니다.
        </p>
      )}
    </div>
  )
}

/** 조직 · 단계 · 본문 — 소스의 `options`. */
export function RaOptionsFields({
  options,
  onChange,
  canLoad,
  onLoad,
}: {
  options: SourceOptions
  onChange: (next: SourceOptions) => void
  /** 주소 · 토큰 · 타입이 있어야 저장하고 RA 에 물을 수 있다. */
  canLoad: boolean
  /** 저장하고 RA 의 조직 트리를 받는다. */
  onLoad: () => Promise<RaBoard[]>
}) {
  const [boards, setBoards] = useState<RaBoard[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const board = options.board ?? ''

  async function load() {
    setBusy(true)
    setError(null)
    try {
      setBoards(await onLoad())
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3 rounded-md border p-3">
      <p className="text-sm font-medium">무엇을 받나 — RA 의 조직 하나와 그 하위</p>
      {error && <ErrorNotice error={error} />}
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label className="text-xs">조직</Label>
          {boards ? (
            <SearchablePicker
              ariaLabel="RA 조직"
              className="w-80"
              value={board || null}
              placeholder="조직을 고르세요"
              searchPlaceholder="조직 이름 · slug"
              options={boards.map((one) => ({
                value: one.slug,
                label: `${'　'.repeat(one.depth)}${one.name}`,
                hint: one.path,
                keywords: one.slug,
              }))}
              onChange={(next) => onChange({ ...options, board: next })}
            />
          ) : (
            <p className="text-sm">
              {board ? (
                <code>{board}</code>
              ) : (
                <span className="text-muted-foreground">아직 안 고름</span>
              )}
            </p>
          )}
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy || !canLoad}
          onClick={load}
        >
          <Building2 className="mr-1 size-3.5" />
          {boards ? '조직 다시 불러오기' : '저장하고 RA 조직 불러오기'}
        </Button>
      </div>
      <div className="flex flex-wrap items-end gap-4">
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-4"
            checked={options.include_descendants ?? true}
            onChange={(event) =>
              onChange({
                ...options,
                include_descendants: event.target.checked,
              })
            }
          />
          하위 조직까지
        </label>
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-4"
            checked={options.include_text ?? true}
            onChange={(event) => onChange({ ...options, include_text: event.target.checked })}
          />
          본문까지 (에이전트가 내용으로 답하려면 켭니다)
        </label>
        <div className="space-y-1">
          <Label className="text-xs">단계</Label>
          <Select
            value={options.phase ?? 'finalized'}
            onValueChange={(next) =>
              onChange({ ...options, phase: next as SourceOptions['phase'] })
            }
          >
            <SelectTrigger className="w-56" aria-label="단계">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="finalized">발행본만 (발행 단추를 누른 것)</SelectItem>
              <SelectItem value="published">게시된 것 전부</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      <p className="text-muted-foreground text-xs">
        RA 의 <b>공용 게시판</b>에 오른 보고서만 옵니다. 증분은 지난번 이후만 받고, 하루 한 번
        전량을 대조합니다 — 원본에서 내려간 보고서는 <b>지우지 않고</b> 「원본에서 내려감」 으로
        표시합니다. 작성 부서와 slug 가 같은 SP 부서가 있으면 그 부서의 기록이 됩니다(없으면 아래
        소유 부서).
      </p>
    </div>
  )
}
