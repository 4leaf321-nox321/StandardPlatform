/**
 * 그래프 화면이 지키는 것 — **잘린 것을 말하나, 「전부」 단추가 없나.**
 *
 * 캔버스 자체는 여기서 안 그린다(happy-dom 에는 canvas 가 없다). 보는 것은 서버 응답이
 * 화면의 말로 바뀌는 자리다: 상한 안내, 「+N」 의 근거, 빈 상태의 이유.
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  MapData,
  Neighborhood,
  Overview,
  PathResult,
  Subgraph,
  Whole,
} from '@/modules/graph/api'

vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { is_system_admin: true, memberships: [] } }),
}))
vi.mock('@/shared/theme/ThemeProvider', () => ({
  useTheme: () => ({ theme: 'light', toggle: () => {} }),
}))

const OVERVIEW: Overview = {
  nodes: [
    { slug: 'part', label: '부품', icon: '', group_slug: 'domain', count: 7 },
    { slug: 'vendor', label: '공급사', icon: '', group_slug: 'domain', count: 2 },
  ],
  edges: [
    {
      relation: 'supplied_by',
      label: '공급받음',
      directed: true,
      src_type: 'part',
      dst_type: 'vendor',
      count: 5,
    },
    {
      relation: 'part_of',
      label: '속함',
      directed: true,
      src_type: 'part',
      dst_type: 'part',
      count: 0,
    },
  ],
  object_count: 9,
  edge_count: 5,
}

const NEIGHBORHOOD: Neighborhood = {
  focus: 'hub',
  nodes: [
    {
      id: 'hub',
      label: '허브',
      key: null,
      type_slug: 'part',
      type_label: '부품',
      status: 'active',
      owner_workspace_slug: null,
      degree: 5,
      truncated: true,
    },
    {
      id: 'a',
      label: '잎A',
      key: null,
      type_slug: 'part',
      type_label: '부품',
      status: 'active',
      owner_workspace_slug: null,
      degree: 1,
      truncated: false,
    },
    {
      id: 'b',
      label: '잎B',
      key: null,
      type_slug: 'part',
      type_label: '부품',
      status: 'active',
      owner_workspace_slug: null,
      degree: 1,
      truncated: false,
    },
  ],
  edges: [
    {
      id: 'e1',
      relation: 'near',
      label: '가까움',
      inverse_label: '가까움',
      directed: true,
      src: 'hub',
      dst: 'a',
    },
    {
      id: 'e2',
      relation: 'near',
      label: '가까움',
      inverse_label: '가까움',
      directed: true,
      src: 'hub',
      dst: 'b',
    },
  ],
  depth: 1,
  fanout: 2,
  node_limit: 200,
  truncated: true,
}

const SUBGRAPH: Subgraph = {
  nodes: [
    {
      id: 'p1',
      label: '부품1',
      key: 'P-1',
      type_slug: 'part',
      type_label: '부품',
      status: 'active',
      owner_workspace_slug: null,
      degree: 1,
      truncated: false,
    },
    {
      id: 'p2',
      label: '부품2',
      key: 'P-2',
      type_slug: 'part',
      type_label: '부품',
      status: 'active',
      owner_workspace_slug: null,
      degree: 1,
      truncated: false,
    },
  ],
  edges: [
    {
      id: 'e9',
      relation: 'near',
      label: '가까움',
      inverse_label: '가까움',
      directed: true,
      src: 'p1',
      dst: 'p2',
    },
  ],
  total: 7,
  limit: 2,
  offset: 0,
  truncated: true,
}

const graphApi = vi.hoisted(() => ({
  overview: vi.fn(),
  search: vi.fn(),
  neighborhood: vi.fn(),
  subgraph: vi.fn(),
  path: vi.fn(),
  whole: vi.fn(),
  map: vi.fn(),
  mapPoints: vi.fn(),
  mapLinks: vi.fn(),
  mapLocate: vi.fn(),
  mapRelayout: vi.fn(),
  hubKeys: vi.fn(),
  setHubKey: vi.fn(),
}))
vi.mock('@/modules/graph/api', () => ({ graphApi }))
/** 지도 시험이 카메라를 움직인 척할 때 알릴 보이는 범위. */
const camera = vi.hoisted(() => ({ view: { x0: 0, y0: 0, x1: 1, y1: 1 } }))

// 정적 그리기 — sigma(WebGL)는 happy-dom 에서 안 돈다. 그리는 쪽을 경계에서 바꿔, 무엇을 받았는지
// (이름 · 자리 수 · 그림 위의 말)만 본다. 배치 작업자도 여기서 바꾼다(작업자가 없다).
vi.mock('@/modules/graph/WholeCanvas', () => ({
  default: ({
    labels,
    positions,
    onSelect,
    onOpen,
    overlay,
    map,
    legend,
    onLegendClick,
  }: {
    labels: string[]
    positions: Float32Array
    onSelect: (at: number | null) => void
    onOpen?: (at: number) => void
    overlay?: ReactNode
    map?: {
      bbox: number[]
      home?: number[] | null
      onView?: (view: unknown) => void
      extra?: { labels: string[] } | null
      links?: { src: number[]; dst: number[] } | null
    } | null
    legend?: { label: string; line?: boolean; key?: number }[]
    onLegendClick?: (key: number) => void
  }) => (
    <div
      data-testid="whole-canvas"
      data-placed={positions.length / 2}
      data-frame={map ? map.bbox.join(',') : ''}
      data-home={map?.home ? map.home.join(',') : ''}
      data-extra={map?.extra?.labels.length ?? 0}
      data-links={
        map?.links ? map.links.src.map((a, k) => `${a}>${map.links!.dst[k]}`).join(' ') : ''
      }
    >
      {map &&
        legend
          ?.filter((item) => item.line && item.key !== undefined)
          .map((item) => (
            <button key={item.label} type="button" onClick={() => onLegendClick?.(item.key!)}>
              선 범례 {item.label}
            </button>
          ))}
      {labels.map((label, at) => (
        <button key={label} type="button" onClick={() => onSelect(at)}>
          그림 노드 {label}
        </button>
      ))}
      {labels.map((label, at) => (
        <button key={`open:${label}`} type="button" onClick={() => onOpen?.(at)}>
          두 번 클릭 {label}
        </button>
      ))}
      {map && (
        <button
          type="button"
          onClick={() =>
            map.onView?.({
              ...camera.view,
              width: 800,
              height: 600,
              ratio: 0.1,
              unitPx: 4,
              project: (x: number, y: number) => [x, y],
            })
          }
        >
          카메라 움직임
        </button>
      )}
      {overlay}
    </div>
  ),
}))
const layoutWorker = vi.hoisted(() => ({ startCluster: vi.fn() }))
vi.mock('@/modules/graph/wholeWorker', () => layoutWorker)
const objectApi = vi.hoisted(() => ({
  list: vi.fn(),
  profile: vi.fn(() =>
    Promise.resolve({
      object: {
        id: 'hub',
        type_slug: 'part',
        key: null,
        label: '허브',
        description: '가운데 것',
        properties: { material: '스틸', weight: 3 },
        ref_labels: {},
        status: 'active',
        owner_workspace_slug: null,
        valid_from_year: null,
        valid_to_year: null,
        created_at: '',
        updated_at: '',
      },
      type_label: '부품',
      properties_schema: [
        { key: 'material', label: '재질', data_type: 'enum', multi: false, unit: '' },
        {
          key: 'weight',
          label: '무게',
          data_type: 'number',
          multi: false,
          unit: 'kg',
          decimals: null,
        },
        { key: 'note', label: '비고', data_type: 'text', multi: false, unit: '' },
      ],
      attachments: [],
      related: [
        {
          relation_id: 'e1',
          relation: 'near',
          label: '가까움',
          outgoing: true,
          object_id: 'a',
          object_label: '잎A',
          object_key: null,
          object_type_slug: 'part',
          object_type_label: '부품',
          properties: {},
          evidence_note: '',
          created_at: '',
        },
        {
          relation_id: 'e9',
          relation: 'near',
          label: '가까움',
          outgoing: true,
          object_id: 'zzz',
          object_label: '먼 것',
          object_key: null,
          object_type_slug: 'part',
          object_type_label: '부품',
          properties: {},
          evidence_note: '',
          created_at: '',
        },
      ],
      can_edit: false,
    }),
  ),
}))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

