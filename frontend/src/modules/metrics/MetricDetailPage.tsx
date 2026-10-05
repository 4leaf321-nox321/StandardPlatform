/**
 * 지표 하나 — **표 · 추이 · 코호트 · 분석**, 그리고 셀마다 「N건 보기」.
 *
 * 머리에 계산 시각 · 닫힌 기간 · 겹침 · 못 묶은 수를 적는다 — 세어 둔 값은 그 사실을 숨기면
 * 안 된다. 기준 값으로 거르면 분모도 같이 걸리고, 셀의 수는 목록 조건으로 그대로 돌아간다.
 *
 * 「계산 기록」 은 최근 계산들이다 — 실패한 계산은 옛 값을 그대로 두므로, 값이 왜 안 바뀌었는지
 * 물을 자리가 여기다(운영 안내 5d 가 이 탭을 가리킨다).
 */

import { Suspense, lazy, useMemo, useState } from 'react'
import { Link, useLocation, useParams, useSearchParams } from 'react-router-dom'

import { AlertsTab } from '@/modules/metrics/AlertsTab'
import { metricsApi } from '@/modules/metrics/api'
import type {
  Metric,
  MetricCohort,
  MetricDim,
  MetricRun,
  MetricSeries,
  MetricTable,
  ReadHeader,
} from '@/modules/metrics/api'
import { GRAIN_LABELS, drillHref, shownNumber } from '@/modules/metrics/metricDrill'
import { Chart } from '@/shared/charts'
import { LazyPlot } from '@/shared/charts/LazyPlot'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/shared/components/ui/tabs'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

// 분석은 그림 도구가 무거워 탭을 열 때만 받는다.
const AnalysisTab = lazy(() => import('@/modules/metrics/analysis/AnalysisTab'))

const ALL = '__all__'
const EMPTY = '__empty__'
/** 코호트 표에 늘어놓을 경과 칸의 상한 — 그보다 길면 히트맵만 읽는다. */
const AGES_IN_TABLE = 24

type Tab = 'table' | 'series' | 'cohort' | 'analysis' | 'alerts' | 'runs'
const TABS: Tab[] = ['table', 'series', 'cohort', 'analysis', 'alerts', 'runs']
type By = 'none' | 'period' | 'cohort'

const RUN_STATUS: Record<string, { label: string; className: string }> = {
  running: { label: '도는 중', className: 'text-muted-foreground' },
  ok: { label: '성공', className: 'text-emerald-700 dark:text-emerald-400' },
  failed: { label: '실패', className: 'text-destructive' },
}

