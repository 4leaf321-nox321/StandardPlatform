/**
 * 로그인 화면의 **HWAX 포털로 로그인**(ADR 0028).
 *
 * - 단추는 서버가 주소를 심었을 때만 선다 — 포털에 붙지 않은 설치에 단추가 있으면 누른 사람이
 *   어디에도 닿지 않는다.
 * - 서버가 `?sso=<사유>` 로 돌려보내면 사람 말로 보인다. 모르는 사유도 말한다.
 */

import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import LoginPage from '@/modules/auth/LoginPage'
import { ssoMessage } from '@/modules/auth/portal'

vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ status: 'anonymous', user: null, login: vi.fn() }),
}))

const LAUNCH = 'https://portal.example/launch/hwaxapp'

function setLaunch(value: string | null) {
  document.head.querySelector('meta[name="app-portal-launch"]')?.remove()
  if (value === null) return
  const meta = document.createElement('meta')
  meta.name = 'app-portal-launch'
  meta.content = value
  document.head.appendChild(meta)
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LoginPage />
    </MemoryRouter>,
  )
}

afterEach(() => setLaunch(null))

describe('HWAX 포털로 로그인', () => {
  it('서버가 주소를 심으면 단추가 그 주소로 간다', () => {
    setLaunch(LAUNCH)
    renderAt('/login')
    const link = screen.getByRole('link', { name: 'HWAX 포털로 로그인' })
    expect(link).toHaveAttribute('href', LAUNCH)
  })

  it('주소가 없거나 비면 단추가 없다', () => {
    renderAt('/login')
    expect(screen.queryByRole('link', { name: 'HWAX 포털로 로그인' })).toBeNull()
    setLaunch('')
    renderAt('/login')
    expect(screen.queryByRole('link', { name: 'HWAX 포털로 로그인' })).toBeNull()
  })

  it('승인 대기는 기다리라고 말하고 오류 색을 쓰지 않는다', () => {
    renderAt('/login?sso=pending')
    const shown = screen.getByRole('status')
    expect(shown).toHaveTextContent('가입 승인을 기다립니다')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('정지 · 확인 실패는 사유를 오류로 보인다', () => {
    renderAt('/login?sso=suspended')
    expect(screen.getByRole('alert')).toHaveTextContent('정지된 계정입니다')
  })

  it('사유가 없으면 아무것도 안 뜬다', () => {
    renderAt('/login')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByRole('status')).toBeNull()
  })
})

describe('?sso= 사유 문구', () => {
  it.each([
    ['pending', 'info', '가입 승인을 기다립니다'],
    ['deleted', 'error', '삭제된 계정입니다'],
    ['expired', 'error', '유효 시간'],
    ['invalid', 'error', '확인하지 못했습니다'],
    ['unavailable', 'error', '공개 키를 받지 못해'],
    ['admin_sub', 'error', '아이디 · 비밀번호로 로그인하세요'],
  ])('%s', (reason, tone, words) => {
    const got = ssoMessage(reason)
    expect(got.tone).toBe(tone)
    expect(got.text).toContain(words)
  })

  it('모르는 사유도 말한다 — 화면이 모르는 말이 와도 아무것도 안 뜨지는 않는다', () => {
    const got = ssoMessage('brand-new')
    expect(got.tone).toBe('error')
    expect(got.text).toContain('brand-new')
  })
})