// plotly 는 happy-dom 에서 안 뜬다(그리고 1MB 를 받을 이유도 없다) — 사케이에 실제로
// 무엇이 실렸는지만 본다.
vi.mock('@/shared/charts/LazyPlot', () => ({
  LazyPlot: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="plot">{JSON.stringify(data)}</div>
  ),
}))
vi.mock('@/modules/ontology/api', () => ({
  ontologyApi: {
    schema: () =>
      Promise.resolve({
        groups: [],
        types: [
          { slug: 'part', label: '부품', is_active: true, object_count: 7 },
          { slug: 'vendor', label: '공급사', is_active: true, object_count: 2 },
        ],
        relation_types: [
          { slug: 'supplied_by', label: '공급받음', is_active: true },
          { slug: 'part_of', label: '속함', is_active: true },
        ],
        data_types: [],
        generated_at: '',
      }),
  },
}))

async function mount(path = '/graph') {
  const { default: GraphPage } = await import('@/modules/graph/GraphPage')
  return render(
    <MemoryRouter initialEntries={[path]}>
      <GraphPage />
    </MemoryRouter>,
  )
}

describe('지식 그래프', () => {
  beforeEach(() => vi.clearAllMocks())

  it('탐색 그림도 떠나면 받던 요청을 끊는다 — 타입 전체 표시 · 이웃(ADR 0048)', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.subgraph.mockReturnValue(new Promise(() => {}))
    const types = await mount('/graph?type=part')
    await waitFor(() => expect(graphApi.subgraph).toHaveBeenCalledTimes(1))
    const listed = graphApi.subgraph.mock.calls[0][0].signal as AbortSignal
    expect(listed.aborted).toBe(false)
    types.unmount()
    expect(listed.aborted).toBe(true)

    graphApi.neighborhood.mockReturnValue(new Promise(() => {}))
    const focused = await mount('/graph?focus=hub')
    await waitFor(() => expect(graphApi.neighborhood).toHaveBeenCalledTimes(1))
    const around = graphApi.neighborhood.mock.calls[0][0].signal as AbortSignal
    focused.unmount()
    expect(around.aborted).toBe(true)
  })

  it('구조 그림은 타입·관계 종류·객체 수를 요약한다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    await mount()
    await waitFor(() =>
      expect(screen.getByText(/타입 2 · 관계 종류 2 · 객체 9 · 관계 5/)).toBeInTheDocument(),
    )
    // **「전부 보기」 단추는 없다.** 전부는 이 그림이고, 자세한 것은 탐색에서 한 걸음씩.
    expect(screen.queryByRole('button', { name: /전부 보기|전체 보기/ })).not.toBeInTheDocument()
  })

  it('선 굵기는 찍어 둔 때를 말하고, 아직 안 셌으면 세는 중이라고 한다', async () => {
    // ADR 0038 — 개요는 선 굵기를 작업이 찍어 둔 값으로 그린다.
    graphApi.overview.mockResolvedValue({ ...OVERVIEW, counted_at: '2026-10-26T09:30:00+09:00' })
    const { unmount } = await mount()
    await waitFor(() => expect(screen.getByText(/관계 5 \(.+ 기준\)/)).toBeInTheDocument())
    unmount()

    graphApi.overview.mockResolvedValue({ ...OVERVIEW, edge_count: 0, counting: true })
    await mount()
    await waitFor(() => expect(screen.getByText(/관계 수는 세는 중입니다/)).toBeInTheDocument())
  })

  it('정의가 없으면 이유와 갈 곳을 말한다', async () => {
    graphApi.overview.mockResolvedValue({ nodes: [], edges: [], object_count: 0, edge_count: 0 })
    await mount()
    await waitFor(() => expect(screen.getByText('정의된 타입이 없습니다')).toBeInTheDocument())
    expect(screen.getByRole('link', { name: /온톨로지 열기/ })).toHaveAttribute(
      'href',
      '/admin/ontology/types',
    )
  })

  it('탐색은 시작점 없이는 그리지 않고 그 이유를 말한다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    await mount()
    await userEvent.click(screen.getByRole('tab', { name: '탐색' }))
    expect(screen.getByText('시작점을 선택하세요')).toBeInTheDocument()
    // 전부를 한 번에 보는 길은 그림이 아니라 지도다 — 어디 있는지 말한다.
    expect(screen.getByText(/「전체 지도」 — 서버가 미리 놓아 둔 지도입니다/)).toBeInTheDocument()
    expect(graphApi.neighborhood).not.toHaveBeenCalled()
  })

  it('?focus= 로 들어오면 탐색을 열고 잘린 것을 말한다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.neighborhood.mockResolvedValue(NEIGHBORHOOD)
    await mount('/graph?focus=hub')
    await waitFor(() => expect(screen.getByText(/노드 3 · 관계 2/)).toBeInTheDocument())
    expect(graphApi.neighborhood).toHaveBeenCalledWith(
      expect.objectContaining({ focus: 'hub', depth: 1, fanout: 30 }),
    )
    // 잘렸다는 말 — 안 하면 그림은 「이게 전부」 로 읽힌다.
    expect(screen.getByText(/일부만 실었습니다/)).toBeInTheDocument()
    // 시작점이 골라져 있고, 화면에 없는 관계의 수(5 - 2)를 근거로 「확장」 가 선다.
    expect(screen.getByText('화면에 없는 것 3')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '여기서 확장 (+3)' })).toBeEnabled()
    // 상한은 서버가 준 값 그대로 적는다.
    expect(screen.getByText(/노드당 이웃 2개 · 노드 200개까지/)).toBeInTheDocument()
  })

  it('펼치면 이미 든 것에 합친다 — 다시 그리지 않는다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.neighborhood.mockResolvedValueOnce(NEIGHBORHOOD).mockResolvedValueOnce({
      ...NEIGHBORHOOD,
      focus: 'hub',
      nodes: [
        NEIGHBORHOOD.nodes[0],
        {
          id: 'c',
          label: '잎C',
          key: null,
          type_slug: 'part',
          type_label: '부품',
          status: 'active',
          owner_workspace_slug: null,
          degree: 1,
          truncated: false,
        },
      ],
      edges: [
        {
          id: 'e3',
          relation: 'near',
          label: '가까움',
          inverse_label: '가까움',
          directed: true,
          src: 'hub',
          dst: 'c',
        },
      ],
      truncated: false,
    })
    await mount('/graph?focus=hub')
    await waitFor(() => expect(screen.getByText(/노드 3 · 관계 2/)).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: '여기서 확장 (+3)' }))
    // 3 + 1 노드, 2 + 1 관계 — 앞서 든 잎A·잎B 가 사라지지 않는다.
    await waitFor(() => expect(screen.getByText(/노드 4 · 관계 3/)).toBeInTheDocument())
    // **몇 개가 늘었는지 말한다.** 새 노드가 화면 밖에 놓이면 「아무 일도 안 일어났다」 로
    // 읽히므로, 늘어난 수와 볼 방법(화면 맞춤)을 함께 적는다.
    expect(screen.getByText(/1개/)).toBeInTheDocument()
    expect(screen.getByText(/화면 맞춤/)).toBeInTheDocument()
    expect(graphApi.neighborhood).toHaveBeenLastCalledWith(
      expect.objectContaining({ focus: 'hub', depth: 1 }),
    )
  })

  it('?type= 으로 들어오면 그 타입의 인스턴스 전부를 「N개 중 M개」 로 그린다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.subgraph.mockResolvedValue(SUBGRAPH)
    await mount('/graph?type=part')
    await waitFor(() => expect(screen.getByText(/7개 중 1–2/)).toBeInTheDocument())
    // 「한 타입 전부」 도 화면에서 고른 노드 상한을 쓴다 — 여기만 다른 수를 쓰면
    // 「왜 저기서는 되고 여기서는 안 되지」 가 된다. 수를 손으로 박지 않는다.
    expect(graphApi.subgraph).toHaveBeenCalledWith(
      expect.objectContaining({ types: ['part'], offset: 0, limit: expect.any(Number) }),
    )
    // 한 쪽에 다 안 들어가므로 쪽 넘기기가 선다 — **잘렸는데 넘길 길이 없으면 그것이 전부로 읽힌다.**
    await userEvent.click(screen.getByRole('button', { name: '다음 쪽' }))
    await waitFor(() =>
      expect(graphApi.subgraph).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 2 })),
    )
  })

  it('구조에서 타입을 선택하면 그 인스턴스 전부를 그릴 수 있다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.subgraph.mockResolvedValue(SUBGRAPH)
    await mount()
    await waitFor(() => expect(screen.getByText(/타입 2 · 관계 종류 2/)).toBeInTheDocument())
    // 캔버스가 없으니(happy-dom) 노드를 누를 수 없다 — 목록 쪽으로 같은 길을 밟는다.
    objectApi.list.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 })
    await userEvent.click(screen.getByRole('tab', { name: '탐색' }))
    await userEvent.click(await screen.findByRole('button', { name: /^부품 7$/ }))
    await userEvent.click(screen.getByRole('button', { name: '부품 전체 표시 (7)' }))
    await waitFor(() => expect(screen.getByText(/7개 중 1–2/)).toBeInTheDocument())
  })

  it('타입에서 탐색해 시작점을 고른다 — 검색어를 모르는 사람의 길', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.neighborhood.mockResolvedValue(NEIGHBORHOOD)
    objectApi.list.mockResolvedValue({
      items: [
        { id: 'hub', label: '허브', key: 'H-1', properties: {}, ref_labels: {} },
        { id: 'a', label: '잎A', key: null, properties: {}, ref_labels: {} },
      ],
      total: 2,
      limit: 20,
      offset: 0,
    })
    await mount()
    await userEvent.click(screen.getByRole('tab', { name: '탐색' }))
    await userEvent.click(await screen.findByRole('button', { name: /^부품 7$/ }))
    await userEvent.click(await screen.findByRole('button', { name: /허브/ }))
    expect(objectApi.list).toHaveBeenCalledWith('part', { limit: 20, offset: 0 })
    await waitFor(() => expect(screen.getByText(/노드 3 · 관계 2/)).toBeInTheDocument())
    expect(graphApi.neighborhood).toHaveBeenCalledWith(expect.objectContaining({ focus: 'hub' }))
  })

  it('타입을 여럿 선택하면 한 그림에 전부 그린다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.subgraph.mockResolvedValue({ ...SUBGRAPH, total: 9 })
    objectApi.list.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 })
    await mount()
    await userEvent.click(screen.getByRole('tab', { name: '탐색' }))
    await userEvent.click(await screen.findByRole('button', { name: /^부품 7$/ }))
    await userEvent.click(screen.getByRole('button', { name: /^공급사 2$/ }))
    await userEvent.click(screen.getByRole('button', { name: '고른 2개 타입 전체 표시 (9)' }))
    await waitFor(() =>
      expect(graphApi.subgraph).toHaveBeenCalledWith(
        expect.objectContaining({ types: ['part', 'vendor'], offset: 0 }),
      ),
    )
    // 주소로도 남는다 — 붙여 넣으면 같은 그림.
    graphApi.subgraph.mockClear()
    await mount('/graph?type=part,vendor')
    await waitFor(() =>
      expect(graphApi.subgraph).toHaveBeenCalledWith(
        expect.objectContaining({ types: ['part', 'vendor'] }),
      ),
    )
  })

  it('타입을 여럿 선택하면 훑을 타입을 그 사이에서 고른다', async () => {
    // 마지막에 고른 타입에 묶이면 앞서 고른 타입은 훑을 길이 없다.
    graphApi.overview.mockResolvedValue(OVERVIEW)
    objectApi.list.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 })
    await mount()
    await userEvent.click(screen.getByRole('tab', { name: '탐색' }))
    await userEvent.click(await screen.findByRole('button', { name: /^부품 7$/ }))
    await userEvent.click(screen.getByRole('button', { name: /^공급사 2$/ }))
    await waitFor(() =>
      expect(objectApi.list).toHaveBeenLastCalledWith('vendor', { limit: 20, offset: 0 }),
    )
    await userEvent.click(screen.getByRole('tab', { name: '부품' }))
    await waitFor(() =>
      expect(objectApi.list).toHaveBeenLastCalledWith('part', { limit: 20, offset: 0 }),
    )
    // 칩을 빼도 보던 타입이 아직 고른 채면 그대로, 새로 고른 칩은 그것을 훑는다.
    await userEvent.click(screen.getByRole('button', { name: /^공급사 2$/ }))
    expect(objectApi.list).toHaveBeenLastCalledWith('part', { limit: 20, offset: 0 })
    await userEvent.click(screen.getByRole('button', { name: /^공급사 2$/ }))
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: '공급사' })).toHaveAttribute('aria-selected', 'true'),
    )
  })

  it('고른 노드의 상세 — 속성 요약과 관계를 그림과 잇는다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.neighborhood.mockResolvedValue(NEIGHBORHOOD)
    await mount('/graph?focus=hub')
    await waitFor(() => expect(screen.getByText(/노드 3 · 관계 2/)).toBeInTheDocument())
    // 시작점이 골라져 있고, 프로필을 읽어 속성 몇 개를 적는다. 빈 속성(비고)은 안 적는다.
    await waitFor(() => expect(screen.getByText('재질')).toBeInTheDocument())
    expect(screen.getByText('스틸')).toBeInTheDocument()
    expect(screen.queryByText('비고')).not.toBeInTheDocument()
    expect(screen.getByText('가운데 것')).toBeInTheDocument()
    // 관계는 종류별로 묶이고, 그림에 있는 것은 눌러 고를 수 있으며 없는 것은 「화면 밖」.
    expect(screen.getByText('가까움')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /잎A/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /먼 것.*화면 밖/ })).toBeDisabled()
  })

  it('키보드 — Enter 는 펼치고, ESC 는 선택을 푼다, 입력 칸에서는 안 듣는다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.neighborhood.mockResolvedValue(NEIGHBORHOOD)
    await mount('/graph?focus=hub')
    await waitFor(() => expect(screen.getByText(/노드 3 · 관계 2/)).toBeInTheDocument())
    expect(graphApi.neighborhood).toHaveBeenCalledTimes(1)

    // 시작점이 골라진 상태에서 Enter → 그 노드에서 한 단계 더.
    await userEvent.keyboard('{Enter}')
    await waitFor(() => expect(graphApi.neighborhood).toHaveBeenCalledTimes(2))
    expect(graphApi.neighborhood).toHaveBeenLastCalledWith(
      expect.objectContaining({ focus: 'hub', depth: 1 }),
    )

    // 입력 칸 안에서는 단축키가 안 듣는다 — 검색어에 「r」 을 치는데 새로고침되면 버그다.
    await userEvent.click(screen.getByPlaceholderText(/그림 안에서 검색/))
    await userEvent.keyboard('r')
    expect(graphApi.neighborhood).toHaveBeenCalledTimes(2)

    // ESC — 검색 칸을 비우고 나온 뒤, 한 번 더 누르면 선택이 풀린다.
    await userEvent.keyboard('{Escape}')
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /여기서 확장/ })).not.toBeInTheDocument(),
    )
  })

  it('조작 상태가 주소에 남는다 — 붙여넣으면 같은 그림', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.neighborhood.mockResolvedValue(NEIGHBORHOOD)
    await mount('/graph?focus=hub&d=2&fo=100&rel=supplied_by&color=community')
    await waitFor(() =>
      expect(graphApi.neighborhood).toHaveBeenCalledWith(
        expect.objectContaining({
          focus: 'hub',
          depth: 2,
          fanout: 100,
          relations: ['supplied_by'],
        }),
      ),
    )
    // 관계 칩은 이웃을 부른 **뒤에** 그려진다 — 부른 것을 본 순간 바로 찾으면 느린 CI 에서만
    // 없다고 나온다(v0.2.0 에서 실제로 막혔다). 그려질 때까지 기다린다.
    expect(await screen.findByRole('checkbox', { name: /공급받음/ })).toBeChecked()
  })
})

