/**
 * 인프라 — 부서를 바꾼 직후 **앞 부서의 값이 새 부서에 저장되지 않는다.** 폼은 그 부서의 응답이
 * 온 뒤에야 채워지고, 그 전(또는 그 응답이 오류면)에는 저장이 막힌다(2026-10-16).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const dtApi = vi.hoisted(() => ({
  defs: vi.fn(),
  capacity: vi.fn(),
  capacitySummary: vi.fn(),
  capacitySave: vi.fn(),
}))
vi.mock('@/extensions/caegroup/api', () => ({ dtApi }))
vi.mock('@/modules/workspaces/api', () => ({
  workspaceApi: {
    list: vi.fn().mockResolvedValue([
      { slug: 'a', name: 'A 부서' },
      { slug: 'b', name: 'B 부서' },
    ]),
  },
}))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { home_workspace_slug: 'a', memberships: [{ slug: 'a' }] } }),
}))

function capacityOf(note: string) {
  return {
    workspace_id: note,
    workspace_name: note,
    sw: [],
    hw: [],
    material_types: null,
    has_process_std: false,
    note,
  }
}

function setup(answerB: () => Promise<unknown>) {
  dtApi.defs.mockResolvedValue({ sw_units: [], sw_purposes: [] })
  dtApi.capacitySummary.mockResolvedValue({
    departments: 1,
    sw: [],
    hw: { cpu_cores: 0, ram_gb: 0, gpu_units: 0 },
    material_types: 0,
    process_std: 0,
  })
  dtApi.capacity.mockImplementation((slug: string) =>
    slug === 'a' ? Promise.resolve(capacityOf('A 메모')) : answerB(),
  )
  dtApi.capacitySave.mockResolvedValue(capacityOf('저장됨'))
}

async function switchToB() {
  const { default: InfraPage } = await import('@/extensions/caegroup/InfraPage')
  render(<InfraPage />)
  expect(await screen.findByDisplayValue('A 메모')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('combobox'))
  await userEvent.click(await screen.findByRole('option', { name: 'B 부서' }))
  await waitFor(() => expect(dtApi.capacity).toHaveBeenCalledWith('b'))
}

describe('인프라 — 부서 바꾸기', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('새 부서의 값이 오기 전에는 저장을 막고, 온 뒤에는 그 값을 저장한다', async () => {
    let releaseB: (value: unknown) => void = () => undefined
    setup(
      () =>
        new Promise((resolve) => {
          releaseB = resolve
        }),
    )
    await switchToB()
    expect(screen.getByRole('button', { name: /저장/ })).toBeDisabled()

    releaseB(capacityOf('B 메모'))
    expect(await screen.findByDisplayValue('B 메모')).toBeInTheDocument()
    const save = screen.getByRole('button', { name: /저장/ })
    await waitFor(() => expect(save).toBeEnabled())
    await userEvent.click(save)
    await waitFor(() =>
      expect(dtApi.capacitySave).toHaveBeenCalledWith(
        'b',
        expect.objectContaining({ note: 'B 메모' }),
      ),
    )
    expect(dtApi.capacitySave).not.toHaveBeenCalledWith(
      'b',
      expect.objectContaining({ note: 'A 메모' }),
    )
  })

  it('새 부서를 못 읽으면 앞 부서의 값이 남아 있어도 저장할 수 없다', async () => {
    setup(() => Promise.reject(new Error('B 를 못 읽음')))
    await switchToB()
    expect(await screen.findByText('B 를 못 읽음')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /저장/ })).toBeDisabled()
    expect(dtApi.capacitySave).not.toHaveBeenCalled()
  })
})
