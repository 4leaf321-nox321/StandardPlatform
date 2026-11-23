/**
 * 칸 대응에 칸을 더하면 — 저장하면 받은 시점을 비워 처음부터 다시 받는다고 **저장하기 전에**
 * 말한다(ADR 0089). 이어 받기는 바뀐 행만 와서, 이미 받은 행에는 새 칸의 값이 안 들어왔다.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const datasourceApi = vi.hoisted(() => ({
  list: vi.fn(),
  coreSubscriptions: vi.fn(async () => []),
  update: vi.fn(),
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

function prop(key: string, label: string) {
  return { key, label, data_type: 'text', multi: false, unit: '', enum_options: null }
}

const VENDOR = {
  id: 't1',
  slug: 'vendor',
  label: '공급사',
  icon: '',
  description: '',
  kind_class: 'record',
  managed_by: '',
  sort_order: 0,
  is_active: true,
  nav_group_slug: null,
  properties: [prop('country', '국가'), prop('rating', '점수')],
}

function source(sinceMark: string) {
  return {
    id: 's1',
    slug: 'sib_vendor',
    name: '형제 공급사',
    kind: 'sp_core',
    base_url: 'http://sibling.local/api',
    entity_set: 'vendor',
    options: {},
    filter: '',
    select: '',
    auth_kind: 'bearer',
    auth_user: '',
    has_secret: true,
    page_size: 500,
    type_slug: 'vendor',
    workspace_slug: null,
    mapping: {
      external_key: 'key',
      columns: [
        { source: 'key', target: 'key' },
        { source: 'label', target: 'label' },
        { source: 'country', target: 'properties.country' },
      ],
    },
    source_name: '',
    deprecate_missing: false,
    since_mark: sinceMark,
    interval_minutes: 0,
    is_active: true,
    last_run_at: null,
    last_status: null,
    created_at: '2026-10-03T00:00:00Z',
  }
}

async function openWith(sinceMark: string) {
  datasourceApi.list.mockResolvedValue([source(sinceMark)])
  ontologyApi.schema.mockResolvedValue({ types: [VENDOR], relation_types: [], groups: [] })
  workspaceApi.list.mockResolvedValue([])
  render(<DataSourcesPage />)
  await userEvent.click(await screen.findByRole('button', { name: '수정' }))
}

describe('칸 대응에 칸을 더할 때', () => {
  it('받은 시점이 있으면 저장 전에 처음부터 다시 받는다고 말한다', async () => {
    await openWith('2026-09-02T12:00:00.000000Z')
    expect(screen.queryByText(/저장하면 받은 시점을 비워/)).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /속성 추가/ }))
    const inputs = screen.getAllByPlaceholderText('바깥 열')
    await userEvent.type(inputs[inputs.length - 1], 'rating')
    expect(await screen.findByText(/저장하면 받은 시점을 비워 다음 동기화가/)).toBeInTheDocument()
  })

  it('아직 받은 적이 없으면 말하지 않는다', async () => {
    await openWith('')
    await userEvent.click(screen.getByRole('button', { name: /속성 추가/ }))
    const inputs = screen.getAllByPlaceholderText('바깥 열')
    await userEvent.type(inputs[inputs.length - 1], 'rating')
    expect(screen.queryByText(/저장하면 받은 시점을 비워/)).toBeNull()
  })
})
