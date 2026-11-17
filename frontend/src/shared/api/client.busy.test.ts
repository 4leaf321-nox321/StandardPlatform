/**
 * 몰림의 429 · 503(ADR 0080) — **읽기는 `Retry-After` 만큼 기다려 다시 부르고, 쓰기는 다시 보내지
 * 않는다.** 화면은 목록 · 통계 · 위젯을 한꺼번에 부르므로, 몰릴 때 그 몇 개가 오류로 비지 않게.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api } from '@/shared/api/client'

function answer(status: number, body: unknown, retryAfter?: string): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json',
      ...(retryAfter ? { 'Retry-After': retryAfter } : {}),
    },
  })
}

const BUSY = { error: { code: 'APP-COMMON-0429', message: '자리가 없습니다' } }

describe('몰림의 다시 부르기', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('읽기는 Retry-After 만큼 기다려 다시 부른다', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(answer(429, BUSY, '3'))
      .mockResolvedValueOnce(answer(503, BUSY, '5'))
      .mockResolvedValueOnce(answer(200, { total: 7 }))
    vi.stubGlobal('fetch', fetch)
    const got = api.get<{ total: number }>('/objects/svc')
    await vi.advanceTimersByTimeAsync(2_900)
    expect(fetch).toHaveBeenCalledTimes(1) // 아직 기다리는 중
    await vi.advanceTimersByTimeAsync(10_000)
    await expect(got).resolves.toEqual({ total: 7 })
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it('두 번 다시 불러도 안 되면 서버의 말을 그대로 오류로', async () => {
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(answer(429, BUSY, '1')))
    vi.stubGlobal('fetch', fetch)
    const got = api.get('/objects/svc').catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(10_000)
    const error = await got
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(429)
    expect((error as ApiError).message).toBe('자리가 없습니다')
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it('쓰기 · Retry-After 없음 · 너무 긴 기다림은 다시 부르지 않는다', async () => {
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(answer(429, BUSY, '2')))
    vi.stubGlobal('fetch', fetch)
    await expect(api.post('/objects/svc', { label: 'x' })).rejects.toBeInstanceOf(ApiError)
    expect(fetch).toHaveBeenCalledTimes(1)

    fetch.mockImplementation(() => Promise.resolve(answer(503, BUSY)))
    await expect(api.get('/objects/svc')).rejects.toBeInstanceOf(ApiError)
    expect(fetch).toHaveBeenCalledTimes(2)

    fetch.mockImplementation(() => Promise.resolve(answer(429, BUSY, '60')))
    await expect(api.get('/objects/svc')).rejects.toBeInstanceOf(ApiError)
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it('끊으면 기다리던 것도 멈춘다', async () => {
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(answer(429, BUSY, '5')))
    vi.stubGlobal('fetch', fetch)
    const controller = new AbortController()
    const got = api
      .get('/objects/svc', { signal: controller.signal })
      .catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(100)
    controller.abort()
    const error = await got
    expect(error).toBeInstanceOf(DOMException)
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
