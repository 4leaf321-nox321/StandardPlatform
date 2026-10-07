/**
 * **바깥을 정본으로**(거울) — 바깥에서 비운 칸은 이쪽도 비우고, 뺀 별칭은 뺀다.
 *
 * 동기화가 더하고 바꾸기만 했다. 기본은 그대로 「빈 칸은 안 건드림」 이다(바깥 표에는 우리가
 * 안 채운 칸이 흔하다) — 바깥이 정본인 표만 켠다. 형제 코어(`sp_core`)는 늘 거울이고, 화면이
 * 그것을 말한다.
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
}

describe('바깥을 정본으로', () => {
  beforeEach(() => {
    for (const fn of Object.values(datasourceApi)) fn.mockReset()
    datasourceApi.runs.mockResolvedValue([])
  })

  it('켜면 저장에 실려 간다 — 기본은 꺼짐', async () => {
    const odata = source({ kind: 'odata', base_url: 'http://plm.local/odata' })
    datasourceApi.update.mockResolvedValue(odata)
    await open(odata)
    const box = screen.getByRole('checkbox', { name: /바깥을 정본으로/ })
    expect(box).not.toBeChecked()
    await userEvent.click(box)
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    expect(datasourceApi.update).toHaveBeenCalled()
    const [, payload] = datasourceApi.update.mock.calls[0]
    expect(payload.options.mirror).toBe(true)
  })

  it('형제 코어는 늘 정본이라 고르지 않고, 목록이 그렇다고 말한다', async () => {
    await open(source())
    expect(screen.queryByRole('checkbox', { name: /바깥을 정본으로/ })).not.toBeInTheDocument()
    expect(screen.getByText(/상대를/)).toHaveTextContent('비운 칸 · 뺀 별칭')
    expect(screen.getAllByText(/바깥이 정본/).length).toBeGreaterThan(0)
  })
})
