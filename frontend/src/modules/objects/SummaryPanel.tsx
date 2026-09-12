/**
 * 묶어 보기 — 목록 위에서 **「그래서 몇 건인데」** 에 답한다.
 *
 * 그리는 일은 `shared/charts` 가 한다. 이 화면은 **무엇을 그릴지만** 정한다 —
 * 축 색·범례 높이·빈 데이터 처리를 여기서 또 정하면 다른 화면의 그림과 어긋나고,
 * 그때 같은 값이 화면마다 다른 색으로 선다.
 *
 * ## 기본은 막대다
 *
 * 사람은 각도보다 길이를 훨씬 잘 읽는다. 원그래프는 조각이 서넛일 때 비율을
 * 보여 주는 데만 쓴다 — 여덟 조각짜리 원에서는 순위를 읽을 수 없다.
 *
 * ## 막대를 누르면 그 줄만 걸러진다
 *
 * 「B 등급이 12건」 다음의 물음은 언제나 「그 12건이 뭔데」 다. 거기서 거르기 칸으로
 * 돌아가 값을 다시 치게 하면, 그 한 번이 사람을 엑셀로 돌려보낸다.
 *
 * ## 홈에 올리는 단추가 여기 있다
 *
 * 「이거 홈에 두고 싶다」 는 생각은 **그림을 막 그려 놓고 보는 이 자리**에서 난다.
 * 그때 뷰 메뉴를 열어 저장하고 부서와 함께 쓰기를 켜고 메뉴를 다시 열게 하면, 그
 * 경로를 찾아낸 사람만 이 기능을 쓴다.
 */

import { useEffect, useMemo, useState } from 'react'
import {
  ArrowUpDown,
  BarChart3,
  Box as BoxIcon,
  ChartArea,
  Download,
  ChartLine,
  ChartPie,
  Grid3x3,
  House,
  Layers,
  Loader2,
  ScatterChart,
  X,
} from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import type { ObjectQuery, Points, SavedViewQuery, Summary } from '@/modules/objects/api'
import { PinToHomeDialog } from '@/modules/objects/PinToHomeDialog'
import { DEFAULT_SUMMARY } from '@/modules/objects/summarySettings'
import type { SummarySettings } from '@/modules/objects/summarySettings'
import { useAuth } from '@/shared/auth/AuthContext'
import { isManagerOf } from '@/shared/auth/roles'
import { Chart, LazyPlot, colorFor } from '@/shared/charts'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import { cn } from '@/shared/lib/utils'

/** 목록 화면은 `summarySettings` 에서 **바로** 가져간다 — 여기서 가져가면 그 화면이
 *  이 패널을 정적으로 끌어와 `lazy()` 가 무색해진다(차트 라이브러리가 목록을 열
 *  때마다 함께 내려온다). 여기서 다시 내보내는 것은 패널만 아는 쪽의 편의다. */
export { DEFAULT_SUMMARY }
export type { SummarySettings }

/** 그림 모양. **막대가 기본이다** — 길이는 눈이 바로 비교하지만 각도는 못 한다. */
const KINDS: { value: SummarySettings['chart']; label: string; Icon: typeof BarChart3 }[] = [
  { value: 'bar', label: '막대', Icon: BarChart3 },
  { value: 'line', label: '꺾은선', Icon: ChartLine },
  { value: 'area', label: '영역', Icon: ChartArea },
  { value: 'pie', label: '원', Icon: ChartPie },
  // **두 축일 때만 뜻이 있다.** 한 축짜리 히트맵은 색칠한 막대 하나일 뿐이다.
  { value: 'heatmap', label: '히트맵', Icon: Grid3x3 },
  // 아래 둘은 **안 센 값**을 그린다 — 분포는 집계로 안 보인다.
  { value: 'box', label: '상자', Icon: BoxIcon },
  { value: 'scatter', label: '산점도', Icon: ScatterChart },
]

