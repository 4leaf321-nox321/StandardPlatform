/**
 * 정적 그리기의 캔버스 — **정해진 자리의 노드 · 선을 그리기만 한다**(ADR 0045).
 *
 * 자리가 어디서 왔는지(무리 · 단계 · 서버가 미리 계산한 자리) 모른다. 받는 것은 노드마다의 이름 ·
 * 색 · 크기 · 자리와 선의 양 끝 · 종류뿐이다 — 배치를 바꾸거나 다음 단계에서 서버가 자리를 줘도
 * 이 파일은 그대로다.
 *
 * ## 왜 WebGL(sigma)인가
 *
 * 탐색 그림(`GraphCanvas`, 캔버스 2D)은 노드마다 그리기 함수를 부른다 — 수천 개면 충분하지만
 * 20만 개면 한 장 그리는 데 초 단위다. sigma 는 노드 · 선을 GPU 버퍼에 한 번 싣고 카메라만
 * 움직이므로 확대 · 이동이 노드 수와 상관없이 부드럽다. 무겁고 이 그림에서만 쓰므로 정적 그리기
 * 덩어리(`WholeView`, lazy)에만 든다.
 *
 * ## 많을 때 지키는 것
 *
 *   - 강조는 **바뀐 노드 · 선만 다시 처리한다**(`refresh({ partialGraph })`). 다른 것을 흐리게 하면
 *     선 62만 개를 다시 처리해야 해서 hover 마다 0.3초씩 멈춘다 — 그래서 흐리지 않고, 고른 노드의
 *     선을 진하게 · 이웃을 조금 크게 한다.
 *   - 이름표는 sigma 가 크기 차례로 자리를 나눠 붙인다 — 큰(연결 많은) 노드부터, 확대하면 더.
 *   - 연결 많은 노드를 **나중에** 싣는다(`drawOrder`) — 나중 것이 위에 그려져 허브가 기록 아래
 *     묻히지 않는다.
 *
 * ## 지도 좌표 고정 모드(`map`, ADR 0046)
 *
 * 전체 지도는 자리가 판에 고정돼 있고, 확대할 때마다 상자의 점이 들고 난다. 그래서 `map` 을 주면:
 *
 *   - sigma 의 정규화를 **지도 틀에 고정**한다(`setCustomBBox`) — 점이 들고 나도 화면 맞춤으로 다시
 *     서지 않고, 다시 만들어도 카메라를 그대로 둔다.
 *   - 노드 크기는 화면 px 그대로(`zoomToSizeRatioFunction` = 1) — 원반의 크기는 그래프 단위라
 *     밑그림(`underlay`, 캔버스 2D — 원반 · 밀도)이 카메라에 맞춰 그린다.
 *   - 확대한 상자의 점(`extra`)과 지도의 선(`links` — 기준정보끼리 · 점의 선 · 공동 참조)은 **그것만
 *     갈아 끼운다** — sigma 를 다시 만들지 않는다. 덧붙인 노드의 번호는 기준정보 다음
 *     (`labels.length + k`)이고, 덧붙인 선은 선마다 흐린 색 · 진한 색을 들고 있다.
 *   - 카메라가 움직이면 보이는 범위를 알린다(`onView`) — 호스트가 그 상자의 점을 받는다.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Graph from 'graphology'
import Sigma from 'sigma'
import { EdgeLineProgram } from 'sigma/rendering'
import type { NodeDisplayData, EdgeDisplayData } from 'sigma/types'
import { Download, Home, Maximize2, Minimize2 } from 'lucide-react'

import { useElementSize, useFillHeight } from '@/modules/graph/useElementSize'
import { useShortcuts } from '@/modules/graph/useShortcuts'
import { Button } from '@/shared/components/ui/button'
import { useTheme } from '@/shared/theme/ThemeProvider'

export interface WholeLegendItem {
  color: string
  label: string
  count: number
  /** 선 범례면 참 — 점 대신 짧은 선. */
  line?: boolean
  /** 누르면 「이 범주만 또렷」 — 호스트가 `matches` 로 답한다. */
  key?: number
  /** 누를 때의 풀이(마우스를 올리면) — 비우면 「이 타입만 크게」. */
  title?: string
}