describe('두 객체 사이의 길', () => {
  beforeEach(() => vi.clearAllMocks())

  const FOUND: PathResult = {
    source: 'a',
    target: 'b',
    found: true,
    length: 2,
    paths: [{ nodes: ['a', 'hub', 'b'], edges: ['e1', 'e2'] }],
    nodes: NEIGHBORHOOD.nodes,
    edges: NEIGHBORHOOD.edges,
    max_depth: 6,
    visited: 12,
    truncated: false,
  }

  it('방향이 주소에서 이웃 요청으로 실리고, 기본(양쪽)이면 「양쪽」 으로 보인다(ADR 0047)', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.neighborhood.mockResolvedValue(NEIGHBORHOOD)
    await mount('/graph?focus=hub&dir=in')
    await waitFor(() =>
      expect(graphApi.neighborhood).toHaveBeenCalledWith(
        expect.objectContaining({ focus: 'hub', direction: 'in' }),
      ),
    )
    expect(screen.getByRole('combobox', { name: '방향' })).toHaveTextContent('들어오는 선만')
    cleanup()

    await mount('/graph?focus=hub&dir=nope')
    await waitFor(() =>
      expect(graphApi.neighborhood).toHaveBeenLastCalledWith(
        expect.objectContaining({ focus: 'hub', direction: 'both' }),
      ),
    )
    expect(screen.getByRole('combobox', { name: '방향' })).toHaveTextContent('양쪽')
  })

  it('기준일이 이웃 · 연결 경로 요청에 실린다(ADR 0039)', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.neighborhood.mockResolvedValue(NEIGHBORHOOD)
    graphApi.path.mockResolvedValue(FOUND)
    await mount('/graph?focus=hub&as_of=2026-02-15')
    await waitFor(() =>
      expect(graphApi.neighborhood).toHaveBeenCalledWith(
        expect.objectContaining({ focus: 'hub', asOf: '2026-02-15' }),
      ),
    )
    // 고르개에서 바꾸면 그날의 관계로 다시 그린다.
    fireEvent.change(screen.getByLabelText('기준일'), { target: { value: '2026-04-15' } })
    await waitFor(() =>
      expect(graphApi.neighborhood).toHaveBeenLastCalledWith(
        expect.objectContaining({ focus: 'hub', asOf: '2026-04-15' }),
      ),
    )
    cleanup()

    await mount('/graph?focus=a&to=b&as_of=2026-02-15')
    await waitFor(() =>
      expect(graphApi.path).toHaveBeenCalledWith(
        expect.objectContaining({ source: 'a', target: 'b', asOf: '2026-02-15' }),
      ),
    )
  })

  it('?focus=&to= 로 들어오면 길을 찾아 그리고 길이를 말한다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.path.mockResolvedValue(FOUND)
    await mount('/graph?focus=a&to=b')
    await waitFor(() => expect(screen.getByText('2단계')).toBeInTheDocument())
    expect(graphApi.path).toHaveBeenCalledWith(
      expect.objectContaining({ source: 'a', target: 'b' }),
    )
    expect(graphApi.neighborhood).not.toHaveBeenCalled()
    expect(screen.getByText(/노드 3 · 관계 2/)).toBeInTheDocument()
    expect(screen.getByText(/노드마다 자르지 않고 탐색합니다/)).toBeInTheDocument()
  })

  it('못 찾은 것과 찾다가 멈춘 것을 가른다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    const none = { ...FOUND, found: false, length: null, paths: [], nodes: [], edges: [] }
    graphApi.path.mockResolvedValueOnce(none)
    const { unmount } = await mount('/graph?focus=a&to=b')
    await waitFor(() =>
      expect(screen.getByText(/6단계 안에 연결 경로가 없습니다/)).toBeInTheDocument(),
    )
    unmount()
    graphApi.path.mockResolvedValueOnce({ ...none, truncated: true })
    await mount('/graph?focus=a&to=b')
    await waitFor(() => expect(screen.getByText(/경로가 없다는 뜻이 아닙니다/)).toBeInTheDocument())
  })

  it('「경로 해제」 는 출발점을 중심으로 한 탐색으로 돌아간다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.path.mockResolvedValue(FOUND)
    graphApi.neighborhood.mockResolvedValue({ ...NEIGHBORHOOD, focus: 'a' })
    await mount('/graph?focus=a&to=b')
    await waitFor(() => expect(screen.getByText('2단계')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: '경로 해제' }))
    await waitFor(() =>
      expect(graphApi.neighborhood).toHaveBeenCalledWith(expect.objectContaining({ focus: 'a' })),
    )
  })

  it('노드 상세에서 상대를 찾아 고르면 그 사이의 길을 그린다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.neighborhood.mockResolvedValue(NEIGHBORHOOD)
    graphApi.search.mockResolvedValue([
      { id: 'far', label: '먼 부품', key: 'P-9', type_slug: 'part', type_label: '부품' },
    ])
    graphApi.path.mockResolvedValue({ ...FOUND, source: 'hub', target: 'far' })
    await mount('/graph?focus=hub')
    await waitFor(() => expect(screen.getByText(/노드 3 · 관계 2/)).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: /^연결 경로$/ }))
    await userEvent.type(screen.getByLabelText('연결 경로의 상대 검색'), '먼')
    await userEvent.click(await screen.findByRole('button', { name: /먼 부품/ }))
    await waitFor(() =>
      expect(graphApi.path).toHaveBeenCalledWith(
        expect.objectContaining({ source: 'hub', target: 'far' }),
      ),
    )
  })
})

