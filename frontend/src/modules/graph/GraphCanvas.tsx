/**
 * 그래프 캔버스 — 구조 그림과 탐색 그림이 **함께 쓰는** 렌더러.
 *
 * 무엇을 그릴지는 호스트가 정한다(노드의 색·크기·표식, 선의 굵기·점선). 여기는
 * force 배치·페인트·항해(줌·집중·전체화면)만 한다. 두 그림이 페인트를 따로 가지면
 * 같은 「잘림」 표식이 한쪽에서만 보이게 된다.
 *
 * ## 클릭을 놓치지 않으려면 (ReportArchive 에서 배운 것)
 *
 *   - **히트 영역은 화면 기준으로 최소 폭을 보장한다.** 커스텀 paint 를 쓰면 기본
 *     히트영역이 안 맞고, 월드 단위 반지름은 축소하면 몇 px 로 줄어 눌러도 안 잡힌다.
 *   - **움직이는 과녁을 없앤다.** 시뮬레이션이 식고 화면 맞춤이 끝날 때까지(`ready`)
 *     캔버스를 숨긴다. 맞춤은 애니메이션 없이 즉시 — 카메라가 움직이는 동안 누르면
 *     노드가 손가락 밑에서 도망간다.
 *   - **데이터가 바뀌어도 자리를 지킨다.** 펼치기·색 바꾸기마다 처음부터 다시 흩어지면
 *     방금 보던 노드가 어디로 갔는지 잃는다. 있던 노드는 x,y 를 이어받고, 새 노드는
 *     이웃 곁에서 시작한다.
 *   - 안내문·범례는 `pointer-events-none` — 캔버스 위 띠가 클릭을 먹지 않게.
 *
 * ## 항해
 *
 *   클릭        선택 — 그 노드와 이웃만 밝히고 나머지는 흐린다
 *   더블클릭    호스트에게 넘긴다(「여기를 중심으로」)
 *   빈 곳       선택 해제
 *   hover       이웃 강조(선택보다 우선) + 미니카드
 *   홈          화면 맞춤으로 되돌린다
 *   넓게 보기   전체화면. ESC 로 돌아온다
 *   무리 외곽선 색이 무리별일 때 같은 무리를 convex hull 로 감싼다(ReportArchive §11.4 4b)
 *   찾아가기    호스트가 목록에서 고른 노드로 카메라를 옮긴다(`centerOn`)
 *
 * 키보드(입력 칸 밖에서): ESC 넓게 보기 닫기 → 선택 해제 · F 화면 맞춤 · +/- 줌 ·
 * ? 도움말. Enter·`/` 같은 호스트 몫은 `useShortcuts` 로 호스트가 건다.
 *
 * ## 많아졌을 때
 *
 *   - 라벨은 확대했을 때만(노드가 적으면 늘). 선 라벨은 호스트가 켤 때만.
 *   - `cooldownTicks` 로 시뮬레이션을 끊는다. 안 끊으면 큰 그래프가 영원히 흔들린다.
 *
 * react-force-graph-2d 는 무겁고 이 화면에서만 쓰므로 lazy 로 분리한다. 라이브러리가
 * node/link 객체를 **직접 변형**(x, y 주입, source/target 을 노드 참조로 치환)하므로
 * 호스트는 데이터가 바뀔 때마다 새 객체를 넘기고, 여기서 옛 객체의 좌표를 옮겨 싣는다.
 */

import { Fragment, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  ForceGraphMethods,
  ForceGraphProps,
  LinkObject,
  NodeObject,
} from 'react-force-graph-2d'
import { forceCollide } from 'd3-force'
import { polygonHull } from 'd3-polygon'
import { Download, HelpCircle, Home, Loader2, Maximize2, Minimize2 } from 'lucide-react'

import { withAlpha } from '@/modules/graph/colors'
import { useElementSize } from '@/modules/graph/useElementSize'
import { useShortcuts } from '@/modules/graph/useShortcuts'
import { Button } from '@/shared/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { useTheme } from '@/shared/theme/ThemeProvider'

