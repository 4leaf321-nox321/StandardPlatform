/**
 * 위젯 추가 — **홈에서 출발하는 길.** 두 가지를 올린다: 목록 통계(저장된 뷰)와 지표.
 *
 * - **목록 통계**: 위젯을 올리는 자리는 목록 화면의 「통계」 다. 그런데 「여기 뭘 좀 띄우고
 *   싶다」 는 생각은 **홈을 보다가** 나고, 그때 사람은 어느 타입의 목록으로 가야 하는지부터
 *   막힌다. 여기서 타입만 고르면 그 목록이 통계를 펼친 채로 열린다. 이 창이 그 위젯을 직접
 *   만들지 않는 이유: 축과 조건은 데이터를 보면서 정하는 것이다.
 * - **지표**: 지표는 이미 정의가 다 정해져 있다 — 고르고, 나눠 볼 기준만 정해 **바로 올린다.**
 *   홈에는 최근 열두 기간의 추이가 선다.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { metricsApi } from '@/modules/metrics/api'
import { ontologyApi } from '@/modules/ontology/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Label } from '@/shared/components/ui/label'
import { useResource } from '@/shared/hooks/useResource'

type Mode = 'view' | 'metric'

/**
 * 통계를 펼친 목록 — **이 부서를 싣고 간다**(`home=`).
 *
 * 타입만 싣고 가던 때는 목록의 「홈 게시」 가 늘 내 대표 소속에 올렸다 — 시스템 관리자(대표 hq)가
 * /w/sales 에서 추가하면 hq 홈에 섰고, 이 창의 「여기에 표시됩니다」 는 거짓이었다(2026-10-08).
 */
export function statsHref(typeSlug: string, workspace: string): string {
  const params = new URLSearchParams({ group: '1', home: workspace })
  return `/o/${typeSlug}?${params.toString()}`
}

interface Props {
  /** 올릴 부서 — 지표는 여기서 바로 그 부서 홈에 오른다. */
  workspace: string
  onClose: () => void
  /** 지표를 올린 뒤 — 홈이 다시 읽는다. */
  onAdded?: () => void
}

export function AddWidgetDialog({ workspace, onClose, onAdded }: Props) {
  const [mode, setMode] = useState<Mode>('view')
  const schema = useResource(() => ontologyApi.schema(), [])
  const metrics = useResource(() => metricsApi.list(), [])
  const [slug, setSlug] = useState<string | null>(null)
  const [metricSlug, setMetricSlug] = useState<string | null>(null)
  const [split, setSplit] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const navigate = useNavigate()

  // 투영 타입은 행이 없어 못 센다 — 고를 수 있다고 보여 주고 나서 거절하지 않는다.
  const types = (schema.data?.types ?? []).filter(
    (one) => one.kind_class !== 'system' && one.is_active,
  )
  const chosen = (metrics.data ?? []).find((one) => one.slug === metricSlug) ?? null

  async function pin() {
    if (!metricSlug) return
    setBusy(true)
    setError(null)
    try {
      await metricsApi.pinHome(metricSlug, { workspace_slug: workspace, split: split || null })
      onAdded?.()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>홈에 위젯 추가</DialogTitle>
          <DialogDescription>
            {mode === 'view' ? (
              <>
                무엇을 표시할지 선택하면 그 목록이 <strong>통계를 펼친 채로</strong> 열립니다.
                거기서 조건과 기준을 정하고 「홈 게시」 를 클릭하면 여기에 표시됩니다.
              </>
            ) : (
              <>
                지표를 고르면 이 부서 홈에 <strong>최근 열두 기간의 추이</strong>가 섭니다. 기간 ·
                기준을 바꿔 보는 일은 지표 화면에서 합니다.
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="flex gap-1" role="tablist" aria-label="무엇을 올릴까">
          {(
            [
              ['view', '목록 통계'],
              ['metric', '지표'],
            ] as const
          ).map(([value, label]) => (
            <Button
              key={value}
              type="button"
              role="tab"
              aria-selected={mode === value}
              size="sm"
              variant={mode === value ? 'default' : 'outline'}
              onClick={() => setMode(value)}
            >
              {label}
            </Button>
          ))}
        </div>

        {mode === 'view' ? (
          <>
            <ErrorNotice error={schema.error} />
            <SearchablePicker
              options={types.map((one) => ({
                value: one.slug,
                label: one.label,
                hint: one.description || undefined,
                keywords: one.slug,
              }))}
              value={slug}
              onChange={setSlug}
              placeholder="어느 것을 띄울까요"
              searchPlaceholder="타입 이름으로 검색"
              emptyText="정의된 타입이 없습니다. 먼저 온톨로지에서 타입을 만드세요."
              loading={schema.loading}
            />
          </>
        ) : (
          <>
            <ErrorNotice error={metrics.error ?? error} />
            <SearchablePicker
              options={(metrics.data ?? []).map((one) => ({
                value: one.slug,
                label: one.label,
                hint: one.description || one.source_type_label,
                keywords: one.slug,
              }))}
              value={metricSlug}
              onChange={(next) => {
                setMetricSlug(next)
                setSplit('')
              }}
              placeholder="어느 지표를 띄울까요"
              searchPlaceholder="지표 이름으로 검색"
              emptyText="정의된 지표가 없습니다. 공통 › 지표에서 먼저 만드세요."
              loading={metrics.loading}
            />
            {chosen && chosen.dims.length > 0 && (
              <div className="space-y-1">
                <Label htmlFor="widget-split">선을 나눌 기준</Label>
                <select
                  id="widget-split"
                  className="border-input bg-background h-9 w-full rounded-md border px-2 text-sm"
                  value={split}
                  onChange={(event) => setSplit(event.target.value)}
                >
                  <option value="">나누지 않음(합계 한 줄)</option>
                  {chosen.dims.map((dim) => (
                    <option key={dim.name} value={dim.name}>
                      {dim.label}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            취소
          </Button>
          {mode === 'view' ? (
            <Button disabled={!slug} onClick={() => slug && navigate(statsHref(slug, workspace))}>
              목록으로 가기
            </Button>
          ) : (
            <Button disabled={!metricSlug || busy} onClick={pin}>
              홈에 올리기
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