/** 지도 화면의 보이는 범위 — 그래프 좌표와 투영. */
export interface MapViewport {
  x0: number
  y0: number
  x1: number
  y1: number
  width: number
  height: number
  /** 카메라 배율(1 = 지도 전체). */
  ratio: number
  /** 그래프 한 단위가 화면 몇 px. */
  unitPx: number
  project: (x: number, y: number) => [number, number]
}

/** 덧붙인 노드 — 확대한 상자의 점. */
export interface ExtraNodes {
  labels: string[]
  x: Float32Array
  y: Float32Array
  colors: string[]
  sizes: Float32Array
}

export interface WholeCanvasMap {
  /** 지도 틀 [x0, y0, x1, y1] — 정규화를 여기에 고정한다. */
  bbox: number[]
  /** 처음 화면 · 화면 맞춤 · PNG 가 맞출 내용의 범위 [x0, y0, x1, y1]. 틀은 비어 있는 「미지정」
   * 자리까지 잡아 두어 내용보다 넓다 — 틀에 맞추면 지도가 작게 한쪽으로 섰다. 비우면 틀. */
  home?: number[] | null
  /** 밑그림(원반 · 밀도) — 카메라가 움직일 때마다 다시 그린다. */
  underlay?: (ctx: CanvasRenderingContext2D, view: MapViewport) => void
  /** 밑그림이 바뀌었다(켠 타입 · 테마) — 값이 바뀌면 다시 그린다. */
  underlayKey?: unknown
  onView?: (view: MapViewport) => void
  extra?: ExtraNodes | null
  /** 덧붙인 선 — 노드 번호(기준정보 · 덧붙인 노드)끼리, 선마다 흐린 색 · 진한 색(고른 노드의 선). */
  links?: { src: number[]; dst: number[]; colors: string[]; strong: string[] } | null
  /** 이 자리로 카메라를 옮긴다(그래프 좌표). */
  goTo?: { x: number; y: number; ratio: number; nonce: number } | null
}

export interface WholeCanvasProps {
  labels: string[]
  /** 노드마다의 색 · 크기(화면 px). */
  nodeColors: string[]
  nodeSizes: Float32Array
  /** 싣는 차례 — 뒤의 것이 위에 그려진다. 비우면 번호 순. */
  drawOrder?: Int32Array | null
  /** 1 이면 안 그린다(관계 없는 것 숨기기). */
  hidden?: Uint8Array | null
  /** 자리 — x0, y0, x1, y1, …. 바뀌면 화면 맞춤으로 다시 선다. */
  positions: Float32Array
  src: Int32Array
  dst: Int32Array
  /** 선의 종류 번호와, 종류마다 흐린 색 · 진한 색(고른 노드의 선). */
  edgeKind: Int32Array
  kindColors: string[]
  kindStrong: string[]
  selected: number | null
  /** 찾기 · 범례 — 이 노드들을 크게. */
  matches?: Set<number> | null
  /** 이 노드로 카메라를 옮긴다. 같은 노드를 다시 찾아가려면 nonce 를 올린다. */
  focusOn?: { index: number; nonce: number } | null
  onSelect: (index: number | null) => void
  /** 더블클릭 — 호스트는 「여기를 중심으로」 로 쓴다. */
  onOpen?: (index: number) => void
  legend?: WholeLegendItem[]
  legendActive?: number | null
  onLegendClick?: (key: number) => void
  /** 전체화면은 호스트가 맡는다(옆 판까지 덮는다). */
  wide: boolean
  onToggleWide: () => void
  /** 캔버스 위에 띄울 것 — 진행률 · 정직한 줄. 클릭을 먹지 않는다. */
  overlay?: React.ReactNode
  /** PNG 파일 이름 앞부분. */
  exportName?: string
  /** 지도 좌표 고정 모드(ADR 0046) — 위 머리 주석. */
  map?: WholeCanvasMap | null
}

/** 이웃이 이보다 적을 때만 이웃의 이름표를 늘 붙인다 — 허브의 이웃 수만 개에 붙이면 글자 덩어리다. */
const NEIGHBOR_LABELS = 40
/** 찾은 노드가 이보다 적을 때만 이름표를 늘 붙인다. */
const MATCH_LABELS = 30
/** 찾아가기의 배율 — 1 이 전체다. */
const FOCUS_RATIO = 0.08
/** 지도의 처음 화면에서 비워 둘 가장자리(px) — 위는 상태 한 줄, 아래는 판 · 풀이(좁으면 세 줄)가
 * 덮고, 이름표는 점의 오른쪽에 붙는다(오른쪽을 좁게 두면 끝의 이름표가 잘렸다). */
