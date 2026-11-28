/**
 * 장표 한 장의 「제목 수정」 — **저장은 한 번만, 실패는 말한다.** 전에는 저장 중에도 다시 클릭할 수
 * 있었고, 거절은 처리되지 않은 채 흘러 아무 데도 안 떴다(2026-11-28).
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const slideApi = vi.hoisted(() => ({ get: vi.fn(), update: vi.fn() }))
vi.mock('@/modules/slides/api', () => ({ slideApi }))
// 영역의 기록 동작(로그인 · 객체 화면)은 이 시험의 일이 아니다.
vi.mock('@/modules/slides/useRecordActions', () => ({
  useRecordActions: () => ({ actions: {}, dialogs: null }),
}))

import SlidePage from '@/modules/slides/SlidePage'

const slide = {
  id: 's1',
  title: '변경점 한 장',
  template_id: 't1',
  template_name: '변경점',
  subject: null,
  author_name: '작성자',
  created_at: '2026-11-28T09:00:00+09:00',
  edited_at: null,
  rows: 1,
  cols: 1,
  areas: [],
  can_edit: true,
  rendered_at: '2026-11-28T09:00:00+09:00',
}

describe('장표 제목 수정', () => {
  it('저장 중에는 다시 보내지 않고, 거절은 오류로 표시하고 입력을 남긴다', async () => {
    slideApi.get.mockResolvedValue(slide)
    let refuse: (error: Error) => void = () => undefined
    slideApi.update.mockReturnValue(
      new Promise((_resolve, reject) => {
        refuse = reject
      }),
    )
    render(
      <MemoryRouter initialEntries={['/slides/s1']}>
        <Routes>
          <Route path="/slides/:slideId" element={<SlidePage />} />
        </Routes>
      </MemoryRouter>,
    )

    await userEvent.click(await screen.findByRole('button', { name: '제목 수정' }))
    const input = screen.getByRole('textbox', { name: '제목' })
    await userEvent.clear(input)
    await userEvent.type(input, '새 제목')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    const busy = screen.getByRole('button', { name: '저장 중…' })
    expect(busy).toBeDisabled()
    await userEvent.click(busy)
    expect(slideApi.update).toHaveBeenCalledTimes(1)
    expect(slideApi.update).toHaveBeenCalledWith('s1', { title: '새 제목' })

    refuse(new Error('제목을 저장하지 못했습니다.'))
    expect(await screen.findByText('제목을 저장하지 못했습니다.')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '제목' })).toHaveValue('새 제목')
    expect(screen.getByRole('button', { name: '저장' })).toBeEnabled()
  })
})