/** 원값을 그리는 것들 — 묶어 세는 대신 값을 그대로 받아 온다. */
const RAW_KINDS = new Set(['box', 'scatter'])

/** 그림 안에서 쓰는 키. 사람이 고른 칸 이름과 안 겹치게 둔다. */
const VALUE = '__value__'
const KEY = '__key__'

const METRICS = [
  { value: 'count', label: '건수' },
  { value: 'sum', label: '합계' },
  { value: 'avg', label: '평균' },
  { value: 'min', label: '최솟값' },
  { value: 'max', label: '최댓값' },
]

/** 「쪼개지 않음」. 빈 문자열은 Select 가 「고른 것 없음」 으로 보고 자리표시자로 돌아간다. */
const NO_SPLIT = '__none__'

interface Props {
  typeSlug: string
  /** 목록이 지금 쓰는 거르기 그대로. */
  query: ObjectQuery
  /**
   * 지금 설정. **화면이 들고 있다** — 뷰를 불러오면 그 뷰의 축으로 열려야 하고,
   * 저장할 때는 지금 축이 함께 담겨야 한다. 이 패널이 혼자 들고 있으면 둘 다 못 한다.
   */
  settings: SummarySettings
  onSettings: (next: SummarySettings) => void
  /** 막대를 눌렀을 때 — 걸 수 있는 축이면 부른다. */
  onPick: (field: string, key: string | null) => void
  onClose: () => void
}

/**
 * 원값을 plotly 의 trace 로.
 *
 * 상자는 무리마다 하나씩(무리가 없으면 전부 하나로), 산점도는 무리마다 점 무더기로.
 * **색은 이름으로 고정한다** — 같은 무리가 그림마다 다른 색이면 비교가 안 된다.
 */
function plotTraces(kind: string, points: Points): Record<string, unknown>[] {
  const groups = [...new Set(points.rows.map((one) => one.group || ''))]
  return groups.map((group) => {
    const rows = points.rows.filter((one) => (one.group || '') === group)
    const name = group || points.x_label
    return kind === 'box'
      ? {
          type: 'box',
          name,
          y: rows.map((one) => one.x),
          marker: { color: colorFor(name) },
          boxpoints: rows.length <= 40 ? 'all' : 'outliers',
          jitter: 0.4,
        }
      : {
          type: 'scatter',
          mode: 'markers',
          name,
          x: rows.map((one) => one.x),
          y: rows.map((one) => one.y),
          text: rows.map((one) => one.label),
          hovertemplate: '%{text}<br>%{x} · %{y}<extra></extra>',
          marker: { color: colorFor(name), size: 8 },
        }
  })
}

/** 이 축으로 묶은 값을 목록 거르기에 그대로 걸 수 있나. */
function filterable(field: string): boolean {
  return field === 'status' || field.startsWith('properties.')
}