describe('상한 선택', () => {
  it('단계·이웃·노드 수를 주소에 담고 그대로 묻는다', async () => {
    // **주소가 곧 상태다.** 크게 펼친 그림을 복사해 보내면 상대도 같은 것을 본다.
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.neighborhood.mockResolvedValue(NEIGHBORHOOD)
    await mount('/graph?focus=hub&d=5&fo=300&n=1500')
    await waitFor(() =>
      expect(graphApi.neighborhood).toHaveBeenCalledWith(
        expect.objectContaining({ focus: 'hub', depth: 5, fanout: 300, limit: 1500 }),
      ),
    )
  })

  it('모르는 값은 기본으로 떨어진다 — 주소를 손으로 수정해도 안 깨진다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.neighborhood.mockResolvedValue(NEIGHBORHOOD)
    await mount('/graph?focus=hub&d=99&fo=7&n=999999')
    await waitFor(() =>
      expect(graphApi.neighborhood).toHaveBeenCalledWith(
        expect.objectContaining({ depth: 1, fanout: 30, limit: 300 }),
      ),
    )
  })
})

describe('구조 — 흐름(사케이)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('그물과 흐름을 오가고, 흐름에는 실제 연결된 관계만 싣는다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    await mount()
    await waitFor(() => expect(screen.getByText(/타입 2 · 관계 종류 2/)).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: /흐름으로/ }))
    const plot = await screen.findByTestId('plot')
    const [trace] = JSON.parse(plot.textContent ?? '[]')
    expect(trace.type).toBe('sankey')
    // 부품 → 공급사 5건 하나만. 0건인 「속함」 은 굵기가 없어 그릴 것이 없다.
    expect(trace.link.value).toEqual([5])
    expect(trace.link.label).toEqual(['공급받음'])
    expect(trace.node.label).toEqual(['부품', '공급사'])

    // **돌아갈 길.** 흐름만 보고 갇히면 옆 판의 선택이 안 된다.
    await userEvent.click(screen.getByRole('button', { name: /그물로/ }))
    await waitFor(() => expect(screen.queryByTestId('plot')).not.toBeInTheDocument())
  })

  it('되돌아오는 관계는 빼고, 뺐다고 말한다', async () => {
    // 사케이는 순환을 못 그린다 — 굵은 것을 남기고 되돌아오는 것을 뺀다.
    graphApi.overview.mockResolvedValue({
      ...OVERVIEW,
      edges: [
        ...OVERVIEW.edges,
        {
          relation: 'supplies',
          label: '공급함',
          directed: true,
          src_type: 'vendor',
          dst_type: 'part',
          count: 2,
        },
      ],
    })
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: /흐름으로/ }))
    const plot = await screen.findByTestId('plot')
    const [trace] = JSON.parse(plot.textContent ?? '[]')
    expect(trace.link.value).toEqual([5])
    expect(screen.getByText(/되돌아오는 관계 1개는 흐름에서 뺐습니다/)).toBeInTheDocument()
  })

  it('이을 관계가 하나도 없으면 흐름 단추를 안 낸다', async () => {
    graphApi.overview.mockResolvedValue({
      ...OVERVIEW,
      edges: [OVERVIEW.edges[1]],
      edge_count: 0,
    })
    await mount()
    await waitFor(() => expect(screen.getByText(/타입 2 · 관계 종류 1/)).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /흐름으로/ })).not.toBeInTheDocument()
  })
})

