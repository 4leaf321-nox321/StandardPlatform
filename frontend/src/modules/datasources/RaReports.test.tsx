/**
 * RA 보고서 소스(ADR 0018) — **사람이 고르는 것은 「RA 의 어느 조직인가」 하나다.**
 *
 * 칸 대응을 적지 않는다(보고서 기록 타입의 칸 키가 약속). 조직 목록은 **저장된** 주소 · 토큰으로
 * RA 에 묻는다 — 그래서 새 소스는 「저장하고 불러오기」 를 먼저 누르고, 그다음 저장은 고치기여야
 * 한다(또 만들면 「이미 있다」). 원본에서 내려간 보고서는 지우지 않고 표시한다.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const datasourceApi = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
  runs: vi.fn(),
  sync: vi.fn(),
  raBoards: vi.fn(),
  raReportType: vi.fn(),
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

function kindOf(slug: string, label: string, usage: 'axis' | 'log') {
  return {
    id: slug,
    slug,
    label,
    icon: '',
    description: '',
    kind_class: 'record',
    usage,
    sort_order: 0,
    is_active: true,
    nav_group_slug: null,
    properties: [],
  }
}

const MODEL = kindOf('plm_model', '개발모델', 'axis')
const REPORT = kindOf('ra_report', '보고서', 'log')

const BOARDS = [
  { slug: 'hq', name: '본사', parent_slug: null, depth: 0, path: '본사' },
  { slug: 'cae', name: 'CAE그룹', parent_slug: 'hq', depth: 1, path: '본사 / CAE그룹' },
  {
    slug: 'cae-2',
    name: '해석2팀',
    parent_slug: 'cae',
    depth: 2,
    path: '본사 / CAE그룹 / 해석2팀',
  },
]

function raSource(extra: Record<string, unknown> = {}) {
  return {
    id: 's1',
    slug: 'ra_cae',
    name: 'CAE 보고서',
    kind: 'ra_reports',
    base_url: 'https://ra.local',
    entity_set: '/api/feeds/published-reports',
    options: { board: 'cae' },
    filter: '',
    select: '',
    auth_kind: 'bearer',
    auth_user: '',
    has_secret: true,
    page_size: 500,
    type_slug: 'ra_report',
    workspace_slug: null,
    mapping: {},
    source_name: '',
    deprecate_missing: false,
    since_mark: '',
    reconciled_at: null,
    interval_minutes: 60,
    is_active: true,
    last_run_at: null,
    last_status: null,
    created_at: '2026-10-04T00:00:00Z',
    ...extra,
  }
}

function setup(rows: Record<string, unknown>[]) {
  datasourceApi.list.mockResolvedValue(rows)
  ontologyApi.schema.mockResolvedValue({
    types: [MODEL, REPORT],
    relation_types: [],
    groups: [],
  })
  workspaceApi.list.mockResolvedValue([])
  render(<DataSourcesPage />)
}

describe('RA 보고서 소스', () => {
  beforeEach(() => {
    for (const fn of Object.values(datasourceApi)) fn.mockReset()
    for (const fn of Object.values(ontologyApi)) fn.mockReset()
    datasourceApi.runs.mockResolvedValue([])
  })

  it('새로 만들 때 — 타입을 짓고, 저장하고 조직을 불러와 고른 뒤 저장은 고치기다', async () => {
    datasourceApi.raReportType.mockResolvedValue({
      type_slug: 'ra_report',
      created: true,
      changes: ['type ra_report: create'],
    })
    datasourceApi.create.mockResolvedValue(raSource({ options: {} }))
    datasourceApi.update.mockResolvedValue(raSource())
    datasourceApi.raBoards.mockResolvedValue(BOARDS)
    setup([])
    await userEvent.click(await screen.findByRole('button', { name: /생성/ }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText(/^slug/), 'ra_cae')
    await userEvent.type(within(dialog).getByLabelText('이름'), 'CAE 보고서')
    await userEvent.click(within(dialog).getAllByRole('combobox')[0])
    await userEvent.click(
      await screen.findByRole('option', { name: 'ReportArchive 보고서 (조직 하나)' }),
    )

    // 칸 대응 · 인증 방식 · 경로는 없다 — 사람이 채워야 하는 줄 알면 안 된다.
    expect(screen.queryByText('① 같은 것 검색')).not.toBeVisible()
    expect(screen.queryByRole('textbox', { name: /엔티티 셋|경로|파일 위치/ })).toBeNull()
    await userEvent.type(screen.getByLabelText('RA 주소'), 'https://ra.local')
    await userEvent.type(screen.getByLabelText(/RA 개인 토큰/), 'ra_pat_x')

    // 타입 — 축만 고를 거리로 서고(기록 타입은 아니다), 만든 것이 넣을 타입이 된다.
    expect(screen.getByRole('checkbox', { name: '개발모델' })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: '보고서' })).toBeNull()
    await userEvent.click(screen.getByRole('checkbox', { name: '개발모델' }))
    await userEvent.click(screen.getByRole('button', { name: '만들기' }))
    expect(datasourceApi.raReportType).toHaveBeenCalledWith({
      slug: 'ra_report',
      label: '보고서',
      axes: ['plm_model'],
    })
    expect(await screen.findByText(/새로 만들었습니다/)).toBeInTheDocument()
    expect(ontologyApi.schema).toHaveBeenCalledTimes(2) // 고르개가 새 타입을 받는다

    await userEvent.click(screen.getByRole('button', { name: '저장하고 RA 조직 불러오기' }))
    expect(datasourceApi.create).toHaveBeenCalledTimes(1)
    expect(datasourceApi.create.mock.calls[0][0]).toMatchObject({
      kind: 'ra_reports',
      entity_set: '/api/feeds/published-reports',
      auth_kind: 'bearer',
      auth_secret: 'ra_pat_x',
      mapping: {},
      deprecate_missing: false,
      type_slug: 'ra_report',
    })
    expect(datasourceApi.raBoards).toHaveBeenCalledWith('ra_cae')

    await userEvent.click(await screen.findByRole('combobox', { name: 'RA 조직' }))
    // 단추 이름은 「이름 + 경로」 — 하위 조직의 경로에도 CAE그룹 이 있다. 위에서 아래로 그리므로 첫째.
    await userEvent.click((await screen.findAllByRole('button', { name: /CAE그룹/ }))[0])
    // 고른 뒤에는 경로로 선다 — 이름이 같은 조직이 여럿이어도 어느 것인지 안다.
    expect(screen.getByRole('combobox', { name: 'RA 조직' })).toHaveTextContent('본사 / CAE그룹')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    // 두 번째는 고치기 — 이미 만든 것을 또 만들지 않는다.
    expect(datasourceApi.create).toHaveBeenCalledTimes(1)
    expect(datasourceApi.update).toHaveBeenCalledWith(
      'ra_cae',
      expect.objectContaining({
        options: { board: 'cae' },
        mapping: {},
      }),
    )
  })

  it('다른 종류에서 온 설정은 떨군다 — 서버가 모르는 설정을 거절한다', async () => {
    datasourceApi.update.mockResolvedValue(raSource())
    setup([raSource({ options: { board: 'cae', rows_path: 'items', paging: 'page' } })])
    await userEvent.click(await screen.findByRole('button', { name: '수정' }))
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    expect(datasourceApi.update.mock.calls[0][1].options).toEqual({ board: 'cae' })
  })

  it('목록은 조직과 전량 대조를, 실행 기록은 「원본에서 내려감」 을 말한다', async () => {
    datasourceApi.runs.mockResolvedValue([
      {
        id: 'r1',
        status: 'ok',
        applied: true,
        actor_label: '시스템',
        rows_seen: 40,
        counts: { create: 3, unchanged: 37, gone: 2, back: 1, full_read: 1, tags_as_text: 0 },
        errors: [],
        started_at: '2026-10-04T01:00:00Z',
        finished_at: '2026-10-04T01:00:05Z',
      },
    ])
    setup([raSource({ reconciled_at: '2026-10-04T01:00:05Z' })])
    expect(await screen.findByText(/조직 cae 과 하위/)).toBeInTheDocument()
    expect(screen.getByText(/마지막 전량 대조/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'CAE 보고서' }))
    expect(
      await screen.findByText(/새로 3 · 그대로 37 · 원본에서 내려감 2 · 다시 게시 1 · 전량 대조/),
    ).toBeInTheDocument()
  })

  it('깨진 보고서가 섞여도 서버가 넣을 수 있다고 하면 적용할 수 있다', async () => {
    datasourceApi.sync.mockResolvedValue({
      run: {
        id: 'r1',
        status: 'planned',
        applied: false,
        actor_label: '관리자',
        rows_seen: 3,
        counts: { create: 2, error: 1 },
        errors: ['1행 RA-?: 제목이 비었습니다'],
        started_at: '2026-10-04T01:00:00Z',
        finished_at: '2026-10-04T01:00:01Z',
      },
      applied: false,
      counts: { create: 2, error: 1 },
      rows: [{ row: 1, action: 'error', label: 'RA-?', message: '제목이 비었습니다' }],
      errors: ['1행 RA-?: 제목이 비었습니다'],
      truncated: false,
    })
    setup([raSource()])
    await userEvent.click(await screen.findByRole('button', { name: /동기화/ }))
    expect(await screen.findByText(/제목이 같아도 다른 보고서/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /적용 — 2개 새로/ })).toBeEnabled()
  })
})
