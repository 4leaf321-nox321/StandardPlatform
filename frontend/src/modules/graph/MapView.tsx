/**
 * 전체 지도 — **서버가 미리 계산해 둔 자리**를 그린다(ADR 0046). 정적 그리기의 상한(노드 20만)을 넘는
 * 범위 · 전체를 보는 길이다.
 *
 *   멀리   기준정보 점 + 허브 원반(넓이 = 보이는 기록 수)의 밀도 — 기록을 하나도 안 받는다.
 *   확대   화면의 기대 수(집계 x 원반 넓이 몫)가 상한(5만) 이하면 그 상자의 기록 점을 받아 그린다.
 *          넘으면 밀도 그대로 「N건 — 더 확대」.
 *
 * 그리기는 정적 그리기의 캔버스(`WholeCanvas`)를 **지도 좌표 고정 모드**로 쓴다 — 틀이 판에 고정이라
 * 점이 들고 나도 화면이 다시 서지 않는다. 원반 안의 자리는 기록 id 의 해시라 뜻이 없다(그렇게 적는다).
 *
 * 선 — 기준정보끼리(참조 칸 · 관계)는 늘, 확대한 점의 선은 점이 문턱(5천) 이하일 때, 고른 허브의 공동
 * 참조는 고를 때. 종류마다 색이고(멀리 보기가 준 종류 차례), 범례에서 종류를 끄고 켠다.
 *
 * 정적 그리기 덩어리처럼 lazy 로 매단다 — sigma 를 안 여는 사람은 받지 않는다.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'

import { graphApi } from '@/modules/graph/api'
import type { MapBox, MapData, MapLinks, MapPoints } from '@/modules/graph/api'
import { BACKDROP, blend, colorScale } from '@/modules/graph/colors'
import { discsOf, drawDensity, expectedIn } from '@/modules/graph/mapDensity'
import WholeCanvas from '@/modules/graph/WholeCanvas'
import type { ExtraNodes, MapViewport, WholeLegendItem } from '@/modules/graph/WholeCanvas'
import { shownDateTime } from '@/shared/lib/datetime'
import { useTheme } from '@/shared/theme/ThemeProvider'

/** 고른 것 — 기준정보(허브면 `hub`) 또는 상자의 기록 점. */
export interface MapSelection {
  id: string
  kind: 'master' | 'hub' | 'record'
  label: string
  typeSlug: string
}

export interface MapViewProps {
  data: MapData
  /** 켠 기록 타입(slug). */
  on: Set<string>
  onToggleType: (slug: string) => void
  typeColor: (slug: string) => string
  /** 고른 객체 id. */
  selected: string | null
  onSelect: (selection: MapSelection | null) => void
  /** 더블클릭 — 「여기서 정적 그리기」. */
  onOpen: (selection: MapSelection) => void
  /** 고른 허브의 공동 참조 선 — 호스트가 받아 준다. */
  links: MapLinks | null
  goTo: { x: number; y: number; ratio: number; nonce: number } | null
  /** 이름 찾기 — 맞는 기준정보 번호. */
  matches: Set<number> | null
  /** 카메라 배율이 바뀌었다(주소의 확대 수준). */
  onRatio?: (ratio: number) => void
  wide: boolean
  onToggleWide: () => void
}

const EMPTY = new Int32Array(0)
/** 점을 받을 상자를 화면보다 이만큼 넓게 — 조금 움직여도 다시 안 받게. */
const MARGIN = 0.2
const MASTER_GRAY = '#64748b'
/** 선을 바탕과 섞는 몫 — 기준정보끼리는 덜, 점의 선(많다)은 더 흐리게. */
const MASTER_FADE = 0.55
const POINT_FADE = 0.65

function covers(outer: MapBox, inner: MapBox): boolean {
  return (
    outer.x0 <= inner.x0 && outer.y0 <= inner.y0 && outer.x1 >= inner.x1 && outer.y1 >= inner.y1
  )
}

