/**
 * 장표 목록 — **볼 수 없는 주제의 장표는 링크로 두지 않는다.** 서버는 그 주제를
 * `{label: '(볼 수 없는 객체)', type_slug: ''}` 로 보내는데, 링크로 두면 주제는 `/o//<id>` 로,
 * 제목은 열지 않는 한 장(SLIDES-40)으로 갔다(2026-11-28).
 */

import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/shared/api/client', () => ({ api: client }))

import SlidesPage from '@/modules/slides/SlidesPage'

function slide(id: string, title: string, subject: Record<string, unknown> | null) {
  return {
    id,
    title,
    template_id: 't1',
    template_name: '변경점 한 장',
    subject,
    author_name: '작성자',
    created_at: '2026-11-28T09:00:00+09:00',
    edited_at: null,
  }
}

describe('장표 목록', () => {
  it('볼 수 없는 주제의 제목 · 주제는 글자로, 지운 주제는 「(삭제됨)」 으로', async () => {
    client.get.mockImplementation((path: string) =>
      Promise.resolve(
        path.startsWith('/slides/templates')
          ? []
          : {
              items: [
                slide('s1', '(볼 수 없는 주제) 변경점 한 장', {
                  id: 'o1',
                  label: '(볼 수 없는 객체)',
                  type_slug: '',
                  deleted: false,
                }),
                slide('s2', 'M-2 변경점 한 장', {
                  id: 'o2',
                  label: 'M-2',
                  type_slug: 'model',
                  deleted: true,
                }),
              ],
              total: 2,
              limit: 50,
              offset: 0,
            },
      ),
    )
    render(
      <MemoryRouter>
        <SlidesPage />
      </MemoryRouter>,
    )

    const hiddenTitle = await screen.findByText('(볼 수 없는 주제) 변경점 한 장')
    expect(hiddenTitle.closest('a')).toBeNull()
    expect(screen.getByText('(볼 수 없는 객체)').closest('a')).toBeNull()
    expect(screen.queryByRole('link', { name: /볼 수 없는/ })).toBeNull()

    expect(screen.getByRole('link', { name: 'M-2 변경점 한 장' })).toHaveAttribute(
      'href',
      '/slides/s2',
    )
    const shown = screen.getByRole('link', { name: 'M-2 (삭제됨)' })
    expect(shown).toHaveAttribute('href', '/o/model/o2')
    expect(screen.queryByText(/지워짐/)).toBeNull()
  })
})
