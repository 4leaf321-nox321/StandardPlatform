/**
 * 통계가 지키는 것 — **목록과 같은 필터를 넘기고, 합이 안 맞으면 그 차이를 적고,
 * 막대를 누르면 원래 값으로 거른다.**
 *
 * 그림 자체는 `shared/charts` 의 몫이라 여기서는 **무엇을 넘기나**만 본다. 그래서
 * 차트를 가짜로 바꿔 끼우고, 넘어온 행과 `onPick` 을 들여다본다.
 */

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const objectApi = vi.hoisted(() => ({
  summary: vi.fn(),
  exportSummary: vi.fn(),
}))
const viewApi = vi.hoisted(() => ({ create: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi, viewApi }))

// 「홈에 올리기」 는 부서 관리자에게만 보인다 — 누구로 로그인했는지가 이 시험의 조건이다.
const auth = vi.hoisted(() => ({
  user: {
    home_workspace_slug: 'cae',
    memberships: [{ slug: 'cae', role: 'manager', name: '해석팀', path: '해석팀' }],
  } as Record<string, unknown>,
}))
vi.mock('@/shared/auth/AuthContext', () => ({ useAuth: () => auth }))

/** 가짜 차트 — 행마다 단추 하나. 누르면 진짜 차트가 하듯 **행 그대로** 넘긴다. */
const charts = vi.hoisted(() => ({
  rows: [] as Record<string, unknown>[],
  series: [] as { key: string; label?: string }[],
}))
vi.mock('@/shared/charts', () => ({
  colorFor: () => '#2563eb',
  LazyPlot: ({ title }: { title?: string }) => <div data-testid="plot">{title}</div>,
  Chart: ({
    data,
    x,
    series,
    onPick,
    title,
  }: {
    data: Record<string, unknown>[]
    x: string
    series: { key: string; label?: string }[]
    onPick?: (row: Record<string, unknown>) => void
    title?: string
  }) => {
    charts.rows = data
    charts.series = series
    return (
      <div aria-label={title}>
        {data.map((row) => (
          <button
            key={String(row[x])}
            type="button"
            disabled={!onPick}
            onClick={() => onPick?.(row)}
          >
            {String(row[x])}
          </button>
        ))}
      </div>
    )
  },
}))

const BASE = {
  group_field: 'properties.grade',
  group_label: '등급',
  order: 'desc',
  split_field: '',
  split_label: '',
  splits: [],
  other_splits: 0,
  metric: 'count',
  metric_field: null,
  metric_label: '건수',
  total: 5,
  buckets: [
    { key: 'A', label: 'A', count: 2, value: null, parts: [] },
    { key: 'B', label: 'B', count: 1, value: null, parts: [] },
    { key: null, label: '(비어 있음)', count: 2, value: null, parts: [] },
  ],
  other_groups: 0,
  other_count: 0,
  group_options: [
    { field: 'status', label: '상태', kind: 'fixed' },
    { field: 'properties.grade', label: '등급', kind: 'enum' },
  ],
  metric_options: [{ field: 'properties.weight', label: '무게', kind: 'number' }],
}

async function panel(data: object) {
  objectApi.summary.mockResolvedValue(data)
  const { SummaryPanel, DEFAULT_SUMMARY } = await import('@/modules/objects/SummaryPanel')
  const onPick = vi.fn()
  const onSettings = vi.fn()
  // 설정은 **화면이 들고 있다** — 여기서는 호스트 노릇만 한다.
  render(
    // 올린 뒤의 「홈에서 보기」 가 링크라 라우터가 필요하다.
    <MemoryRouter>
      <SummaryPanel
        typeSlug="part"
        query={{ q: '볼트' }}
        settings={DEFAULT_SUMMARY}
        onSettings={onSettings}
        onPick={onPick}
        onClose={vi.fn()}
      />
    </MemoryRouter>,
  )
  await screen.findByText(/조건에 맞는 전체/)
  return { onPick, onSettings }
}

