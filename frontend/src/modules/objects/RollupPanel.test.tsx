/**
 * 「아래 전부」(롤업) — **무엇을 어떻게 셌나를 값 옆에 적는다**(ADR 0040).
 *
 * - 「수량 반영」 · 「말단만」 을 배지로 적고, 수량 반영에서 1개로 센 관계는 그 수를 적는다.
 * - 값을 못 냈으면(트리 순환 등) 까닭을 값 자리에 적는다.
 * - 롤업마다 센 대상의 수가 다르면(말단만) 머리말에 하나로 적지 않는다.
 */

import { act, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Rollup } from '@/modules/objects/api'

const objectApi = vi.hoisted(() => ({ rollup: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

function rollup(extra: Partial<Rollup> = {}): Rollup {
  return {
    property: 'weight',
    label: '무게 합계',
    fn: 'sum',
    value: 12.5,
    count: 4,
    missing: 0,
    descendants: 4,
    weighted: false,
    leaves_only: false,
    quantity_missing: 0,
    note: '',
    ...extra,
  }
}

async function mount() {
  const { RollupPanel } = await import('@/modules/objects/RollupPanel')
  // 그리자마자 센다 — 응답이 들어오는 갱신까지 한 번에 끝내고 읽는다.
  await act(async () => {
    render(<RollupPanel typeSlug="part" objectId="me" reloadKey={null} />)
  })
}

describe('롤업 패널', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('수량 반영 · 말단만을 배지로, 1개로 센 관계 수를 함께 적는다', async () => {
    objectApi.rollup.mockResolvedValue([
      rollup({
        label: '총 무게',
        weighted: true,
        leaves_only: true,
        quantity_missing: 3,
        descendants: 2,
        missing: 1,
      }),
    ])
    await mount()
    expect(await screen.findByText('수량 반영')).toBeInTheDocument()
    expect(screen.getByText('말단만')).toBeInTheDocument()
    expect(screen.getByText('수량 없음 3개(1개로 셈)')).toBeInTheDocument()
    expect(screen.getByText('말단 2개 중 1개는 값이 없습니다')).toBeInTheDocument()
    expect(screen.getByText(/트리 아래 말단 2개를 모은 값/)).toBeInTheDocument()
  })

  it('값을 못 냈으면 까닭을 값 자리에 적는다', async () => {
    objectApi.rollup.mockResolvedValue([
      rollup({ value: null, note: '트리가 순환합니다 — A → B → A', weighted: true }),
    ])
    await mount()
    expect(await screen.findByText('트리가 순환합니다 — A → B → A')).toBeInTheDocument()
    expect(screen.queryByText('—')).toBeNull()
  })

  it('롤업마다 센 대상 수가 다르면 머리말에 하나로 적지 않는다', async () => {
    objectApi.rollup.mockResolvedValue([
      rollup({ descendants: 10 }),
      rollup({ property: 'cost', label: '말단 원가', descendants: 6, leaves_only: true }),
    ])
    await mount()
    expect(await screen.findByText(/트리 아래를 모은 값/)).toBeInTheDocument()
    expect(screen.queryByText(/트리 아래 10개/)).toBeNull()
    expect(screen.getByText('10개 전부 값이 있습니다')).toBeInTheDocument()
    expect(screen.getByText('말단 6개 전부 값이 있습니다')).toBeInTheDocument()
    // 표시 안 한 설정은 배지도 없다.
    expect(screen.getAllByText('말단만')).toHaveLength(1)
    expect(screen.queryByText('수량 반영')).toBeNull()
  })
})
