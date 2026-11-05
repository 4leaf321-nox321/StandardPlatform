/**
 * 사이드바의 **묶음 펼침과 메뉴 찾기** — 타입이 늘면 목록이 길어지고, 그때 사람은 자기가 쓰는
 * 묶음을 찾으려고 매번 스크롤한다.
 *
 * - 묶음은 기본으로 접혀 있고, **지금 보는 화면이 든 묶음만** 저절로 펴진다. 사람이 편 묶음은
 *   브라우저에 기억한다.
 * - 지금 화면이 든 묶음을 접으면 점으로 알려 준다(접었다고 「어디 있는지」 를 모르게 두지 않는다).
 * - 「메뉴 찾기」 는 접힌 묶음까지 뒤지고, Enter 로 간다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { is_system_admin: false, memberships: [] } }),
}))
const systemApi = vi.hoisted(() => ({ health: vi.fn() }))
vi.mock('@/shared/api/system', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/api/system')>()),
  systemApi,
}))
const ontologyApi = vi.hoisted(() => ({ nav: vi.fn() }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

const OPENED = 'standardplatform.sidebar.opened'

/** 지금 주소 — Enter 로 옮겼는지 본다. */
function Where() {
  return <output aria-label="지금 주소">{useLocation().pathname}</output>
}

async function mount(path = '/w/hq', nav: unknown[] = []) {
  systemApi.health.mockResolvedValue({ status: 'ok', version: '0.0.0' })
  ontologyApi.nav.mockResolvedValue(nav)
  const { Sidebar } = await import('@/shared/layout/Sidebar')
  render(
    <MemoryRouter initialEntries={[path]}>
      <Sidebar collapsed={false} workspaceSlug="hq" />
      <Where />
    </MemoryRouter>,
  )
  await waitFor(() => expect(screen.getByRole('button', { name: /내 활동/ })).toBeInTheDocument())
}

