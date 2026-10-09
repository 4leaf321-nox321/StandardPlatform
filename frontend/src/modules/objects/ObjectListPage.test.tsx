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

import { cleanup, render, screen, waitFor } from '@testing-library/react'
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
/** 통계 — 날짜 축에서 월로 묶고 있다고 알린다(진짜 패널이 하듯). */
vi.mock('@/modules/objects/SummaryPanel', () => ({
  SummaryPanel: (props: {
    onGrain?: (grain: string | null) => void
    homeWorkspace?: string | null
    query: unknown
  }) => {
    const { onGrain } = props
    useEffect(() => onGrain?.('month'), [onGrain])
    return (
      <pre data-testid="summary-panel">
        {JSON.stringify({ home: props.homeWorkspace, query: props.query })}
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
