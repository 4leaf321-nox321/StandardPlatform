/**
 * 코어 현황 — **공개한 것을 놓치지 않기 위한 화면.**
 *
 * 여기서 지키는 것: 연동 주소를 그대로 표시하는가, 전체 읽기 토큰을 구분하는가, 미사용
 * 토큰과 사용 중인 토큰을 구별하는가, 그리고 연동 키트를 전달할 수 있는가.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const ontologyApi = vi.hoisted(() => ({ coreStatus: vi.fn(), downloadCoreKit: vi.fn() }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

const STATUS = {
  base: 'https://sp.example.com/api/core',
  types: [
    {
      slug: 'material',
      label: '재료',
      description: '',
      count: 1240,
      updated_at: '2026-09-22T08:00:00Z',
      endpoint: 'https://sp.example.com/api/core/material',
      properties: [{ key: 'density', label: '밀도', data_type: 'number', multi: false }],
    },
  ],
  consumers: [
    {
      name: 'MatNexus 야간',
      owner: '연동계정',
      last_used_at: '2026-09-23T01:00:00Z',
      expires_at: null,
      narrow: true,
      created_at: '2026-09-01T00:00:00Z',
    },
    {
      name: '정제 스크립트',
      owner: '홍길동',
      last_used_at: null,
      expires_at: '2026-12-31T00:00:00Z',
      narrow: false,
      created_at: '2026-09-02T00:00:00Z',
    },
  ],
  recent: [
    {
      at: '2026-09-23T01:00:00Z',
      actor: '연동계정',
      token: 'MatNexus 야간',
      type_slug: 'material',
      rows: 12,
      since: '2026-09-22T01:00:00Z',
    },
  ],
}

async function open(data = STATUS) {
  ontologyApi.coreStatus.mockResolvedValue(data)
  const { default: OntologyCorePage } = await import('@/modules/ontology/OntologyCorePage')
  render(
    <MemoryRouter>
      <OntologyCorePage />
    </MemoryRouter>,
  )
}

describe('코어 현황', () => {
  it('연동 주소와 공개 타입을 표시한다', async () => {
    await open()
    expect(await screen.findByText('https://sp.example.com/api/core')).toBeInTheDocument()
    expect(screen.getByText('재료')).toBeInTheDocument()
    expect(screen.getByText('1240')).toBeInTheDocument()
  })

  it('전체 읽기 토큰을 구분해 표시한다 — read 는 코어 외 자료도 조회한다', async () => {
    await open()
    await userEvent.click(await screen.findByRole('tab', { name: /액세스 토큰/ }))
    expect(await screen.findByText('코어 전용')).toBeInTheDocument()
    // 설명 문단에도 「전체 읽기」 가 나오므로 배지까지 여럿이다 — 존재 여부만 본다.
    expect(screen.getAllByText('전체 읽기').length).toBeGreaterThan(0)
    // 미사용 토큰은 아직 연결되지 않은 연동이다 — 사용 중인 토큰과 구분한다.
    expect(screen.getByText('미사용')).toBeInTheDocument()
  })

  it('연동 키트를 내려받는다 — 수신 측에 그대로 전달하는 묶음', async () => {
    ontologyApi.downloadCoreKit.mockResolvedValue(undefined)
    await open()
    await userEvent.click(await screen.findByRole('button', { name: /연동 키트 다운로드/ }))
    await waitFor(() => expect(ontologyApi.downloadCoreKit).toHaveBeenCalled())
  })

  it('공개한 것이 없으면 다음 절차를 안내한다', async () => {
    await open({ ...STATUS, types: [], consumers: [], recent: [] })
    expect(await screen.findByText('공개된 타입이 없습니다')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: /최근 조회 이력/ }))
    expect(await screen.findByText('조회 이력이 없습니다')).toBeInTheDocument()
  })

  it('세 구역이 탭으로 나뉜다 — 공개 타입이 늘어도 나머지가 밀려나지 않게', async () => {
    await open()
    const tabs = await screen.findAllByRole('tab')
    expect(tabs.map((one) => one.textContent)).toEqual([
      '공개 타입 1',
      '액세스 토큰 2',
      '최근 조회 이력 1',
    ])
  })
})