describe('사이드바 묶음 펼침', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
  })

  it('기본은 접혀 있고, 누르면 펴지며 기억한다', async () => {
    await mount()
    const head = screen.getByRole('button', { name: /내 활동/ })
    expect(head).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('link', { name: '작업' })).not.toBeInTheDocument()

    await userEvent.click(head)
    expect(head).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('link', { name: '작업' })).toBeInTheDocument()
    // 브라우저에 남는다 — 새로고침해도 편 채다.
    expect(window.localStorage.getItem(OPENED)).toContain('내 활동')

    await userEvent.click(head)
    expect(head).toHaveAttribute('aria-expanded', 'false')
    expect(window.localStorage.getItem(OPENED)).not.toContain('내 활동')
  })

  it('다 펼쳐 두던 때의 「접은 묶음」 기억은 지운다 — 뜻이 거꾸로다', async () => {
    window.localStorage.setItem('standardplatform.sidebar.folded', '["공통"]')
    await mount()
    expect(window.localStorage.getItem('standardplatform.sidebar.folded')).toBeNull()
  })

  it('지금 화면이 든 묶음은 저절로 펴지고, 접으면 점을 찍는다', async () => {
    await mount('/jobs')
    const head = screen.getByRole('button', { name: /내 활동/ })
    expect(head).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('link', { name: '작업' })).toBeInTheDocument()
    expect(within(head).queryByTitle(/지금 보는 화면/)).not.toBeInTheDocument()
    // 저절로 펴진 것은 기억하지 않는다 — 기억하면 들른 묶음마다 펼쳐져 다시 길어진다.
    expect(window.localStorage.getItem(OPENED)).toBeNull()

    await userEvent.click(head)
    expect(head).toHaveAttribute('aria-expanded', 'false')
    expect(within(head).getByTitle(/지금 보는 화면/)).toBeInTheDocument()

    // 다른 화면을 보고 있는 묶음에는 점이 없다.
    expect(
      within(screen.getByRole('button', { name: /공통/ })).queryByTitle(/지금 보는 화면/),
    ).not.toBeInTheDocument()
  })

  it('상위 묶음 아래에 자식이 들어간다 — 정의가 그렇게 오면', async () => {
    const group = (slug: string, label: string, parent: string | null, item: string) => ({
      slug,
      label,
      icon: '',
      audience: 'everyone',
      parent,
      items: item ? [{ label: item, icon: '', to: `/o/${item}`, slug: item }] : [],
    })
    // **자식이 먼저 온다** — 순서는 sort_order · 이름이 정한다(상위를 나중에 만들면 흔하다).
    await mount('/w/hq', [
      group('machine', '기계 부품', 'baseinfo', 'part'),
      group('baseinfo', '설계 기준정보', null, ''),
    ])
    const top = await screen.findByRole('button', { name: /설계 기준정보/ })
    expect(screen.queryByRole('button', { name: /기계 부품/ })).not.toBeInTheDocument()

    await userEvent.click(top)
    await userEvent.click(screen.getByRole('button', { name: /기계 부품/ }))
    expect(screen.getByRole('link', { name: 'part' })).toBeInTheDocument()

    // 상위를 접으면 그 아래가 통째로 사라진다(그리지 않는다 — 탭 이동으로도 못 간다).
    await userEvent.click(top)
    expect(screen.queryByRole('button', { name: /기계 부품/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'part' })).not.toBeInTheDocument()
  })

  it('지금 화면이 하위 묶음에 있으면 상위 · 하위가 함께 펴진다', async () => {
    await mount('/o/part', [
      {
        slug: 'machine',
        label: '기계 부품',
        icon: '',
        audience: 'everyone',
        parent: 'baseinfo',
        items: [{ label: '부품', icon: '', to: '/o/part', slug: 'part' }],
      },
      { slug: 'baseinfo', label: '설계 기준정보', icon: '', audience: 'everyone', items: [] },
    ])
    expect(await screen.findByRole('link', { name: '부품' })).toBeInTheDocument()
  })

  it('정의가 바뀌면 그 자리에서 다시 읽는다', async () => {
    await mount()
    expect(ontologyApi.nav).toHaveBeenCalledTimes(1)
    const { navChanged } = await import('@/shared/layout/navSignal')
    navChanged()
    // 안 읽으면 새로 고칠 때까지 옛 메뉴가 남는다 — 「정했는데 안 바뀐다」 가 된다.
    await waitFor(() => expect(ontologyApi.nav).toHaveBeenCalledTimes(2))
  })

  it('제목 없는 묶음(홈)은 접는 단추가 없다', async () => {
    await mount()
    expect(screen.getByRole('link', { name: '홈' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '홈' })).not.toBeInTheDocument()
  })
})

describe('메뉴 찾기', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
  })

  it('접힌 묶음까지 뒤져 맞는 것만 묶음 이름과 함께 보인다', async () => {
    await mount()
    await userEvent.type(screen.getByRole('searchbox', { name: '메뉴 찾기' }), '작 업')

    const found = screen.getByRole('list', { name: '찾은 메뉴' })
    expect(within(found).getByRole('link', { name: /작업/ })).toHaveTextContent('내 활동')
    // 찾는 동안에는 묶음 제목이 서지 않는다.
    expect(screen.queryByRole('button', { name: /내 활동/ })).not.toBeInTheDocument()
  })

  it('Enter 로 첫 줄로 가고 칸을 비운다 — Esc 는 비우기만', async () => {
    await mount()
    const box = screen.getByRole('searchbox', { name: '메뉴 찾기' })
    await userEvent.type(box, '작업{Enter}')
    expect(screen.getByRole('status', { name: '지금 주소' })).toHaveTextContent('/jobs')
    expect(box).toHaveValue('')
    expect(screen.getByRole('button', { name: /내 활동/ })).toBeInTheDocument()

    await userEvent.type(box, '작업{Escape}')
    expect(box).toHaveValue('')
    expect(screen.getByRole('status', { name: '지금 주소' })).toHaveTextContent('/jobs')
  })

  it('맞는 것이 없으면 그렇게 말한다', async () => {
    await mount()
    await userEvent.type(screen.getByRole('searchbox', { name: '메뉴 찾기' }), '없는메뉴')
    expect(screen.getByText('맞는 메뉴가 없습니다.')).toBeInTheDocument()
  })
})
