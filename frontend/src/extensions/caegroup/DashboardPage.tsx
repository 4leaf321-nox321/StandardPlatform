/**
 * 디지털 트윈 › 대시보드 — **지금 어디까지 왔고, 무엇이 안 채워졌나.**
 *
 * 세 가지를 한 화면에 둔다:
 *   1. 평가 완료율   축마다 몇 건을 매겼나 (숫자 + 얇은 막대)
 *   2. 모판          고른 축의 수준을 색으로 (묶음 = 담당 부서)
 *   3. 최근 변경     누가 무엇을 고쳤나
 *
 * ⚠️ **분포와 벽은 같은 자료에서 나온다**(`/board` 한 번). 서버가 분포를 따로 세어 주면
 *    둘이 갈릴 수 있고, 그때 어느 쪽이 맞는지 아무도 답할 수 없다.
 * ⚠️ **색은 서열 램프 하나**(같은 파랑의 밝기 단계)다. 계열 색을 쓰면 사람이 빨강을
 *    경고로 읽어 순서가 아니라 뜻을 읽는다. 미평가는 색이 없다(점선).
 * ⚠️ **표로 보는 길이 있어야 한다** — 색만으로 값을 전하지 않는다. 「역량」 화면의 목록이
 *    그 표이고, 벽의 액자를 누르면 그 줄로 간다.
 */

import { ArrowRight } from 'lucide-react'
import { lazy, Suspense, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { LEVEL_NONE_LIGHT, levelColor } from '@/shared/charts'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Button } from '@/shared/components/ui/button'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/shared/components/ui/tabs'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

import { dtApi, type AxisDef, type Defs, type Tile } from './api'
import type { WallItem } from './TileWall'

// 벽은 `d3-hierarchy` 를 쓴다 — 대시보드를 안 여는 사람이 그만큼을 받지 않게 매단다.
const TileWall = lazy(() => import('./TileWall').then((one) => ({ default: one.TileWall })))

