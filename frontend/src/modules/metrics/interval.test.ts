/**
 * 지표의 주기 — 고르는 말이 곧 도는 방식이다(`metrics/services.due`).
 *
 * 예전에는 「주기(시간)」 숫자 칸 하나였고 타이머는 하루 한 번만 돌았다 — 「6시간」 이라 적어도
 * 하루 한 번 셌다. 이제 「밤마다(N일)」 · 「시간마다(N시간, 낮에도)」 · 「손으로만」 이다.
 */

import { describe, expect, it } from 'vitest'

import { intervalText, joinInterval, splitInterval } from '@/modules/metrics/interval'

describe('지표 주기', () => {
  it('저장된 시간을 뜻으로 읽는다', () => {
    expect(intervalText(0)).toBe('손으로만')
    expect(intervalText(6)).toBe('6시간마다')
    expect(intervalText(24)).toBe('매일 밤')
    expect(intervalText(72)).toBe('3일마다 밤')
    expect(splitInterval(48)).toEqual({ mode: 'nights', count: 2 })
    expect(splitInterval(0)).toEqual({ mode: 'manual', count: 1 })
  })

  it('고른 것을 저장할 시간으로 — 하루 이상은 날 단위, 시간마다는 23시간까지', () => {
    expect(joinInterval('nights', 1)).toBe(24)
    expect(joinInterval('nights', 7)).toBe(168)
    expect(joinInterval('hours', 6)).toBe(6)
    expect(joinInterval('hours', 30)).toBe(23)
    expect(joinInterval('hours', 0)).toBe(1)
    expect(joinInterval('manual', 5)).toBe(0)
  })
})
