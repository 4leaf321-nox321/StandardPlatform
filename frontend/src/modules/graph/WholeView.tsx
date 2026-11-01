/**
 * 정적 그리기 — **자리를 정하고(배치), 그 자리에 그린다(`WholeCanvas`)**(ADR 0045).
 *
 * 탐색 화면이 lazy 로 매다는 덩어리다 — 그리기(sigma) · 배치(ForceAtlas2 작업자)는 무거워서, 정적
 * 그리기를 한 번도 안 켜는 사람은 받지 않는다(AGENTS.md 「차트를 쓰는 화면은 lazy」 와 같은 까닭).
 *
 * 자리는 셋 중 하나에서 온다. 그리는 쪽은 그 차이를 모른다:
 *
 *   서버      응답에 x · y 가 있으면 그대로(다음 단계 — 지금은 늘 비어 있다)
 *   단계      동심원 — 이 스레드에서 거의 즉시(`ringLayout`)
 *   무리      ForceAtlas2 — 작업자 스레드에서, 진행률을 받으며(`startCluster`)
 *
 * 한 번 센 자리는 그 그림 동안 기억한다 — 무리 ↔ 단계를 오가도 다시 세지 않는다. 씨앗이 바뀌면
 * 돌던 배치를 끝낸다.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, X } from 'lucide-react'

import type { Whole } from '@/modules/graph/api'
import { BACKDROP, blend, colorScale } from '@/modules/graph/colors'
import WholeCanvas from '@/modules/graph/WholeCanvas'
import type { WholeLegendItem } from '@/modules/graph/WholeCanvas'
import type { PreparedWhole } from '@/modules/graph/wholeData'
import { CORE_NODES, ringLayout } from '@/modules/graph/wholeLayout'
import type { Positions } from '@/modules/graph/wholeLayout'
import { startCluster } from '@/modules/graph/wholeWorker'
import { Button } from '@/shared/components/ui/button'
import { useTheme } from '@/shared/theme/ThemeProvider'

/** 배치 — 무리(ForceAtlas2) · 단계(동심원). */
export type Arrange = 'cluster' | 'rings'

interface LayoutState {
  /** 이 자리가 어느 그림의 것인가 — 다른 그림의 자리를 잠깐이라도 그리지 않게. */
  owner: PreparedWhole | null
  positions: Positions | null
  /** 0 ~ 1 — 무리 배치가 도는 동안만. */
  progress: number | null
  error: string | null
}

/**
 * 자리 — 서버 값 · 단계 · 무리 중 하나. 무리는 작업자에서 돌고, 도는 동안 앞 자리(같은 그림의 다른
 * 배치)를 그대로 둔다 — 빈 화면보다 낫고, 다 되면 갈아 끼운다.
 */
export function useWholeLayout(prepared: PreparedWhole, arrange: Arrange): LayoutState {
  const kept = useRef(new WeakMap<PreparedWhole, Map<Arrange, Positions>>())
  const [state, setState] = useState<LayoutState>({
    owner: null,
    positions: null,
    progress: null,
    error: null,
  })
  useEffect(() => {
    if (prepared.server) {
      setState({ owner: prepared, positions: prepared.server, progress: null, error: null })
      return undefined
    }
    let memo = kept.current.get(prepared)
    if (!memo) {
      memo = new Map()
      kept.current.set(prepared, memo)
    }
    const ready = memo.get(arrange)
    if (ready) {
      setState({ owner: prepared, positions: ready, progress: null, error: null })
      return undefined
    }
    if (arrange === 'rings') {
      const positions = ringLayout(prepared)
      memo.set('rings', positions)
      setState({ owner: prepared, positions, progress: null, error: null })
      return undefined
    }
    setState((current) => ({
      owner: prepared,
      positions: current.owner === prepared ? current.positions : null,
      progress: 0,
      error: null,
    }))
    let live = true
    const job = startCluster(
      { n: prepared.n, src: prepared.src, dst: prepared.dst, type: prepared.type },
      (progress) => {
        if (live) setState((current) => ({ ...current, progress }))
      },
    )
    const remember = memo
    job.promise
      .then((positions) => {
        if (!live) return
        remember.set('cluster', positions)
        setState({ owner: prepared, positions, progress: null, error: null })
      })
      .catch((caught: unknown) => {
        if (!live) return
        const message = caught instanceof Error ? caught.message : String(caught)
        setState((current) => ({ ...current, progress: null, error: message }))
      })
    return () => {
      live = false
      job.cancel()
    }
  }, [prepared, arrange])
  return state.owner === prepared ? state : { ...state, positions: null }
}

/** 셈 정렬 — 연결 적은 것부터(그리는 차례: 뒤의 것이 위). */
function ascendingDegree(degree: Uint32Array): Int32Array {
  let most = 0
  for (const one of degree) most = Math.max(most, one)
  const count = new Uint32Array(most + 2)
  for (const one of degree) count[one + 1] += 1
  for (let d = 0; d <= most; d += 1) count[d + 1] += count[d]
  const out = new Int32Array(degree.length)
  for (let i = 0; i < degree.length; i += 1) out[count[degree[i]]++] = i
  return out
}