/** 호스트가 넘기는 노드. 색·크기는 **호스트가 정한 것**이다 — 여기서 다시 고르지 않는다. */
export interface CanvasNode {
  id: string
  label: string
  /** 아래 붙는 작은 글씨(타입 이름·개수). */
  sublabel?: string
  color: string
  /** world 단위 반지름. */
  radius: number
  /** 사각형이면 타입 노드(구조 그림). 원이면 객체 노드. */
  shape: 'circle' | 'square'
  /** 강조 링 색. 시작점. */
  ring?: string | null
  /** 노드 옆에 붙는 표식(「+12」). **잘렸다는 말**이다 — 없으면 그림이 전부로 읽힌다. */
  badge?: string | null
  /** 미니카드(툴팁)에 적을 줄들. 없으면 label·sublabel 로 만든다. */
  card?: string[]
  /** 새로 들어온 노드를 어느 노드 곁에 놓을지 — 펼치기의 출발점. */
  near?: string | null
  /** 같은 값끼리 외곽선으로 감싼다(무리). 없으면 안 감싼다. */
  hull?: string | null
  /** hull 의 색. `hull` 이 있을 때만 본다. */
  hullColor?: string
}

export interface CanvasLink {
  id: string
  source: string
  target: string
  label: string
  /** hover 툴팁. 없으면 label. 「개발사 ↔ 개발함」 처럼 양방향을 함께 적는 자리. */
  tooltip?: string
  directed: boolean
  width: number
  /** 선 색. 없으면 회색 — 관계 종류가 여럿일 때 호스트가 준다. */
  color?: string
  /** 정의만 있고 비어 있는 선. */
  dashed?: boolean
}

export interface LegendItem {
  color: string
  label: string
  /** 선 범례면 참 — 점 대신 짧은 선으로 그린다. */
  line?: boolean
  /** 눌렀을 때 「이 범주만 또렷」 — 호스트가 matchIds 로 답한다. */
  key?: string
}

type Node = NodeObject<CanvasNode>
/** 라이브러리가 source/target 을 **노드 참조로 바꿔 넣는다** — 호스트의 문자열 타입과 겹치면 안 된다. */
type LinkData = Omit<CanvasLink, 'source' | 'target'>
type Link = LinkObject<CanvasNode, LinkData>
type Methods = ForceGraphMethods<Node, Link>

// lazy 를 거치면 제네릭이 `{}` 로 떨어진다 — 여기서 한 번 모양을 박아 둔다.
type TypedProps = ForceGraphProps<Node, Link> & {
  ref?: React.MutableRefObject<Methods | undefined>
}
const ForceGraph2D = lazy(async () => {
  const mod = await import('react-force-graph-2d')
  return { default: mod.default as unknown as React.ComponentType<TypedProps> }
})

interface GraphCanvasProps {
  nodes: CanvasNode[]
  links: CanvasLink[]
  /** 선 위에 관계 이름을 늘 그릴지. 선이 수십 개인 구조 그림에서만 켠다. */
  linkLabels?: boolean
  selectedId?: string | null
  /** 그림 안 검색 — 이 집합만 또렷하게. null 이면 검색 없음. */
  matchIds?: Set<string> | null
  /** 색 범례. 없으면 안 그린다. */
  legend?: LegendItem[]
  /** 범례 항목을 눌렀을 때(key 가 있는 것만). 같은 것을 다시 누르면 해제하는 것은 호스트 몫. */
  onLegendClick?: (key: string) => void
  /** 지금 눌려 있는 범례 항목. */
  legendActive?: string | null
  onNodeClick?: (id: string) => void
  /** 같은 노드를 350ms 안에 두 번. 호스트는 대개 「여기를 중심으로」 로 쓴다. */
  onNodeDoubleClick?: (id: string) => void
  onBackgroundClick?: () => void
  /** ESC — 넓게 보기가 아닐 때. 호스트는 대개 선택 해제로 쓴다. */
  onEscape?: () => void
  /** 이 노드로 카메라를 옮긴다. 같은 노드를 다시 찾아가려면 nonce 를 올린다. */
  centerOn?: { id: string; nonce: number } | null
  /** 호스트가 거는 단축키를 도움말에 함께 적는다. */
  hostShortcuts?: { keys: string; what: string }[]
  /** 캔버스 위에 절대배치할 것 — 빈 상태·로딩·잘림 안내. **클릭을 먹지 않게** 만든다. */
  overlay?: React.ReactNode
  /** PNG 파일 이름 앞부분. */
  exportName?: string
  className?: string
}

