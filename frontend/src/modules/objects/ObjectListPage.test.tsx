/**
 * 타입 목록이 지키는 것 — **주소가 싣고 온 조건을 버리지 않고, 못 누를 단추는 안 세운다.**
 *
 * - 「생성」 · 「일괄 입력」 은 어느 부서든 관리자일 때만 선다 — 서버가 소유 부서의 관리자만
 *   받아 준다(`resolve_owner_workspace`).
 * - 지표의 「N건 보기」 가 싣는 `status=` 를 읽는다 — 버리면 목록이 셀의 수보다 많다.
 * - 홈 위젯이 싣는 `year=all` 을 읽는다 — 위젯은 연도 없이 센다.
 * - 「뷰로 저장」 이 통계의 기간 단위까지 담는다.
 * - 파일 열은 쪽마다 실려 온 칸을 그린다 — 사진은 미리보기(원본이 아니다), 그 밖은 이름.
 *
 * 무거운 이웃(조건 줄 · 트리 · 통계 · 뷰 고르개)은 가짜로 두고 **넘기는 것**만 본다.
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const objectApi = vi.hoisted(() => ({
  list: vi.fn(),
  fields: vi.fn(),
  export: vi.fn(),
  exportRelations: vi.fn(),
}))
vi.mock('@/modules/objects/api', () => ({ objectApi }))
const ontologyApi = vi.hoisted(() => ({ schema: vi.fn() }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))
const workspaceApi = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('@/modules/workspaces/api', () => ({ workspaceApi }))

const auth = vi.hoisted(() => ({ user: null as unknown }))
vi.mock('@/shared/auth/AuthContext', () => ({ useAuth: () => auth }))

/** 뷰 고르개 — 저장할 내용(지금 조건 · 통계 설정)을 글로 드러낸다. */
vi.mock('@/modules/objects/ViewPicker', () => ({
  ViewPicker: (props: { current: unknown; summary: unknown; homeWorkspace: unknown }) => (
    <pre data-testid="view-picker">
      {JSON.stringify({
        current: props.current,
        summary: props.summary,
        home: props.homeWorkspace,
      })}
    </pre>
  ),
}))
/** 통계 — 날짜 축에서 월로 묶고 있다고 알린다(진짜 패널이 하듯). 받은 설정을 글로 드러낸다. */
vi.mock('@/modules/objects/SummaryPanel', () => ({
  SummaryPanel: (props: {
    onGrain?: (grain: string | null) => void
    homeWorkspace?: string | null
    query: unknown
    settings: unknown
  }) => {
    const { onGrain } = props
    useEffect(() => onGrain?.('month'), [onGrain])
    return (
      <pre data-testid="summary-panel">
        {JSON.stringify({
          home: props.homeWorkspace,
          query: props.query,
          settings: props.settings,
        })}
      </pre>
    )
  },
}))
vi.mock('@/modules/objects/ConditionBar', () => ({ ConditionBar: () => null }))
// 미리보기는 blob 주소로 — 받은 경로를 그대로 적어 무엇을 받았는지 본다.
vi.mock('@/shared/hooks/useBlobUrl', () => ({
  useBlobUrl: (path: string | null) => ({ url: path ? `blob:${path}` : null, error: null }),
}))
vi.mock('@/modules/objects/ObjectTree', () => ({ ObjectTree: () => null }))

const TYPE = {
  id: 't1',
  slug: 'part',
  label: '부품',
  description: '',
  icon: 'Box',
  kind_class: 'record',
  temporal_kind: 'yearly',
  is_active: true,
  managed_by: null,
  properties: [],
  list_view: {},
  interface_slugs: [],
}

const MEMBER = {
  home_workspace_slug: 'cae',
  is_system_admin: false,
  memberships: [{ slug: 'cae', name: '해석팀', path: '해석팀', role: 'member' }],
}