/** 계산 한 번이 걸린 시간 — 끝나지 않았으면 「—」. */
function tookOf(run: MetricRun): string {
  if (!run.finished_at) return '—'
  const took = (Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000
  return Number.isFinite(took) ? `${shownNumber(Math.max(0, took), 1)}초` : '—'
}

/** 응답 머리 — 계산 시각 · 신선도 · 겹침 · 못 묶은 수 · 잘림 · 분모 없음. */
function HeaderLine({ header }: { header: ReadHeader }) {
  const parts: string[] = []
  parts.push(
    header.computed_at
      ? `계산 시각 ${shownDateTime(header.computed_at)}`
      : '아직 한 번도 안 셌습니다',
  )
  if (header.stale) parts.push('오래됨 — 세 주기가 지나도록 안 셌습니다')
  if (header.settle_days > 0) parts.push(`닫힌 기간: ${header.settle_days}일 지난 것`)
  if (header.stay)
    parts.push(
      header.stay.periods_from_label
        ? `기간마다 최근 ${header.stay.periods_from_label}만큼(최대 ${header.stay.periods}기간)의 합`
        : `기간마다 최근 ${header.stay.periods}기간의 합`,
    )
  if (header.overlap) parts.push('겹침 — 한 기록이 여러 셀에 듭니다')
  if (header.unbucketed > 0)
    parts.push(`날짜가 없거나 못 읽은 기록 ${shownNumber(header.unbucketed)}건`)
  if (header.unbucketed_cohort > 0)
    parts.push(`코호트가 빈 기록 ${shownNumber(header.unbucketed_cohort)}건`)
  if (header.negative_age > 0)
    parts.push(`코호트보다 앞선 기록 ${shownNumber(header.negative_age)}건`)
  if (header.truncated) parts.push('상한에서 잘렸습니다 — 기준이나 기간을 좁히세요')
  if (header.denominator?.missing)
    parts.push(`분모가 없어 비율이 빈 셀 ${shownNumber(header.denominator.missing)}개`)
  return <p className="text-muted-foreground text-xs">{parts.join(' · ')}</p>
}

/** 기준 값으로 거르기 — 값 목록은 서버가 세어 둔 것에서 온다(이름 포함). */
function DimFilter({
  slug,
  dim,
  value,
  onChange,
}: {
  slug: string
  dim: MetricDim
  value: string | null | undefined
  onChange: (next: string | null | undefined) => void
}) {
  const values = useResource(() => metricsApi.dims(slug, dim.name), [slug, dim.name])
  const options = useMemo(
    () => [
      { value: ALL, label: '전체' },
      ...(values.data?.values ?? []).map((one) => ({
        value: one.value === null ? EMPTY : one.value,
        label: one.label,
        hint: `${shownNumber(one.count)}건`,
      })),
    ],
    [values.data],
  )
  const current = value === undefined ? ALL : value === null ? EMPTY : value
  return (
    <div className="space-y-1">
      <Label htmlFor={`filter-${dim.name}`}>{dim.label}</Label>
      <SearchablePicker
        id={`filter-${dim.name}`}
        options={options}
        value={current}
        loading={values.loading}
        onChange={(next) => onChange(next === ALL ? undefined : next === EMPTY ? null : next)}
        placeholder="전체"
        searchPlaceholder="값 이름으로 찾기"
      />
    </div>
  )
}

function Drill({ drill, count }: { drill: MetricTable['cells'][number]['drill']; count: number }) {
  return (
    <Link to={drillHref(drill)} className="whitespace-nowrap hover:underline">
      {shownNumber(count)}건 보기
      {drill.partial.length > 0 && (
        <span
          title={`조건으로 못 적은 축: ${drill.partial.join(', ')} — 목록이 더 많을 수 있습니다`}
        >
          {' '}
          ≈
        </span>
      )}
    </Link>
  )
}

export default function MetricDetailPage() {
  const { slug = '' } = useParams()
  const { search } = useLocation()
  // 주소가 바뀌면 새로 연다 — 경보 탭의 「분석 열기」 처럼 같은 화면 안에서 눌러도 그 인자로.
  return <MetricDetail key={`${slug}${search}`} slug={slug} />
}

function MetricDetail({ slug }: { slug: string }) {
  const [search] = useSearchParams()
  const metric = useResource(() => metricsApi.get(slug), [slug])
  // 경보 알림의 링크(ADR 0016)는 `?tab=analysis&recipe=…&d.<기준>=…` 로 그 분석을 그 인자로
  // 연다 — 처음 한 번만 읽는다(그 뒤의 고르기는 화면의 것).
  const [opened] = useState(() => {
    const asked = search.get('tab') as Tab | null
    const filtersAsked: Record<string, string | null> = {}
    const params: Record<string, string> = {}
    search.forEach((value, key) => {
      if (key.startsWith('d.')) filtersAsked[key.slice(2)] = value === '' ? null : value
      else if (key !== 'tab' && key !== 'recipe') params[key] = value
    })
    const recipe = search.get('recipe')
    return {
      tab: asked && TABS.includes(asked) ? asked : 'table',
      filters: filtersAsked,
      periodFrom: params.period_from ?? '',
      periodTo: params.period_to ?? '',
      analysis: recipe ? { recipe, params } : null,
    }
  })
  const [tab, setTab] = useState<Tab>(opened.tab)
  const [dims, setDims] = useState<string[] | null>(null)
  const [by, setBy] = useState<By>('none')
  const [filters, setFilters] = useState<Record<string, string | null>>(opened.filters)
  const [periodFrom, setPeriodFrom] = useState(opened.periodFrom)
  const [periodTo, setPeriodTo] = useState(opened.periodTo)
  const [split, setSplit] = useState('')
  const [cumulative, setCumulative] = useState(true)
  const [ratio, setRatio] = useState(true)

  const found = metric.data
  const chosen = dims ?? (found?.dims[0] ? [found.dims[0].name] : [])
  // 조건 비율은 같은 기록 전체가 분모다 — 분모 지표 없이도 비율이 선다.
  const hasDenominator = Boolean(found?.spec.denominator) || found?.spec.measure === 'share'
  const showRatio = hasDenominator && ratio
  const common = useMemo(
    () => ({
      filters,
      period_from: periodFrom || undefined,
      period_to: periodTo || undefined,
    }),
    [filters, periodFrom, periodTo],
  )
  const tableKey = JSON.stringify([chosen, by, common])
  const table = useResource<MetricTable | null>(
    () =>
      tab === 'table' && found
        ? metricsApi.values(slug, { ...common, dims: chosen, by: by === 'none' ? [] : [by] })
        : Promise.resolve(null),
    [slug, tab, tableKey, Boolean(found)],
  )
  const seriesKey = JSON.stringify([split, common])
  const series = useResource<MetricSeries | null>(
    () =>
      tab === 'series' && found
        ? metricsApi.series(slug, { ...common, split: split || undefined })
        : Promise.resolve(null),
    [slug, tab, seriesKey, Boolean(found)],
  )
  const runs = useResource<MetricRun[] | null>(
    () => (tab === 'runs' ? metricsApi.runs(slug) : Promise.resolve(null)),
    [slug, tab],
  )
  const cohortKey = JSON.stringify([filters, cumulative])
  const cohort = useResource<MetricCohort | null>(
    () =>
      tab === 'cohort' && found
        ? metricsApi.cohort(slug, { filters, cumulative })
        : Promise.resolve(null),
    [slug, tab, cohortKey, Boolean(found)],
  )

  if (metric.error) return <ErrorNotice error={metric.error} />
  if (!found) return null

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader
        title={found.label}
        description={found.description || `${found.source_type_label} · ${found.measure_label}`}
        back={{ to: '/metrics', label: '지표' }}
      />
      {found.broken && <ErrorNotice error={new Error(found.broken)} />}

      <section className="grid gap-3 md:grid-cols-3">
        {found.dims.map((dim) => (
          <DimFilter
            key={dim.name}
            slug={slug}
            dim={dim}
            value={dim.name in filters ? filters[dim.name] : undefined}
            onChange={(next) =>
              setFilters((current) => {
                const copy = { ...current }
                if (next === undefined) delete copy[dim.name]
                else copy[dim.name] = next
                return copy
              })
            }
          />
        ))}
        {found.grain && (
          <div className="flex gap-2">
            <div className="space-y-1">
              <Label htmlFor="period-from">기간 시작</Label>
              <Input
                id="period-from"
                type="date"
                value={periodFrom}
                onChange={(event) => setPeriodFrom(event.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="period-to">기간 끝(앞까지)</Label>
              <Input
                id="period-to"
                type="date"
                value={periodTo}
                onChange={(event) => setPeriodTo(event.target.value)}
              />
            </div>
          </div>
        )}
        {hasDenominator && (
          <label className="flex items-center gap-2 self-end text-sm">
            <input
              type="checkbox"
              checked={ratio}
              onChange={(event) => setRatio(event.target.checked)}
            />
            비율로 보기
          </label>
        )}
      </section>

      <Tabs value={tab} onValueChange={(next) => setTab(next as Tab)}>
        <TabsList>
          <TabsTrigger value="table">표</TabsTrigger>
          {found.grain && <TabsTrigger value="series">추이</TabsTrigger>}
          {found.cohort_grain && <TabsTrigger value="cohort">코호트</TabsTrigger>}
          {found.analyses.length > 0 && <TabsTrigger value="analysis">분석</TabsTrigger>}
          <TabsTrigger value="alerts">경보</TabsTrigger>
          <TabsTrigger value="runs">계산 기록</TabsTrigger>
        </TabsList>

        <TabsContent value="table" className="space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <span className="text-sm">기준</span>
              <div className="flex flex-wrap gap-1">
                {found.dims.map((dim) => {
                  const on = chosen.includes(dim.name)
                  return (
                    <Button
                      key={dim.name}
                      size="sm"
                      variant={on ? 'default' : 'outline'}
                      aria-pressed={on}
                      onClick={() =>
                        setDims(
                          on ? chosen.filter((one) => one !== dim.name) : [...chosen, dim.name],
                        )
                      }
                    >
                      {dim.label}
                    </Button>
                  )
                })}
              </div>
            </div>
            {found.grain && (
              <div className="space-y-1">
                <Label htmlFor="by">기간으로 묶기</Label>
                <Select value={by} onValueChange={(next) => setBy(next as By)}>
                  <SelectTrigger id="by" className="w-40">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">묶지 않음</SelectItem>
                    <SelectItem value="period">
                      {GRAIN_LABELS[found.grain] ?? found.grain}별
                    </SelectItem>
                    {found.cohort_grain && <SelectItem value="cohort">코호트별</SelectItem>}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
          {table.error && <ErrorNotice error={table.error} />}
          {table.data && (
            <>
              <HeaderLine header={table.data} />
              <Table>
                <TableHeader>
                  <TableRow>
                    {by !== 'none' && <TableHead>{by === 'period' ? '기간' : '코호트'}</TableHead>}
                    {chosen.map((name) => (
                      <TableHead key={name}>
                        {found.dims.find((one) => one.name === name)?.label ?? name}
                      </TableHead>
                    ))}
                    <TableHead className="text-right">건수</TableHead>
                    {found.spec.measure !== 'count' && (
                      <TableHead className="text-right">
                        {found.spec.measure === 'share' ? '조건 건수' : found.measure_label}
                      </TableHead>
                    )}
                    {showRatio && (
                      <TableHead className="text-right">
                        {found.spec.measure === 'share' ? '비율(%)' : '비율'}
                      </TableHead>
                    )}
                    {by === 'period' && <TableHead>닫힘</TableHead>}
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {table.data.cells.map((cell, index) => (
                    <TableRow key={index}>
                      {by !== 'none' && (
                        <TableCell>
                          {by === 'period' ? cell.period_label : cell.cohort_label}
                        </TableCell>
                      )}
                      {chosen.map((name) => (
                        <TableCell key={name}>{cell.labels[name]}</TableCell>
                      ))}
                      <TableCell className="text-right">{shownNumber(cell.count)}</TableCell>
                      {found.spec.measure !== 'count' && (
                        <TableCell className="text-right">
                          {cell.value_drill ? (
                            <Drill drill={cell.value_drill} count={cell.value ?? 0} />
                          ) : (
                            shownNumber(cell.value)
                          )}
                        </TableCell>
                      )}
                      {showRatio && (
                        <TableCell className="text-right">{shownNumber(cell.ratio)}</TableCell>
                      )}
                      {by === 'period' && (
                        <TableCell className="text-muted-foreground text-xs">
                          {cell.closed ? '닫힘' : '열림'}
                        </TableCell>
                      )}
                      <TableCell className="text-right">
                        <Drill drill={cell.drill} count={cell.count} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <p className="text-muted-foreground text-xs">
                합 {shownNumber(table.data.total_count)}건
                {table.data.total_value !== null &&
                  found.spec.measure !== 'count' &&
                  ` · ${found.measure_label} ${shownNumber(table.data.total_value)}`}
              </p>
            </>
          )}
        </TabsContent>

        <TabsContent value="series" className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="split">선 나누기</Label>
            <Select
              value={split || ALL}
              onValueChange={(next) => setSplit(next === ALL ? '' : next)}
            >
              <SelectTrigger id="split" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>한 선</SelectItem>
                {found.dims.map((dim) => (
                  <SelectItem key={dim.name} value={dim.name}>
                    {dim.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {series.error && <ErrorNotice error={series.error} />}
          {series.data && <SeriesView data={series.data} metric={found} ratio={showRatio} />}
        </TabsContent>

        <TabsContent value="cohort" className="space-y-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={cumulative}
              onChange={(event) => setCumulative(event.target.checked)}
            />
            누적 — 코호트마다 경과순으로 더한 값
          </label>
          {cohort.error && <ErrorNotice error={cohort.error} />}
          {cohort.data && (
            <CohortView data={cohort.data} ratio={showRatio} cumulative={cumulative} />
          )}
        </TabsContent>

        <TabsContent value="analysis" className="space-y-3">
          {tab === 'analysis' && (
            <Suspense fallback={<p className="text-muted-foreground text-sm">불러오는 중…</p>}>
              <AnalysisTab metric={found} read={common} initial={opened.analysis} />
            </Suspense>
          )}
        </TabsContent>

        <TabsContent value="alerts" className="space-y-3">
          {tab === 'alerts' && <AlertsTab metric={found} />}
        </TabsContent>

        <TabsContent value="runs" className="space-y-3">
          {runs.error && <ErrorNotice error={runs.error} />}
          {runs.data && <RunsView runs={runs.data} />}
        </TabsContent>
      </Tabs>
    </div>
  )
}

function SeriesView({
  data,
  metric,
  ratio,
}: {
  data: MetricSeries
  metric: Metric
  ratio: boolean
}) {
  const valueOf = (point: MetricSeries['lines'][number]['points'][number]) =>
    ratio ? point.ratio : point.value
  const rows = useMemo(() => {
    const byPeriod = new Map<string, Record<string, unknown>>()
    for (const line of data.lines) {
      for (const point of line.points) {
        const row = byPeriod.get(point.period) ?? { name: point.label }
        row[line.label] = valueOf(point) ?? 0
        byPeriod.set(point.period, row)
      }
    }
    return [...byPeriod.values()]
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, ratio])
  const single = data.lines.length === 1 ? data.lines[0] : null
  return (
    <>
      <HeaderLine header={data} />
      {data.lines_truncated && (
        <p className="text-muted-foreground text-xs">선이 많아 위의 것만 그렸습니다.</p>
      )}
      <Chart
        kind="line"
        data={rows}
        x="name"
        series={data.lines.map((line) => ({ key: line.label }))}
        height={320}
        title={`${metric.label} 추이`}
        emptyText="이 범위에는 셀이 없습니다."
      />
      {single && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>기간</TableHead>
              <TableHead className="text-right">건수</TableHead>
              {metric.spec.measure !== 'count' && (
                <TableHead className="text-right">{metric.measure_label}</TableHead>
              )}
              {ratio && <TableHead className="text-right">비율</TableHead>}
              <TableHead className="text-right">전기</TableHead>
              <TableHead className="text-right">전년 동기</TableHead>
              <TableHead>닫힘</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {single.points.map((point) => (
              <TableRow key={point.period}>
                <TableCell>{point.label}</TableCell>
                <TableCell className="text-right">{shownNumber(point.count)}</TableCell>
                {metric.spec.measure !== 'count' && (
                  <TableCell className="text-right">{shownNumber(point.value)}</TableCell>
                )}
                {ratio && <TableCell className="text-right">{shownNumber(point.ratio)}</TableCell>}
                <TableCell className="text-right">{shownNumber(point.prev)}</TableCell>
                <TableCell className="text-right">{shownNumber(point.yoy)}</TableCell>
                <TableCell className="text-muted-foreground text-xs">
                  {point.closed ? '닫힘' : '열림'}
                </TableCell>
                <TableCell className="text-right">
                  <Drill drill={point.drill} count={point.count} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </>
  )
}

function CohortView({
  data,
  ratio,
  cumulative,
}: {
  data: MetricCohort
  ratio: boolean
  cumulative: boolean
}) {
  const pick = (cell: MetricCohort['rows'][number]['cells'][number]) =>
    ratio ? cell.ratio : cumulative ? cell.cumulative : cell.value
  const ages = data.ages.slice(0, AGES_IN_TABLE)
  return (
    <>
      <HeaderLine header={data} />
      {data.rows.length > 0 && (
        <LazyPlot
          height={Math.max(240, Math.min(520, data.rows.length * 26 + 120))}
          title={`코호트 × 경과${ratio ? ' (비율)' : cumulative ? ' (누적)' : ''}`}
          data={[
            {
              type: 'heatmap',
              z: data.rows.map((row) => row.cells.map((cell) => pick(cell) ?? null)),
              x: data.ages.map((age) => `${age}`),
              y: data.rows.map((row) => row.label),
              colorscale: 'Blues',
              hovertemplate: '%{y} · %{x}개월째: %{z}<extra></extra>',
            },
          ]}
          layout={{ xaxis: { title: { text: '경과' } }, showlegend: false }}
        />
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>코호트</TableHead>
            {data.denominator && <TableHead className="text-right">분모</TableHead>}
            {ages.map((age) => (
              <TableHead key={age} className="text-right">
                {age}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.rows.map((row) => (
            <TableRow key={row.cohort}>
              <TableCell>{row.label}</TableCell>
              {data.denominator && (
                <TableCell className="text-right">{shownNumber(row.denominator)}</TableCell>
              )}
              {ages.map((age) => {
                const cell = row.cells[age]
                if (!cell) return <TableCell key={age} />
                return (
                  <TableCell key={age} className="text-right">
                    <Link
                      to={drillHref(cell.drill)}
                      className="hover:underline"
                      title={`${shownNumber(cell.count)}건 보기${cell.closed ? '' : ' (열린 기간)'}`}
                    >
                      {shownNumber(pick(cell))}
                    </Link>
                  </TableCell>
                )
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {data.ages.length > AGES_IN_TABLE && (
        <p className="text-muted-foreground text-xs">
          경과 {AGES_IN_TABLE}칸까지만 표로 — 나머지는 히트맵에서 봅니다.
        </p>
      )}
    </>
  )
}

function RunsView({ runs }: { runs: MetricRun[] }) {
  if (runs.length === 0) {
    return <p className="text-muted-foreground text-sm">아직 한 번도 안 셌습니다.</p>
  }
  return (
    <>
      <p className="text-muted-foreground text-xs">
        최근 {runs.length}번. 실패한 계산은 옛 값을 그대로 두고 이유를 남깁니다.
      </p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>시작</TableHead>
            <TableHead>상태</TableHead>
            <TableHead className="text-right">걸린 시간</TableHead>
            <TableHead className="text-right">기록</TableHead>
            <TableHead className="text-right">셀</TableHead>
            <TableHead className="text-right">날짜 없음</TableHead>
            <TableHead>이유</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((run) => {
            const status = RUN_STATUS[run.status] ?? { label: run.status, className: '' }
            const done = run.status === 'ok'
            return (
              <TableRow key={run.id}>
                <TableCell className="whitespace-nowrap">{shownDateTime(run.started_at)}</TableCell>
                <TableCell className={status.className}>{status.label}</TableCell>
                <TableCell className="text-right">{tookOf(run)}</TableCell>
                <TableCell className="text-right">{done ? shownNumber(run.rows) : '—'}</TableCell>
                <TableCell className="text-right">{done ? shownNumber(run.cells) : '—'}</TableCell>
                <TableCell className="text-right">
                  {done ? shownNumber(run.stats.unbucketed ?? 0) : '—'}
                </TableCell>
                <TableCell className="text-destructive text-xs">{run.error ?? ''}</TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </>
  )
}