export function SummaryPanel({ typeSlug, query, settings, onSettings, onPick, onClose }: Props) {
  const { groupBy, metric, metricField, chart: kind } = settings
  const patch = (next: Partial<SummarySettings>) => onSettings({ ...settings, ...next })
  const [data, setData] = useState<Summary | null>(null)
  const [points, setPoints] = useState<Points | null>(null)
  const raw = RAW_KINDS.has(settings.chart)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [pinning, setPinning] = useState(false)
  const { user } = useAuth()
  // 올릴 곳은 내 대표 소속이다 — 뷰를 부서와 함께 쓸 때와 같은 규칙(`ViewPicker`).
  const myWorkspace = user?.home_workspace_slug ?? user?.memberships[0]?.slug ?? null
  const canPin = Boolean(myWorkspace && isManagerOf(user, myWorkspace))

  // 거르기가 바뀌면 다시 센다. `query` 는 매 렌더 새 객체라 **내용**으로 비교한다 —
  // 안 그러면 이 효과가 끝없이 돈다.
  const signature = JSON.stringify(query)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    if (raw) {
      // **안 센 값**을 받아 온다. 분포는 집계로 안 보인다.
      const numbers = data?.metric_options ?? []
      const x = settings.x || numbers[0]?.field
      if (!x) {
        setLoading(false)
        return
      }
      objectApi
        .points(typeSlug, JSON.parse(signature) as ObjectQuery, {
          x,
          y: settings.chart === 'scatter' ? settings.y || numbers[1]?.field : null,
          groupBy: settings.splitBy || (settings.chart === 'box' ? groupBy : null),
        })
        .then((found) => {
          if (cancelled) return
          setPoints(found)
          setError(null)
        })
        .catch((caught: unknown) => {
          if (!cancelled) setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
      return () => {
        cancelled = true
      }
    }
    objectApi
      .summary(typeSlug, JSON.parse(signature) as ObjectQuery, {
        groupBy,
        splitBy: settings.splitBy || null,
        metric,
        metricField: metric === 'count' ? null : metricField,
        order: settings.order,
      })
      .then((found) => {
        if (cancelled) return
        setData(found)
        setError(null)
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [
    typeSlug,
    signature,
    groupBy,
    settings.splitBy,
    metric,
    metricField,
    settings.order,
    raw,
    settings.chart,
    settings.x,
    settings.y,
    // 숫자 칸 목록은 집계 응답이 알려 준다 — 처음 한 번은 그것을 기다린다.
    data?.metric_options,
  ])

  const numbers = data?.metric_options ?? []
  /**
   * 그림이 받는 행 — 이름과 값, 그리고 눌렀을 때 되찾을 원래 값.
   *
   * 쪼갰으면 계열마다 칸을 하나씩 편다. **없는 계열은 0 으로 채운다** — 빠뜨리면
   * 쌓은 막대에서 그 자리만 비어 사람은 데이터가 없는 것과 0 을 구별 못 한다.
   */
  const rows = useMemo(() => {
    const splits = data?.splits ?? []
    return (data?.buckets ?? []).map((one) => {
      const row: Record<string, unknown> = {
        name: one.label,
        [VALUE]: data?.metric === 'count' ? one.count : (one.value ?? 0),
        [KEY]: one.key,
        건수: one.count,
      }
      for (const label of splits) {
        const part = one.parts.find((each) => each.label === label)
        row[label] = part ? (data?.metric === 'count' ? part.count : (part.value ?? 0)) : 0
      }
      return row
    })
  }, [data])

  /** 계열 — 쪼갠 값의 **차례를 서버가 준다.** 그래야 같은 값이 언제나 같은 색이다. */
  const series = useMemo(
    () =>
      (data?.splits ?? []).length > 0
        ? (data?.splits ?? []).map((label) => ({ key: label, label, color: colorFor(label) }))
        : [{ key: VALUE, label: data?.metric_label ?? '건수' }],
    [data],
  )
  const canFilter = Boolean(data && filterable(data.group_field))

  /**
   * 지금 그림을 **같은 숫자로** 파일에. 막대를 보고 옮겨 적으면 그 사이에 틀리고, 틀린
   * 숫자가 회의에 들어간다. 원값 그림이면 그린 점 그대로(행 하나가 객체 하나).
   */
  const [exporting, setExporting] = useState(false)
  async function exportAs(format: 'csv' | 'xlsx') {
    const current = JSON.parse(signature) as ObjectQuery
    setExporting(true)
    setError(null)
    try {
      if (raw) {
        const x = settings.x || numbers[0]?.field
        if (!x) return
        await objectApi.exportPoints(
          typeSlug,
          current,
          {
            x,
            y: settings.chart === 'scatter' ? settings.y || numbers[1]?.field : null,
            groupBy: settings.splitBy || (settings.chart === 'box' ? groupBy : null),
          },
          format,
          `${typeSlug}-${points?.x_label ?? '값'}.${format}`,
        )
        return
      }
      await objectApi.exportSummary(
        typeSlug,
        current,
        {
          groupBy,
          splitBy: settings.splitBy || null,
          metric,
          metricField: metric === 'count' ? null : metricField,
          order: settings.order,
        },
        format,
        `${typeSlug}-${data?.group_label ?? '묶어보기'}별.${format}`,
      )
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="space-y-3 rounded-md border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <BarChart3 className="text-muted-foreground size-4" />
        <span className="text-sm font-medium">묶어 보기</span>

        <Select value={groupBy} onValueChange={(next) => patch({ groupBy: next })}>
          <SelectTrigger className="w-48" aria-label="묶을 축">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(data?.group_options ?? []).map((one) => (
              <SelectItem key={one.field} value={one.field}>
                {one.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* **두 번째 축.** 「부서별 몇 건」 다음 물음은 거의 언제나 「그 안에서 등급은」
            이다. 그때 거르기를 바꿔 가며 여섯 번 세게 하면 사람은 그 답을 포기한다. */}
        <Select
          value={settings.splitBy || NO_SPLIT}
          onValueChange={(next) => patch({ splitBy: next === NO_SPLIT ? '' : next })}
        >
          <SelectTrigger className="w-44" aria-label="쪼갤 축">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_SPLIT}>쪼개지 않음</SelectItem>
            {(data?.group_options ?? [])
              .filter((one) => one.field !== groupBy)
              .map((one) => (
                <SelectItem key={one.field} value={one.field}>
                  {one.label}로 쪼개기
                </SelectItem>
              ))}
          </SelectContent>
        </Select>

        {!raw && (
          <Select
            value={metric}
            onValueChange={(next) => {
              // 합·평균으로 바꾸면 셀 칸이 필요하다. 하나뿐이면 그것으로 정해 준다 —
              // 고를 것이 하나인 고르개는 묻는 시늉일 뿐이다.
              const field =
                next !== 'count' && !metricField && numbers.length > 0
                  ? numbers[0].field
                  : metricField
              patch({ metric: next, metricField: field })
            }}
          >
            <SelectTrigger className="w-32" aria-label="세는 방법">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {METRICS.map((one) => (
                <SelectItem
                  key={one.value}
                  value={one.value}
                  disabled={one.value !== 'count' && numbers.length === 0}
                >
                  {one.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        {/* 원값 그림은 **숫자 칸**을 받는다 — 세는 방법·차례는 뜻이 없어 감춘다. */}
        {raw && (
          <>
            <Select
              value={settings.x || numbers[0]?.field || ''}
              onValueChange={(next) => patch({ x: next })}
            >
              <SelectTrigger className="w-40" aria-label={kind === 'scatter' ? '가로 칸' : '값 칸'}>
                <SelectValue placeholder="숫자 칸" />
              </SelectTrigger>
              <SelectContent>
                {numbers.map((one) => (
                  <SelectItem key={one.field} value={one.field}>
                    {kind === 'scatter' ? `가로: ${one.label}` : one.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {kind === 'scatter' && (
              <Select
                value={settings.y || numbers[1]?.field || ''}
                onValueChange={(next) => patch({ y: next })}
              >
                <SelectTrigger className="w-40" aria-label="세로 칸">
                  <SelectValue placeholder="두 번째 숫자 칸" />
                </SelectTrigger>
                <SelectContent>
                  {numbers.map((one) => (
                    <SelectItem key={one.field} value={one.field}>
                      세로: {one.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </>
        )}

        {!raw && metric !== 'count' && (
          <Select value={metricField ?? ''} onValueChange={(next) => patch({ metricField: next })}>
            <SelectTrigger className="w-40" aria-label="셀 칸">
              <SelectValue placeholder="숫자 칸" />
            </SelectTrigger>
            <SelectContent>
              {numbers.map((one) => (
                <SelectItem key={one.field} value={one.field}>
                  {one.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        <div className="flex items-center rounded-md border">
          {KINDS.map((one) => (
            <button
              key={one.value}
              type="button"
              aria-label={one.label}
              aria-pressed={kind === one.value}
              title={one.label}
              className={cn('px-2 py-1.5', kind === one.value ? 'bg-muted' : 'hover:bg-muted/50')}
              onClick={() => patch({ chart: one.value })}
            >
              <one.Icon className="size-4" />
            </button>
          ))}
        </div>

        {/* 「가장 낮은 것」 을 찾는 물음이 따로 있다 — 점수가 제일 낮은 공급사, 불량이
            제일 적은 공정. 그때마다 데이터를 거꾸로 넣게 할 수는 없다. */}
        {!raw && (
          <Button
            variant="outline"
            size="sm"
            title={settings.order === 'desc' ? '많은 것부터' : '적은 것부터'}
            aria-label="차례 바꾸기"
            onClick={() => patch({ order: settings.order === 'desc' ? 'asc' : 'desc' })}
          >
            <ArrowUpDown className="mr-1 size-4" />
            {settings.order === 'desc' ? '많은 것부터' : '적은 것부터'}
          </Button>
        )}

        {/* 쌓기는 **쪼갰을 때만** 뜻이 있다. 계열이 하나면 쌓아도 같은 그림이다. */}
        {settings.splitBy && (kind === 'bar' || kind === 'area') && (
          <Button
            variant={settings.stacked ? 'secondary' : 'outline'}
            size="sm"
            aria-pressed={settings.stacked}
            title="쌓아서 전체를 보거나, 나란히 놓고 서로 견주거나"
            onClick={() => patch({ stacked: !settings.stacked })}
          >
            <Layers className="mr-1 size-4" />
            {settings.stacked ? '쌓음' : '나란히'}
          </Button>
        )}

        {loading && <Loader2 className="text-muted-foreground size-4 animate-spin" />}
        <div className="ml-auto flex items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                disabled={loading || exporting || (raw ? !points : !data)}
              >
                {exporting ? (
                  <Loader2 className="mr-1 size-4 animate-spin" />
                ) : (
                  <Download className="mr-1 size-4" />
                )}
                내보내기
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => void exportAs('xlsx')}>
                Excel (.xlsx)
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void exportAs('csv')}>CSV</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          {/* **의도가 생기는 자리에 단추를 둔다.** 못 올리는 사람에게는 누가 올리는지
              적는다 — 단추만 없으면 그 기능이 있는 줄도 모른다. */}
          {canPin ? (
            <Button variant="outline" size="sm" onClick={() => setPinning(true)}>
              <House className="mr-1 size-4" />
              홈에 올리기
            </Button>
          ) : (
            <span className="text-muted-foreground text-xs">부서 관리자가 홈에 올립니다</span>
          )}
          <Button variant="ghost" size="icon" aria-label="묶어 보기 닫기" onClick={onClose}>
            <X className="size-4" />
          </Button>
        </div>
      </div>

      <ErrorNotice error={error} />

      {pinning && myWorkspace && (
        <PinToHomeDialog
          typeSlug={typeSlug}
          workspaceSlug={myWorkspace}
          query={
            (query.conditions
              ? { q: query.q ?? '', conditions: query.conditions, status: null }
              : { q: query.q ?? '', conditions: [], status: null }) as SavedViewQuery
          }
          summary={
            settings.groupBy
              ? {
                  group_by: settings.groupBy,
                  split_by: settings.splitBy,
                  metric: settings.metric,
                  metric_field: settings.metricField,
                  chart: settings.chart,
                  stacked: settings.stacked,
                  order: settings.order,
                }
              : null
          }
          suggested={data ? `${data.group_label}별 ${data.metric_label}` : ''}
          onClose={() => setPinning(false)}
        />
      )}

      {data && !error && (
        <div className="space-y-1">
          {raw ? (
            /* **안 센 값들.** 상자는 무리별 분포를, 산점도는 두 칸의 관계를 보여 준다 —
               둘 다 집계로는 안 보이는 것이다. */
            points === null ? (
              <p className="text-muted-foreground py-8 text-center text-sm">
                숫자 칸을 고르면 그립니다.
              </p>
            ) : points.rows.length === 0 ? (
              <p className="text-muted-foreground py-8 text-center text-sm">
                값이 있는 것이 없습니다 — 값이 빈 행은 점이 되지 않습니다.
              </p>
            ) : (
              <LazyPlot
                height={320}
                title={
                  kind === 'box'
                    ? `${points.group_label || data.group_label}별 ${points.x_label} 분포`
                    : `${points.x_label} × ${points.y_label}`
                }
                data={plotTraces(kind, points)}
                layout={
                  kind === 'scatter'
                    ? {
                        xaxis: { title: { text: points.x_label } },
                        yaxis: { title: { text: points.y_label } },
                      }
                    : { yaxis: { title: { text: points.x_label } } }
                }
              />
            )
          ) : kind === 'heatmap' && data.splits.length > 0 ? (
            /* 히트맵은 plotly 가 그린다 — **두 축일 때만.** 색이 값이라 계열이 많아도
               읽히는 것이 장점이고, 그래서 쪼갠 값이 열둘까지여도 견딘다. */
            <LazyPlot
              height={Math.max(240, Math.min(460, rows.length * 30 + 120))}
              title={`${data.group_label} × ${data.split_label}`}
              data={[
                {
                  type: 'heatmap',
                  z: rows.map((row) => data.splits.map((label) => Number(row[label] ?? 0))),
                  x: data.splits,
                  y: rows.map((row) => String(row.name)),
                  colorscale: 'Blues',
                  hovertemplate: `%{y} · %{x}: %{z}<extra></extra>`,
                },
              ]}
            />
          ) : (
            <Chart
              kind={kind === 'heatmap' || kind === 'box' || kind === 'scatter' ? 'bar' : kind}
              data={rows}
              x="name"
              series={series}
              stacked={settings.stacked}
              height={Math.max(220, Math.min(420, rows.length * 28 + 80))}
              title={`${data.group_label}별 ${data.metric_label}`}
              emptyText="셀 것이 없습니다."
              onPick={
                canFilter
                  ? (row) => {
                      const key = row[KEY]
                      // 빈 칸은 「값이 없음」 이라 거르기로 옮길 값이 없다.
                      if (typeof key === 'string') onPick(data.group_field, key)
                    }
                  : undefined
              }
            />
          )}
          <p className="text-muted-foreground pt-1 text-xs">
            {/* **막대의 합과 전체가 다르면 그 차이를 적는다.** 안 적으면 사람은 그것을
                오류로 읽고, 그 뒤로 이 그림을 안 믿는다. */}
            {raw && points
              ? `점 ${points.rows.length.toLocaleString()}개`
              : `거른 것 전체 ${data.total.toLocaleString()}건`}
            {raw && points?.truncated && (
              <>
                {' · '}
                <strong>{points.total.toLocaleString()}개 중 앞의 것만</strong> 그렸습니다 —
                거르기로 좁히면 전부 보입니다
              </>
            )}
            {data.other_groups > 0 && (
              <>
                {' · '}
                <strong>그 밖에</strong> {data.other_groups}종류 {data.other_count}건은 접혔습니다
              </>
            )}
            {data.metric !== 'count' && ' · 숫자로 안 읽히는 값은 셈에서 빠집니다'}
            {canFilter && kind !== 'heatmap' && ' · 막대를 누르면 그것만 걸러집니다'}
            {kind === 'heatmap' && data.splits.length === 0 && (
              <> · 히트맵은 두 축이 필요합니다 — 「쪼갤 축」 을 고르세요</>
            )}
            {data.group_field === 'label' && data.metric === 'count' && (
              <>
                {' '}
                · 이름으로 묶고 건수를 세면 대개 전부 1 입니다 — 숫자 칸의 합·평균으로 바꾸면 순위가
                됩니다
              </>
            )}
            {data.other_splits > 0 && (
              <> · 쪼갠 값 {data.other_splits}가지는 빠졌습니다(색이 겹쳐 못 읽습니다)</>
            )}
          </p>
        </div>
      )}
    </div>
  )
}
