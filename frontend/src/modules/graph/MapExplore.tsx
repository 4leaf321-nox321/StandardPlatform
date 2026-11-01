/**
 * 탐색의 「전체 지도」 — 왼쪽 판(기록 타입 · 판 · 전체 다시 배치), 가운데 지도(lazy), 오른쪽 상세(ADR 0046).
 *
 * 지도의 자료(`/graph/map`)는 판 번호 · 집계 시각으로 ETag 가 붙어 다시 열 때 브라우저가 묻기만 한다.
 * 상세 판은 탐색 화면의 것(`NodeDetail`)을 호스트가 넘겨준다 — 같은 객체가 두 모양으로 안 보이게.
 */

import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Loader2, Map as MapIcon, RotateCw, Search, X } from 'lucide-react'

import { graphApi } from '@/modules/graph/api'
import type { MapData, MapLinks } from '@/modules/graph/api'
import type { MapSelection } from '@/modules/graph/MapView'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { shownDateTime } from '@/shared/lib/datetime'

/** 그리기(sigma)는 무겁다 — 지도를 열 때만 받는다. */
const MapView = lazy(() => import('@/modules/graph/MapView'))

export interface MapExploreProps {
  /** 켠 기록 타입(slug) — 비면 전부. 주소의 `mty`. */
  types: Set<string>
  onTypes: (next: Set<string>) => void
  /** 주소의 `at` — 이 객체로 간다. */
  at: string
  onAt: (id: string) => void
  /** 주소의 확대 수준(`z`, 0 = 지도 전체). */
  zoom: number | null
  onZoom: (level: number) => void
  typeColor: (slug: string) => string
  onClose: () => void
  /** 고른 것의 상세 — 호스트가 탐색의 상세 판으로 그린다. 「여기서 정적 그리기」 를 품는다. */
  detail: (selection: MapSelection, close: () => void) => React.ReactNode
  onOpen: (selection: MapSelection) => void
  wide: boolean
  onToggleWide: () => void
}

function levelOf(ratio: number): number {
  return Math.max(0, Math.round(Math.log2(1 / Math.max(ratio, 1e-9))))
}