/** 이 수 이하면 확대하지 않아도 라벨을 그린다. */
const LABEL_ALWAYS_BELOW = 60
/** 이보다 확대해야 라벨이 보인다(노드가 많을 때). */
const LABEL_MIN_SCALE = 1.1
/** 화면 맞춤 뒤 허용할 최대 배율 — 노드가 적을 때 과확대를 막는다. */
const MAX_FIT_ZOOM = 2.2
/** 히트 영역의 화면상 최소 반지름(px). 이보다 작으면 축소했을 때 눌러도 안 잡힌다. */
const MIN_HIT_PX = 10
/** 같은 노드를 이 안에 두 번 누르면 더블클릭. */
const DOUBLE_CLICK_MS = 350
/** 흐려진 노드·선의 투명도. */
const DIM_ALPHA = 0.12
/** 화살촉 길이(월드). 노드 밖에 보이도록 relPos 를 계산한다. */
const ARROW_LEN = 5
/** 무리 외곽선 — 이 수 미만의 무리는 안 감싼다(셋 미만은 선이 안 된다). */
const HULL_MIN_NODES = 3
const HULL_PAD = 12
const HULL_FILL_ALPHA = 0.09
const HULL_STROKE_ALPHA = 0.5
/** +/- 한 번의 줌 배율. */
const ZOOM_STEP = 1.3

const CANVAS_SHORTCUTS = [
  { keys: 'Esc', what: '넓게 보기 닫기 → 선택 해제' },
  { keys: 'F', what: '화면 맞춤' },
  { keys: '+ / −', what: '확대 / 축소' },
  { keys: '?', what: '이 도움말' },
]
/**
 * 간격 — 기본 force 는 노드를 서로 당겨 붙인다(반발 -30, 링크 30). 라벨이 겹쳐 아무것도
 * 못 읽는 상태가 그것이다. 반발을 키우고 링크를 늘리고, **겹침 금지(collide)** 를 더한다.
 * 링크 거리는 양 끝 반지름에 비례 — 큰 노드끼리는 더 멀리.
 */
const CHARGE_STRENGTH = -220
const LINK_DISTANCE_BASE = 70
/** 라벨이 노드 아래 두 줄 붙으므로 반지름보다 넉넉히 띄운다. */
const COLLIDE_PADDING = 14

function idOf(end: Link['source']): string {
  if (end === undefined) return ''
  return typeof end === 'object' ? String(end.id) : String(end)
}

