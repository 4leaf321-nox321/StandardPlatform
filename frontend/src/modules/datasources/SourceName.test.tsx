/**
 * 데이터 소스의 **출처 이름** — 잠긴 타입에 받으려면 이 이름이 맞아야 한다.
 *
 * 허브가 내려준 정의는 받는 쪽에서 고치는 길을 막는다(타입의 `managed_by`). 막기만 하면 **받기도
 * 막히므로**, 적재는 자기 출처 이름을 말하고 그것이 같을 때만 통과한다.
 *
 * 그런데 「누가 관리하는가」 는 타입 화면에 적혀 있다 — 소스 화면에서 말해 주지 않으면 운영자는
 * **동기화가 실패한 뒤에야** 무엇을 적어야 하는지 안다. 그래서 이 화면이 적을 이름을 말한다.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const datasourceApi = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
  runs: vi.fn(),
}))
const ontologyApi = vi.hoisted(() => ({ schema: vi.fn() }))
const workspaceApi = vi.hoisted(() => ({ list: vi.fn() }))

vi.mock('@/modules/datasources/api', async (original) => {
  const real = await original<typeof import('@/modules/datasources/api')>()
  return { ...real, datasourceApi }
})
vi.mock('@/modules/ontology/api', async (original) => {
  const real = await original<typeof import('@/modules/ontology/api')>()
  return { ...real, ontologyApi }
})
vi.mock('@/modules/workspaces/api', async (original) => {
  const real = await original<typeof import('@/modules/workspaces/api')>()
  return { ...real, workspaceApi }
})

import DataSourcesPage from '@/modules/datasources/DataSourcesPage'

/** 허브가 관리하는 타입 하나 — 받기만 하는 자리다. */
const VENDOR = {
  id: 't1',
  slug: 'vendor',
  label: '공급사',
  icon: '',
  description: '',
  kind_class: 'record',
  managed_by: 'hub',
  sort_order: 0,
  is_active: true,
  nav_group_slug: null,
  properties: [],
}

function source(extra: Record<string, unknown> = {}) {
  return {
    id: 's1',
    slug: 'plm_vendor',
    name: 'PLM 공급사',
    kind: 'sp_core',
    base_url: 'http://plm.local/api/core',
    entity_set: 'vendor',
    options: {},
    filter: '',
    select: '',
    auth_kind: 'none',
    auth_user: '',
    has_secret: false,
    page_size: 500,
    type_slug: 'vendor',
    workspace_slug: null,
    mapping: { external_key: 'VendorNo', columns: [{ source: 'Name', target: 'label' }] },
    source_name: '',
    deprecate_missing: false,
    since_mark: '',
    interval_minutes: 0,
    is_active: true,
    last_run_at: null,
    last_status: null,
    created_at: '2026-10-03T00:00:00Z',
    ...extra,
  }
}

async function open(row: Record<string, unknown>) {
  datasourceApi.list.mockResolvedValue([row])
  ontologyApi.schema.mockResolvedValue({ types: [VENDOR], relation_types: [], groups: [] })
  workspaceApi.list.mockResolvedValue([])
  render(<DataSourcesPage />)
  await userEvent.click(await screen.findByRole('button', { name: '수정' }))
  return screen.getByLabelText('출처 이름 (비우면 slug)') as HTMLInputElement
}

describe('데이터 소스의 출처 이름', () => {
  beforeEach(() => {
    for (const fn of Object.values(datasourceApi)) fn.mockReset()
    datasourceApi.runs.mockResolvedValue([])
  })

  it('잠긴 타입이면 적을 이름을 말한다 — 실패한 뒤에 알게 하지 않는다', async () => {
    await open(source())
    expect(await screen.findByText(/관리하는 타입입니다/)).toBeInTheDocument()
    // 비워 두면 slug 가 이름이 된다는 사실까지 말한다 — 그것이 지금 막히는 이유다.
    expect(
      await screen.findByText(/비우면 slug\(「plm_vendor」\)가 이름이 됩니다/),
    ).toBeInTheDocument()
  })

  it('맞게 적혀 있으면 맞다고 말한다', async () => {
    const input = await open(source({ source_name: 'hub' }))
    expect(input.value).toBe('hub')
    expect(await screen.findByText(/맞게 적혀 있습니다/)).toBeInTheDocument()
  })

  it('고친 이름이 저장에 실려 간다', async () => {
    datasourceApi.update.mockResolvedValue(source({ source_name: 'hub' }))
    const input = await open(source())
    await userEvent.type(input, 'hub')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    expect(datasourceApi.update).toHaveBeenCalled()
    expect(datasourceApi.update.mock.calls[0][1]).toMatchObject({ source_name: 'hub' })
  })
})
