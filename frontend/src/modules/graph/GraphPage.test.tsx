/**
 * 그래프 화면이 지키는 것 — **잘린 것을 말하나, 「전부」 단추가 없나.**
 *
 * 캔버스 자체는 여기서 안 그린다(happy-dom 에는 canvas 가 없다). 보는 것은 서버 응답이
 * 화면의 말로 바뀌는 자리다: 상한 안내, 「+N」 의 근거, 빈 상태의 이유.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Neighborhood, Overview, Subgraph } from '@/modules/graph/api'

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
}))
vi.mock('@/modules/graph/api', () => ({ graphApi }))
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

  it('구조 그림은 타입·관계 종류·객체 수를 요약한다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    await mount()
    await waitFor(() =>
      expect(screen.getByText(/타입 2 · 관계 종류 2 · 객체 9 · 관계 5/)).toBeInTheDocument(),
    )
    // **「전부 보기」 단추는 없다.** 전부는 이 그림이고, 자세한 것은 탐색에서 한 걸음씩.
    expect(screen.queryByRole('button', { name: /전부 보기|전체 보기/ })).not.toBeInTheDocument()
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
    expect(screen.getByText('시작점을 고르세요')).toBeInTheDocument()
    expect(screen.getByText(/모든 타입을 한 번에 그리는 단추는 없습니다/)).toBeInTheDocument()
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
    // 시작점이 골라져 있고, 화면에 없는 관계의 수(5 - 2)를 근거로 「펼치기」 가 선다.
    expect(screen.getByText('화면에 없는 것 3')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '여기서 펼치기 (+3)' })).toBeEnabled()
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
    await userEvent.click(screen.getByRole('button', { name: '여기서 펼치기 (+3)' }))
    // 3 + 1 노드, 2 + 1 관계 — 앞서 든 잎A·잎B 가 사라지지 않는다.
    await waitFor(() => expect(screen.getByText(/노드 4 · 관계 3/)).toBeInTheDocument())
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

  it('구조에서 타입을 고르면 그 인스턴스 전부를 그릴 수 있다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.subgraph.mockResolvedValue(SUBGRAPH)
    await mount()
    await waitFor(() => expect(screen.getByText(/타입 2 · 관계 종류 2/)).toBeInTheDocument())
    // 캔버스가 없으니(happy-dom) 노드를 누를 수 없다 — 훑기 쪽으로 같은 길을 밟는다.
    objectApi.list.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 })
    await userEvent.click(screen.getByRole('tab', { name: '탐색' }))
    await userEvent.click(await screen.findByRole('button', { name: /^부품 7$/ }))
    await userEvent.click(screen.getByRole('button', { name: '부품 전부 그리기 (7)' }))
    await waitFor(() => expect(screen.getByText(/7개 중 1–2/)).toBeInTheDocument())
  })

  it('타입에서 훑어 시작점을 고른다 — 검색어를 모르는 사람의 길', async () => {
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

  it('타입을 여럿 고르면 한 그림에 전부 그린다', async () => {
    graphApi.overview.mockResolvedValue(OVERVIEW)
    graphApi.subgraph.mockResolvedValue({ ...SUBGRAPH, total: 9 })
    objectApi.list.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 })
    await mount()
    await userEvent.click(screen.getByRole('tab', { name: '탐색' }))
    await userEvent.click(await screen.findByRole('button', { name: /^부품 7$/ }))
    await userEvent.click(screen.getByRole('button', { name: /^공급사 2$/ }))
    await userEvent.click(screen.getByRole('button', { name: '고른 2개 타입 전부 그리기 (9)' }))
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
    await userEvent.click(screen.getByPlaceholderText(/그림 안에서 찾기/))
    await userEvent.keyboard('r')
    expect(graphApi.neighborhood).toHaveBeenCalledTimes(2)

    // ESC — 검색 칸을 비우고 나온 뒤, 한 번 더 누르면 선택이 풀린다.
    await userEvent.keyboard('{Escape}')
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /여기서 펼치기/ })).not.toBeInTheDocument(),
    )
  })

  it('조작 상태가 주소에 남는다 — 붙여 넣으면 같은 그림', async () => {
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

describe('상한 고르기', () => {
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

  it('모르는 값은 기본으로 떨어진다 — 주소를 손으로 고쳐도 안 깨진다', async () => {
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

  it('그물과 흐름을 오가고, 흐름에는 실제로 걸린 관계만 싣는다', async () => {
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
