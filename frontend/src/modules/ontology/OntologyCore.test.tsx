/**
 * 코어 현황 — **열어 둔 것을 잊지 않게.**
 *
 * 여기서 지키는 것: 주소를 그대로 보여 주나, 넓은 자격을 눈에 띄게 가르나, 한 번도 안 쓴
 * 자격과 받아 간 자격을 구별하나.
 */

import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const ontologyApi = vi.hoisted(() => ({ coreStatus: vi.fn() }))
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
  it('건넬 주소와 열린 타입을 보여 준다', async () => {
    await open()
    expect(await screen.findByText('https://sp.example.com/api/core')).toBeInTheDocument()
    expect(screen.getByText('재료')).toBeInTheDocument()
    expect(screen.getByText('1240')).toBeInTheDocument()
  })

  it('넓은 자격을 눈에 띄게 가른다 — read 는 코어 밖도 읽는다', async () => {
    await open()
    expect(await screen.findByText('코어만')).toBeInTheDocument()
    // 설명 문단에도 「넓음」 이 나오므로 표의 배지까지 여럿이다 — 있기만 하면 된다.
    expect(screen.getAllByText('넓음').length).toBeGreaterThan(0)
    // 한 번도 안 쓴 자격은 아직 안 붙은 연동이다 — 어제 받아 간 것과 무게가 다르다.
    expect(screen.getByText('아직 안 씀')).toBeInTheDocument()
  })

  it('아무것도 안 열었으면 무엇을 해야 하는지 적는다', async () => {
    await open({ ...STATUS, types: [], consumers: [], recent: [] })
    expect(await screen.findByText('아직 아무것도 안 열었습니다')).toBeInTheDocument()
    expect(screen.getByText('아직 아무도 안 받아 갔습니다')).toBeInTheDocument()
  })
})
