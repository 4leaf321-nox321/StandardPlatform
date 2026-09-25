/**
 * 설정 화면이 지키는 것 — **현재값이 채워져 오고, 쓰이는 수가 보이고, 고친 것만 나간다.**
 *
 * 빈 표를 주면 무엇을 적어야 하는지 알 수 없고, 쓰이는 수를 안 보여 주면 지워도 되는 값인지
 * 판단할 자리가 없다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const dtApi = vi.hoisted(() => ({ catalogs: vi.fn(), catalogSave: vi.fn() }))
vi.mock('@/extensions/caegroup/api', () => ({ dtApi }))

const CATALOGS = [
  {
    name: 'sw_units',
    label: 'S/W 단위',
    help: '라이선스를 무엇으로 세나',
    items: [
      { key: 'copy', label: '카피' },
      { key: 'token', label: '토큰' },
    ],
    in_use: { copy: 3 },
    is_default: true,
  },
]

async function open() {
  dtApi.catalogs.mockResolvedValue(CATALOGS)
  dtApi.catalogSave.mockResolvedValue({ ...CATALOGS[0], is_default: false })
  const { default: SettingsPage } = await import('@/extensions/caegroup/SettingsPage')
  render(<SettingsPage />)
  await waitFor(() => expect(screen.getByLabelText('1번 줄 key')).toHaveValue('copy'))
}

describe('디지털 트윈 설정', () => {
  it('현재값이 채워져 오고 쓰이는 줄 수가 보인다', async () => {
    await open()
    expect(screen.getByLabelText('1번 줄 label')).toHaveValue('카피')
    // **쓰이는 값은 지울 수 없다**(서버가 막는다) — 그 수를 보고 판단한다.
    expect(screen.getByLabelText('1번 줄 쓰이는 줄')).toHaveValue('3')
    expect(screen.getByLabelText('2번 줄 쓰이는 줄')).toHaveValue('0')
    expect(screen.getByText(/기본값입니다/)).toBeInTheDocument()
  })

  it('한 줄 더해 저장하면 그 목록만 나간다', async () => {
    await open()
    // 표는 있는 항목만 들고 시작한다 — 새 줄은 「줄 추가」 로 낸다.
    await userEvent.click(screen.getByRole('button', { name: /줄 추가/ }))
    await userEvent.type(screen.getByLabelText('3번 줄 key'), 'core')
    await userEvent.type(screen.getByLabelText('3번 줄 label'), '코어')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(dtApi.catalogSave).toHaveBeenCalledWith('sw_units', [
        { key: 'copy', label: '카피' },
        { key: 'token', label: '토큰' },
        { key: 'core', label: '코어' },
      ]),
    )
  })

  it('기본값으로 되돌리면 빈 목록을 보낸다 — 서버가 설정에서 지운다', async () => {
    dtApi.catalogs.mockResolvedValue([{ ...CATALOGS[0], is_default: false }])
    dtApi.catalogSave.mockResolvedValue(CATALOGS[0])
    const { default: SettingsPage } = await import('@/extensions/caegroup/SettingsPage')
    render(<SettingsPage />)
    await waitFor(() => expect(screen.getByLabelText('1번 줄 key')).toHaveValue('copy'))
    await userEvent.click(screen.getByRole('button', { name: '기본값으로' }))
    await waitFor(() => expect(dtApi.catalogSave).toHaveBeenCalledWith('sw_units', []))
  })
})
