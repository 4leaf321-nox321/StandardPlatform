/**
 * 연도 배정 — **지금 배정을 못 읽었으면 저장하지 않는다.**
 *
 * 저장은 통째 교체(PUT)다. 읽기 실패를 삼키던 때는 실패가 빈 목록으로 보였고, 거기서 한 해를
 * 눌러 저장하면 기존 배정이 그 한 해로 바뀌었다(2026-10-08).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const objectApi = vi.hoisted(() => ({ years: vi.fn(), setYears: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

const THIS_YEAR = new Date().getFullYear()

async function show() {
  const { ObjectYears } = await import('@/modules/objects/ObjectYears')
  render(<ObjectYears typeSlug="part" objectId="o1" canEdit />)
}

describe('연도 배정', () => {
  beforeEach(() => vi.clearAllMocks())

  it('읽기에 실패하면 고르지도 저장하지도 못하고, 다시 읽을 수 있다', async () => {
    objectApi.years.mockRejectedValueOnce(new Error('잠시 연결이 끊겼습니다'))
    await show()
    expect(await screen.findByText(/잠시 연결이 끊겼습니다/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: String(THIS_YEAR) })).toBeDisabled()
    expect(screen.getByRole('button', { name: '저장' })).toBeDisabled()

    objectApi.years.mockResolvedValueOnce([THIS_YEAR - 1])
    await userEvent.click(screen.getByRole('button', { name: '다시 읽기' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: String(THIS_YEAR) })).toBeEnabled(),
    )
    expect(screen.getByRole('button', { name: String(THIS_YEAR - 1) })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await userEvent.click(screen.getByRole('button', { name: String(THIS_YEAR) }))
    objectApi.setYears.mockResolvedValue([THIS_YEAR - 1, THIS_YEAR])
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    // 읽은 배정에 더해 저장한다 — 지우지 않는다.
    expect(objectApi.setYears).toHaveBeenCalledWith('part', 'o1', [THIS_YEAR - 1, THIS_YEAR])
  })
})