describe('정적 그리기', () => {
  const WHOLE: Whole = {
    seed: 'focus',
    focus: 'hub',
    seed_types: ['part'],
    depth: 1,
    records: false,
    as_of: '2026-11-01',
    max_nodes: 200_000,
    max_edges: 700_000,
    types: [{ slug: 'part', label: '부품', count: 3 }],
    relations: [
      { slug: 'near', label: '가까움', inverse_label: '가까움', directed: true, count: 2 },
    ],
    nodes: {
      ids: ['hub', 'a', 'b'],
      type: [0, 0, 0],
      label: ['허브', '잎A', '잎B'],
      depth: [0, 1, 1],
      parent: [-1, 0, 0],
      x: null,
      y: null,
    },
    edges: { src: [0, 0], dst: [1, 2], rel: [0, 0] },
    over: null,
  }
  const TYPES: Whole = { ...WHOLE, seed: 'types', focus: null }

  function Where() {
    return <output data-testid="where">{useLocation().search}</output>
  }

  async function mountWhole(path: string) {
    const { default: GraphPage } = await import('@/modules/graph/GraphPage')
    return render(
      <MemoryRouter initialEntries={[path]}>
        <GraphPage />
        <Where />
      </MemoryRouter>,
    )
  }

  beforeEach(() => {
    vi.clearAllMocks()
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.hubKeys.mockResolvedValue([])
    layoutWorker.startCluster.mockImplementation((input: { n: number }) => ({
      promise: Promise.resolve(new Float32Array(input.n * 2)),
      cancel: vi.fn(),
    }))
  })

  it('켜면 주소에 남고, 노드마다 자르는 고르개를 감추고 통째 그림을 받는다', async () => {
    graphApi.neighborhood.mockResolvedValue(NEIGHBORHOOD)
    graphApi.whole.mockResolvedValue(WHOLE)
    await mountWhole('/graph?focus=hub')
    await waitFor(() => expect(graphApi.neighborhood).toHaveBeenCalled())
    expect(screen.getByText('노드당 이웃')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('checkbox', { name: '정적 그리기' }))
    // **붙여 넣으면 같은 그림** — 다른 조작처럼 주소에 남는다.
    await waitFor(() => expect(screen.getByTestId('where').textContent).toContain('whole=1'))
    await waitFor(() =>
      expect(graphApi.whole).toHaveBeenCalledWith(
        expect.objectContaining({ focus: 'hub', depth: 1, signal: expect.any(AbortSignal) }),
      ),
    )
    // 노드마다 자르지 않으니 그 고르개는 뜻이 없다.
    expect(screen.queryByText('노드당 이웃')).not.toBeInTheDocument()
    expect(screen.queryByText('그림에 세울 노드')).not.toBeInTheDocument()
    // 정직한 줄 — 무엇을 전부 그렸는지 말한다. 단계 배치라 자리가 노드 수만큼 있다.
    expect(
      await screen.findByText(/노드 3 · 선 2 — 1단계 안을 전부 그렸습니다/),
    ).toBeInTheDocument()
    expect(screen.getByTestId('whole-canvas')).toHaveAttribute('data-placed', '3')
    expect(layoutWorker.startCluster).not.toHaveBeenCalled()
  })

  it('끄거나 떠나면 받던 그림 요청을 끊는다 — 서버가 질의를 멈추게(ADR 0048)', async () => {
    // 오래 걸리는 그림 — 답이 오지 않는다.
    graphApi.whole.mockReturnValue(new Promise(() => {}))
    const { unmount } = await mountWhole('/graph?focus=hub&whole=1')
    await waitFor(() => expect(graphApi.whole).toHaveBeenCalledTimes(1))
    const first = graphApi.whole.mock.calls[0][0].signal as AbortSignal
    expect(first.aborted).toBe(false)
    await userEvent.click(screen.getByRole('checkbox', { name: '정적 그리기' }))
    await waitFor(() => expect(first.aborted).toBe(true))

    await userEvent.click(screen.getByRole('checkbox', { name: '정적 그리기' }))
    await waitFor(() => expect(graphApi.whole).toHaveBeenCalledTimes(2))
    const second = graphApi.whole.mock.calls[1][0].signal as AbortSignal
    unmount()
    expect(second.aborted).toBe(true)
  })

  it('배치의 기본값 — 객체 하나는 단계, 타입 전부는 무리이고 단계는 쓸 수 없다', async () => {
    graphApi.whole.mockResolvedValue(WHOLE)
    const { unmount } = await mountWhole('/graph?focus=hub&whole=1')
    expect(await screen.findByRole('radio', { name: '단계' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    expect(screen.getByRole('radio', { name: '무리' })).toHaveAttribute('aria-checked', 'false')
    unmount()

    graphApi.whole.mockResolvedValue(TYPES)
    await mountWhole('/graph?type=part&whole=1&lay=rings')
    // 주소에 「단계」 가 적혀 와도 타입 전부에는 출발점이 없어 무리로 그린다.
    expect(await screen.findByRole('radio', { name: '무리' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    expect(screen.getByRole('radio', { name: '단계' })).toBeDisabled()
    expect(screen.getByText(/타입 전부에는 출발점이 없어/)).toBeInTheDocument()
    await waitFor(() =>
      expect(graphApi.whole).toHaveBeenLastCalledWith(
        expect.objectContaining({ types: ['part'], depth: 1 }),
      ),
    )
    await waitFor(() => expect(layoutWorker.startCluster).toHaveBeenCalled())
  })

  it('무리 배치는 진행률을 보이며 자리를 잡는다', async () => {
    graphApi.whole.mockResolvedValue(TYPES)
    layoutWorker.startCluster.mockImplementation(
      (_input: unknown, onProgress: (fraction: number) => void) => {
        onProgress(0.4)
        return { promise: new Promise(() => {}), cancel: vi.fn() }
      },
    )
    await mountWhole('/graph?type=part&whole=1')
    expect(await screen.findByText(/자리 잡는 중 40%/)).toBeInTheDocument()
    expect(screen.queryByTestId('whole-canvas')).not.toBeInTheDocument()
  })

  it('상한을 넘으면 그리지 않고 타입별 수와 좁히는 길을 말한다', async () => {
    graphApi.whole.mockResolvedValue({
      ...WHOLE,
      types: [],
      relations: [],
      nodes: { ids: [], type: [], label: [], depth: [], parent: [] },
      edges: { src: [], dst: [], rel: [] },
      over: {
        what: 'nodes',
        count: 200_001,
        exact: false,
        limit: 200_000,
        types: [
          { slug: 'vendor', label: '공급사', count: 200_000 },
          { slug: 'part', label: '부품', count: 1 },
        ],
        layers: [1, 200_000],
        relations: [],
      },
    })
    await mountWhole('/graph?focus=hub&whole=1')
    expect(
      await screen.findByText(/노드 200,001개 이상 — 정적 그리기 상한\(200,000개\)을 넘습니다/),
    ).toBeInTheDocument()
    const table = screen.getByRole('table')
    expect(table).toHaveTextContent('공급사200,000')
    expect(screen.getByText(/1단계 200,000\(세다가 멈춤\)/)).toBeInTheDocument()
    expect(screen.queryByTestId('whole-canvas')).not.toBeInTheDocument()

    // 좁히는 길 — 가장 많은 이웃 타입을 제외하고 다시(「이웃 타입」 은 고른 것만 싣는 필터다).
    await userEvent.click(screen.getByRole('button', { name: '「공급사」 제외하고 다시 그리기' }))
    await waitFor(() =>
      expect(graphApi.whole).toHaveBeenLastCalledWith(
        expect.objectContaining({ neighborTypes: ['part'] }),
      ),
    )
    await userEvent.click(screen.getByRole('button', { name: /정적 그리기 해제/ }))
    await waitFor(() => expect(screen.getByTestId('where').textContent).not.toContain('whole=1'))
  })

  it('노드를 고르면 상세가 나오고 「여기서 확장」 은 없다 — 이미 전부다', async () => {
    graphApi.whole.mockResolvedValue(WHOLE)
    await mountWhole('/graph?focus=hub&whole=1')
    await userEvent.click(await screen.findByRole('button', { name: '그림 노드 잎A' }))
    expect(await screen.findByText('그림 안의 선 1개')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /여기서 확장/ })).not.toBeInTheDocument()
  })

  /** 기록 1.2만여 건이 판매 모델 · 센터 두 축에 함께 걸린 큰 그림(무리 배치가 가운데로 모으는 모양). */
  function bigRecords(records: number): Whole {
    const ids = ['sku1', 'center1', ...Array.from({ length: records }, (_, k) => `r${k}`)]
    const src: number[] = []
    const dst: number[] = []
    for (let k = 0; k < records; k += 1) {
      src.push(2 + k, 2 + k)
      dst.push(0, 1)
    }
    return {
      ...WHOLE,
      seed: 'types',
      focus: null,
      seed_types: ['svc'],
      types: [
        { slug: 'svc', label: '서비스', count: records },
        { slug: 'sku', label: '판매 모델', count: 1 },
        { slug: 'center', label: '센터', count: 1 },
      ],
      nodes: {
        ids,
        type: [1, 2, ...Array.from({ length: records }, () => 0)],
        label: ids,
        depth: ids.map((_, at) => (at < 2 ? 1 : 0)),
        parent: ids.map(() => -1),
        x: null,
        y: null,
      },
      edges: { src, dst, rel: src.map(() => 0) },
    }
  }
  const HUB_KEYS = [
    {
      type_slug: 'svc',
      type_label: '서비스',
      mode: 'auto',
      key: 'sku',
      auto_key: 'sku',
      used_key: 'sku',
      in_layout: true,
      stale: false,
      candidates: [
        {
          key: 'sku',
          label: '판매 모델',
          target_label: '판매 모델',
          target_types: ['sku'],
          required: true,
          hubs: 1,
        },
      ],
    },
  ]

  it('기록 씨앗이 여러 축에 걸린 큰 무리 그림이면 「가운데가 뭉친다」 와 허브 칸 하나로 좁히는 단추를 낸다', async () => {
    window.localStorage.clear()
    graphApi.whole.mockResolvedValue(bigRecords(12_500))
    graphApi.hubKeys.mockResolvedValue(HUB_KEYS)
    await mountWhole('/graph?type=svc&whole=1')
    expect(
      await screen.findByText(/기록이 여러 축에 함께 걸려 가운데가 뭉칩니다/),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '판매 모델만 보기' }))
    await waitFor(() => expect(screen.getByTestId('where').textContent).toContain('ty=sku'))
  })

  it('작은 그림 · 축 하나 · 허브 칸이 없는 타입에서는 단추가 없거나 뜨지 않는다', async () => {
    window.localStorage.clear()
    graphApi.hubKeys.mockResolvedValue(HUB_KEYS)
    graphApi.whole.mockResolvedValue({ ...TYPES, seed_types: ['svc'] })
    const { unmount } = await mountWhole('/graph?type=svc&whole=1')
    await waitFor(() => expect(screen.getByTestId('whole-canvas')).toBeInTheDocument())
    expect(screen.queryByText(/가운데가 뭉칩니다/)).not.toBeInTheDocument()
    unmount()

    // 허브 칸이 없는 기록 타입 — 단추 없이 「하나만 골라 보세요」.
    graphApi.hubKeys.mockResolvedValue([{ ...HUB_KEYS[0], key: null, mode: 'none' }])
    graphApi.whole.mockResolvedValue(bigRecords(12_500))
    const second = await mountWhole('/graph?type=svc&whole=1')
    expect(await screen.findByText(/「이웃 타입」 에서 하나만 골라 보세요/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /만 보기/ })).not.toBeInTheDocument()
    // 닫으면 그 씨앗에서는 다시 안 뜬다.
    await userEvent.click(screen.getByRole('button', { name: '안내 닫기' }))
    expect(screen.queryByText(/가운데가 뭉칩니다/)).not.toBeInTheDocument()
    second.unmount()
    await mountWhole('/graph?type=svc&whole=1')
    await waitFor(() => expect(screen.getByTestId('whole-canvas')).toBeInTheDocument())
    expect(screen.queryByText(/가운데가 뭉칩니다/)).not.toBeInTheDocument()
  })

  it('연결 경로에는 쓰지 않는다 — 경로 자체가 그림이다', async () => {
    graphApi.path.mockResolvedValue({
      source: 'a',
      target: 'b',
      found: false,
      length: null,
      paths: [],
      nodes: [],
      edges: [],
      max_depth: 6,
      visited: 2,
      truncated: false,
    } satisfies PathResult)
    await mountWhole('/graph?focus=a&to=b&whole=1')
    expect(await screen.findByRole('checkbox', { name: '정적 그리기' })).toBeDisabled()
    await waitFor(() => expect(graphApi.path).toHaveBeenCalled())
    expect(graphApi.whole).not.toHaveBeenCalled()
  })
})

describe('전체 지도', () => {
  const MAP: MapData = {
    ready: true,
    layout: {
      version: 3,
      kind: 'append',
      laid_at: '2026-11-01T04:00:00+09:00',
      full_version: 2,
      full_at: '2026-10-31T12:00:00+09:00',
      by_timer: false,
      masters: 2,
      moved_p50: 0.012,
      moved_p95: 0.03,
    },
    counted_at: '2026-11-01T11:00:00+09:00',
    scale: 1,
    bbox: [-50, -50, 150, 50],
    max_points: 50,
    types: [
      { slug: 'part', label: '부품', record: false, count: 2 },
      {
        slug: 'svc',
        label: '서비스',
        record: true,
        hub_key: 'model',
        hub_label: '모델',
        count: 100,
      },
    ],
    masters: { ids: ['m1', 'm2'], type: [0, 0], label: ['모델1', '모델2'], x: [0, 100], y: [0, 0] },
    discs: {
      x: [0],
      y: [0],
      r: [10],
      hub: [0],
      slot: [-1],
      count_disc: [0],
      count_type: [1],
      count_n: [100],
    },
    unplaced: [],
    relations: [
      { slug: 'ref:part.next', label: '다음', inverse_label: '', directed: true, count: 1 },
      { slug: 'ref:svc.model', label: '모델', inverse_label: '', directed: true, count: 0 },
    ],
    // 모델1 → 모델2(기준정보끼리) — 늘 긋는다.
    lines: { src: [0], dst: [1], rel: [0] },
    job: null,
    last_error: null,
    can_relayout: true,
    drift: [],
  }

  function Where() {
    return <output data-testid="where">{useLocation().search}</output>
  }

  async function mountMap(path: string) {
    const { default: GraphPage } = await import('@/modules/graph/GraphPage')
    return render(
      <MemoryRouter initialEntries={[path]}>
        <GraphPage />
        <Where />
      </MemoryRouter>,
    )
  }

  beforeEach(() => {
    vi.clearAllMocks()
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.map.mockResolvedValue(MAP)
    graphApi.mapLinks.mockResolvedValue({ hub: 'm1', items: [], withheld: false })
    // 「여기서 정적 그리기」 로 가면 탐색 · 정적 그리기가 받는다 — 앞 시험이 넣어 둔 값에 기대지 않게.
    graphApi.neighborhood.mockResolvedValue(NEIGHBORHOOD)
    graphApi.whole.mockReturnValue(new Promise(() => {}))
  })

  it('시작점 없이 「전체 지도」 로 들어가고, 판 · 기준 시각 · 「자리는 뜻이 없다」 를 적는다', async () => {
    await mountMap('/graph')
    await userEvent.click(screen.getByRole('tab', { name: '탐색' }))
    await userEvent.click(screen.getByRole('button', { name: '전체 지도' }))
    await waitFor(() => expect(screen.getByTestId('where').textContent).toContain('map=1'))
    await waitFor(() => expect(graphApi.map).toHaveBeenCalled())
    const canvas = await screen.findByTestId('whole-canvas')
    // 지도 좌표 고정 모드 — 판의 틀을 그대로 준다(자리가 바뀌어도 다시 맞추지 않는다).
    expect(canvas).toHaveAttribute('data-frame', '-50,-50,150,50')
    // 처음 화면은 내용(기준정보 · 원반 반지름)에 맞춘다 — 틀은 비어 있는 「미지정」 자리까지 잡아 넓다.
    expect(canvas).toHaveAttribute('data-home', '-10,-10,100,10')
    expect(screen.getByText(/판 3 · 덧붙임/)).toBeInTheDocument()
    expect(screen.getByText(/원반 안의 자리는 뜻이 없습니다/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /서비스/ })).toBeChecked()
    expect(screen.getByRole('button', { name: /전체 다시 배치/ })).toBeInTheDocument()
  })

  it('확대해 화면의 기대 수가 상한 이하면 상자의 점을 받아 그린다', async () => {
    graphApi.mapPoints.mockResolvedValue({
      box: [-3, -3, 3, 3],
      expected: 5,
      over: false,
      limit: 50,
      count: 2,
      types: ['svc'],
      ids: ['r1', 'r2'],
      x: [0, 1],
      y: [0, 1],
      type: [0, 0],
      labels: ['건1', '건2'],
      skipped: [],
      lines_up_to: 5000,
      lines: null,
    })
    camera.view = { x0: -2, y0: -2, x1: 2, y1: 2 }
    await mountMap('/graph?map=1')
    await userEvent.click(await screen.findByRole('button', { name: '카메라 움직임' }))
    await waitFor(() => expect(graphApi.mapPoints).toHaveBeenCalled())
    const [box, types] = graphApi.mapPoints.mock.calls[0] as [Record<string, number>, string[]]
    expect(types).toEqual(['svc'])
    expect(box.x0).toBeLessThanOrEqual(-2)
    await waitFor(() =>
      expect(screen.getByTestId('whole-canvas')).toHaveAttribute('data-extra', '2'),
    )
    expect(screen.getByText(/점 2개 — 이 상자의 기록 전부/)).toBeInTheDocument()
    expect(
      screen.getByText(/기록의 선은 점이 5,000개 이하로 확대하면 긋습니다/),
    ).toBeInTheDocument()
  })

  it('기준정보끼리의 선은 늘 긋고, 범례에서 종류를 끄고 켠다', async () => {
    await mountMap('/graph?map=1')
    const canvas = await screen.findByTestId('whole-canvas')
    expect(canvas).toHaveAttribute('data-links', '0>1')
    // 참조 칸은 「타입 → 칸」 — 같은 이름의 타입과 헷갈리지 않게. 안 그은 종류는 범례에 없다.
    await userEvent.click(screen.getByRole('button', { name: '선 범례 부품 → 다음' }))
    expect(canvas).toHaveAttribute('data-links', '')
    expect(screen.queryByRole('button', { name: /선 범례 서비스 → 모델/ })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: '선 범례 부품 → 다음 (끔)' }))
    expect(canvas).toHaveAttribute('data-links', '0>1')
  })

  it('확대한 점의 선 — 끝이 점이면 점 번호, 기준정보면 그 기준정보 번호로 긋는다', async () => {
    graphApi.mapPoints.mockResolvedValue({
      box: [-3, -3, 3, 3],
      expected: 5,
      over: false,
      limit: 50,
      count: 2,
      types: ['svc'],
      ids: ['r1', 'r2'],
      x: [0, 1],
      y: [0, 1],
      type: [0, 0],
      labels: ['건1', '건2'],
      skipped: [],
      lines_up_to: 5000,
      // 건1 → 모델1(점 수 2 다음 번호 = masters[0]), 건2 → 건1.
      lines: {
        src: [0, 1],
        dst: [2, 0],
        rel: [0, 1],
        kinds: ['ref:svc.model', 'rel'],
        masters: ['m1'],
      },
    })
    camera.view = { x0: -2, y0: -2, x1: 2, y1: 2 }
    await mountMap('/graph?map=1')
    await userEvent.click(await screen.findByRole('button', { name: '카메라 움직임' }))
    // 기준정보는 0 · 1, 점은 2 · 3. 주 참조(허브 칸 「모델」)의 선은 처음엔 꺼 둔다 — 원반 자리가
    // 이미 말한다.
    await waitFor(() =>
      expect(screen.getByTestId('whole-canvas')).toHaveAttribute('data-links', '0>1 3>2'),
    )
    expect(screen.queryByText(/기록의 선은 점이/)).toBeNull()
    // 켜면 건1 → 모델1(기준정보 번호 0)이다.
    await userEvent.click(screen.getByRole('button', { name: '선 범례 서비스 → 모델 (끔)' }))
    expect(screen.getByTestId('whole-canvas')).toHaveAttribute('data-links', '0>1 2>0 3>2')
  })

  it('기대 수가 상한을 넘으면 점을 안 받고 「더 확대」 를 말한다', async () => {
    camera.view = { x0: -50, y0: -50, x1: 150, y1: 50 }
    await mountMap('/graph?map=1')
    await userEvent.click(await screen.findByRole('button', { name: '카메라 움직임' }))
    expect(
      await screen.findByText(/이 화면에 기록 약 100건 — 더 확대하면 점으로 그립니다\(50건까지\)/),
    ).toBeInTheDocument()
    expect(graphApi.mapPoints).not.toHaveBeenCalled()
  })

  it('점을 고르면 상세에 「여기서 정적 그리기」 가 있고 누르면 그 객체에서 정적 그리기로 간다', async () => {
    await mountMap('/graph?map=1')
    await userEvent.click(await screen.findByRole('button', { name: '그림 노드 모델1' }))
    await userEvent.click(await screen.findByRole('button', { name: /여기서 정적 그리기/ }))
    await waitFor(() => {
      const where = screen.getByTestId('where').textContent ?? ''
      expect(where).toContain('focus=m1')
      expect(where).toContain('whole=1')
      expect(where).not.toContain('map=1')
    })
  })

  it('허브를 고르면 주 참조 밖의 연결을 받는다', async () => {
    graphApi.mapLinks.mockResolvedValue({
      hub: 'm1',
      items: [{ id: 'm2', label: '모델2', type_slug: 'part', count: 7, x: 100, y: 0 }],
      withheld: false,
      counted_at: '2026-10-31T12:00:00+09:00',
    })
    await mountMap('/graph?map=1')
    await userEvent.click(await screen.findByRole('button', { name: '그림 노드 모델1' }))
    await waitFor(() => expect(graphApi.mapLinks).toHaveBeenCalledWith('m1'))
    expect(await screen.findByText('7건')).toBeInTheDocument()
  })

  it('정적 그리기 거절 안내에서 「지도에서 보기」 — 거절된 타입만 켜고 그 객체로 간다', async () => {
    graphApi.whole.mockResolvedValue({
      seed: 'focus',
      focus: 'm1',
      seed_types: ['part'],
      depth: 1,
      records: true,
      max_nodes: 200_000,
      max_edges: 700_000,
      types: [],
      relations: [],
      nodes: { ids: [], type: [], label: [], depth: [], parent: [] },
      edges: { src: [], dst: [], rel: [] },
      over: {
        what: 'nodes',
        count: 200_001,
        exact: false,
        limit: 200_000,
        types: [
          { slug: 'svc', label: '서비스', count: 200_000 },
          { slug: 'part', label: '부품', count: 1 },
        ],
        layers: [1, 200_000],
        relations: [],
      },
    } satisfies Whole)
    graphApi.mapLocate.mockResolvedValue({
      id: 'm1',
      kind: 'master',
      x: 0,
      y: 0,
      r: null,
      label: '모델1',
      type_slug: 'part',
      reason: null,
    })
    await mountMap('/graph?focus=m1&whole=1')
    await userEvent.click(await screen.findByRole('button', { name: /지도에서 보기/ }))
    await waitFor(() => {
      const where = decodeURIComponent(screen.getByTestId('where').textContent ?? '')
      expect(where).toContain('map=1')
      expect(where).toContain('mty=svc,part')
      expect(where).toContain('at=m1')
    })
    await waitFor(() => expect(graphApi.mapLocate).toHaveBeenCalledWith('m1'))
    // 지도에 없는 「부품」 은 기록 타입이 아니라 버리고, 서비스만 켠다.
    expect(await screen.findByRole('checkbox', { name: /서비스/ })).toBeChecked()
  })
})