export default function MapView({
  data,
  on,
  onToggleType,
  typeColor,
  selected,
  onSelect,
  onOpen,
  links,
  goTo,
  matches,
  onRatio,
  wide,
  onToggleWide,
}: MapViewProps) {
  const { theme } = useTheme()
  const dark = theme === 'dark'
  const n = data.masters.ids.length

  // 기준정보 — 판의 자리 그대로. 허브(원반이 있는 것)는 조금 크게, 나중에 실어 위에.
  const masters = useMemo(() => {
    const hubDisc = new Map<number, number>()
    data.discs.hub.forEach((hub, disc) => {
      if (hub >= 0) hubDisc.set(hub, disc)
    })
    const positions = new Float32Array(n * 2)
    const colors: string[] = []
    const sizes = new Float32Array(n)
    const order: number[] = []
    const hubs: number[] = []
    for (let i = 0; i < n; i += 1) {
      positions[i * 2] = data.masters.x[i]
      positions[i * 2 + 1] = data.masters.y[i]
      const slug = data.types[data.masters.type[i]]?.slug ?? ''
      colors.push(slug ? typeColor(slug) : MASTER_GRAY)
      const hub = hubDisc.has(i)
      sizes[i] = hub ? 4.5 : 3
      if (hub) hubs.push(i)
      else order.push(i)
    }
    const index = new Map<string, number>()
    data.masters.ids.forEach((id, at) => index.set(id, at))
    return {
      positions,
      colors,
      sizes,
      order: Int32Array.from([...order, ...hubs]),
      hubDisc,
      index,
    }
  }, [data, n, typeColor])

  const discs = useMemo(() => discsOf(data, on), [data, on])
  const typesOn = useMemo(() => [...on].sort(), [on])

  // --- 확대 — 화면의 기대 수가 상한 이하면 상자의 점을 받는다 -----------------------------
  const [view, setView] = useState<MapViewport | null>(null)
  const [points, setPoints] = useState<{ data: MapPoints; key: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const generation = useRef(0)
  const timer = useRef<number | null>(null)
  const onView = useCallback(
    (next: MapViewport) => {
      if (timer.current !== null) window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => {
        setView(next)
        onRatio?.(next.ratio)
      }, 250)
    },
    [onRatio],
  )
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
    },
    [],
  )
  const box = useMemo<MapBox | null>(
    () => (view ? { x0: view.x0, y0: view.y0, x1: view.x1, y1: view.y1 } : null),
    [view],
  )
  const expected = useMemo(() => (box ? expectedIn(discs, box) : 0), [box, discs])
  const typesKey = typesOn.join(',')
  const loadedFits = Boolean(
    points &&
    box &&
    points.key === typesKey &&
    !points.data.over &&
    covers(boxOf(points.data), box),
  )
  useEffect(() => {
    if (!box) return
    if (expected > data.max_points || typesOn.length === 0) {
      generation.current += 1
      setPoints(null)
      setLoading(false)
      return
    }
    if (loadedFits) return
    const width = box.x1 - box.x0
    const height = box.y1 - box.y0
    const wider = {
      x0: box.x0 - width * MARGIN,
      y0: box.y0 - height * MARGIN,
      x1: box.x1 + width * MARGIN,
      y1: box.y1 + height * MARGIN,
    }
    const ask = expectedIn(discs, wider) <= data.max_points ? wider : box
    const mine = ++generation.current
    setLoading(true)
    setError(null)
    graphApi
      .mapPoints(ask, typesOn)
      .then((got) => {
        if (generation.current !== mine) return
        setPoints({ data: got, key: typesKey })
      })
      .catch((caught: unknown) => {
        if (generation.current !== mine) return
        setError(caught instanceof Error ? caught.message : String(caught))
      })
      .finally(() => {
        if (generation.current === mine) setLoading(false)
      })
  }, [box, expected, data.max_points, typesOn, typesKey, discs, loadedFits])

  const shownPoints = points && !points.data.over && points.key === typesKey ? points.data : null
  const extra = useMemo<ExtraNodes | null>(() => {
    if (!shownPoints) return null
    const m = shownPoints.ids.length
    const colors: string[] = []
    for (let k = 0; k < m; k += 1)
      colors.push(typeColor(shownPoints.types[shownPoints.type[k]] ?? ''))
    return {
      labels: shownPoints.labels ?? Array.from({ length: m }, () => ''),
      x: Float32Array.from(shownPoints.x),
      y: Float32Array.from(shownPoints.y),
      colors,
      sizes: new Float32Array(m).fill(2.2),
    }
  }, [shownPoints, typeColor])
  const pointIndex = useMemo(() => {
    const out = new Map<string, number>()
    shownPoints?.ids.forEach((id, at) => out.set(id, n + at))
    return out
  }, [shownPoints, n])

  // --- 선 — 기준정보끼리 · 확대한 점의 선 · 고른 허브의 공동 참조 -------------------------------
  // 주 참조(허브 칸)의 선은 처음에 꺼 둔다 — 원반 자리가 이미 말하고, 그으면 허브로 모이는 살이 다른
  // 선을 덮는다(개발 DB 실측). 범례에서 켠다. 사람이 누른 종류는 `flipped` 에 — 기본과 다르게 둔 것.
  const hubKinds = useMemo(
    () =>
      new Set(
        data.types
          .filter((one) => one.record && one.hub_key)
          .map((one) => `ref:${one.slug}.${one.hub_key}`),
      ),
    [data.types],
  )
  const [flipped, setFlipped] = useState<ReadonlySet<string>>(() => new Set())
  const linesOff = useMemo(() => {
    const out = new Set<string>()
    for (const one of data.relations)
      if (hubKinds.has(one.slug) !== flipped.has(one.slug)) out.add(one.slug)
    return out
  }, [data.relations, hubKinds, flipped])
  const pointLines = shownPoints?.lines ?? null
  // 색은 **처음 나온 차례로** 붙이고 바꾸지 않는다 — 종류는 수십이고 팔레트는 열둘이라, 지금 그리는
  // 종류부터 받아야 회색으로 안 넘친다. 기준정보끼리의 선(멀리 보기가 앞에 준다)이 먼저, 확대한 점의
  // 선은 나온 때. 한 번 붙은 색은 점이 들고 나도 그대로다.
  const colorOrder = useRef<string[]>([])
  const kindColor = useMemo(() => {
    const order = colorOrder.current
    const seen = new Set(order)
    const present = [
      ...data.relations.filter((one) => one.count > 0).map((one) => one.slug),
      ...(pointLines?.kinds ?? []),
    ]
    for (const slug of present) {
      if (seen.has(slug)) continue
      seen.add(slug)
      order.push(slug)
    }
    const scale = colorScale(order)
    const backdrop = dark ? BACKDROP.dark : BACKDROP.light
    const out = new Map<string, { base: string; master: string; point: string }>()
    for (const slug of order) {
      const base = scale(slug)
      out.set(slug, {
        base,
        master: blend(base, backdrop, MASTER_FADE),
        point: blend(base, backdrop, POINT_FADE),
      })
    }
    return out
  }, [data.relations, pointLines, dark])
  /** 종류마다 지금 그은 선 수 — 범례. */
  const lineCounts = useMemo(() => {
    const out = new Map<string, number>()
    const bump = (slug: string) => out.set(slug, (out.get(slug) ?? 0) + 1)
    for (const rel of data.lines.rel) bump(data.relations[rel]?.slug ?? '')
    if (pointLines) for (const rel of pointLines.rel) bump(pointLines.kinds[rel])
    return out
  }, [data.lines, data.relations, pointLines])
  const lineEdges = useMemo(() => {
    const src: number[] = []
    const dst: number[] = []
    const colors: string[] = []
    const strong: string[] = []
    const fallback = dark ? BACKDROP.dark : BACKDROP.light
    const push = (a: number | undefined, b: number | undefined, color: string, bold: string) => {
      if (a === undefined || b === undefined) return
      src.push(a)
      dst.push(b)
      colors.push(color)
      strong.push(bold)
    }
    const { lines } = data
    for (let k = 0; k < lines.src.length; k += 1) {
      const slug = data.relations[lines.rel[k]]?.slug ?? ''
      if (linesOff.has(slug)) continue
      const color = kindColor.get(slug)
      push(
        lines.src[k],
        lines.dst[k],
        color?.master ?? blend(MASTER_GRAY, fallback, MASTER_FADE),
        color?.base ?? MASTER_GRAY,
      )
    }
    if (pointLines && shownPoints) {
      const count = shownPoints.ids.length
      const end = (k: number) =>
        k < count ? n + k : masters.index.get(pointLines.masters[k - count])
      for (let k = 0; k < pointLines.src.length; k += 1) {
        const slug = pointLines.kinds[pointLines.rel[k]]
        if (linesOff.has(slug)) continue
        const color = kindColor.get(slug)
        push(
          end(pointLines.src[k]),
          end(pointLines.dst[k]),
          color?.point ?? blend(MASTER_GRAY, fallback, POINT_FADE),
          color?.base ?? MASTER_GRAY,
        )
      }
    }
    // 고른 허브의 공동 참조 — 고를 때만, 진하게.
    const hub = links ? masters.index.get(links.hub) : undefined
    if (links && hub !== undefined) {
      const amber = dark ? '#fbbf24' : '#d97706'
      for (const one of links.items) push(hub, masters.index.get(one.id), amber, amber)
    }
    return src.length > 0 ? { src, dst, colors, strong } : null
  }, [data, linesOff, kindColor, pointLines, shownPoints, n, masters.index, links, dark])

  // --- 밑그림(원반 · 밀도) ----------------------------------------------------------------
  const underlay = useCallback(
    (ctx: CanvasRenderingContext2D, port: MapViewport) =>
      drawDensity(ctx, discs, port, { scale: data.scale, dark, faint: Boolean(shownPoints) }),
    [discs, data.scale, dark, shownPoints],
  )

  const select = useCallback(
    (index: number | null): MapSelection | null => {
      if (index === null) return null
      if (index < n) {
        const slug = data.types[data.masters.type[index]]?.slug ?? ''
        return {
          id: data.masters.ids[index],
          kind: masters.hubDisc.has(index) ? 'hub' : 'master',
          label: data.masters.label[index],
          typeSlug: slug,
        }
      }
      const at = index - n
      if (!shownPoints || at >= shownPoints.ids.length) return null
      return {
        id: shownPoints.ids[at],
        kind: 'record',
        label: shownPoints.labels?.[at] ?? '',
        typeSlug: shownPoints.types[shownPoints.type[at]] ?? '',
      }
    },
    [n, data, masters.hubDisc, shownPoints],
  )

  const selectedIndex = selected
    ? (masters.index.get(selected) ?? pointIndex.get(selected) ?? null)
    : null

  // 범례 — 타입(기록 타입은 누르면 끄고 켬)과 지금 그은 선의 종류(누르면 끄고 켬). 선 종류의 번호는
  // 음수(-1 부터)라 타입 번호와 안 겹친다.
  const legend = useMemo<WholeLegendItem[]>(() => {
    const typeLabel = new Map(data.types.map((one) => [one.slug, one.label]))
    return [
      ...data.types.map((one, at) => ({
        color: typeColor(one.slug),
        label: one.record ? `${one.label}${on.has(one.slug) ? '' : ' (끔)'}` : one.label,
        count: one.count,
        key: one.record ? at : undefined,
        title: one.record ? '클릭하면 이 기록 타입을 끄고 켭니다' : undefined,
      })),
      ...data.relations.flatMap((one, at) => {
        const count = lineCounts.get(one.slug) ?? 0
        // 지금 그은 선이 없는 종류는 빼되, 사람이 누른 것은 남긴다(다시 켤 수 있게).
        if (count === 0 && !flipped.has(one.slug)) return []
        return [
          {
            color: kindColor.get(one.slug)?.base ?? MASTER_GRAY,
            label: `${lineLabel(one.slug, one.label, typeLabel)}${linesOff.has(one.slug) ? ' (끔)' : ''}`,
            count,
            line: true,
            key: -(at + 1),
            title: hubKinds.has(one.slug)
              ? '주 참조 — 원반 자리가 이미 말해 처음엔 꺼 둡니다. 클릭하면 끄고 켭니다'
              : '클릭하면 이 선을 끄고 켭니다',
          },
        ]
      }),
    ]
  }, [
    data.types,
    data.relations,
    typeColor,
    on,
    lineCounts,
    linesOff,
    kindColor,
    hubKinds,
    flipped,
  ])

  const visibleRecords = data.types.reduce(
    (sum, one) => sum + (one.record && on.has(one.slug) ? one.count : 0),
    0,
  )
  const layout = data.layout
  const serverOver = points && points.key === typesKey && points.data.over ? points.data : null
  const status = !box
    ? null
    : serverOver
      ? `이 상자에 기록 약 ${serverOver.expected.toLocaleString()}건 — 더 확대하면 점으로 그립니다(${data.max_points.toLocaleString()}건까지)`
      : typesOn.length === 0
        ? '켠 기록 타입이 없습니다 — 왼쪽 「기록 타입」 에서 켭니다'
        : expected > data.max_points
          ? `이 화면에 기록 약 ${Math.round(expected).toLocaleString()}건 — 더 확대하면 점으로 그립니다(${data.max_points.toLocaleString()}건까지)`
          : shownPoints
            ? `점 ${shownPoints.count.toLocaleString()}개 — 이 상자의 기록 전부${shownPoints.skipped.length ? ` · 미지정 ${shownPoints.skipped.reduce((s, one) => s + one.count, 0).toLocaleString()}건은 점으로 못 그림(타입이 커 색인이 없습니다)` : ''}${pointLinesNote(shownPoints)}`
            : null

  const overlay = (
    <>
      {(loading || status || error) && (
        <div className="absolute inset-x-0 top-3 flex justify-center">
          <p
            className={`bg-background/90 flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs shadow-sm ${
              error ? 'border-red-500/40' : ''
            }`}
            role="status"
          >
            {loading && <Loader2 className="size-3.5 animate-spin" />}
            {error ? `점을 못 받았습니다 — ${error}` : status}
          </p>
        </div>
      )}
      <div className="text-muted-foreground bg-background/85 absolute bottom-2 left-2 max-w-[70%] space-y-0.5 rounded px-2 py-1 text-xs">
        <p>
          {layout
            ? `판 ${layout.version} · ${layout.kind === 'full' ? '전체 배치' : '덧붙임'} ${shownDateTime(layout.laid_at)}`
            : ''}
          {data.counted_at ? ` · 수는 ${shownDateTime(data.counted_at)} 기준` : ''}
          {` · 보이는 기록 ${visibleRecords.toLocaleString()}건 · 기준정보 ${n.toLocaleString()}개`}
        </p>
        <p>
          원반 넓이 = 그 기준정보를 주 참조로 가리키는 기록 수 · 색 = 기록 밀도(진할수록 빽빽) ·
          원반 안의 자리는 뜻이 없습니다(id 로 정한 자리) · 선 = 참조 칸 · 관계(범례에서 끄고 켬)
        </p>
      </div>
    </>
  )

  return (
    <WholeCanvas
      labels={data.masters.label}
      nodeColors={masters.colors}
      nodeSizes={masters.sizes}
      drawOrder={masters.order}
      positions={masters.positions}
      src={EMPTY}
      dst={EMPTY}
      edgeKind={EMPTY}
      kindColors={[]}
      kindStrong={[]}
      selected={selectedIndex}
      matches={matches}
      onSelect={(index) => onSelect(select(index))}
      onOpen={(index) => {
        const picked = select(index)
        if (picked) onOpen(picked)
      }}
      legend={legend}
      onLegendClick={(key) => {
        if (key < 0) {
          const slug = data.relations[-key - 1]?.slug
          if (!slug) return
          setFlipped((was) => {
            const next = new Set(was)
            if (next.has(slug)) next.delete(slug)
            else next.add(slug)
            return next
          })
          return
        }
        const slug = data.types[key]?.slug
        if (slug) onToggleType(slug)
      }}
      wide={wide}
      onToggleWide={onToggleWide}
      overlay={overlay}
      exportName="전체 지도"
      map={{
        bbox: data.bbox,
        underlay,
        underlayKey: underlay,
        onView,
        extra,
        links: lineEdges,
        goTo,
      }}
    />
  )
}

/** 점의 선이 없을 때의 한마디 — 문턱을 넘었거나 선이 너무 많다. */
function pointLinesNote(points: MapPoints): string {
  if (points.lines) return ''
  if (points.lines_over) return ' · 선이 너무 많아 긋지 않았습니다(여러 값 칸)'
  return ` · 기록의 선은 점이 ${points.lines_up_to.toLocaleString()}개 이하로 확대하면 긋습니다`
}

/** 선 종류의 이름 — 참조 칸은 「타입 → 칸」(같은 이름의 타입과 헷갈리지 않게), 관계는 그대로. */
function lineLabel(slug: string, label: string, typeLabel: Map<string, string>): string {
  if (!slug.startsWith('ref:')) return label
  const owner = slug.slice(4).split('.')[0]
  const name = typeLabel.get(owner)
  return name ? `${name} → ${label}` : label
}

function boxOf(points: MapPoints): MapBox {
  const [x0, y0, x1, y1] = points.box
  return { x0, y0, x1, y1 }
}
