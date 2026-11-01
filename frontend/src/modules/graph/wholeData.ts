/**
 * 정적 그리기의 자료 — 서버가 준 열 배열(`Whole`)을 **한 번** 형식 배열로 바꾼다(ADR 0045).
 *
 * 노드 20만 · 선 70만을 그리는 동안 배치 · 그리기 · 검색 · 선택이 같은 배열을 여러 번 읽는다. JSON
 * 의 숫자 배열을 매번 훑으면 그때마다 상자에 든 수를 꺼내고, 작업자 스레드로 보낼 때도 그만큼을
 * 복사한다 — 그래서 여기서 한 번 `Int32Array` 로 옮기고 그 뒤로는 이것만 쓴다.
 *
 * 이 파일은 아무것도 import 하지 않는다 — 탐색 화면(지식 그래프의 덩어리)이 고른 노드를 찾을 때
 * 쓰므로, 무거운 것(sigma · ForceAtlas2)은 정적 그리기 덩어리(`WholeView`)에만 든다.
 */

import type { Whole } from '@/modules/graph/api'

export interface PreparedWhole {
  /** 노드 수 · 선 수. */
  n: number
  m: number
  ids: string[]
  labels: string[]
  /** 노드의 타입 — `Whole.types` 표의 번호. */
  type: Int32Array
  /** 씨앗에서 몇 단계(씨앗 = 0). */
  depth: Int32Array
  /** 데려온 노드의 번호(씨앗은 -1). */
  parent: Int32Array
  src: Int32Array
  dst: Int32Array
  /** 선의 종류 — `Whole.relations` 표의 번호. */
  rel: Int32Array
  /** 이 그림 안의 연결 수(선의 양 끝을 센다) — 노드 크기와 「연결 많은 것부터」 의 근거. */
  degree: Uint32Array
  /** id → 번호. 상세 판의 관계 목록에서 고른 것을 그림에서 찾는다. */
  index: Map<string, number>
  /** 서버가 정한 자리(x0, y0, x1, y1, …) — 다음 단계(ADR 0045 「다음」)에서만 온다. */
  server: Float32Array | null
}

/** 그림의 연결 수 — 선의 양 끝을 하나씩 센다(자기 자신을 가리키는 선은 한 번). */
export function degreesOf(n: number, src: ArrayLike<number>, dst: ArrayLike<number>): Uint32Array {
  const out = new Uint32Array(n)
  for (let k = 0; k < src.length; k += 1) {
    out[src[k]] += 1
    if (dst[k] !== src[k]) out[dst[k]] += 1
  }
  return out
}

export function prepareWhole(whole: Whole): PreparedWhole {
  const { nodes, edges } = whole
  const n = nodes.ids.length
  const src = Int32Array.from(edges.src)
  const dst = Int32Array.from(edges.dst)
  const index = new Map<string, number>()
  nodes.ids.forEach((id, at) => index.set(id, at))
  let server: Float32Array | null = null
  if (nodes.x && nodes.y && nodes.x.length === n && nodes.y.length === n) {
    server = new Float32Array(n * 2)
    for (let i = 0; i < n; i += 1) {
      server[i * 2] = nodes.x[i]
      server[i * 2 + 1] = nodes.y[i]
    }
  }
  return {
    n,
    m: src.length,
    ids: nodes.ids,
    labels: nodes.label,
    type: Int32Array.from(nodes.type),
    depth: Int32Array.from(nodes.depth),
    parent: Int32Array.from(nodes.parent),
    src,
    dst,
    rel: Int32Array.from(edges.rel),
    degree: degreesOf(n, src, dst),
    index,
    server,
  }
}