async function open(url = '/o/part') {
  const { default: ObjectListPage } = await import('@/modules/objects/ObjectListPage')
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/o/:typeSlug" element={<ObjectListPage />} />
      </Routes>
    </MemoryRouter>,
  )
  await waitFor(() => expect(objectApi.list).toHaveBeenCalled())
  await screen.findByText(/아직 아무것도 없습니다|필터에 맞는 것이 없습니다/)
}

describe('타입 목록', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.user = MEMBER
    ontologyApi.schema.mockResolvedValue({ types: [TYPE], interfaces: [] })
    objectApi.list.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 })
    objectApi.fields.mockResolvedValue([])
    workspaceApi.list.mockResolvedValue([])
  })

  it('관리하는 부서가 없으면 「생성」 · 「일괄 입력」 이 안 선다', async () => {
    // 누구에게나 세우던 때는 눌러 보고서야 403 을 알았다(2026-10-08).
    await open()
    expect(screen.queryByRole('button', { name: /생성/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /일괄 입력/ })).not.toBeInTheDocument()
  })

  it('어느 부서든 관리자면 선다 — 대표 소속이 아니어도', async () => {
    auth.user = {
      ...MEMBER,
      memberships: [
        ...MEMBER.memberships,
        { slug: 'lab', name: '시험팀', path: '시험팀', role: 'manager' },
      ],
    }
    await open()
    expect(screen.getByRole('button', { name: /생성/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /일괄 입력/ })).toBeInTheDocument()
    // 파일 속성이 없는 타입에는 사진을 올릴 칸이 없다.
    expect(screen.queryByRole('button', { name: /사진 일괄 업로드/ })).not.toBeInTheDocument()
  })

  it('지표의 「N건 보기」 가 싣고 온 상태로 거르고, 걸려 있다고 보이며 풀 수 있다', async () => {
    // 안 읽던 때는 `status=` 가 버려져 목록이 셀의 수보다 많았다(2026-10-08).
    await open('/o/part?status=deprecated&f.grade.eq=A')
    expect(objectApi.list.mock.calls.at(-1)?.[1]).toMatchObject({
      status: 'deprecated',
      conditions: [{ field: 'grade', op: 'eq', value: 'A' }],
    })
    // 뷰로 저장하면 상태도 담긴다.
    expect(screen.getByTestId('view-picker')).toHaveTextContent('"status":"deprecated"')
    expect(screen.getByText('안 씀')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '상태 거르기 해제' }))
    await waitFor(() =>
      expect(objectApi.list.mock.calls.at(-1)?.[1]).toMatchObject({ status: null }),
    )
    expect(screen.queryByText('안 씀')).not.toBeInTheDocument()
  })

  it('기본은 올해다 — 홈 위젯이 싣고 온 year=all 이면 연도 없이 연다', async () => {
    // 정의가 오기 전의 첫 요청은 연도 없이 나간다 — 정의가 오면 올해로 다시 물어야 한다(그 다시
    // 묻기가 없어 고르개는 「올해」 인데 목록은 전체 연도였다, 2026-10-08).
    await open()
    await waitFor(() =>
      expect(objectApi.list.mock.calls.at(-1)?.[1]).toMatchObject({
        year: new Date().getFullYear(),
      }),
    )

    cleanup()
    objectApi.list.mockClear()
    await open('/o/part?year=all&view=v1')
    await waitFor(() => expect(objectApi.list).toHaveBeenCalledTimes(2))
    expect(objectApi.list.mock.calls.at(-1)?.[1]).toMatchObject({ year: null })
  })

  it('주소가 싣고 온 해로 연다 — MCP 답의 화면 주소가 그 해로 센 수를 싣는다', async () => {
    // 안 읽던 때는 `year=2025` 를 버리고 올해로 열어, 답의 수와 화면의 수가 달랐다.
    await open('/o/part?year=2025')
    await waitFor(() => expect(objectApi.list.mock.calls.at(-1)?.[1]).toMatchObject({ year: 2025 }))
    // 고르개의 목록 밖 해라도 걸려 있다고 보인다.
    cleanup()
    await open('/o/part?year=2001')
    await waitFor(() => expect(objectApi.list.mock.calls.at(-1)?.[1]).toMatchObject({ year: 2001 }))
    expect(screen.getByText('2001년')).toBeInTheDocument()
    // 못 읽는 해는 기본(올해)이다.
    cleanup()
    await open('/o/part?year=20x5')
    await waitFor(() =>
      expect(objectApi.list.mock.calls.at(-1)?.[1]).toMatchObject({
        year: new Date().getFullYear(),
      }),
    )
  })

  it('group=1 이면 주소의 통계 설정으로 펼쳐 연다 — 모르는 값은 기본으로', async () => {
    await open(
      '/o/part?group=1&group_by=properties.grade&split_by=status&metric=sum&metric_field=price&order=key&grain=month',
    )
    const panel = await screen.findByTestId('summary-panel')
    const shown = JSON.parse(panel.textContent ?? '{}') as { settings: Record<string, unknown> }
    expect(shown.settings).toMatchObject({
      groupBy: 'properties.grade',
      splitBy: 'status',
      metric: 'sum',
      metricField: 'price',
      order: 'key',
      grain: 'month',
      chart: 'bar',
    })

    cleanup()
    await open('/o/part?group=1&metric=median&order=random&grain=decade')
    const fallback = JSON.parse((await screen.findByTestId('summary-panel')).textContent ?? '{}')
    expect(fallback.settings).toMatchObject({
      groupBy: 'status',
      metric: 'count',
      order: 'desc',
      grain: '',
    })

    // `group=1` 없이 온 설정은 안 읽는다 — 통계를 펼치지 않은 목록의 주소다.
    cleanup()
    await open('/o/part?group_by=properties.grade')
    expect(screen.queryByTestId('summary-panel')).not.toBeInTheDocument()
  })

  it('「뷰로 저장」 이 통계의 기간 단위까지 담고, 홈에서 온 부서를 통계에 넘긴다', async () => {
    // grain 을 빠뜨리던 때는 월별로 저장한 뷰가 다시 열면 해별이었다(2026-10-08).
    await open('/o/part?group=1&home=sales')
    await screen.findByTestId('summary-panel')
    await waitFor(() =>
      expect(screen.getByTestId('view-picker')).toHaveTextContent('"grain":"month"'),
    )
    expect(screen.getByTestId('view-picker')).toHaveTextContent('"home":"sales"')
    expect(screen.getByTestId('summary-panel')).toHaveTextContent('"home":"sales"')
  })

  it('파일 열은 쪽마다 실려 온 칸을 그린다 — 사진은 미리보기, 그 밖은 이름', async () => {
    auth.user = {
      ...MEMBER,
      memberships: [
        ...MEMBER.memberships,
        { slug: 'lab', name: '시험팀', path: '시험팀', role: 'manager' },
      ],
    }
    const file = (key: string, label: string, accept: string | null) => ({
      key,
      label,
      data_type: 'file',
      accept,
      required: false,
      multi: false,
    })
    ontologyApi.schema.mockResolvedValue({
      types: [
        {
          ...TYPE,
          properties: [file('photo', '사진', 'image'), file('doc', '성적서', null)],
          list_view: { columns: ['label', 'properties.photo', 'properties.doc'] },
        },
      ],
      interfaces: [],
    })
    objectApi.list.mockResolvedValue({
      items: [
        {
          id: 'o1',
          type_slug: 'part',
          key: null,
          label: '볼트',
          description: '',
          properties: {},
          ref_labels: {},
          status: 'active',
          owner_workspace_slug: 'lab',
          owner_workspace_name: '시험팀',
          valid_from_year: null,
          valid_to_year: null,
          created_at: '2026-10-08T00:00:00Z',
          updated_at: '2026-10-08T00:00:00Z',
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
      files: {
        o1: {
          photo: { count: 2, first: { id: 'a1', original_name: '앞면.jpg', is_image: true } },
          doc: { count: 1, first: { id: 'f1', original_name: '성적서.pdf', is_image: false } },
        },
      },
    })
    const { default: ObjectListPage } = await import('@/modules/objects/ObjectListPage')
    render(
      <MemoryRouter initialEntries={['/o/part']}>
        <Routes>
          <Route path="/o/:typeSlug" element={<ObjectListPage />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByText('볼트')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: '앞면.jpg' })).toHaveAttribute(
      'src',
      'blob:/attachments/a1/thumbnail',
    )
    expect(screen.getByText('+1')).toBeInTheDocument()
    expect(screen.getByText('성적서.pdf')).toBeInTheDocument()
    // 파일 속성이 있는 타입에서만 선다.
    expect(screen.getByRole('button', { name: /사진 일괄 업로드/ })).toBeInTheDocument()
  })
})