describe('통계', () => {
  it('목록과 같은 필터를 넘기고, 빈 값도 한 칸으로 그린다', async () => {
    await panel(BASE)
    expect(objectApi.summary).toHaveBeenCalledWith(
      'part',
      { q: '볼트' },
      { groupBy: 'status', splitBy: null, metric: 'count', metricField: null, order: 'desc' },
    )
    // **빈 값을 숨기면 막대의 합이 전체와 안 맞고, 그 차이는 화면 어디에도 안 적힌다.**
    expect(charts.rows.map((one) => one.name)).toEqual(['A', 'B', '(비어 있음)'])
    expect(screen.getByText(/조건에 맞는 전체 5건/)).toBeInTheDocument()
  })

  it('접힌 그룹이 있으면 몇 종류 몇 건이 빠졌는지 적는다', async () => {
    await panel({ ...BASE, total: 40, other_groups: 7, other_count: 35 })
    expect(screen.getByText(/7종류 35건은 접혔습니다/)).toBeInTheDocument()
  })

  it('막대를 클릭하면 **원래 값**으로 거른다 — 빈 칸은 거를 값이 없다', async () => {
    const { onPick } = await panel(BASE)
    await userEvent.click(screen.getByRole('button', { name: 'A' }))
    expect(onPick).toHaveBeenCalledWith('properties.grade', 'A')

    onPick.mockClear()
    await userEvent.click(screen.getByRole('button', { name: '(비어 있음)' }))
    expect(onPick).not.toHaveBeenCalled()
  })

  it('거를 수 없는 기준이면 아예 안 누르게 한다', async () => {
    await panel({ ...BASE, group_field: 'workspace', group_label: '소유 부서' })
    expect(screen.getByRole('button', { name: 'A' })).toBeDisabled()
  })

  it('셀 숫자 칸이 없으면 합계·평균을 못 고른다', async () => {
    await panel({ ...BASE, metric_options: [] })
    await userEvent.click(screen.getByRole('combobox', { name: '집계 방식' }))
    expect(await screen.findByRole('option', { name: '합계' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
  })

  it('그림 모양을 바꾸면 **호스트에게 알린다** — 그래야 뷰에 담긴다', async () => {
    const { onSettings } = await panel(BASE)
    const pie = screen.getByRole('button', { name: '원' })
    expect(pie).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(pie)
    expect(onSettings).toHaveBeenCalledWith(expect.objectContaining({ chart: 'pie' }))
  })
})

describe('홈 게시', () => {
  it('부서 관리자에게는 단추가, 아닌 사람에게는 누가 올리는지가 보인다', async () => {
    // **단추만 없으면 그 기능이 있는 줄도 모른다.**
    await panel(BASE)
    expect(screen.getByRole('button', { name: '홈 게시' })).toBeInTheDocument()

    cleanup()
    auth.user = {
      home_workspace_slug: 'cae',
      memberships: [{ slug: 'cae', role: 'member', name: '해석팀', path: '해석팀' }],
    }
    await panel(BASE)
    expect(screen.queryByRole('button', { name: '홈 게시' })).not.toBeInTheDocument()
    expect(screen.getByText('부서 관리자가 홈에 게시합니다')).toBeInTheDocument()
  })

  it('한 번에 부서 뷰로 저장하고 홈에 올린다', async () => {
    // 요청 둘로 나누면 저장은 됐는데 안 올라간 상태가 생기고, 그때 사람은 자기가
    // 무엇을 빠뜨렸는지 모른다.
    auth.user = {
      home_workspace_slug: 'cae',
      memberships: [{ slug: 'cae', role: 'manager', name: '해석팀', path: '해석팀' }],
    }
    viewApi.create.mockResolvedValue({ id: 'v9' })
    await panel(BASE)
    await userEvent.click(screen.getByRole('button', { name: '홈 게시' }))

    // 이름이 미리 채워진다 — 무엇을 올리는지 사람이 이미 안다.
    const name = await screen.findByPlaceholderText(/홈에 뜰 이름/)
    expect(name).toHaveValue('등급별 건수')
    await userEvent.click(screen.getByRole('button', { name: /홈 게시/ }))

    await waitFor(() =>
      expect(viewApi.create).toHaveBeenCalledWith(
        'part',
        expect.objectContaining({
          name: '등급별 건수',
          workspace_slug: 'cae',
          on_home: true,
          summary: expect.objectContaining({ group_by: 'status' }),
        }),
      ),
    )
    expect(await screen.findByRole('link', { name: '홈에서 보기' })).toBeInTheDocument()
  })
})

describe('세부 기준', () => {
  const SPLIT = {
    ...BASE,
    split_field: 'properties.area',
    split_label: '지역',
    splits: ['영남', '수도권'],
    buckets: [
      {
        key: 'A',
        label: 'A',
        count: 3,
        value: null,
        parts: [
          { key: '영남', label: '영남', count: 2, value: null },
          { key: '수도권', label: '수도권', count: 1, value: null },
        ],
      },
      // 이 칸에는 영남이 없다 — **0 으로 채워져야** 쌓은 막대의 자리가 안 빈다.
      {
        key: 'B',
        label: 'B',
        count: 1,
        value: null,
        parts: [{ key: '수도권', label: '수도권', count: 1, value: null }],
      },
    ],
  }

  it('계열의 차례를 서버에서 받고, 없는 계열은 0 으로 채운다', async () => {
    await panel(SPLIT)
    expect(charts.series.map((one) => one.key)).toEqual(['영남', '수도권'])
    expect(charts.rows[1]).toMatchObject({ name: 'B', 영남: 0, 수도권: 1 })
  })

  it('세부 기준 값이 잘렸으면 그렇다고 적는다', async () => {
    await panel({ ...SPLIT, other_splits: 4 })
    expect(screen.getByText(/세부 기준 값 4가지는 빠졌습니다/)).toBeInTheDocument()
  })

  it('세부 기준 없이 히트맵을 선택하면 세부 기준이 필요하다고 말한다', async () => {
    // 세부 기준 없는 히트맵은 색칠한 막대 하나일 뿐이다.
    const { onSettings } = await panel(BASE)
    await userEvent.click(screen.getByRole('button', { name: '히트맵' }))
    expect(onSettings).toHaveBeenCalledWith(expect.objectContaining({ chart: 'heatmap' }))
  })
})

describe('개별 순위', () => {
  it('이름을 기준으로 건수를 세면 그것이 순위가 아님을 말한다', async () => {
    // 막대가 전부 1 인 그림을 주고 아무 말도 안 하면, 사람은 기능이 고장난 줄 안다.
    await panel({ ...BASE, group_field: 'label', group_label: '이름' })
    expect(screen.getByText(/숫자 칸의 합·평균으로/)).toBeInTheDocument()
  })

  it('차례를 뒤집으면 「가장 낮은 것」 을 찾는다', async () => {
    const { onSettings } = await panel(BASE)
    await userEvent.click(screen.getByRole('button', { name: '순서 변경' }))
    expect(onSettings).toHaveBeenCalledWith(expect.objectContaining({ order: 'asc' }))
  })

  it('내보내기는 그림과 같은 조건·기준으로 파일을 받는다', async () => {
    // **화면과 같은 숫자여야 한다** — 따로 만들면 「화면에는 12 인데 파일에는 15」.
    objectApi.exportSummary.mockResolvedValue(undefined)
    await panel(BASE)
    await userEvent.click(screen.getByRole('button', { name: /내보내기/ }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /Excel/ }))
    await waitFor(() =>
      expect(objectApi.exportSummary).toHaveBeenCalledWith(
        'part',
        { q: '볼트' },
        { groupBy: 'status', splitBy: null, metric: 'count', metricField: null, order: 'desc' },
        'xlsx',
        'part-등급별.xlsx',
      ),
    )
  })

  it('여러 값 기준이면 막대의 합이 전체보다 클 수 있다고 적고, 고르개에 표시한다', async () => {
    await panel({
      ...BASE,
      overlap: true,
      group_options: [
        ...BASE.group_options,
        { field: 'properties.field', label: '해석 분야', kind: 'enum', multi: true },
      ],
    })
    expect(screen.getByText(/한 행이 여러 막대에 들어갑니다/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('combobox', { name: '기준' }))
    expect(await screen.findByRole('option', { name: '해석 분야 (여러 값)' })).toBeInTheDocument()
  })

  it('이어진 것 너머의 기준은 제목 아래로 서서 같은 이름이어도 갈린다', async () => {
    await panel({
      ...BASE,
      group_options: [
        ...BASE.group_options,
        {
          field: 'ref.vendor.country',
          label: '개발사 › 국가',
          kind: 'enum',
          heading: '개발사 (시뮬레이션 기업)',
        },
        {
          field: 'out.developed_by.country',
          label: '개발사 › 국가',
          kind: 'enum',
          heading: '관계 · 개발사 (시뮬레이션 기업)',
        },
      ],
    })
    await userEvent.click(screen.getByRole('combobox', { name: '기준' }))
    expect(await screen.findByText('개발사 (시뮬레이션 기업)')).toBeInTheDocument()
    expect(screen.getByText('관계 · 개발사 (시뮬레이션 기업)')).toBeInTheDocument()
    expect(screen.getAllByRole('option', { name: '개발사 › 국가' })).toHaveLength(2)
  })

  it('이어진 것 너머의 기준도 막대를 클릭하면 그 주소로 거른다', async () => {
    const { onPick } = await panel({ ...BASE, group_field: 'ref.vendor.country' })
    await userEvent.click(screen.getByRole('button', { name: 'A' }))
    expect(onPick).toHaveBeenCalledWith('ref.vendor.country', 'A')
  })
})
