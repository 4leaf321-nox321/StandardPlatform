/**
 * 모판 — **고른 축의 수준을 색으로 보이는 벽.**
 *
 * 바깥 액자는 묶음(담당 부서), 안의 액자 하나가 연계다. 자리 계산은 `d3-hierarchy` 의
 * 트리맵이 한다 — 손으로 짜면 그 코드가 곧 라이브러리의 절반이 되고, 묶음이 늘 때마다
 * 자리가 어긋난다.
 *
 * ⚠️ **크기는 뜻이 없다.** 모든 연계를 같은 값으로 넣는다 — 색만 정보다. 크기에 뜻을
 *    실으면(예: 미평가 수) 큰 액자가 곧 나쁜 것으로 읽히는데, 그 해석은 축마다 다르다.
 * ⚠️ **미평가에는 색을 주지 않는다**(점선 테두리 + 표면색). 값이 없는 것은 낮은 값이
 *    아니고, 색맹 · 인쇄에서도 점선은 남는다.
 */

import { hierarchy, treemap, treemapSquarify } from 'd3-hierarchy'
import { useMemo, useState } from 'react'

import { LEVEL_NONE_DARK, LEVEL_NONE_LIGHT, levelColor } from '@/shared/charts'

import type { AxisDef, Tile } from './api'

export interface WallItem {
  tile: Tile
  /** 이 축의 서열 자리. -1 이면 미평가. */
  index: number
  /** 서열 이름 — 툴팁과 표 보기에 그대로 쓴다. */
  label: string
}

interface Props {
  items: WallItem[]
  axis: AxisDef
  steps: number
  dark: boolean
  onPick: (tile: Tile) => void
}

interface Placed {
  x0: number
  y0: number
  x1: number
  y1: number
  item?: WallItem
  group?: string
}

/** 묶음별로 나누고 트리맵으로 자리를 잡는다 — 액자 크기는 균등(값 1). */
function layout(items: WallItem[], width: number, height: number): Placed[] {
  const groups = new Map<string, WallItem[]>()
  for (const one of items) {
    const key = one.tile.group
    groups.set(key, [...(groups.get(key) ?? []), one])
  }
  const root = hierarchy<{ name: string; children?: unknown[]; item?: WallItem }>(
    {
      name: '',
      children: [...groups.entries()].map(([name, rows]) => ({
        name,
        children: rows.map((item) => ({ name: item.tile.id, item })),
      })),
    },
    (node) => node.children as { name: string; item?: WallItem }[] | undefined,
  ).count()

  treemap<{ name: string; item?: WallItem }>()
    .tile(treemapSquarify)
    .size([width, height])
    .paddingOuter(3)
    .paddingTop(18)
    .paddingInner(2)(root)

  const out: Placed[] = []
  for (const node of root.descendants()) {
    const box = node as unknown as { x0: number; y0: number; x1: number; y1: number }
    if (node.depth === 1) out.push({ ...box, group: node.data.name })
    if (node.depth === 2) out.push({ ...box, item: node.data.item })
  }
  return out
}

export function TileWall({ items, axis, steps, dark, onPick }: Props) {
  const [hovered, setHovered] = useState<WallItem | null>(null)
  // 벽은 고정 좌표계에 그리고 SVG 가 화면 폭에 맞춘다 — 폭을 재느라 화면이 한 번 더
  // 그려지지 않게.
  const width = 960
  const height = Math.max(240, Math.ceil(items.length / 6) * 110)
  const placed = useMemo(() => layout(items, width, height), [items, height])

  if (items.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        연계가 없습니다 — 「역량」 화면에서 먼저 등록합니다.
      </p>
    )
  }

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full"
        role="img"
        aria-label={`${axis.label} 수준을 색으로 보여 주는 벽. 연계 ${items.length}개.`}
      >
        {placed.map((box, index) =>
          box.group !== undefined ? (
            <g key={`g${index}`}>
              <rect
                x={box.x0}
                y={box.y0}
                width={Math.max(0, box.x1 - box.x0)}
                height={Math.max(0, box.y1 - box.y0)}
                fill="none"
                stroke="currentColor"
                strokeOpacity={0.15}
                rx={4}
              />
              <text
                x={box.x0 + 6}
                y={box.y0 + 13}
                className="fill-muted-foreground"
                fontSize={11}
              >
                {box.group}
              </text>
            </g>
          ) : null,
        )}
        {placed.map((box) =>
          box.item ? (
            <g key={box.item.tile.id}>
              <rect
                x={box.x0}
                y={box.y0}
                width={Math.max(0, box.x1 - box.x0)}
                height={Math.max(0, box.y1 - box.y0)}
                rx={3}
                fill={
                  box.item.index < 0
                    ? dark
                      ? LEVEL_NONE_DARK
                      : LEVEL_NONE_LIGHT
                    : levelColor(box.item.index, steps, dark)
                }
                stroke={box.item.index < 0 ? 'currentColor' : 'none'}
                strokeOpacity={0.35}
                strokeDasharray={box.item.index < 0 ? '3 2' : undefined}
                className="cursor-pointer"
                onMouseEnter={() => setHovered(box.item ?? null)}
                onMouseLeave={() => setHovered(null)}
                onClick={() => box.item && onPick(box.item.tile)}
              />
              {box.x1 - box.x0 > 90 && box.y1 - box.y0 > 26 && (
                <text
                  x={box.x0 + 6}
                  y={box.y0 + 16}
                  fontSize={10}
                  className="pointer-events-none fill-white mix-blend-luminosity"
                >
                  {box.item.tile.subject_label.slice(0, 12)}
                </text>
              )}
            </g>
          ) : null,
        )}
      </svg>

      {/* 호버 — 어느 연계이고 수준이 무엇인지. 색만으로는 서열의 이름을 알 수 없다. */}
      {hovered && (
        <div className="bg-popover pointer-events-none absolute top-2 right-2 max-w-72 rounded-md border p-2 text-xs shadow-md">
          <p className="font-medium">
            {hovered.tile.subject_label} · {hovered.tile.agent_label}
          </p>
          <p className="text-muted-foreground">
            {axis.label}: <b className="text-foreground">{hovered.label}</b>
          </p>
          {hovered.tile.agent_dept && (
            <p className="text-muted-foreground">담당 {hovered.tile.agent_dept}</p>
          )}
        </div>
      )}
    </div>
  )
}
