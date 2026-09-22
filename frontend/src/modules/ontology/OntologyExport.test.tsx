/**
 * 구조 내보내기에서 지키는 것 — **어느 온톨로지 화면에서든 제목 줄에 있고, 두 길이 있고,
 * 실패하면 말한다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ downloadFile: vi.fn() }))
vi.mock('@/shared/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/api/client')>()),
  downloadFile: client.downloadFile,
}))

async function open() {
  const { OntologyExportButton } = await import('@/modules/ontology/OntologyExportButton')
  const onError = vi.fn()
  render(<OntologyExportButton onError={onError} />)
  return onError
}

describe('온톨로지 구조 내보내기', () => {
  it('엑셀과 JSON 을 각각 받는다', async () => {
    client.downloadFile.mockResolvedValue(undefined)
    await open()

    await userEvent.click(screen.getByRole('button', { name: /구조 내보내기/ }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /Excel/ }))
    await waitFor(() =>
      expect(client.downloadFile).toHaveBeenCalledWith(
        '/ontology/export?format=xlsx',
        '온톨로지-구조.xlsx',
      ),
    )

    await userEvent.click(screen.getByRole('button', { name: /구조 내보내기/ }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /JSON/ }))
    await waitFor(() =>
      expect(client.downloadFile).toHaveBeenLastCalledWith(
        '/ontology/export?format=json',
        '온톨로지-정의.json',
      ),
    )
  })

  it('무엇이 다른지 고르는 자리에 적는다 — 고른 뒤에 알면 파일을 두 번 받는다', async () => {
    client.downloadFile.mockResolvedValue(undefined)
    await open()
    await userEvent.click(screen.getByRole('button', { name: /구조 내보내기/ }))
    expect(await screen.findByRole('menuitem', { name: /타입마다 표 하나/ })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /다시 넣는 모양/ })).toBeInTheDocument()
  })

  it('실패하면 화면이 가진 한 자리로 올린다 — 조용하면 고장으로 읽힌다', async () => {
    client.downloadFile.mockRejectedValue(new Error('권한이 없습니다'))
    const onError = await open()

    await userEvent.click(screen.getByRole('button', { name: /구조 내보내기/ }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /Excel/ }))
    await waitFor(() =>
      expect(onError).toHaveBeenLastCalledWith(
        expect.objectContaining({ message: '권한이 없습니다' }),
      ),
    )
  })
})
