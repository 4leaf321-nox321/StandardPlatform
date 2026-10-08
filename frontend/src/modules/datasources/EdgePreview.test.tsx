/**
 * 선 소스의 「미리 보기」 — **저장된 선 대응을 지우지 않는다.** 미리 보기는 칸 대응을 맞추기
 * 전에 누르는 단추라 덜 된 객체 대응은 빼고 저장한다. 그 판정(바깥 식별자 · 이름 열)에 선
 * 소스는 늘 「덜 됨」 이라 빈 대응을 저장해 왔다 — 미리 보기만 눌러도 선 대응이 사라졌다
 * (2026-10-08).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const datasourceApi = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
  runs: vi.fn(),
  preview: vi.fn(),
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

const PART = {
  id: 't1',
  slug: 'part',
  label: '부품',
  icon: '',
  description: '',
  kind_class: 'record',
  managed_by: '',
  sort_order: 0,
  is_active: true,
  nav_group_slug: null,
  properties: [],
}

const EDGES = {
  relation: 'made_of',
  src: { column: 'Parent' },
  dst: { column: 'Child' },
  mode: 'add',
}

describe('선 소스의 미리 보기', () => {
  it('저장된 선 대응을 그대로 보낸다', async () => {
    datasourceApi.list.mockResolvedValue([
      {
        id: 's1',
        slug: 'bom',
        name: 'BOM',
        kind: 'odata',
        base_url: 'http://plm.local/odata',
        entity_set: 'Bom',
        options: {},
        filter: '',
        select: '',
        auth_kind: 'none',
        auth_user: '',
        has_secret: false,
        page_size: 500,
        type_slug: 'part',
        workspace_slug: null,
        mapping: { relations: EDGES },
        source_name: '',
        deprecate_missing: false,
        since_mark: '',
        interval_minutes: 0,
        is_active: true,
        last_run_at: null,
        last_status: null,
        created_at: '2026-10-03T00:00:00Z',
      },
    ])
    ontologyApi.schema.mockResolvedValue({
      types: [PART],
      relation_types: [
        {
          slug: 'made_of',
          label: '구성',
          src_type_slugs: ['part'],
          dst_type_slugs: ['part'],
        },
      ],
      groups: [],
    })
    workspaceApi.list.mockResolvedValue([])
    datasourceApi.update.mockResolvedValue({})
    datasourceApi.preview.mockResolvedValue({
      columns: [],
      rows: [],
      mapped: [],
      mapping_error: null,
    })
    render(<DataSourcesPage />)
    await userEvent.click(await screen.findByRole('button', { name: '수정' }))
    await userEvent.click(
      await screen.findByRole('button', { name: /미리 보기 \(저장하고 앞 5행 읽기\)/ }),
    )
    await waitFor(() => expect(datasourceApi.update).toHaveBeenCalled())
    const [, body] = datasourceApi.update.mock.calls[0]
    expect(body.mapping).toMatchObject({ relations: EDGES })
  })
})
