/**
 * 검색 화면이 지키는 것 — **왜 걸렸는지 말하고, 주소가 상태고, 좁힐 수 있다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const searchApi = vi.hoisted(() => ({ find: vi.fn() }))
vi.mock('@/modules/search/api', () => ({ searchApi }))

const RESULT = {
  q: 'ansys',
  total: 3,
  types: [
    { type_slug: 'vendor', type_label: '공급사', icon: 'Building2', count: 2 },
    { type_slug: 'tool', type_label: '툴', icon: 'Cog', count: 1 },
  ],
  items: [
    {
      id: 'a1',
      type_slug: 'vendor',
      type_label: '공급사',
      icon: 'Building2',
      label: 'ANSYS Inc.',
      key: 'V-001',
      matched: 'label',
      matched_text: '',
    },
    {
      id: 'b2',
      type_slug: 'tool',
      type_label: '툴',
      icon: 'Cog',
      label: '구조 해석기',
      key: null,
      matched: 'alias',
      matched_text: 'Ansys Mechanical (plm)',
    },
  ],
  limit: 50,
  offset: 0,
  min_query: 2,
}

async function open(url: string) {
  const { default: SearchPage } = await import('@/modules/search/SearchPage')
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/search" element={<SearchPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('검색', () => {
  // 부른 횟수를 보는 시험이 있다 — 앞 시험의 호출이 남으면 그 셈이 틀어진다.
  beforeEach(() => searchApi.find.mockClear())

  it('이름에 없는 말로 걸린 줄은 왜 걸렸는지 적는다', async () => {
    // **안 적으면 엉뚱해 보이는 줄이 오류로 읽힌다.** 외부에서 온 것은 저쪽 코드로
    // 기억되고 있어서, 그 별칭이 곧 사람이 아는 유일한 이름일 때가 있다.
    searchApi.find.mockResolvedValue(RESULT)
    await open('/search?q=ansys')
    await waitFor(() => expect(screen.getByText('ANSYS Inc.')).toBeInTheDocument())

    expect(screen.getByText(/별칭: Ansys Mechanical \(plm\)/)).toBeInTheDocument()
    // 이름으로 걸린 줄에는 안 적는다 — 당연한 것을 적으면 그 자리를 아무도 안 읽는다.
    expect(screen.queryByText('이름')).not.toBeInTheDocument()
  })

  it('줄은 그 객체로 간다', async () => {
    searchApi.find.mockResolvedValue(RESULT)
    await open('/search?q=ansys')
    await waitFor(() => expect(screen.getByText('ANSYS Inc.')).toBeInTheDocument())
    expect(screen.getByRole('link', { name: /ANSYS Inc./ })).toHaveAttribute('href', '/o/vendor/a1')
  })

  it('타입 단추로 좁히면 그 타입만 다시 묻는다', async () => {
    searchApi.find.mockResolvedValue(RESULT)
    await open('/search?q=ansys')
    await waitFor(() => expect(screen.getByText('ANSYS Inc.')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: /공급사 2/ }))
    await waitFor(() =>
      expect(searchApi.find).toHaveBeenLastCalledWith('ansys', {
        type: 'vendor',
        offset: 0,
      }),
    )
  })

  it('좁힌 뒤에도 「전체」 는 전체 수고, 다른 타입 단추가 그대로 선다', async () => {
    // 서버는 좁히면 types · total 을 그 타입 것으로 준다 — 그것을 그대로 쓰던 때는 「전체 3」
    // 이 「전체 2」 가 되고 「툴」 단추가 사라졌다(2026-10-08).
    const narrowed = {
      ...RESULT,
      total: 2,
      types: [RESULT.types[0]],
      items: [RESULT.items[0]],
    }
    searchApi.find.mockImplementation(async (_q: string, options?: { type?: string | null }) =>
      options?.type ? narrowed : RESULT,
    )
    await open('/search?q=ansys')
    await waitFor(() => expect(screen.getByText('ANSYS Inc.')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: /공급사 2/ }))
    await waitFor(() => expect(screen.queryByText('구조 해석기')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '전체 3' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /툴 1/ })).toBeInTheDocument()
  })

  it('좁힌 주소로 바로 들어와도 안 좁힌 수를 따로 물어 단추를 세운다', async () => {
    const narrowed = { ...RESULT, total: 2, types: [RESULT.types[0]], items: [RESULT.items[0]] }
    searchApi.find.mockImplementation(async (_q: string, options?: { type?: string | null }) =>
      options?.type ? narrowed : RESULT,
    )
    await open('/search?q=ansys&type=vendor')
    expect(await screen.findByRole('button', { name: '전체 3' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /툴 1/ })).toBeInTheDocument()
    expect(searchApi.find).toHaveBeenCalledWith('ansys')
  })

  it('아직 안 쳤으면 무엇을 칠 수 있는지 말한다', async () => {
    searchApi.find.mockResolvedValue(RESULT)
    await open('/search')
    expect(screen.getByText('무엇을 찾으시나요')).toBeInTheDocument()
    expect(searchApi.find).not.toHaveBeenCalled()
  })

  it('너무 짧으면 그렇다고 말한다 — 빈 결과를 오류로 읽지 않게', async () => {
    searchApi.find.mockResolvedValue({ ...RESULT, total: 0, types: [], items: [] })
    await open('/search?q=볼')
    await waitFor(() => expect(screen.getByText('2글자 이상 쳐 주세요')).toBeInTheDocument())
  })

  it('섞어 볼 때 잘렸으면 좁히라고 말한다', async () => {
    // 여기에 쪽 넘기기를 붙이면 「몇 건 중 몇 건」 이 거짓말이 된다 — 타입마다 따로
    // 세고 투영 타입은 첫 쪽에만 얹기 때문이다.
    searchApi.find.mockResolvedValue({ ...RESULT, total: 120 })
    await open('/search?q=ansys')
    await waitFor(() => expect(screen.getByText(/120건 중 2건/)).toBeInTheDocument())
  })

  it('기록은 섞지 않고 수로 말하며, 그 타입을 고르면 본다', async () => {
    searchApi.find.mockResolvedValue({
      ...RESULT,
      types: [
        ...RESULT.types,
        { type_slug: 'case', type_label: '시장 서비스', icon: '', count: 900, usage: 'log' },
      ],
      records: 900,
    })
    await open('/search?q=ansys')
    await waitFor(() =>
      expect(screen.getByText(/기록 900건은 여기 섞지 않았습니다/)).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /시장 서비스 900/ })).toHaveTextContent('기록')
  })
})