/** 축의 서열 자리 — **축 종류마다 읽는 법이 다르다.** */
function levelOf(axis: AxisDef, tile: Tile): { index: number; label: string; steps: number } {
  const saved = tile.levels[axis.key]
  const rungs = axis.hide_empty ? axis.rungs.slice(1) : axis.rungs
  if (!saved) return { index: -1, label: '미평가', steps: rungs.length }
  if (axis.kind === 'set') {
    // 선후가 없는 항목이라 **서열은 켠 개수**다.
    const on = saved.rungs.length
    const names = saved.rungs
      .map((key) => axis.rungs.find((one) => one.key === key)?.label ?? key)
      .join(' · ')
    return { index: on === 0 ? -1 : on - 1, label: names || '미평가', steps: rungs.length }
  }
  const found = rungs.findIndex((one) => one.key === saved.rung)
  const label = rungs[found]?.label ?? saved.rung ?? '미평가'
  const shown = axis.unit && saved.value !== null ? `${saved.value}${axis.unit} · ${label}` : label
  return { index: found, label: shown, steps: rungs.length }
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const defs = useResource(() => dtApi.defs(), [])
  const board = useResource(() => dtApi.board(), [])
  const coverage = useResource(() => dtApi.coverage(), [])
  const [axisKey, setAxisKey] = useState<string | null>(null)
  // 화면이 어두운가 — 서열 램프는 그 화면의 표면에서 다시 고른다.
  const dark =
    typeof document !== 'undefined' && document.documentElement.classList.contains('dark')

  const defsData: Defs | null = defs.data
  const axis = defsData?.axes.find((one) => one.key === (axisKey ?? defsData.axes[0]?.key)) ?? null
  const tiles = board.data?.tiles ?? []

  const items: WallItem[] = axis
    ? tiles.map((tile) => {
        const level = levelOf(axis, tile)
        return { tile, index: level.index, label: level.label }
      })
    : []
  const steps = axis ? (axis.hide_empty ? axis.rungs.length - 1 : axis.rungs.length) : 1

  // 분포는 벽과 **같은 목록**에서 센다.
  const spread = axis
    ? (axis.hide_empty ? axis.rungs.slice(1) : axis.rungs).map((rung, index) => ({
        key: rung.key,
        label: axis.kind === 'set' ? `${index + 1}개 켬` : rung.label,
        count: items.filter((one) => one.index === index).length,
      }))
    : []
  const unassessed = items.filter((one) => one.index < 0).length

  return (
    <div className="space-y-6">
      <PageHeader
        title="대시보드"
        description="연계마다의 수준과 평가 완료율입니다. 액자를 누르면 「역량」 화면의 그 연계로 갑니다."
      />

      <ErrorNotice error={defs.error ?? board.error ?? coverage.error} />

      {/* 1. 평가 완료율 — 숫자가 먼저, 막대는 그 옆에서 크기를 말한다. */}
      <section className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {(coverage.data?.axes ?? []).map((one) => (
          <div key={one.axis} className="rounded-md border p-3">
            <p className="text-muted-foreground text-xs">{one.label}</p>
            <p className="text-2xl font-semibold tabular-nums">
              {Math.round(one.ratio * 100)}
              <span className="text-muted-foreground ml-1 text-sm font-normal">%</span>
            </p>
            <p className="text-muted-foreground text-xs tabular-nums">
              {one.assessed} / {coverage.data?.pairs ?? 0}건
            </p>
            <div
              className="bg-muted mt-2 h-1.5 overflow-hidden rounded-full"
              role="img"
              aria-label={`${one.label} 평가 완료율 ${Math.round(one.ratio * 100)}%`}
            >
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.round(one.ratio * 100)}%`,
                  background: levelColor(4, 5, dark),
                }}
              />
            </div>
          </div>
        ))}
      </section>

      {/* 2. 모판 — 축을 고르면 그 축의 수준이 색이 된다. */}
      {tiles.length === 0 ? (
        <EmptyState
          title="등록된 연계가 없습니다"
          hint="「역량」 화면에서 시험 항목과 시뮬레이션 해석을 연계로 등록하면 이 자리에 섭니다."
        />
      ) : (
        <section className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <Tabs value={axis?.key ?? ''} onValueChange={setAxisKey}>
              <TabsList>
                {(defsData?.axes ?? []).map((one) => (
                  <TabsTrigger key={one.key} value={one.key}>
                    {one.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <Button variant="outline" size="sm" onClick={() => navigate('pairs')}>
              표로 보기 <ArrowRight className="size-4" />
            </Button>
          </div>

          {/* 범례 — 색만으로 서열의 이름을 알 수 없다. 분포 수를 함께 적는다. */}
          <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            {spread.map((one, index) => (
              <li key={one.key} className="flex items-center gap-1.5">
                <span
                  className="inline-block size-3 rounded"
                  style={{ background: levelColor(index, steps, dark) }}
                />
                {one.label}
                <span className="text-muted-foreground tabular-nums">{one.count}</span>
              </li>
            ))}
            <li className="flex items-center gap-1.5">
              <span
                className="inline-block size-3 rounded border border-dashed"
                style={{ background: dark ? 'transparent' : LEVEL_NONE_LIGHT }}
              />
              미평가
              <span className="text-muted-foreground tabular-nums">{unassessed}</span>
            </li>
          </ul>

          <Suspense fallback={<Skeleton className="h-64 w-full" />}>
            {axis && (
              <TileWall
                items={items}
                axis={axis}
                steps={steps}
                dark={dark}
                onPick={(tile) => navigate(`pairs?pair=${tile.id}`)}
              />
            )}
          </Suspense>
        </section>
      )}

      {/* 3. 최근 변경 — 누가 무엇을 고쳤나. 누르면 그 연계로 간다. */}
      <section className="space-y-2">
        <h2 className="text-base font-semibold">최근 변경</h2>
        {(board.data?.recent ?? []).length === 0 ? (
          <p className="text-muted-foreground text-sm">아직 평가 기록이 없습니다.</p>
        ) : (
          <ul className="divide-y rounded-md border text-sm">
            {(board.data?.recent ?? []).map((one, index) => (
              <li key={index} className="flex flex-wrap items-baseline gap-2 p-2">
                <span className="text-muted-foreground text-xs tabular-nums">
                  {shownDateTime(one.changed_at)}
                </span>
                <button
                  type="button"
                  className="font-medium hover:underline"
                  onClick={() => navigate(`pairs?pair=${one.pair_id}`)}
                >
                  {one.label}
                </button>
                <span className="text-muted-foreground">{one.axis_label}</span>
                <span className="text-muted-foreground truncate">{one.note}</span>
                <span className="text-muted-foreground ml-auto text-xs">
                  {one.changed_by_label}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
