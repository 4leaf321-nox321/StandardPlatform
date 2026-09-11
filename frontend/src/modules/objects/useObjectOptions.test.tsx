/**
 * 객체 후보는 **서버가 찾는다** — 친 글자가 `q` 로 나가고, 골라 둔 값은 후보 밖이어도
 * 이름을 잃지 않는다.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const objectApi = vi.hoisted(() => ({ list: vi.fn(), profile: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

import { useObjectOptions } from '@/modules/objects/useObjectOptions'

const page = (
  items: { id: string; label: string; key?: string | null }[],
  total = items.length,
) => ({
  items: items.map((one) => ({ ...one, key: one.key ?? null, status: 'active', properties: {} })),
  total,
  limit: 50,
  offset: 0,
})

describe('useObjectOptions', () => {
  beforeEach(() => vi.clearAllMocks())

  it('친 글자를 서버로 보내고, 전체 수를 말한다', async () => {
    objectApi.list.mockResolvedValue(page([{ id: 'a', label: 'ACME' }], 300))
    const { result } = renderHook(() => useObjectOptions([{ slug: 'vendor' }]))
    await waitFor(() => expect(result.current.options).toHaveLength(1))
    expect(objectApi.list).toHaveBeenCalledWith('vendor', { q: undefined, limit: 50 })
    expect(result.current.total).toBe(300)

    objectApi.list.mockResolvedValue(page([{ id: 'b', label: 'Bolt Co' }], 1))
    act(() => result.current.setQuery('bolt'))
    await waitFor(() =>
      expect(objectApi.list).toHaveBeenLastCalledWith('vendor', { q: 'bolt', limit: 50 }),
    )
    await waitFor(() => expect(result.current.options.map((one) => one.label)).toEqual(['Bolt Co']))
  })

  it('골라 둔 값이 후보 밖이면 상세에서 이름을 읽어 꽂는다', async () => {
    objectApi.list.mockResolvedValue(page([{ id: 'a', label: 'ACME' }], 500))
    objectApi.profile.mockResolvedValue({
      object: { id: 'z', label: 'Zeta', key: 'V-9' },
      type_label: '공급사',
    })
    const { result } = renderHook(() => useObjectOptions([{ slug: 'vendor' }], { value: 'z' }))
    await waitFor(() => expect(result.current.pinned?.label).toBe('Zeta'))
    expect(objectApi.profile).toHaveBeenCalledWith('vendor', 'z')
  })

  it('여러 타입이면 값에 타입을 붙이고 자기 자신은 뺀다', async () => {
    objectApi.list.mockImplementation((slug: string) =>
      Promise.resolve(
        slug === 'part'
          ? page([
              { id: 'me', label: '나' },
              { id: 'p1', label: '볼트', key: 'P-1' },
            ])
          : page([{ id: 'v1', label: 'ACME' }]),
      ),
    )
    const { result } = renderHook(() =>
      useObjectOptions(
        [
          { slug: 'part', label: '부품' },
          { slug: 'vendor', label: '공급사' },
        ],
        {
          exclude: 'me',
          composite: true,
        },
      ),
    )
    await waitFor(() => expect(result.current.options).toHaveLength(2))
    expect(result.current.options.map((one) => one.value)).toEqual(['part:p1', 'vendor:v1'])
    expect(result.current.options[0].hint).toBe('부품 · P-1')
  })
})
