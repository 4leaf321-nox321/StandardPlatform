/**
 * 내보내기에서 지키는 것 — **네 갈래가 각자 제 길로 가고, 실패하면 말한다.**
 *
 * 구조만은 그 자리에서, 데이터까지는 작업으로 — 그 차이가 고르는 자리에 적혀 있나.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const ontologyApi = vi.hoisted(() => ({
  exportStructure: vi.fn(),
  exportEverything: vi.fn(),
}))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

async function open() {
  const { OntologyExportButton } = await import('@/modules/ontology/OntologyExportButton')
  const onError = vi.fn()
  render(<OntologyExportButton onError={onError} />)
  return onError
}

async function pick(name: RegExp) {
  await userEvent.click(screen.getByRole('button', { name: /내보내기/ }))
  await userEvent.click(await screen.findByRole('menuitem', { name }))
}

describe('온톨로지 내보내기', () => {
  it('구조만은 그 자리에서 받는다 — 엑셀과 JSON', async () => {
    ontologyApi.exportStructure.mockResolvedValue(undefined)
    await open()

    await pick(/타입마다 속성 표/)
    await waitFor(() =>
      expect(ontologyApi.exportStructure).toHaveBeenCalledWith('xlsx', '온톨로지-구조.xlsx'),
    )

    await pick(/다시 넣는 모양/)
    await waitFor(() =>
      expect(ontologyApi.exportStructure).toHaveBeenLastCalledWith('json', '온톨로지-정의.json'),
    )
  })

  it('데이터까지는 작업으로 받는다 — 객체 행이 든 엑셀과 묶음 JSON', async () => {
    ontologyApi.exportEverything.mockResolvedValue(undefined)
    await open()

    await pick(/타입마다 객체 행/)
    await waitFor(() =>
      expect(ontologyApi.exportEverything).toHaveBeenCalledWith('xlsx', '온톨로지-전체.xlsx'),
    )

    await pick(/정의 · 객체 · 관계 묶음/)
    await waitFor(() =>
      expect(ontologyApi.exportEverything).toHaveBeenLastCalledWith('json', '온톨로지-전체.json'),
    )
  })

  it('무엇이 다른지 고르는 자리에 적는다 — 고른 뒤에 알면 파일을 두 번 받는다', async () => {
    await open()
    await userEvent.click(screen.getByRole('button', { name: /내보내기/ }))
    expect(await screen.findByText(/구조만 — 정의가 어떻게 생겼나/)).toBeInTheDocument()
    expect(screen.getByText(/데이터까지 — 채워진 객체를 함께/)).toBeInTheDocument()
  })

  it('실패하면 화면이 가진 한 자리로 올린다 — 조용하면 고장으로 읽힌다', async () => {
    ontologyApi.exportEverything.mockRejectedValue(new Error('작업 워커가 꺼져 있습니다'))
    const onError = await open()

    await pick(/타입마다 객체 행/)
    await waitFor(() =>
      expect(onError).toHaveBeenLastCalledWith(
        expect.objectContaining({ message: '작업 워커가 꺼져 있습니다' }),
      ),
    )
  })
})