describe('기준일(ADR 0039)', () => {
  const TREE_TYPE = { ...TYPE, list_view: { tree: { relation: 'bom_of', parent: 'dst' } } }

  beforeEach(() => {
    vi.clearAllMocks()
    auth.user = MEMBER
    objectApi.list.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 })
    objectApi.fields.mockResolvedValue([])
    workspaceApi.list.mockResolvedValue([])
  })

  it('트리가 있는 타입에는 기준일이 서고, 고른 날이 목록 요청에 실린다', async () => {
    ontologyApi.schema.mockResolvedValue({ types: [TREE_TYPE], interfaces: [] })
    await open()
    const picker = await screen.findByLabelText('기준일')
    fireEvent.change(picker, { target: { value: '2026-03-01' } })
    await waitFor(() =>
      expect(objectApi.list).toHaveBeenLastCalledWith(
        'part',
        expect.objectContaining({ asOf: '2026-03-01' }),
      ),
    )
    // 비우면 오늘 — 요청에서 빠진다(서버의 기본).
    await userEvent.click(screen.getByRole('button', { name: '오늘' }))
    await waitFor(() =>
      expect(objectApi.list).toHaveBeenLastCalledWith(
        'part',
        expect.objectContaining({ asOf: null }),
      ),
    )
  })

  it('주소의 as_of 를 읽어 그날의 구조로 연다', async () => {
    ontologyApi.schema.mockResolvedValue({ types: [TREE_TYPE], interfaces: [] })
    await open('/o/part?as_of=2026-02-15')
    expect(objectApi.list).toHaveBeenCalledWith(
      'part',
      expect.objectContaining({ asOf: '2026-02-15' }),
    )
    expect(screen.getByLabelText('기준일')).toHaveValue('2026-02-15')
  })

  it('관계를 따라가는 것이 없는 목록에는 기준일이 안 선다', async () => {
    ontologyApi.schema.mockResolvedValue({ types: [TYPE], interfaces: [] })
    await open()
    expect(screen.queryByLabelText('기준일')).not.toBeInTheDocument()
  })
})

