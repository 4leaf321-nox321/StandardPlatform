/**
 * 무리 배치의 작업자 스레드 — ForceAtlas2 를 화면 밖에서 돌린다(ADR 0045).
 *
 * 노드 수만 개의 배치를 화면 스레드에서 돌리면 그동안 단추도 스크롤도 안 먹는다(탐색 그림의 force
 * 배치가 1만 개를 넘으면 자리 잡는 데 수십 초 동안 그랬다). 진행률을 보내고, 끝나면 자리를 넘긴다
 * (복사 없이 — `transfer`). 씨앗이 바뀌면 부르는 쪽이 작업자를 통째로 끝낸다(`terminate`).
 */

import { clusterLayout } from '@/modules/graph/wholeLayout'
import type { ClusterInput } from '@/modules/graph/wholeLayout'

export type LayoutMessage =
  | { kind: 'progress'; progress: number }
  | { kind: 'done'; positions: Float32Array }
  | { kind: 'error'; message: string }

self.onmessage = (event: MessageEvent<ClusterInput>) => {
  try {
    const positions = clusterLayout(event.data, {
      onProgress: (progress) => self.postMessage({ kind: 'progress', progress } as LayoutMessage),
    })
    self.postMessage({ kind: 'done', positions } as LayoutMessage, { transfer: [positions.buffer] })
  } catch (caught) {
    const message = caught instanceof Error ? caught.message : String(caught)
    self.postMessage({ kind: 'error', message } as LayoutMessage)
  }
}
