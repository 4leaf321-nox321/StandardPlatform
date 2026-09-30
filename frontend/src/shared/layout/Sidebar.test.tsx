/**
 * 사이드바의 **묶음 접기** — 타입이 늘면 목록이 길어지고, 그때 사람은 자기가 쓰는 묶음을
 * 찾으려고 매번 스크롤한다. 접은 것은 브라우저에 기억하고, 접힌 묶음 안에 지금 보는 화면이
 * 있으면 점으로 알려 준다(접었다고 「어디 있는지」 를 모르게 두지 않는다).
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
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

async function mount(path = '/w/hq', nav: unknown[] = []) {
  systemApi.health.mockResolvedValue({ status: 'ok', version: '0.0.0' })
  ontologyApi.nav.mockResolvedValue(nav)
  const { Sidebar } = await import('@/shared/layout/Sidebar')
  render(
    <MemoryRouter initialEntries={[path]}>
      <Sidebar collapsed={false} workspaceSlug="hq" />
    </MemoryRouter>,
  )
  await waitFor(() => expect(screen.getByRole('button', { name: /내 활동/ })).toBeInTheDocument())
}

describe('사이드바 묶음 접기', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
  })

  it('제목을 누르면 접히고 다시 누르면 펴진다 — 기억한다', async () => {
    await mount()
    const head = screen.getByRole('button', { name: /내 활동/ })
    expect(head).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('link', { name: '작업' })).toBeInTheDocument()

    await userEvent.click(head)
    expect(head).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('link', { name: '작업' })).not.toBeInTheDocument()
    // 브라우저에 남는다 — 새로고침해도 접힌 채다.
    expect(window.localStorage.getItem('standardplatform.sidebar.folded')).toContain('내 활동')

    await userEvent.click(head)
    expect(head).toHaveAttribute('aria-expanded', 'true')
  })

  it('접힌 묶음 안에 지금 보는 화면이 있으면 점을 찍는다', async () => {
    await mount('/jobs')
    const head = screen.getByRole('button', { name: /내 활동/ })
    expect(within(head).queryByTitle(/지금 보는 화면/)).not.toBeInTheDocument()

    await userEvent.click(head)
    expect(within(head).getByTitle(/지금 보는 화면/)).toBeInTheDocument()

    // 다른 화면을 보고 있으면 점이 없다.
    screen.getByRole('button', { name: /공통/ }).click()
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
    expect(top).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /기계 부품/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'part' })).toBeInTheDocument()

    // 상위를 접으면 그 아래가 통째로 사라진다(그리지 않는다 — 탭 이동으로도 못 간다).
    await userEvent.click(top)
    expect(screen.queryByRole('button', { name: /기계 부품/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'part' })).not.toBeInTheDocument()
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