/** nodeLabel 은 HTML 로 들어간다 — 사람이 적은 이름을 그대로 넣지 않는다. */
function escapeHtml(raw: string): string {
  return raw
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function GraphCanvas({
  nodes,
  links,
  linkLabels = false,
  selectedId = null,
  matchIds = null,
  legend,
  onLegendClick,
  legendActive = null,
  onNodeClick,
  onNodeDoubleClick,
  onBackgroundClick,
  onEscape,
  centerOn = null,
  hostShortcuts,
  overlay,
  exportName = '그래프',
  className,
}: GraphCanvasProps) {
  const [containerRef, size] = useElementSize<HTMLDivElement>()
  const { theme } = useTheme()
  const graphRef = useRef<Methods | undefined>(undefined)
  const [ready, setReady] = useState(false)
  const [wide, setWide] = useState(false)
  const [help, setHelp] = useState(false)
  const [hoveredId, setHoveredId] = useState<string | null>(null)

  const palette = useMemo(
    () =>
      theme === 'dark'
        ? {
            label: '#e2e8f0',
            sublabel: '#94a3b8',
            link: 'rgba(148,163,184,0.5)',
            linkLabel: '#94a3b8',
            linkLabelBg: 'rgba(15,23,42,0.85)',
            badgeBg: '#f59e0b',
            badgeText: '#0f172a',
            bg: '#0b1220',
          }
        : {
            label: '#1e293b',
            sublabel: '#64748b',
            link: 'rgba(100,116,139,0.5)',
            linkLabel: '#64748b',
            linkLabelBg: 'rgba(248,250,252,0.85)',
            badgeBg: '#f59e0b',
            badgeText: '#0f172a',
            bg: '#f8fafc',
          },
    [theme],
  )

  // 라이브러리가 객체를 변형하므로 **매번 새 객체**를 만들되, 옛 객체의 좌표를 이어받는다.
  // 새 노드는 `near`(펼친 노드) 곁에서 시작 — 화면 반대편에서 날아오지 않게.
  const previous = useRef<{ nodes: Node[]; links: Link[] }>({ nodes: [], links: [] })
  const graphData = useMemo(() => {
    const was = new Map(previous.current.nodes.map((node) => [String(node.id), node]))
    const next: Node[] = nodes.map((node) => {
      const old = was.get(node.id)
      if (old && old.x !== undefined && old.y !== undefined) {
        return { ...node, x: old.x, y: old.y, vx: 0, vy: 0 }
      }
      const anchor = node.near ? was.get(node.near) : undefined
      if (anchor && anchor.x !== undefined && anchor.y !== undefined) {
        const angle = Math.random() * Math.PI * 2
        const distance = LINK_DISTANCE_BASE * (0.6 + Math.random() * 0.6)
        return {
          ...node,
          x: anchor.x + Math.cos(angle) * distance,
          y: anchor.y + Math.sin(angle) * distance,
        }
      }
      return { ...node }
    })
    const data = { nodes: next, links: links.map((link) => ({ ...link })) as Link[] }
    previous.current = data
    return data
  }, [nodes, links])

  // 이웃 — hover·선택 강조가 쓴다. 문자열 id 로 만든다(치환 전후 모두 안전).
  const adjacency = useMemo(() => {
    const map = new Map<string, Set<string>>()
    const add = (a: string, b: string) => {
      if (!map.has(a)) map.set(a, new Set())
      map.get(a)!.add(b)
    }
    for (const link of links) {
      add(link.source, link.target)
      add(link.target, link.source)
    }
    return map
  }, [links])

  // 강조 집합 — hover > 선택 > 검색. null 이면 전부 또렷.
  const focusSet = useMemo(() => {
    const around = (id: string) => new Set([id, ...(adjacency.get(id) ?? [])])
    if (hoveredId && adjacency.has(hoveredId)) return around(hoveredId)
    if (selectedId && adjacency.has(selectedId)) return around(selectedId)
    if (matchIds) return matchIds
    return null
  }, [hoveredId, selectedId, matchIds, adjacency])
  const isDim = useCallback((id: string) => focusSet !== null && !focusSet.has(id), [focusSet])

  // force 는 인스턴스에 한 번 걸면 남는다. lazy 라 ref 가 언제 채워질지 몰라서,
  // 채워질 때까지 프레임마다 본다. 빈 그림을 거쳐 다시 그리면 **새 인스턴스**라
  // 어느 인스턴스에 걸었는지를 기억한다 — 안 그러면 두 번째 그림은 기본 force 로 붙는다.
  const tunedFor = useRef<Methods | undefined>(undefined)
  useEffect(() => {
    let raf = 0
    const tune = () => {
      const graph = graphRef.current
      if (!graph) {
        raf = requestAnimationFrame(tune)
        return
      }
      if (tunedFor.current === graph) return
      tunedFor.current = graph
      graph.d3Force('charge')?.strength(CHARGE_STRENGTH)
      graph.d3Force('link')?.distance((link: Link) => {
        const a = typeof link.source === 'object' ? link.source.radius : 0
        const b = typeof link.target === 'object' ? link.target.radius : 0
        return LINK_DISTANCE_BASE + a + b
      })
      graph.d3Force(
        'collide',
        forceCollide<Node>((node) => node.radius + COLLIDE_PADDING).strength(0.9),
      )
      graph.d3ReheatSimulation()
    }
    raf = requestAnimationFrame(tune)
    return () => cancelAnimationFrame(raf)
  }, [graphData])

  // 데이터가 바뀌면 식을 때까지 숨겼다가, 배치가 끝난 뒤 **즉시** 맞춰 드러낸다.
  // 펼쳐지는 과정을 보여 주면 확대→축소로 깜빡이고, 그동안 누른 클릭은 빗나간다.
  // 단, 위치를 이어받은 갱신(펼치기·색)은 이미 자리가 있으니 숨기지 않는다.
  const fitPending = useRef(true)
  const hadPositions = useRef(false)
  useEffect(() => {
    const kept = graphData.nodes.some((node) => node.x !== undefined)
    fitPending.current = true
    if (!kept) {
      hadPositions.current = false
      setReady(false)
    } else {
      hadPositions.current = true
    }
  }, [graphData])

  const fit = useCallback((animate: boolean) => {
    const graph = graphRef.current
    if (!graph) return
    graph.zoomToFit(animate ? 300 : 0, 40)
    // zoomToFit 은 노드가 둘이면 화면이 꽉 차도록 크게 확대한다 — 상한을 건다.
    const clamp = () => {
      const current = graphRef.current
      if (current && current.zoom() > MAX_FIT_ZOOM) current.zoom(MAX_FIT_ZOOM, 0)
    }
    if (animate) window.setTimeout(clamp, 320)
    else clamp()
  }, [])

  const handleEngineStop = useCallback(() => {
    if (!fitPending.current) return // 드래그로 재가열됐다 식은 것 — 줌을 건드리지 않는다
    fitPending.current = false
    if (!hadPositions.current) fit(false)
    setReady(true)
  }, [fit])

  // 더블클릭 — 라이브러리에 없어 시간으로 가른다.
  const lastClick = useRef<{ id: string; at: number }>({ id: '', at: 0 })
  const handleNodeClick = useCallback(
    (node: Node) => {
      const id = String(node.id)
      const now = Date.now()
      const before = lastClick.current
      if (before.id === id && now - before.at < DOUBLE_CLICK_MS && onNodeDoubleClick) {
        lastClick.current = { id: '', at: 0 }
        onNodeDoubleClick(id)
        return
      }
      lastClick.current = { id, at: now }
      onNodeClick?.(id)
    },
    [onNodeClick, onNodeDoubleClick],
  )

  const zoomBy = useCallback((factor: number) => {
    const graph = graphRef.current
    if (!graph) return
    graph.zoom(graph.zoom() * factor, 200)
  }, [])

  // 단축키 — ESC 는 층이 있다: 도움말 → 넓게 보기 → 선택. 한 번에 하나만 닫는다.
  const shortcuts = useMemo(
    () => ({
      Escape: () => {
        if (help) setHelp(false)
        else if (wide) setWide(false)
        else onEscape?.()
      },
      f: () => fit(true),
      F: () => fit(true),
      '+': () => zoomBy(ZOOM_STEP),
      '=': () => zoomBy(ZOOM_STEP),
      '-': () => zoomBy(1 / ZOOM_STEP),
      '?': () => setHelp((value) => !value),
    }),
    [help, wide, onEscape, fit, zoomBy],
  )
  useShortcuts(shortcuts)

  // 찾아가기 — 목록에서 고른 노드로 카메라를 옮긴다. 배치가 끝난 뒤(ready)에만.
  useEffect(() => {
    if (!centerOn || !ready) return
    const node = previous.current.nodes.find((one) => String(one.id) === centerOn.id)
    const graph = graphRef.current
    if (!node || !graph || node.x === undefined || node.y === undefined) return
    graph.centerAt(node.x, node.y, 400)
    if (graph.zoom() < 1.2) graph.zoom(1.5, 400)
  }, [centerOn, ready])

  const exportPng = useCallback(() => {
    const canvas = containerRef.current?.querySelector('canvas')
    if (!canvas) return
    const now = new Date()
    const two = (n: number) => String(n).padStart(2, '0')
    const stamp = `${now.getFullYear()}${two(now.getMonth() + 1)}${two(now.getDate())}-${two(now.getHours())}${two(now.getMinutes())}`
    const anchor = document.createElement('a')
    anchor.href = canvas.toDataURL('image/png')
    anchor.download = `${exportName}_${stamp}.png`
    anchor.click()
  }, [containerRef, exportName])

  const alwaysLabel = nodes.length <= LABEL_ALWAYS_BELOW

  const paintNode = useCallback(
    (node: Node, ctx: CanvasRenderingContext2D, scale: number) => {
      const id = String(node.id)
      const x = node.x ?? 0
      const y = node.y ?? 0
      const r = node.radius
      ctx.save()
      if (isDim(id)) ctx.globalAlpha = DIM_ALPHA
      ctx.beginPath()
      if (node.shape === 'square') {
        ctx.rect(x - r, y - r, r * 2, r * 2)
      } else {
        ctx.arc(x, y, r, 0, Math.PI * 2)
      }
      ctx.fillStyle = node.color
      ctx.fill()

      const selected = id === selectedId
      const ring = selected ? palette.label : node.ring
      if (ring) {
        ctx.lineWidth = Math.max(1.5, 2.5 / scale)
        ctx.strokeStyle = ring
        ctx.stroke()
      }

      if (alwaysLabel || scale >= LABEL_MIN_SCALE || selected || id === hoveredId) {
        const fontSize = Math.max(3, Math.min(12, 11 / scale))
        ctx.font = `${selected ? '600 ' : ''}${fontSize}px sans-serif`
        ctx.textAlign = 'center'
        ctx.textBaseline = 'top'
        ctx.fillStyle = palette.label
        ctx.fillText(node.label, x, y + r + 1.5)
        if (node.sublabel) {
          ctx.font = `${fontSize * 0.8}px sans-serif`
          ctx.fillStyle = palette.sublabel
          ctx.fillText(node.sublabel, x, y + r + 1.5 + fontSize + 1)
        }
      }

      if (node.badge) {
        // 「+N」 — 잘렸다는 표식. 노드 오른쪽 위에 붙는다.
        const fontSize = Math.max(3, Math.min(9, 9 / scale))
        ctx.font = `600 ${fontSize}px sans-serif`
        const width = ctx.measureText(node.badge).width + fontSize * 0.8
        const bx = x + r * 0.7
        const by = y - r - fontSize * 0.2
        ctx.fillStyle = palette.badgeBg
        ctx.beginPath()
        ctx.roundRect(bx, by - fontSize * 0.7, width, fontSize * 1.4, fontSize * 0.7)
        ctx.fill()
        ctx.fillStyle = palette.badgeText
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText(node.badge, bx + width / 2, by)
      }
      ctx.restore()
    },
    [alwaysLabel, palette, selectedId, hoveredId, isDim],
  )

  // 히트 영역 — 그린 모양보다 조금 크게, **화면 기준 최소 폭**을 보장한다.
  const paintPointerArea = useCallback(
    (node: Node, color: string, ctx: CanvasRenderingContext2D, scale: number) => {
      const x = node.x ?? 0
      const y = node.y ?? 0
      const r = Math.max(node.radius + 2, MIN_HIT_PX / scale)
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.arc(x, y, r, 0, Math.PI * 2)
      ctx.fill()
    },
    [],
  )

  const linkDim = useCallback(
    (link: Link) => isDim(idOf(link.source)) || isDim(idOf(link.target)),
    [isDim],
  )

  const paintLinkLabel = useCallback(
    (link: Link, ctx: CanvasRenderingContext2D, scale: number) => {
      if (!linkLabels || scale < 0.6 || linkDim(link)) return
      const source = link.source
      const target = link.target
      if (typeof source !== 'object' || typeof target !== 'object') return
      const sx = source.x ?? 0
      const sy = source.y ?? 0
      const tx = target.x ?? 0
      const ty = target.y ?? 0
      const mx = (sx + tx) / 2
      const my = (sy + ty) / 2
      const fontSize = Math.max(3, Math.min(10, 9 / scale))
      ctx.font = `${fontSize}px sans-serif`
      const width = ctx.measureText(link.label).width + 4
      ctx.fillStyle = palette.linkLabelBg
      ctx.fillRect(mx - width / 2, my - fontSize / 2 - 1, width, fontSize + 2)
      ctx.fillStyle = palette.linkLabel
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(link.label, mx, my)
    },
    [linkLabels, linkDim, palette],
  )

  // 화살촉을 도착 노드 **밖**에 둔다 — relPos=1 이면 노드 중심에 찍혀 안 보인다.
  const arrowRelPos = useCallback((link: Link) => {
    const source = link.source
    const target = link.target
    if (typeof source !== 'object' || typeof target !== 'object') return 1
    const length =
      Math.hypot((target.x ?? 0) - (source.x ?? 0), (target.y ?? 0) - (source.y ?? 0)) || 1
    return Math.max(0, 1 - (target.radius + ARROW_LEN + 2) / length)
  }, [])

  // 무리 외곽선 — 같은 hull 값의 노드를 convex hull 로 감싼다. 매 프레임 현재
  // x,y 로 다시 계산한다(force 가 위치를 갱신). 멤버가 전부 흐림이면 외곽선도 옅게.
  const paintHulls = useCallback(
    (ctx: CanvasRenderingContext2D, scale: number) => {
      const groups = new Map<string, Node[]>()
      for (const node of previous.current.nodes) {
        if (!node.hull || node.x === undefined) continue
        if (!groups.has(node.hull)) groups.set(node.hull, [])
        groups.get(node.hull)!.push(node)
      }
      for (const members of groups.values()) {
        if (members.length < HULL_MIN_NODES) continue
        const color = members[0].hullColor
        if (!color) continue
        const points: [number, number][] = []
        for (const node of members) {
          const x = node.x ?? 0
          const y = node.y ?? 0
          const r = node.radius + HULL_PAD
          const d = r * 0.7071
          points.push(
            [x + r, y], [x - r, y], [x, y + r], [x, y - r],
            [x + d, y + d], [x - d, y + d], [x + d, y - d], [x - d, y - d],
          )
        }
        const hull = polygonHull(points)
        if (!hull || hull.length < 3) continue
        const k = members.some((node) => !isDim(String(node.id))) ? 1 : 0.3
        ctx.beginPath()
        ctx.moveTo(hull[0][0], hull[0][1])
        for (let i = 1; i < hull.length; i += 1) ctx.lineTo(hull[i][0], hull[i][1])
        ctx.closePath()
        ctx.fillStyle = withAlpha(color, HULL_FILL_ALPHA * k)
        ctx.fill()
        ctx.strokeStyle = withAlpha(color, HULL_STROKE_ALPHA * k)
        ctx.lineWidth = 1.5 / scale
        ctx.stroke()
      }
    },
    [isDim],
  )

  const cardOf = useCallback((node: Node) => {
    const lines = node.card ?? [node.label, node.sublabel ?? ''].filter(Boolean)
    const [first, ...rest] = lines
    return `<div style="font:12px sans-serif;max-width:260px;line-height:1.35">
      <div style="font-weight:600">${escapeHtml(first ?? '')}</div>
      ${rest.map((line) => `<div style="opacity:.7">${escapeHtml(line)}</div>`).join('')}
    </div>`
  }, [])

  const hasNodes = graphData.nodes.length > 0
  const frame = wide
    ? 'fixed inset-0 z-50 rounded-none border-0'
    : 'h-[min(640px,calc(100vh-220px))] min-h-[420px] rounded-md border'

  return (
    // **높이는 고정이고 캔버스는 레이아웃 밖(absolute)이다.** 높이가 내용을 따르면
    // 캔버스가 컨테이너를 키우고, ResizeObserver 가 더 큰 값을 재고, 캔버스가 또
    // 커진다 — 화면이 아래로 끝없이 자라는 되먹임이 그것이다.
    <div
      ref={containerRef}
      className={`bg-muted/20 relative w-full overflow-hidden ${frame} ${className ?? ''}`}
      style={wide ? { background: palette.bg } : undefined}
    >
      {hasNodes && size.width > 0 && (
        <Suspense
          fallback={
            <div className="absolute inset-0 flex items-center justify-center">
              <Loader2 className="text-muted-foreground size-5 animate-spin" />
            </div>
          }
        >
          <div
            className="absolute inset-0 transition-opacity duration-200"
            style={{ opacity: ready ? 1 : 0 }}
          >
            <ForceGraph2D
              ref={graphRef}
              graphData={graphData}
              width={size.width}
              height={size.height}
              nodeCanvasObjectMode={() => 'replace'}
              nodeCanvasObject={paintNode}
              nodePointerAreaPaint={paintPointerArea}
              nodeLabel={cardOf}
              linkColor={(link: Link) =>
                linkDim(link) ? 'rgba(148,163,184,0.08)' : (link.color ?? palette.link)
              }
              linkWidth={(link: Link) => link.width}
              linkLineDash={(link: Link) => (link.dashed ? [3, 3] : null)}
              linkDirectionalArrowLength={(link: Link) =>
                link.directed && !linkDim(link) ? ARROW_LEN : 0
              }
              linkDirectionalArrowRelPos={arrowRelPos}
              linkLabel={(link: Link) => escapeHtml(link.tooltip ?? link.label)}
              linkCanvasObjectMode={() => 'after'}
              linkCanvasObject={paintLinkLabel}
              // 자기 자신을 가리키는 선은 굽혀야 보인다.
              linkCurvature={(link: Link) => (idOf(link.source) === idOf(link.target) ? 0.6 : 0)}
              cooldownTicks={120}
              warmupTicks={nodes.length > 200 ? 40 : 0}
              onRenderFramePre={paintHulls}
              onEngineStop={handleEngineStop}
              onNodeClick={handleNodeClick}
              onNodeHover={(node: Node | null) => setHoveredId(node ? String(node.id) : null)}
              onBackgroundClick={() => onBackgroundClick?.()}
              minZoom={0.2}
              maxZoom={8}
            />
          </div>
          {!ready && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <Loader2 className="text-muted-foreground size-5 animate-spin" />
            </div>
          )}
          {/* 도구 — 오른쪽 위. 홈·넓게 보기·PNG. */}
          <div className="absolute top-2 right-2 z-10 flex items-center gap-1">
            <Button
              size="icon-sm"
              variant="outline"
              className="bg-background/90 backdrop-blur"
              aria-label="화면 맞춤"
              title="화면 맞춤"
              onClick={() => fit(true)}
            >
              <Home className="size-3.5" />
            </Button>
            <Button
              size="icon-sm"
              variant="outline"
              className="bg-background/90 backdrop-blur"
              aria-label={wide ? '축소' : '넓게 보기'}
              title={wide ? '축소 (ESC)' : '넓게 보기'}
              onClick={() => setWide((value) => !value)}
            >
              {wide ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
            </Button>
            <Button
              size="icon-sm"
              variant="outline"
              className="bg-background/90 backdrop-blur"
              aria-label="PNG 저장"
              title="현재 화면을 PNG 로 저장"
              disabled={!ready}
              onClick={exportPng}
            >
              <Download className="size-3.5" />
            </Button>
            <Popover open={help} onOpenChange={setHelp}>
              <PopoverTrigger asChild>
                <Button
                  size="icon-sm"
                  variant="outline"
                  className="bg-background/90 backdrop-blur"
                  aria-label="조작법"
                  title="조작법 (?)"
                >
                  <HelpCircle className="size-3.5" />
                </Button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-64 p-3 text-xs">
                <p className="mb-2 font-medium">조작법</p>
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                  <dt className="text-muted-foreground">클릭</dt>
                  <dd>선택 — 이웃만 밝게</dd>
                  <dt className="text-muted-foreground">더블클릭</dt>
                  <dd>{onNodeDoubleClick ? '여기를 중심으로' : '—'}</dd>
                  <dt className="text-muted-foreground">드래그</dt>
                  <dd>노드 옮기기 · 빈 곳은 화면 이동</dd>
                  <dt className="text-muted-foreground">휠</dt>
                  <dd>확대 / 축소</dd>
                  {[...CANVAS_SHORTCUTS, ...(hostShortcuts ?? [])].map((one) => (
                    <Fragment key={one.keys}>
                      <dt className="text-muted-foreground font-mono">{one.keys}</dt>
                      <dd>{one.what}</dd>
                    </Fragment>
                  ))}
                </dl>
              </PopoverContent>
            </Popover>
          </div>
          {legend && legend.length > 0 && (
            <ul className="text-muted-foreground bg-background/80 absolute right-2 bottom-2 z-10 max-h-48 space-y-0.5 overflow-y-auto rounded px-2 py-1 text-xs">
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
                const active = item.key !== undefined && item.key === legendActive
                return (
                  <li key={`${item.line ? 'l' : 'n'}:${item.label}`}>
                    {item.key !== undefined && onLegendClick ? (
                      <button
                        type="button"
                        className={`hover:text-foreground flex w-full items-center gap-1.5 rounded px-0.5 text-left ${
                          active ? 'text-foreground font-medium' : ''
                        }`}
                        title="이 범주만 또렷하게 (다시 누르면 해제)"
                        onClick={() => onLegendClick(item.key!)}
                      >
                        {swatch}
                        <span className="truncate">{item.label}</span>
                      </button>
                    ) : (
                      <span className="flex items-center gap-1.5 px-0.5">
                        {swatch}
                        <span className="truncate">{item.label}</span>
                      </span>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </Suspense>
      )}
      {/* 호스트의 안내문 — 캔버스 위에 뜨되 **클릭을 먹지 않는다.** 안의 단추만 다시 살린다. */}
      <div className="pointer-events-none absolute inset-0 [&_button]:pointer-events-auto">
        {overlay}
      </div>
    </div>
  )
}
