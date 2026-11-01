/**
 * 무리 배치를 작업자 스레드에 맡긴다 — **시작하고, 진행률을 받고, 끝낼 수 있게.**
 *
 * 이 파일을 따로 둔 까닭: 시험(happy-dom)에는 작업자가 없다. 여기만 가짜로 바꾸면 화면 시험이
 * 「자리 잡는 중 40%」 를 그대로 본다. 작업자가 없는 곳에서는 같은 계산을 이 스레드에서 한다(느리지만
 * 그림은 선다).
 */

import { clusterLayout } from '@/modules/graph/wholeLayout'
import type { ClusterInput, Positions } from '@/modules/graph/wholeLayout'
import type { LayoutMessage } from '@/modules/graph/wholeLayout.worker'

export interface LayoutJob {
  promise: Promise<Positions>
  /** 씨앗이 바뀌면 — 작업자를 통째로 끝낸다. 끝난 뒤의 진행률 · 결과는 오지 않는다. */
  cancel: () => void
}

export function startCluster(
  input: ClusterInput,
  onProgress: (fraction: number) => void,
): LayoutJob {
  if (typeof Worker === 'undefined') {
    let cancelled = false
    const promise = Promise.resolve().then(() => {
      if (cancelled) throw new Error('cancelled')
      return clusterLayout(input, { onProgress })
    })
    return {
      promise,
      cancel: () => {
        cancelled = true
      },
    }
  }
  const worker = new Worker(new URL('./wholeLayout.worker.ts', import.meta.url), {
    type: 'module',
  })
  let settle: { resolve: (p: Positions) => void; reject: (e: Error) => void } | null = null
  const promise = new Promise<Positions>((resolve, reject) => {
    settle = { resolve, reject }
  })
  worker.onmessage = (event: MessageEvent<LayoutMessage>) => {
    const message = event.data
    if (message.kind === 'progress') onProgress(message.progress)
    else if (message.kind === 'done') {
      settle?.resolve(message.positions)
      worker.terminate()
    } else {
      settle?.reject(new Error(message.message))
      worker.terminate()
    }
  }
  worker.onerror = (event) => {
    settle?.reject(new Error(event.message || '배치 작업자가 멈췄습니다'))
    worker.terminate()
  }
  // 형식 배열은 복사해 보낸다 — 그리는 쪽도 같은 배열을 쓴다.
  worker.postMessage(input)
  return { promise, cancel: () => worker.terminate() }
}
