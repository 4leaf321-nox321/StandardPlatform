/**
 * 컨테이너 크기 — ForceGraph2D 는 **명시적 width/height** 가 필요하다.
 *
 * 처음 그릴 때 0 으로 재면 캔버스가 0x0 으로 만들어지고, 그 뒤로 아무것도 안 보인다.
 * 동기 측정 → 다음 프레임 재측정 → ResizeObserver 로 세 겹 보강한다.
 */

import { useLayoutEffect, useRef, useState } from 'react'

export function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const measure = () => {
      const rect = el.getBoundingClientRect()
      if (rect.width > 0 && rect.height > 0) {
        setSize((prev) =>
          prev.width === rect.width && prev.height === rect.height
            ? prev
            : { width: rect.width, height: rect.height },
        )
      }
    }
    measure()
    const raf = requestAnimationFrame(measure)
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => {
      cancelAnimationFrame(raf)
      observer.disconnect()
    }
  }, [])

  return [ref, size] as const
}
