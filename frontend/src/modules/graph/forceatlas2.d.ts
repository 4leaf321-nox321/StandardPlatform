/**
 * ForceAtlas2 의 반복 한 번(`graphology-layout-forceatlas2/iterate.js`) — 패키지가 타입을 안 준다.
 *
 * 패키지의 앞문(`forceAtlas2.assign`)은 부를 때마다 그래프를 행렬로 다시 만들고 반복 사이의 상태
 * (속도 · 수렴)를 버린다. 진행률을 보내려고 나눠 부르면 그때마다 처음부터 다시 식는다 — 그래서 행렬을
 * 한 번 만들어(`wholeLayout.clusterLayout`) 반복만 여기로 부른다.
 */
declare module 'graphology-layout-forceatlas2/iterate.js' {
  import type { ForceAtlas2Settings } from 'graphology-layout-forceatlas2'

  export default function iterate(
    settings: Required<ForceAtlas2Settings>,
    nodes: Float32Array,
    edges: Float32Array,
  ): void
}
