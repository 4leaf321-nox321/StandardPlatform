/**
 * 묶음 구독(ADR 0055) — 미리 보기를 먼저 보고, 만들 것이 있을 때만 「생성」 을 누른다. 입력을 바꾸면
 * 미리 본 계획은 낡아 다시 봐야 한다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SubscribeResult } from '@/modules/datasources/api'
import type { NavGroupRow } from '@/modules/ontology/api'

const datasourceApi = vi.hoisted(() => ({ subscribe: vi.fn() }))
vi.mock('@/modules/datasources/api', () => ({ datasourceApi }))

const GROUPS = [
  { id: 'g1', slug: 'plm', label: 'PLM 기준정보', icon: '', audience: '' },
] as NavGroupRow[]

function result(applied: boolean): SubscribeResult {
  return {
    system: 'hub',
    applied,
    created: applied ? 1 : 0,
    items: [
      {
        type_slug: 'part',
        type_label: '부품',
        action: 'create',
        reason: '',
        source_slug: applied ? 'part_core' : '',
        relations: true,
        source_name: 'hub',
        count: 800000,
        notes: [],
      },
      {
        type_slug: 'memo',
        type_label: '메모',
        action: 'skip',
        reason: '상대가 코어로 공개하지 않은 타입입니다',
        source_slug: '',
        relations: false,
        source_name: '',
        count: 0,
        notes: [],
      },
    ],
  }
}

describe('묶음 구독', () => {
  beforeEach(() => vi.clearAllMocks())

  it('미리 보기로 만들 것과 건너뛸 것을 까닭과 함께 보이고, 그 뒤에 생성한다', async () => {
    const { SubscribeDialog } = await import('@/modules/datasources/SubscribeDialog')
    const onCreated = vi.fn()
    datasourceApi.subscribe.mockResolvedValueOnce(result(false)).mockResolvedValueOnce(result(true))
    render(<SubscribeDialog groups={GROUPS} onClose={vi.fn()} onCreated={onCreated} />)

    const create = screen.getByRole('button', { name: '생성' })
    expect(create).toBeDisabled()
    await userEvent.type(screen.getByLabelText('형제 설치의 API 주소'), 'https://hub.example/api')
    await userEvent.type(screen.getByLabelText('액세스 토큰(core:read)'), 'secret')
    await userEvent.click(screen.getByRole('checkbox', { name: /PLM 기준정보/ }))
    expect(create).toBeDisabled() // 미리 보기 전
    await userEvent.click(screen.getByRole('button', { name: '미리 보기' }))

    expect(await screen.findByText('800,000')).toBeInTheDocument()
    expect(screen.getByText('상대가 코어로 공개하지 않은 타입입니다')).toBeInTheDocument()
    expect(datasourceApi.subscribe).toHaveBeenLastCalledWith(
      expect.objectContaining({ groups: ['plm'], apply: false, auth_kind: 'bearer' }),
    )
    await userEvent.click(screen.getByRole('button', { name: '생성' }))
    await waitFor(() => expect(onCreated).toHaveBeenCalled())
    expect(datasourceApi.subscribe).toHaveBeenLastCalledWith(
      expect.objectContaining({ apply: true }),
    )
    expect(screen.getByText(/소스 1개를 생성했습니다/)).toBeInTheDocument()
  })

  it('입력을 바꾸면 미리 본 계획을 버린다 — 낡은 계획으로 생성하지 않게', async () => {
    const { SubscribeDialog } = await import('@/modules/datasources/SubscribeDialog')
    datasourceApi.subscribe.mockResolvedValue(result(false))
    render(<SubscribeDialog groups={GROUPS} onClose={vi.fn()} onCreated={vi.fn()} />)
    await userEvent.type(screen.getByLabelText('형제 설치의 API 주소'), 'https://hub.example/api')
    await userEvent.type(screen.getByLabelText('액세스 토큰(core:read)'), 'secret')
    await userEvent.click(screen.getByRole('checkbox', { name: /PLM 기준정보/ }))
    await userEvent.click(screen.getByRole('button', { name: '미리 보기' }))
    expect(await screen.findByText('800,000')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('동기화 간격(분)'), '6')
    expect(screen.queryByText('800,000')).toBeNull()
    expect(screen.getByRole('button', { name: '생성' })).toBeDisabled()
  })
})