describe('낡은 것만(ADR 0041)', () => {
  /** 「도면 → 부품」 계보 — 도착(도면)이 원천이고 부품이 파생물이다. */
  const MADE_FROM = {
    slug: 'made_from',
    label: '만든 원천',
    directed: true,
    lineage_source: 'dst',
    src_type_slugs: ['part'],
    dst_type_slugs: ['drawing'],
  }
  const DRAWING = { ...TYPE, id: 't2', slug: 'drawing', label: '도면' }

  function row(id: string, label: string) {
    return {
      id,
      type_slug: 'part',
      key: null,
      label,
      description: '',
      properties: {},
      ref_labels: {},
      status: 'active',
      owner_workspace_slug: 'cae',
      owner_workspace_name: '해석팀',
      valid_from_year: null,
      valid_to_year: null,
      created_at: '2026-10-08T00:00:00Z',
      updated_at: '2026-10-08T00:00:00Z',
    }
  }

  beforeEach(() => {
    vi.clearAllMocks()
    auth.user = MEMBER
    ontologyApi.schema.mockResolvedValue({
      types: [TYPE, DRAWING],
      interfaces: [],
      relation_types: [MADE_FROM],
    })
    objectApi.list.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 })
    objectApi.fields.mockResolvedValue([])
    workspaceApi.list.mockResolvedValue([])
  })

  it('파생물 끝에 설 수 있는 타입에 토글이 서고, 켜면 조건으로 목록 · 저장된 뷰에 실린다', async () => {
    await open()
    const toggle = await screen.findByRole('checkbox', { name: '낡은 것만' })
    expect(toggle).not.toBeChecked()
    await userEvent.click(toggle)
    await waitFor(() =>
      expect(objectApi.list.mock.calls.at(-1)?.[1].conditions).toContainEqual({
        field: 'lineage',
        op: 'eq',
        value: 'stale',
      }),
    )
    expect(screen.getByRole('checkbox', { name: '낡은 것만' })).toBeChecked()
    // 저장된 뷰도 같은 조건을 담는다 — 주소 · 통계 · 내보내기와 한 길.
    expect(screen.getByTestId('view-picker')).toHaveTextContent('"field":"lineage"')
    // 비면 「낡은 것만」 때문일 수 있다고 말한다.
    expect(await screen.findByText(/「낡은 것만」 을 해제하면 다시 표시합니다/)).toBeInTheDocument()
  })

  it('주소의 f.lineage.eq=stale 이면 켜진 채 열리고, 끄면 그 조건만 빠진다', async () => {
    await open('/o/part?f.lineage.eq=stale&f.grade.eq=A')
    expect(objectApi.list.mock.calls.at(-1)?.[1].conditions).toContainEqual({
      field: 'lineage',
      op: 'eq',
      value: 'stale',
    })
    const toggle = screen.getByRole('checkbox', { name: '낡은 것만' })
    expect(toggle).toBeChecked()
    await userEvent.click(toggle)
    await waitFor(() =>
      expect(objectApi.list.mock.calls.at(-1)?.[1].conditions).toEqual([
        { field: 'grade', op: 'eq', value: 'A' },
      ]),
    )
  })

  it('원천 끝에만 서는 타입 · 계보가 없는 설치에는 토글이 없다', async () => {
    await open('/o/drawing')
    expect(screen.queryByRole('checkbox', { name: '낡은 것만' })).not.toBeInTheDocument()
    cleanup()
    ontologyApi.schema.mockResolvedValue({ types: [TYPE], interfaces: [], relation_types: [] })
    await open()
    expect(screen.queryByRole('checkbox', { name: '낡은 것만' })).not.toBeInTheDocument()
  })

  it('줄마다 낡음 · 위에서 낡음 배지 — 서버가 실은 줄만', async () => {
    ontologyApi.schema.mockResolvedValue({
      types: [{ ...TYPE, list_view: { columns: ['label'] } }, DRAWING],
      interfaces: [],
      relation_types: [MADE_FROM],
    })
    objectApi.list.mockResolvedValue({
      items: [row('a', '브래킷'), row('b', '하우징'), row('c', '볼트')],
      total: 3,
      limit: 50,
      offset: 0,
      lineage: { a: 'direct', b: 'upstream' },
    })
    const { default: ObjectListPage } = await import('@/modules/objects/ObjectListPage')
    render(
      <MemoryRouter initialEntries={['/o/part']}>
        <Routes>
          <Route path="/o/:typeSlug" element={<ObjectListPage />} />
        </Routes>
      </MemoryRouter>,
    )
    const line = async (label: string) =>
      (await screen.findByRole('link', { name: label })).closest('tr') as HTMLElement
    expect(within(await line('브래킷')).getByText('낡음')).toBeInTheDocument()
    expect(within(await line('하우징')).getByText('위에서 낡음')).toBeInTheDocument()
    const bolt = await line('볼트')
    expect(within(bolt).queryByText('낡음')).toBeNull()
    expect(within(bolt).queryByText('위에서 낡음')).toBeNull()
  })
})