const HOME_PAD = { top: 44, bottom: 80, left: 24, right: 110 }

/**
 * 지도의 처음 화면 — 내용(`home`, 없으면 틀)이 가장자리를 뺀 칸에 차게. 정규화는 틀에 고정이라
 * 배율(`ratio`)의 뜻은 그대로다(1 = 틀 전체 — 주소의 확대 수준이 이것이다).
 */
function homeState(renderer: Sigma, map: WholeCanvasMap) {
  const reset = { x: 0.5, y: 0.5, ratio: 1, angle: 0 }
  const at = { cameraState: reset }
  const [x0, y0, x1, y1] = map.home ?? map.bbox
  const a = renderer.graphToViewport({ x: x0, y: y0 }, at)
  const b = renderer.graphToViewport({ x: x1, y: y1 }, at)
  const { width, height } = renderer.getDimensions()
  const roomWidth = Math.max(width - HOME_PAD.left - HOME_PAD.right, 1)
  const roomHeight = Math.max(height - HOME_PAD.top - HOME_PAD.bottom, 1)
  const ratio = Math.min(
    Math.max(Math.abs(b.x - a.x) / roomWidth, Math.abs(b.y - a.y) / roomHeight, 0.01),
    4,
  )
  const middle = renderer.viewportToFramedGraph({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, at)
  // 내용의 가운데를 화면 가운데가 아니라 칸의 가운데에 — 가장자리가 고르지 않아 그만큼 카메라를 민다.
  const origin = renderer.viewportToFramedGraph({ x: 0, y: 0 }, at)
  const step = renderer.viewportToFramedGraph({ x: 1, y: 1 }, at)
  const right = ((HOME_PAD.left - HOME_PAD.right) / 2) * ratio
  const down = ((HOME_PAD.top - HOME_PAD.bottom) / 2) * ratio
  return {
    x: middle.x - (step.x - origin.x) * right,
    y: middle.y - (step.y - origin.y) * down,
    ratio,
    angle: 0,
  }
}

function stamp(): string {
  const now = new Date()
  const two = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}${two(now.getMonth() + 1)}${two(now.getDate())}-${two(now.getHours())}${two(now.getMinutes())}`
}

export default function WholeCanvas(props: WholeCanvasProps) {
  const {
    labels,
    nodeColors,
    nodeSizes,
    drawOrder,
    hidden,
    positions,
    src,
    dst,
    edgeKind,
    kindColors,
    kindStrong,
    selected,
    matches = null,
    focusOn = null,
    onSelect,
    onOpen,
    legend,
    legendActive = null,
    onLegendClick,
    wide,
    onToggleWide,
    overlay,
    exportName = '정적 그리기',
    map = null,
  } = props
  const [containerRef, size] = useElementSize<HTMLDivElement>()
  const fillHeight = useFillHeight(containerRef)
  const stageRef = useRef<HTMLDivElement>(null)
  const { theme } = useTheme()
  const rendererRef = useRef<Sigma | null>(null)
  const redrawUnderlay = useRef<(() => void) | null>(null)
  const graphRef = useRef<Graph | null>(null)
  const [hovered, setHovered] = useState<number | null>(null)

  // 그리는 중의 상태 — reducer 가 매번 읽는다(sigma 를 다시 만들지 않으려고 ref 에 둔다).
  const lit = useRef({
    center: new Set<number>(),
    nodes: new Set<number>(),
    edges: new Set<number>(),
    matches: null as Set<number> | null,
    kindStrong,
    edgeKind,
  })
  lit.current.kindStrong = kindStrong
  lit.current.edgeKind = edgeKind
  const callbacks = useRef({ onSelect, onOpen, map })
  callbacks.current = { onSelect, onOpen, map }
  /** 지도 모드의 카메라 — sigma 를 다시 만들어도(지도 자료가 새로 오면) 그 자리에서 이어 본다. */
  const kept = useRef<{
    key: string
    state: { x: number; y: number; ratio: number; angle: number }
  } | null>(null)
  const frameKey = map ? map.bbox.join(',') : ''
  /** 지금 sigma 에 덧붙인 노드 · 선의 수 — 갈아 끼울 때 앞의 것을 뗀다. */
  const added = useRef({ nodes: 0, edges: 0 })
  /** 지금 sigma 에 실린 자리 · 색 · 크기 · 숨김 — 같은 것을 또 싣지 않는다(실을 때마다 선 전부를 다시 처리한다). */
  const applied = useRef<{
    positions: Float32Array | null
    colors: string[] | null
    sizes: Float32Array | null
    hidden: Uint8Array | null | undefined
  }>({ positions: null, colors: null, sizes: null, hidden: null })

  const palette = useMemo(
    () =>
      theme === 'dark' ? { label: '#e2e8f0', bg: '#0b1220' } : { label: '#1e293b', bg: '#f8fafc' },
    [theme],
  )

  // 그래프와 sigma — **노드 · 선이 바뀔 때만** 새로 만든다. 자리 · 강조는 아래에서 갈아 끼운다.
  useEffect(() => {
    const stage = stageRef.current
    if (!stage || size.width === 0) return undefined
    const n = labels.length
    const graph = new Graph({ type: 'directed', multi: true, allowSelfLoops: true })
    const order = drawOrder ?? null
    for (let k = 0; k < n; k += 1) {
      const i = order ? order[k] : k
      graph.addNode(i, {
        x: positions[i * 2] ?? 0,
        y: positions[i * 2 + 1] ?? 0,
        size: nodeSizes[i],
        color: nodeColors[i],
        label: labels[i],
        hidden: hidden ? hidden[i] === 1 : false,
      })
    }
    for (let k = 0; k < src.length; k += 1) {
      graph.addDirectedEdgeWithKey(k, src[k], dst[k], { color: kindColors[edgeKind[k]] })
    }
    const renderer = new Sigma(graph, stage, {
      // 선은 1px 선분(GL_LINES) — 기본(사각형)은 선마다 꼭짓점이 여섯이라 백만 개면 무겁다.
      defaultEdgeType: 'line',
      edgeProgramClasses: { line: EdgeLineProgram },
      renderEdgeLabels: false,
      labelColor: { color: palette.label },
      labelFont: 'ui-sans-serif, system-ui, sans-serif',
      labelSize: 12,
      labelDensity: 0.6,
      labelGridCellSize: 90,
      labelRenderedSizeThreshold: 7,
      // 많으면 움직이는 동안 선을 쉰다 — 카메라가 부드럽게 따라온다.
      hideEdgesOnMove: src.length > 200_000,
      minCameraRatio: map ? 0.00002 : 0.002,
      maxCameraRatio: 4,
      // 지도 — 점은 화면 px 그대로(원반의 크기는 밑그림이 그래프 단위로 그린다), 이름표는 작은
      // 점에도.
      ...(map
        ? {
            zoomToSizeRatioFunction: () => 1,
            labelRenderedSizeThreshold: 3,
            labelDensity: 0.4,
            labelGridCellSize: 120,
          }
        : {}),
      nodeReducer: (key: string, data: Partial<NodeDisplayData>) => {
        const i = Number(key)
        const state = lit.current
        if (state.center.has(i)) {
          data.highlighted = true
          data.forceLabel = true
        } else if (state.nodes.has(i)) {
          // 지도는 이름표 문턱이 낮다(점 2.2 · 문턱 3) — 이웃을 1.5 키우면 허브를 고를 때 상자의 점
          // 전부에 이름표가 붙었다. 조금만 키운다.
          data.size = (data.size ?? 2) + (callbacks.current.map ? 0.6 : 1.5)
          data.forceLabel = state.nodes.size <= NEIGHBOR_LABELS
        }
        if (state.matches?.has(i)) {
          data.size = (data.size ?? 2) * 1.6 + 1.5
          data.forceLabel = data.forceLabel || state.matches.size <= MATCH_LABELS
        }
        return data
      },
      edgeReducer: (key: string, data: Partial<EdgeDisplayData>) => {
        const k = Number(key)
        const state = lit.current
        if (state.edges.has(k)) {
          // 덧붙인 선(지도)은 번호가 그림의 선 다음이고 제 진한 색을 들고 있다.
          data.color =
            k < state.edgeKind.length
              ? state.kindStrong[state.edgeKind[k]]
              : (data as { strong?: string }).strong
        }
        return data
      },
    })
    renderer.on('enterNode', ({ node }) => setHovered(Number(node)))
    renderer.on('leaveNode', () => setHovered(null))
    renderer.on('clickNode', ({ node }) => callbacks.current.onSelect(Number(node)))
    renderer.on('clickStage', () => callbacks.current.onSelect(null))
    renderer.on('doubleClickNode', (event) => {
      // sigma 는 더블클릭에 확대한다 — 노드 위에서는 「여기를 중심으로」 가 먼저다.
      event.preventSigmaDefault()
      callbacks.current.onOpen?.(Number(event.node))
    })
    graphRef.current = graph
    rendererRef.current = renderer
    applied.current = { positions, colors: nodeColors, sizes: nodeSizes, hidden }
    lit.current = { ...lit.current, center: new Set(), nodes: new Set(), edges: new Set() }
    added.current = { nodes: 0, edges: 0 }
    setHovered(null)
    let detach = () => {}
    if (map) detach = attachMap(renderer, frameKey)
    return () => {
      detach()
      renderer.kill()
      rendererRef.current = null
      graphRef.current = null
    }
    // 자리 · 색 · 숨김은 아래 효과가 갈아 끼운다 — 여기 넣으면 그때마다 20만 노드를 다시 싣는다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labels, src, dst, size.width > 0, frameKey])

  /**
   * 지도 모드 — 틀 고정 · 카메라 이어 보기 · 밑그림 층 · 보이는 범위 알리기. 떼는 함수를 돌려준다.
   */
  function attachMap(renderer: Sigma, key: string): () => void {
    const current = callbacks.current.map
    if (!current) return () => {}
    const [x0, y0, x1, y1] = current.bbox
    renderer.setCustomBBox({ x: [x0, x1], y: [y0, y1] })
    const camera = renderer.getCamera()
    const canvas = renderer.createCanvas('underlay', { beforeLayer: 'edges' })
    const viewport = (): MapViewport => {
      const { width, height } = renderer.getDimensions()
      const a = renderer.viewportToGraph({ x: 0, y: 0 })
      const b = renderer.viewportToGraph({ x: width, y: height })
      const origin = renderer.graphToViewport({ x: 0, y: 0 })
      const unit = renderer.graphToViewport({ x: 1, y: 0 })
      return {
        x0: Math.min(a.x, b.x),
        x1: Math.max(a.x, b.x),
        y0: Math.min(a.y, b.y),
        y1: Math.max(a.y, b.y),
        width,
        height,
        ratio: camera.getState().ratio,
        unitPx: Math.hypot(unit.x - origin.x, unit.y - origin.y),
        project: (x, y) => {
          const at = renderer.graphToViewport({ x, y })
          return [at.x, at.y]
        },
      }
    }
    const draw = () => {
      const host = callbacks.current.map
      const ctx = canvas.getContext('2d')
      if (!ctx || !host?.underlay) return
      const { width, height } = renderer.getDimensions()
      const ratio = window.devicePixelRatio || 1
      if (
        canvas.width !== Math.round(width * ratio) ||
        canvas.height !== Math.round(height * ratio)
      ) {
        canvas.width = Math.round(width * ratio)
        canvas.height = Math.round(height * ratio)
      }
      // 화면 크기도 적는다 — sigma 는 덧붙인 캔버스의 크기를 컨테이너가 바뀔 때만 적는다. 안 적으면
      // 캔버스가 제 픽셀 수(x 화면 배율)대로 늘어나 화면 배율 125 · 150%(Windows) · 브라우저
      // 확대에서 원반이 점보다 그만큼 커 보였다(2026-11-01).
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
      ctx.clearRect(0, 0, width, height)
      host.underlay(ctx, viewport())
    }
    const moved = () => {
      kept.current = { key, state: camera.getState() }
      callbacks.current.map?.onView?.(viewport())
    }
    renderer.on('afterRender', draw)
    redrawUnderlay.current = draw
    // 틀에 맞춘 정규화는 sigma 가 처리할 때 선다 — 처리한 뒤에 처음 화면을 잰다.
    renderer.refresh()
    camera.setState(kept.current?.key === key ? kept.current.state : homeState(renderer, current))
    camera.on('updated', moved)
    moved()
    return () => {
      renderer.off('afterRender', draw)
      camera.off('updated', moved)
      redrawUnderlay.current = null
    }
  }

  // 자리 — 바뀌면 다시 맞춰 선다(화면 맞춤).
  useEffect(() => {
    const graph = graphRef.current
    const renderer = rendererRef.current
    if (!graph || !renderer || applied.current.positions === positions) return
    graph.updateEachNodeAttributes(
      (key, attr) => {
        const i = Number(key)
        if (i >= labels.length) return attr // 덧붙인 노드(지도의 점)는 제 자리를 든다
        attr.x = positions[i * 2]
        attr.y = positions[i * 2 + 1]
        return attr
      },
      { attributes: ['x', 'y'] },
    )
    applied.current.positions = positions
    // 지도는 자리가 판에 고정이다 — 카메라를 그대로 둔다.
    if (!callbacks.current.map)
      renderer.getCamera().setState({ x: 0.5, y: 0.5, ratio: 1, angle: 0 })
  }, [positions, labels, src, dst, size.width])

  // 지도 — 덧붙인 노드(상자의 점)를 **그것만** 갈아 끼운다. 점을 떼면 그 점의 선도 떨어진다 —
  // 아래 선 효과가 이어서 다시 단다.
  const extra = map?.extra ?? null
  const links = map?.links ?? null
  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    const base = labels.length
    for (let k = 0; k < added.current.nodes; k += 1) {
      const key = String(base + k)
      if (graph.hasNode(key)) graph.dropNode(key)
    }
    const nodes = extra ? extra.labels.length : 0
    for (let k = 0; k < nodes && extra; k += 1) {
      graph.addNode(base + k, {
        x: extra.x[k],
        y: extra.y[k],
        size: extra.sizes[k],
        color: extra.colors[k],
        label: extra.labels[k],
      })
    }
    added.current.nodes = nodes
  }, [extra, labels, src, dst, size.width, frameKey])

  // 지도 — 덧붙인 선(기준정보끼리 · 점의 선 · 공동 참조)을 **그것만** 갈아 끼운다. 선만 바뀌면(종류를
  // 끄고 켬 · 허브를 고름) 점은 그대로 둔다.
  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    for (let k = 0; k < added.current.edges; k += 1) {
      const key = String(src.length + k)
      if (graph.hasEdge(key)) graph.dropEdge(key)
    }
    let edges = 0
    if (links) {
      for (let k = 0; k < links.src.length; k += 1) {
        const a = String(links.src[k])
        const b = String(links.dst[k])
        if (!graph.hasNode(a) || !graph.hasNode(b)) continue
        graph.addDirectedEdgeWithKey(src.length + edges, a, b, {
          color: links.colors[k],
          strong: links.strong[k],
          size: 1,
        })
        edges += 1
      }
    }
    added.current.edges = edges
    // 바로 처리해 둔다 — 아래 강조가 새 점 · 선을 부분으로 다시 칠하려면 sigma 가 그것을 알아야 한다.
    rendererRef.current?.refresh()
  }, [extra, links, labels, src, dst, size.width, frameKey])

  // 지도 — 밑그림이 바뀌면(켠 타입 · 테마) 다시 그린다.
  const underlayKey = map?.underlayKey
  useEffect(() => {
    redrawUnderlay.current?.()
  }, [underlayKey])

  // 지도 — 이 자리로(주소의 객체 · 찾은 기록). sigma 가 서기 전에 온 것은 선 뒤에 한 번 간다(같은
  // 요청을 두 번 가지 않게 nonce 를 기억한다 — 자료가 새로 와 sigma 를 다시 만들어도 카메라는 그대로).
  const goTo = map?.goTo ?? null
  const wentTo = useRef<number | null>(null)
  useEffect(() => {
    const renderer = rendererRef.current
    if (!renderer || !goTo || wentTo.current === goTo.nonce) return
    wentTo.current = goTo.nonce
    const framed = renderer.viewportToFramedGraph(
      renderer.graphToViewport({ x: goTo.x, y: goTo.y }),
    )
    void renderer
      .getCamera()
      .animate({ x: framed.x, y: framed.y, ratio: goTo.ratio }, { duration: 450 })
  }, [goTo, labels, src, dst, size.width, frameKey])

  // 색 · 크기 · 숨김 — 관계 없는 것 숨기기 · 테마.
  useEffect(() => {
    const graph = graphRef.current
    const was = applied.current
    if (!graph || (was.colors === nodeColors && was.sizes === nodeSizes && was.hidden === hidden)) {
      return
    }
    applied.current = { ...was, colors: nodeColors, sizes: nodeSizes, hidden }
    graph.updateEachNodeAttributes(
      (key, attr) => {
        const i = Number(key)
        if (i >= labels.length) return attr
        attr.color = nodeColors[i]
        attr.size = nodeSizes[i]
        attr.hidden = hidden ? hidden[i] === 1 : false
        return attr
      },
      { attributes: ['color', 'size', 'hidden'] },
    )
  }, [nodeColors, nodeSizes, hidden, labels, src, dst, size.width])

  useEffect(() => {
    rendererRef.current?.setSetting('labelColor', { color: palette.label })
  }, [palette])

  // 강조 — 고른 노드 · hover 한 노드와 그 이웃, 찾은 노드. **바뀐 것만** 다시 처리한다. 지도의 점 ·
  // 선이 갈리면 같은 번호가 다른 선이라 고른 노드의 선을 다시 찾는다.
  useEffect(() => {
    const graph = graphRef.current
    const renderer = rendererRef.current
    if (!graph || !renderer) return
    const before = lit.current
    const center = new Set<number>()
    if (selected !== null) center.add(selected)
    if (hovered !== null) center.add(hovered)
    const nodes = new Set<number>()
    const edges = new Set<number>()
    for (const one of center) {
      if (!graph.hasNode(one)) continue
      graph.forEachEdge(String(one), (edge, _attr, source, target) => {
        edges.add(Number(edge))
        nodes.add(Number(source === String(one) ? target : source))
      })
    }
    const touched = new Set<string>()
    const none = new Set<number>()
    for (const set of [
      before.center,
      before.nodes,
      center,
      nodes,
      before.matches ?? none,
      matches ?? none,
    ]) {
      for (const one of set) if (graph.hasNode(String(one))) touched.add(String(one))
    }
    const touchedEdges = new Set<string>()
    for (const set of [before.edges, edges]) {
      for (const one of set) if (graph.hasEdge(String(one))) touchedEdges.add(String(one))
    }
    lit.current = { ...before, center, nodes, edges, matches }
    try {
      renderer.refresh({
        partialGraph: { nodes: [...touched], edges: [...touchedEdges] },
        skipIndexation: true,
      })
    } catch {
      // 아직 처리 안 된 노드가 섞였다(지도의 점이 막 바뀜) — 통째로 한 번.
      renderer.scheduleRefresh()
    }
  }, [selected, hovered, matches, labels, src, dst, size.width, extra, links])

  // 찾아가기 — 고른 노드로 카메라를 옮긴다.
  useEffect(() => {
    const renderer = rendererRef.current
    if (!renderer || !focusOn) return
    const data = renderer.getNodeDisplayData(String(focusOn.index))
    if (!data) return
    const camera = renderer.getCamera()
    void camera.animate(
      { x: data.x, y: data.y, ratio: Math.min(camera.getState().ratio, FOCUS_RATIO) },
      { duration: 450 },
    )
  }, [focusOn])

  // 컨테이너 크기가 바뀌면(넓게 보기 · 옆 판) 다시 그린다 — sigma 는 그릴 때 크기를 다시 잰다.
  useEffect(() => {
    rendererRef.current?.scheduleRender()
  }, [size.width, size.height])

  const fit = useCallback(() => {
    const renderer = rendererRef.current
    if (!renderer) return
    const map = callbacks.current.map
    if (map) void renderer.getCamera().animate(homeState(renderer, map), { duration: 300 })
    else void renderer.getCamera().animatedReset({ duration: 300 })
  }, [])
  const zoom = useCallback((factor: number) => {
    const camera = rendererRef.current?.getCamera()
    if (!camera) return
    void camera.animate({ ratio: camera.getState().ratio / factor }, { duration: 200 })
  }, [])

  /**
   * PNG — **전체 그림**(화면 맞춤으로 한 장). WebGL 캔버스는 다 그린 뒤 버퍼가 비므로, 그린 그
   * 자리에서 바로 한 장으로 겹쳐 담는다.
   */
  const exportPng = useCallback(() => {
    const renderer = rendererRef.current
    if (!renderer) return
    const camera = renderer.getCamera()
    const kept = camera.getState()
    const map = callbacks.current.map
    camera.setState(map ? homeState(renderer, map) : { x: 0.5, y: 0.5, ratio: 1, angle: 0 })
    renderer.refresh()
    const { width, height } = renderer.getDimensions()
    const canvases = renderer.getCanvases()
    const ratio = canvases.nodes ? canvases.nodes.width / width : 1
    const out = document.createElement('canvas')
    out.width = Math.round(width * ratio)
    out.height = Math.round(height * ratio)
    const ctx = out.getContext('2d')
    if (ctx) {
      ctx.fillStyle = palette.bg
      ctx.fillRect(0, 0, out.width, out.height)
      for (const layer of ['underlay', 'edges', 'nodes', 'labels', 'hovers', 'hoverNodes']) {
        const canvas = canvases[layer]
        if (canvas) ctx.drawImage(canvas, 0, 0, out.width, out.height)
      }
    }
    camera.setState(kept)
    const anchor = document.createElement('a')
    anchor.href = out.toDataURL('image/png')
    anchor.download = `${exportName}_${stamp()}.png`
    anchor.click()
  }, [exportName, palette.bg])

  useShortcuts(
    useMemo(
      () => ({
        Escape: () => {
          if (wide) onToggleWide()
          else callbacks.current.onSelect(null)
        },
        f: fit,
        F: fit,
        '+': () => zoom(1.5),
        '=': () => zoom(1.5),
        '-': () => zoom(1 / 1.5),
      }),
      [wide, onToggleWide, fit, zoom],
    ),
  )

  return (
    <div
      ref={containerRef}
      className="relative w-full overflow-hidden rounded-md border"
      // 바탕은 선의 흐린 색을 섞은 그 색이다(`colors.blend`) — 다르면 선이 바탕 위에 떠 보인다.
      style={{ height: fillHeight, background: palette.bg }}
    >
      {/* sigma 가 캔버스를 여기 겹쳐 싣는다 — 크기는 이 상자의 것. */}
      <div ref={stageRef} className="absolute inset-0" data-testid="whole-stage" />
      <div className="absolute top-2 right-2 z-10 flex items-center gap-1">
        <Button
          size="icon-sm"
          variant="outline"
          className="bg-background/90 backdrop-blur"
          aria-label="화면 맞춤"
          title="화면 맞춤 (F)"
          onClick={fit}
        >
          <Home className="size-3.5" />
        </Button>
        <Button
          size="icon-sm"
          variant="outline"
          className="bg-background/90 backdrop-blur"
          aria-label={wide ? '축소' : '넓게 보기'}
          title={wide ? '축소 (ESC)' : '넓게 보기'}
          onClick={onToggleWide}
        >
          {wide ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
        </Button>
        <Button
          size="icon-sm"
          variant="outline"
          className="bg-background/90 backdrop-blur"
          aria-label="PNG 다운로드"
          title="전체 그림을 PNG 로 다운로드"
          onClick={exportPng}
        >
          <Download className="size-3.5" />
        </Button>
      </div>
      {legend && legend.length > 0 && (
        <ul className="text-muted-foreground bg-background/85 absolute right-2 bottom-2 z-10 max-h-56 space-y-0.5 overflow-y-auto rounded px-2 py-1 text-xs">
          {legend.map((item) => {
            const swatch = item.line ? (
              <span
                className="inline-block h-0.5 w-3 shrink-0 rounded"
                style={{ background: item.color }}
              />
            ) : (
              <span
                className="inline-block size-2.5 shrink-0 rounded-full"
                style={{ background: item.color }}
              />
            )
            const body = (
              <>
                {swatch}
                <span className="truncate">{item.label}</span>
                <span className="ml-auto pl-2 tabular-nums">{item.count.toLocaleString()}</span>
              </>
            )
            return (
              <li key={`${item.line ? 'l' : 'n'}:${item.label}`}>
                {item.key !== undefined && onLegendClick ? (
                  <button
                    type="button"
                    className={`hover:text-foreground flex w-full items-center gap-1.5 rounded px-0.5 text-left ${
                      item.key === legendActive ? 'text-foreground font-medium' : ''
                    }`}
                    title={item.title ?? '이 타입만 크게 (다시 클릭하면 해제)'}
                    onClick={() => onLegendClick(item.key!)}
                  >
                    {body}
                  </button>
                ) : (
                  <span className="flex items-center gap-1.5 px-0.5">{body}</span>
                )}
              </li>
            )
          })}
        </ul>
      )}
      <div className="pointer-events-none absolute inset-0 [&_button]:pointer-events-auto">
        {overlay}
      </div>
    </div>
  )
}