const GRAY = '#64748b'

/**
 * 「가운데가 뭉친다」 안내의 재료 — 씨앗이 기록 타입이고 이웃 타입이 여럿일 때 호스트가 준다. 그림이
 * 클 때(무리 배치가 핵심 밖 노드를 이웃들의 가운데에 둘 때)만 뜬다.
 */
export interface ClumpHint {
  /** 기록 타입의 허브 칸이 가리키는 타입 이름 — 없으면 단추 없이 안내만. */
  target: string | null
  onNarrow: (() => void) | null
  onDismiss: () => void
}

/** 기록 하나가 평균 이만큼 넘게 선을 가지면 「여러 축에 함께 걸렸다」 로 본다. */
const MANY_AXES = 1.5

export interface WholeViewProps {
  whole: Whole
  prepared: PreparedWhole
  arrange: Arrange
  typeColor: (slug: string) => string
  selected: number | null
  onSelect: (index: number | null) => void
  /** 더블클릭 — 「여기를 중심으로」. */
  onOpen: (index: number) => void
  focusOn: { index: number; nonce: number } | null
  /** 그림 안 찾기 — 이 노드들을 크게. */
  matches: Set<number> | null
  hideIsolated: boolean
  wide: boolean
  onToggleWide: () => void
  clump?: ClumpHint | null
}