export function MapExplore({
  types,
  onTypes,
  at,
  onAt,
  zoom,
  onZoom,
  typeColor,
  onClose,
  detail,
  onOpen,
  wide,
  onToggleWide,
}: MapExploreProps) {
  const [data, setData] = useState<MapData | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let live = true
    setError(null)
    graphApi
      .map()
      .then((got) => {
        if (live) setData(got)
      })
      .catch((caught: unknown) => {
        if (live) setError(caught instanceof Error ? caught : new Error(String(caught)))
      })
    return () => {
      live = false
    }
  }, [tick])
  // 지도 작업이 돌면 30초마다 다시 묻는다 — 끝나면 새 판이 선다.
  useEffect(() => {
    if (!data?.job) return undefined
    const timer = window.setTimeout(() => setTick((value) => value + 1), 30_000)
    return () => window.clearTimeout(timer)
  }, [data])

  const records = useMemo(() => (data?.types ?? []).filter((one) => one.record), [data])
  /** 켠 기록 타입 — 주소가 비었거나 지도에 없는 것만 적혔으면 전부. */
  const on = useMemo(() => {
    const known = new Set(records.map((one) => one.slug))
    const picked = [...types].filter((one) => known.has(one))
    return new Set(picked.length ? picked : known)
  }, [types, records])
  const toggle = (slug: string) => {
    const next = new Set(on)
    if (next.has(slug)) next.delete(slug)
    else next.add(slug)
    onTypes(next.size === records.length ? new Set() : next)
  }

  // --- 고른 것 · 이 자리로 ---------------------------------------------------------------
  const [selection, setSelection] = useState<MapSelection | null>(null)
  const [goTo, setGoTo] = useState<{ x: number; y: number; ratio: number; nonce: number } | null>(
    null,
  )
  const [where, setWhere] = useState<string | null>(null)
  const nonce = useRef(0)
  const span = data ? Math.max(data.bbox[2] - data.bbox[0], data.bbox[3] - data.bbox[1]) : 1
  // 주소의 `at` — 처음 한 번(그리고 바뀔 때) 그 객체의 자리로 간다.
  const located = useRef<string | null>(null)
  useEffect(() => {
    if (!data?.ready || !at || located.current === at) return
    located.current = at
    graphApi
      .mapLocate(at)
      .then((found) => {
        if (found.kind === 'none' || found.x == null || found.y == null) {
          setWhere(found.reason ?? '지도에 없습니다.')
          return
        }
        setWhere(null)
        const ratio =
          zoom !== null
            ? 2 ** -zoom
            : found.kind === 'record'
              ? Math.max(((found.r ?? span * 0.01) * 6) / span, 0.0005)
              : 0.06
        nonce.current += 1
        setGoTo({ x: found.x, y: found.y, ratio, nonce: nonce.current })
        setSelection({
          id: found.id,
          kind: found.kind === 'record' ? 'record' : 'master',
          label: found.label,
          typeSlug: found.type_slug,
        })
      })
      .catch((caught: unknown) =>
        setWhere(caught instanceof Error ? caught.message : String(caught)),
      )
  }, [data, at, zoom, span])

  const pick = useCallback(
    (next: MapSelection | null) => {
      setSelection(next)
      if (!next) return
      // 그림에서 고른 것은 이미 화면에 있다 — 주소에 적되 다시 찾아가지 않는다(카메라가 안 튄다).
      located.current = next.id
      onAt(next.id)
    },
    [onAt],
  )

  // 허브를 고르면 주 참조 밖의 연결(공동 참조)을 받는다.
  const [links, setLinks] = useState<MapLinks | null>(null)
  useEffect(() => {
    setLinks(null)
    if (!selection || selection.kind !== 'hub') return undefined
    let live = true
    graphApi
      .mapLinks(selection.id)
      .then((got) => {
        if (live) setLinks(got)
      })
      .catch(() => {
        if (live) setLinks(null)
      })
    return () => {
      live = false
    }
  }, [selection])

  // --- 이름 찾기(기준정보) -----------------------------------------------------------------
  const [find, setFind] = useState('')
  const matches = useMemo(() => {
    const needle = find.trim().toLowerCase()
    if (!needle || !data) return null
    const out = new Set<number>()
    data.masters.label.forEach((label, index) => {
      if (label.toLowerCase().includes(needle)) out.add(index)
    })
    return out
  }, [find, data])
  const cursor = useRef(0)
  useEffect(() => {
    cursor.current = 0
  }, [matches])
  const jump = () => {
    if (!data || !matches?.size) return
    const list = [...matches]
    const index = list[cursor.current % list.length]
    cursor.current += 1
    nonce.current += 1
    setGoTo({
      x: data.masters.x[index],
      y: data.masters.y[index],
      ratio: 0.04,
      nonce: nonce.current,
    })
    const slug = data.types[data.masters.type[index]]?.slug ?? ''
    pick({
      id: data.masters.ids[index],
      kind: data.discs.hub.includes(index) ? 'hub' : 'master',
      label: data.masters.label[index],
      typeSlug: slug,
    })
  }

  const lastLevel = useRef<number | null>(zoom)
  const onRatio = useCallback(
    (ratio: number) => {
      const level = levelOf(ratio)
      if (level !== lastLevel.current) {
        lastLevel.current = level
        onZoom(level)
      }
    },
    [onZoom],
  )

  const [confirm, setConfirm] = useState(false)
  const [queued, setQueued] = useState<string | null>(null)

  const layout = data?.layout
  return (
    <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[260px_1fr_280px]">
      <aside className="min-h-0 space-y-4 overflow-y-auto text-sm lg:pr-1">
        <div className="space-y-2 rounded-md border p-2">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 font-medium">
              <MapIcon className="size-4" />
              전체 지도
            </span>
            <Button size="sm" variant="outline" onClick={onClose}>
              <X className="mr-1 size-3.5" />
              지도 닫기
            </Button>
          </div>
          <p className="text-muted-foreground text-xs">
            서버가 미리 놓아 둔 자리입니다 — 기준정보는 점, 기록은 주 참조가 가리키는 기준정보(허브)
            곁의 원반입니다. 멀리서는 밀도, 확대하면 기록 하나하나가 점으로 보입니다. 자리는 판
            안에서 늘 같습니다.
          </p>
        </div>

        <div className="space-y-1.5">
          <p className="text-muted-foreground text-xs">기록 타입 (원반에 담는 것)</p>
          {records.length === 0 && (
            <p className="text-muted-foreground text-xs">이 판에 기록 타입이 없습니다.</p>
          )}
          {records.map((one) => (
            <label
              key={one.slug}
              className="hover:bg-muted flex cursor-pointer items-center gap-2 rounded px-1 py-0.5"
            >
              <input
                type="checkbox"
                className="size-3.5"
                checked={on.has(one.slug)}
                onChange={() => toggle(one.slug)}
              />
              <span
                className="inline-block size-2.5 shrink-0 rounded-full"
                style={{ background: typeColor(one.slug) }}
              />
              <span className="min-w-0 flex-1 truncate">{one.label}</span>
              <span className="text-muted-foreground text-xs tabular-nums">
                {one.count.toLocaleString()}
              </span>
            </label>
          ))}
          {records.map(
            (one) =>
              !one.hub_key && (
                <p key={`none:${one.slug}`} className="text-muted-foreground text-xs">
                  「{one.label}」 에는 허브 칸이 없어 모두 「미지정」 자리에 있습니다 — 타입
                  설정에서 허브 칸을 정합니다.
                </p>
              ),
          )}
          {records.some((one) => one.hub_key) && (
            <p className="text-muted-foreground text-xs">
              허브 칸:{' '}
              {records
                .filter((one) => one.hub_key)
                .map((one) => `${one.label} → ${one.hub_label ?? one.hub_key}`)
                .join(' · ')}
            </p>
          )}
          {data && data.unplaced.length > 0 && (
            <p className="text-xs text-amber-700 dark:text-amber-400">
              판 뒤에 생긴 기준정보를 가리키는 기록{' '}
              {data.unplaced.reduce((sum, one) => sum + one.count, 0).toLocaleString()}건은 아직
              자리가 없습니다 — 매일 밤 덧붙임에서 놓입니다.
            </p>
          )}
        </div>

        {layout && (
          <div className="text-muted-foreground space-y-1 rounded-md border p-2 text-xs">
            <p className="text-foreground font-medium">판 {layout.version}</p>
            <p>
              {layout.kind === 'full' ? '전체 배치' : '덧붙임'} {shownDateTime(layout.laid_at)}
              {layout.kind !== 'full' &&
                ` · 전체 배치는 판 ${layout.full_version}(${shownDateTime(layout.full_at)})`}
              {layout.by_timer ? ' · 타이머' : ' · 관리자'}
            </p>
            {data?.counted_at && (
              <p>원반의 수 · 밀도: {shownDateTime(data.counted_at)} 기준(한 시간마다)</p>
            )}
            {layout.moved_p50 != null && (
              <p>
                앞 판에서 움직인 거리: 가운데 {(layout.moved_p50 * 100).toFixed(1)}% · 95%{' '}
                {((layout.moved_p95 ?? 0) * 100).toFixed(1)}%(지도 폭 대비)
              </p>
            )}
            <p>매일 밤 새 기준정보만 빈자리에 덧붙입니다 — 있던 자리는 움직이지 않습니다.</p>
            {data && data.drift.length > 0 && (
              <p className="text-amber-700 dark:text-amber-400">
                허브 칸 설정이 이 판과 다른 기록 타입이 있습니다({data.drift.join(', ')}) — 다음
                전체 다시 배치에 반영됩니다.
              </p>
            )}
          </div>
        )}

        {data?.can_relayout && (
          <div className="space-y-1.5 rounded-md border p-2 text-xs">
            <Button
              size="sm"
              variant="outline"
              disabled={Boolean(data.job)}
              onClick={() => setConfirm(true)}
            >
              <RotateCw className="mr-1 size-3.5" />
              전체 다시 배치
            </Button>
            {data.job && (
              <p className="text-muted-foreground flex items-center gap-1">
                <Loader2 className="size-3 animate-spin" />
                지도 작업이 {data.job.status === 'running' ? '도는' : '기다리는'} 중입니다 —{' '}
                <Link className="underline" to="/jobs">
                  작업
                </Link>
              </p>
            )}
            {queued && <p className="text-muted-foreground">작업을 넣었습니다({queued}).</p>}
          </div>
        )}
      </aside>

      <div className="flex min-h-0 flex-col gap-2">
        {error && <ErrorNotice error={error} />}
        {where && (
          <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs">
            주소의 객체를 지도에서 찾지 못했습니다 — {where}
          </p>
        )}
        <div className="relative">
          <Search className="text-muted-foreground absolute top-2 left-2 size-4" />
          <Input
            value={find}
            onChange={(event) => setFind(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') jump()
              if (event.key === 'Escape') setFind('')
            }}
            placeholder="기준정보 이름으로 찾기 — Enter 로 차례로 찾아가기"
            className="pl-8"
            disabled={!data?.ready}
          />
          {matches && (
            <span className="text-muted-foreground absolute top-2 right-2 text-xs">
              {matches.size.toLocaleString()}개
            </span>
          )}
        </div>
        {!data ? (
          <div className="flex min-h-[420px] flex-1 items-center justify-center rounded-md border">
            <Loader2 className="text-muted-foreground size-5 animate-spin" />
          </div>
        ) : !data.ready ? (
          <EmptyState
            title="지도 판이 아직 없습니다"
            hint={
              data.last_error
                ? `마지막 지도 작업이 실패했습니다 — ${data.last_error}`
                : data.job
                  ? '첫 판을 만드는 중입니다 — 잠시 뒤 다시 엽니다.'
                  : '처음 판은 워커가 만듭니다(기준정보가 있으면 몇 분 안). 시스템 관리자는 「전체 다시 배치」 로 바로 만듭니다.'
            }
          />
        ) : (
          <Suspense
            fallback={
              <div className="flex min-h-[420px] flex-1 items-center justify-center rounded-md border">
                <Loader2 className="text-muted-foreground size-5 animate-spin" />
              </div>
            }
          >
            <MapView
              data={data}
              on={on}
              onToggleType={toggle}
              typeColor={typeColor}
              selected={selection?.id ?? null}
              onSelect={pick}
              onOpen={onOpen}
              links={links}
              goTo={goTo}
              matches={matches}
              onRatio={onRatio}
              wide={wide}
              onToggleWide={onToggleWide}
            />
          </Suspense>
        )}
      </div>

      <aside className="min-h-0 space-y-3 overflow-y-auto text-sm lg:pr-1">
        {selection ? (
          <>
            {detail(selection, () => setSelection(null))}
            {selection.kind === 'hub' && (
              <div className="space-y-1 rounded-md border p-3">
                <p className="text-xs font-medium">주 참조 밖의 연결 — 함께 가리킨 기준정보</p>
                {!links ? (
                  <Loader2 className="text-muted-foreground size-4 animate-spin" />
                ) : links.withheld ? (
                  <p className="text-muted-foreground text-xs">
                    이 판은 부서별로 세지 않아 보이지 않습니다 — 전체 다시 배치 뒤에 보입니다.
                  </p>
                ) : links.items.length === 0 ? (
                  <p className="text-muted-foreground text-xs">
                    이 허브의 기록이 함께 가리킨 다른 기준정보가 없습니다.
                  </p>
                ) : (
                  <ul className="space-y-0.5">
                    {links.items.map((one) => (
                      <li key={one.id}>
                        <button
                          type="button"
                          className="hover:bg-muted flex w-full items-baseline justify-between gap-2 rounded px-1.5 py-0.5 text-left text-xs"
                          onClick={() => {
                            nonce.current += 1
                            setGoTo({ x: one.x, y: one.y, ratio: 0.04, nonce: nonce.current })
                            pick({
                              id: one.id,
                              kind: 'master',
                              label: one.label,
                              typeSlug: one.type_slug,
                            })
                          }}
                        >
                          <span className="truncate">{one.label}</span>
                          <span className="text-muted-foreground shrink-0 tabular-nums">
                            {one.count.toLocaleString()}건
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {links?.counted_at && (
                  <p className="text-muted-foreground text-[11px]">
                    판을 만들 때({shownDateTime(links.counted_at)}) 센 수 · 지도에 선으로도 긋습니다
                  </p>
                )}
              </div>
            )}
          </>
        ) : (
          <div className="text-muted-foreground rounded-md border border-dashed p-3">
            점을 클릭하면 상세와 「여기서 정적 그리기」 가 나옵니다. 허브(원반 가운데 점)를 클릭하면
            그 기록들이 함께 가리킨 기준정보(부품 · 센터 · 불량 …)가 선으로 이어집니다. 더블클릭은
            바로 「여기서 정적 그리기」. 범례의 기록 타입 · 선 종류를 클릭하면 켜고 끕니다.
          </div>
        )}
      </aside>

      <ConfirmDialog
        open={confirm}
        title="전체 다시 배치"
        description={
          <>
            기준정보 전부를 다시 배치해 판을 올립니다. 앞 판의 자리에서 출발하므로 대부분은 지도
            폭의 1 ~ 5% 안에서 움직이지만, <b>지금 판의 자리는 그대로 남지 않습니다</b> — 자리를
            눈으로 기억해 둔 사람에게는 조금 옮겨 보입니다. 원반 크기를 정하는 수도 지금 수로 다시
            얼립니다. 큰 설치에서는 몇 분 걸리고, 그동안 지도는 지금 판으로 보입니다.
          </>
        }
        confirmLabel="다시 배치"
        onConfirm={async () => {
          const job = await graphApi.mapRelayout()
          setQueued(job.status === 'running' ? '도는 중' : '기다리는 중')
          setTick((value) => value + 1)
        }}
        onClose={() => setConfirm(false)}
      />
    </div>
  )
}
