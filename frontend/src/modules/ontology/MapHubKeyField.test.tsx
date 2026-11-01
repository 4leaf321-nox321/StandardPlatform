/**
 * 기록 타입의 「지도의 허브 칸」 — 자동이 무엇인지 말하고, 고르면 바로 저장하고, 다음 전체 배치부터라고
 * 적는다(ADR 0046).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { MapHubKey } from '@/modules/graph/api'

const graphApi = vi.hoisted(() => ({ hubKeys: vi.fn(), setHubKey: vi.fn() }))
vi.mock('@/modules/graph/api', () => ({ graphApi }))

const ROW: MapHubKey = {
  type_slug: 'svc',
  type_label: '시장 서비스',
  mode: 'auto',
  key: 'sku',
  auto_key: 'sku',
  used_key: 'sku',
  in_layout: true,
  stale: false,
  candidates: [
    {
      key: 'sku',
      label: '판매 모델',
      target_label: '판매 모델',
      target_types: ['sku'],
      required: true,
      hubs: 84,
    },
    {
      key: 'center',
      label: '센터',
      target_label: '서비스 센터',
      target_types: ['center'],
      required: false,
      hubs: 24,
    },
  ],
}

describe('지도의 허브 칸', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    graphApi.hubKeys.mockResolvedValue([ROW])
    graphApi.setHubKey.mockResolvedValue({ ...ROW, mode: 'admin', key: 'center' })
  })

  it('자동이 지금 무엇인지 말하고, 고르면 바로 저장한다', async () => {
    const { MapHubKeyField } = await import('@/modules/ontology/MapHubKeyField')
    render(<MapHubKeyField typeSlug="svc" canEdit />)
    const field = await screen.findByRole('combobox', { name: '지도의 허브 칸' })
    expect(field).toHaveTextContent('자동 (지금: 판매 모델)')
    expect(screen.getByText(/다음 「전체 다시 배치」 부터/)).toBeInTheDocument()
    await userEvent.click(field)
    await userEvent.click(await screen.findByRole('option', { name: /센터 → 서비스 센터/ }))
    await waitFor(() =>
      expect(graphApi.setHubKey).toHaveBeenCalledWith('svc', { mode: 'key', key: 'center' }),
    )
  })
})