export default function WholeView({
  whole,
  prepared,
  arrange,
  typeColor,
  selected,
  onSelect,
  onOpen,
  focusOn,
  matches,
  hideIsolated,
  wide,
  onToggleWide,
  clump = null,
}: WholeViewProps) {
  const layout = useWholeLayout(prepared, arrange)
  const { n, degree } = prepared

  // 노드 — 타입 색, 크기는 연결 수의 로그. 노드가 많을수록 작게 시작한다(확대하면 커진다).
  const look = useMemo(() => {
    const colorOfType = whole.types.map((one) => typeColor(one.slug))
    const base = n <= 2_000 ? 3 : n <= 20_000 ? 2 : 1
    const colors: string[] = Array.from({ length: n })
    const sizes = new Float32Array(n)
    for (let i = 0; i < n; i += 1) {
      colors[i] = colorOfType[prepared.type[i]] ?? GRAY
      sizes[i] = Math.min(14, base + 1.4 * Math.log2(1 + degree[i]))
    }
    return { colors, sizes, order: ascendingDegree(degree) }
  }, [whole.types, typeColor, n, prepared.type, degree])

  // 관계 없는 것 숨기기 — 시작점은 늘 남긴다.
  const hidden = useMemo(() => {
    if (!hideIsolated) return null
    const out = new Uint8Array(n)
    for (let i = 0; i < n; i += 1) {
      if (degree[i] === 0 && !(whole.seed === 'focus' && prepared.depth[i] === 0)) out[i] = 1
    }
    return out
  }, [hideIsolated, n, degree, whole.seed, prepared.depth])
  const hiddenCount = useMemo(
    () => (hidden ? hidden.reduce((sum, one) => sum + one, 0) : 0),
    [hidden],
  )

  // 선 — 종류가 둘 이상이면 종류마다 색, 하나면 회색. 늘 흐리게, 고른 노드의 선만 진하게.
  const { theme } = useTheme()
  const kinds = useMemo(() => {
    const scale =
      whole.relations.length > 1 ? colorScale(whole.relations.map((one) => one.slug)) : null
    const base = whole.relations.map((one) => (scale ? scale(one.slug) : GRAY))
    const backdrop = theme === 'dark' ? BACKDROP.dark : BACKDROP.light
    // 선이 많을수록 더 흐리게 — 선 수십만 개가 노드를 덮지 않게.
    const fade = prepared.m > 100_000 ? 0.88 : prepared.m > 10_000 ? 0.78 : 0.6
    return {
      base,
      faint: base.map((one) => blend(one, backdrop, fade)),
      strong: base,
    }
  }, [whole.relations, prepared.m, theme])

  // 범례 — 타입(누르면 그 타입만 크게)과 선 종류. 수는 이 그림에 실린 것.
  const [legendActive, setLegendActive] = useState<number | null>(null)
  useEffect(() => setLegendActive(null), [prepared])
  const legend = useMemo<WholeLegendItem[]>(
    () => [
      ...whole.types.map((one, at) => ({
        color: typeColor(one.slug),
        label: one.label,
        count: one.count,
        key: at,
      })),
      ...(whole.relations.length > 1
        ? whole.relations.map((one, at) => ({
            color: kinds.base[at],
            label: one.label,
            count: one.count,
            line: true,
          }))
        : []),
    ],
    [whole.types, whole.relations, typeColor, kinds],
  )
  const legendMatches = useMemo(() => {
    if (legendActive === null) return null
    const out = new Set<number>()
    for (let i = 0; i < n; i += 1) if (prepared.type[i] === legendActive) out.add(i)
    return out
  }, [legendActive, n, prepared.type])

  const approximate = arrange === 'cluster' && !prepared.server && n > CORE_NODES
  // 「가운데가 뭉친다」 — 핵심(1.2만) 밖의 기록은 이어진 이웃들의 가운데에 놓인다. 이웃이 여럿이면
  // 그 가운데가 다 한 곳이라 기록 전부가 가운데로 모인다(시장 서비스 전부 · 이웃 다섯 — 실측). 기록이
  // 평균 `MANY_AXES` 넘게 선을 가질 때만(이웃 축이 실제로 여럿일 때만) 말한다.
  const manyAxes = useMemo(() => {
    const seeds = new Set<number>()
    whole.types.forEach((one, at) => {
      if (whole.seed_types.includes(one.slug)) seeds.add(at)
    })
    let records = 0
    for (let i = 0; i < n; i += 1) if (seeds.has(prepared.type[i])) records += 1
    return records > 0 && prepared.m / records > MANY_AXES
  }, [whole.types, whole.seed_types, n, prepared.type, prepared.m])
  const showClump = Boolean(clump) && approximate && manyAxes && layout.progress === null
  const scope =
    whole.seed === 'focus'
      ? `${whole.depth}단계 안을 전부 그렸습니다`
      : whole.depth === 0
        ? '선택한 타입 전부를 그렸습니다'
        : `선택한 타입 전부에서 ${whole.depth}단계 안을 전부 그렸습니다`

  const overlay = (
    <>
      {layout.progress !== null && (
        <div className="absolute inset-x-0 top-3 flex justify-center">
          <p className="bg-background/90 flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs shadow-sm">
            <Loader2 className="size-3.5 animate-spin" />
            자리 잡는 중 {Math.round(layout.progress * 100)}%
          </p>
        </div>
      )}
      {showClump && clump && (
        <div className="absolute inset-x-0 top-3 flex justify-center">
          <p
            className="bg-background/95 flex max-w-[90%] items-center gap-2 rounded-md border border-amber-500/40 px-2 py-1 text-xs shadow-sm"
            role="note"
          >
            <span>
              기록이 여러 축에 함께 걸려 가운데가 뭉칩니다 —{' '}
              {clump.onNarrow ? '한 축만 보면 나뉩니다' : '「이웃 타입」 에서 하나만 골라 보세요'}
            </span>
            {clump.onNarrow && clump.target && (
              <Button size="xs" variant="outline" onClick={clump.onNarrow}>
                {clump.target}만 보기
              </Button>
            )}
            <button
              type="button"
              className="text-muted-foreground hover:text-foreground"
              aria-label="안내 닫기"
              onClick={clump.onDismiss}
            >
              <X className="size-3.5" />
            </button>
          </p>
        </div>
      )}
      {layout.error && (
        <div className="absolute inset-x-0 top-3 flex justify-center">
          <p className="rounded-md border border-red-500/40 bg-red-500/10 px-2 py-1 text-xs">
            배치가 멈췄습니다 — {layout.error}. 「단계」 배치로 바꾸거나 새로고침하세요.
          </p>
        </div>
      )}
      <div className="text-muted-foreground bg-background/85 absolute bottom-2 left-2 max-w-[70%] rounded px-2 py-1 text-xs">
        노드 {n.toLocaleString()} · 선 {prepared.m.toLocaleString()} — {scope}
        {hiddenCount > 0 && ` · 관계 없는 ${hiddenCount.toLocaleString()}개 숨김`}
        {approximate &&
          ` · 무리 배치는 연결 많은 ${CORE_NODES.toLocaleString()}개를 힘으로 놓고 나머지는 이웃 곁에 둡니다`}
      </div>
    </>
  )

  if (!layout.positions) {
    return (
      <div className="relative flex min-h-[420px] flex-1 items-center justify-center rounded-md border">
        <p className="text-muted-foreground flex items-center gap-2 text-sm" role="status">
          <Loader2 className="size-4 animate-spin" />
          {layout.error
            ? `배치가 멈췄습니다 — ${layout.error}`
            : `자리 잡는 중 ${Math.round((layout.progress ?? 0) * 100)}% — 노드 ${n.toLocaleString()} · 선 ${prepared.m.toLocaleString()}`}
        </p>
      </div>
    )
  }

  return (
    <WholeCanvas
      labels={prepared.labels}
      nodeColors={look.colors}
      nodeSizes={look.sizes}
      drawOrder={look.order}
      hidden={hidden}
      positions={layout.positions}
      src={prepared.src}
      dst={prepared.dst}
      edgeKind={prepared.rel}
      kindColors={kinds.faint}
      kindStrong={kinds.strong}
      selected={selected}
      matches={matches ?? legendMatches}
      focusOn={focusOn}
      onSelect={onSelect}
      onOpen={onOpen}
      legend={legend}
      legendActive={legendActive}
      onLegendClick={(key) => setLegendActive((current) => (current === key ? null : key))}
      wide={wide}
      onToggleWide={onToggleWide}
      overlay={overlay}
      exportName={arrange === 'rings' ? '정적 그리기_단계' : '정적 그리기_무리'}
    />
  )
}
