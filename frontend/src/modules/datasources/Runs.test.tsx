/**
 * 「최근 동기화」 — **방금 돌린 것이 목록에 선다.**
 *
 * 펼쳐 둔 목록을 동기화 뒤에 다시 안 읽던 때는 방금 돌린 계획(또는 실패)이 안 보여, 기록이 안
 * 남은 줄 알았다(2026-10-08).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const datasourceApi = vi.hoisted(() => ({
  list: vi.fn(),
  runs: vi.fn(),
  sync: vi.fn(),
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

const SOURCE = {
  id: 's1',
  slug: 'plm_part',
  name: 'PLM 부품',
  kind: 'odata',
  base_url: 'http://plm.local/odata',
  entity_set: 'Parts',
  options: {},
  filter: '',
  select: '',
  auth_kind: 'none',
  auth_user: '',
  has_secret: false,
  page_size: 500,
  type_slug: 'part',
  workspace_slug: null,
  mapping: { external_key: 'PartNo', columns: [] },
  source_name: '',
  deprecate_missing: false,
  since_mark: '',
  interval_minutes: 0,
  is_active: true,
  last_run_at: null,
  last_status: null,
  created_at: '2026-10-03T00:00:00Z',
}

describe('최근 동기화', () => {
  it('동기화를 돌리면 펼쳐 둔 기록을 다시 읽는다', async () => {
    datasourceApi.list.mockResolvedValue([SOURCE])
    datasourceApi.runs.mockResolvedValue([])
    datasourceApi.sync.mockRejectedValue(new Error('바깥 서버가 응답하지 않습니다'))
    ontologyApi.schema.mockResolvedValue({ types: [], relation_types: [], groups: [] })
    workspaceApi.list.mockResolvedValue([])
    render(<DataSourcesPage />)

    await userEvent.click(await screen.findByRole('button', { name: 'PLM 부품' }))
    await waitFor(() => expect(datasourceApi.runs).toHaveBeenCalledTimes(1))

    await userEvent.click(screen.getByRole('button', { name: /동기화/ }))
    expect(await screen.findByText('바깥 서버가 응답하지 않습니다')).toBeInTheDocument()
    // 실패도 기록이다 — 서버가 남긴 그 줄을 보러 다시 읽는다.
    await waitFor(() => expect(datasourceApi.runs).toHaveBeenCalledTimes(2))
  })

  it('안내뿐인 계획은 적용할 수 있고, 아직 안 끝난 적용은 「실패」 가 아니라 「적용 중」 이다', async () => {
    // 기록의 말(errors)에는 오류가 아닌 안내도 실린다 — 그것만으로 적용을 막았다(2026-10-08).
    datasourceApi.list.mockResolvedValue([SOURCE])
    datasourceApi.runs.mockResolvedValue([
      {
        id: 'r0',
        status: 'failed',
        applied: false,
        actor_label: '관리자',
        rows_seen: 10,
        counts: {},
        errors: ['적용 중입니다 — …'],
        started_at: '2026-10-04T00:59:00Z',
        finished_at: null,
      },
    ])
    datasourceApi.sync.mockResolvedValue({
      run: {
        id: 'r1',
        status: 'planned',
        applied: false,
        actor_label: '관리자',
        rows_seen: 1,
        counts: { create: 1 },
        errors: ['선 1줄은 끝점이 아직 없어 다음 동기화에서 다시 넣습니다'],
        started_at: '2026-10-04T01:00:00Z',
        finished_at: '2026-10-04T01:00:01Z',
      },
      applied: false,
      counts: { create: 1 },
      rows: [{ row: 1, action: 'create', label: 'P-1', message: '' }],
      errors: ['선 1줄은 끝점이 아직 없어 다음 동기화에서 다시 넣습니다'],
      truncated: false,
    })
    ontologyApi.schema.mockResolvedValue({ types: [], relation_types: [], groups: [] })
    workspaceApi.list.mockResolvedValue([])
    render(<DataSourcesPage />)

    await userEvent.click(await screen.findByRole('button', { name: 'PLM 부품' }))
    expect(await screen.findByText('적용 중')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /동기화/ }))
    expect(await screen.findByRole('button', { name: /적용/ })).toBeEnabled()
  })
})
