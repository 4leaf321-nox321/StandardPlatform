/**
 * 정제 도구 키트 안내 — **서버가 같은 판을 내려주고, 받은 뒤의 명령 한 줄을 채워서 보여 준다.**
 *
 * 따로 받게 두었더니 사내망에서는 받을 길이 없었고, 운영의 사람도 AI 도 그런 것이 있는 줄
 * 몰랐다(실측). 명령에는 이 설치의 주소(접두어까지)와 방금 받은 토큰이 들어가야 한다 —
 * 사람은 「서버 주소가 뭐였지」 에서 막힌다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ get: vi.fn(), downloadFile: vi.fn() }))
vi.mock('@/shared/api/client', async (original) => {
  const real = await original<typeof import('@/shared/api/client')>()
  return {
    ...real,
    api: { ...real.api, get: client.get },
    downloadFile: client.downloadFile,
  }
})

import { appUrlFrom, PipelineKitGuide, setupCommand } from '@/modules/auth/PipelineKitGuide'

const KIT = {
  available: true,
  filename: 'sp-pipeline-v0.4.41.zip',
  version: 'v0.4.41',
  size_bytes: 58 * 1024 * 1024,
}

describe('정제 도구 키트 안내', () => {
  beforeEach(() => {
    client.get.mockReset()
    client.downloadFile.mockReset().mockResolvedValue(undefined)
  })

  it('서버가 들고 있는 같은 판을 내려받는다', async () => {
    client.get.mockResolvedValue(KIT)
    render(<PipelineKitGuide token={null} />)
    const button = await screen.findByRole('button', { name: /sp-pipeline-v0\.4\.41\.zip/ })
    expect(button).toHaveTextContent('58MB')
    await userEvent.click(button)
    await waitFor(() =>
      expect(client.downloadFile).toHaveBeenCalledWith(
        '/server/pipeline-kit',
        'sp-pipeline-v0.4.41.zip',
      ),
    )
  })

  it('받은 토큰을 명령에 채운다 — 아직 없으면 자리표시자', async () => {
    client.get.mockResolvedValue(KIT)
    const { unmount } = render(<PipelineKitGuide token={null} />)
    expect(await screen.findByText(/‹발급받은_토큰›/, { selector: 'pre' })).toBeInTheDocument()
    unmount()
    render(<PipelineKitGuide token="spt_abc" />)
    expect(await screen.findByText(/--token spt_abc/, { selector: 'pre' })).toBeInTheDocument()
  })

  it('키트가 없는 설치(개발 · 옛 번들)는 그렇다고 말한다 — 단추를 안 세운다', async () => {
    client.get.mockResolvedValue({ ...KIT, available: false, size_bytes: 0 })
    render(<PipelineKitGuide token={null} />)
    expect(await screen.findByText(/키트가 들어 있지 않습니다/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /sp-pipeline/ })).toBeNull()
  })

  it('명령은 한 줄이고 접두어까지 든 주소를 쓴다', () => {
    // 줄 잇기 기호(cmd 의 ^ · PowerShell 의 `)를 쓰면 다른 셸에서 깨진 명령이 된다.
    const command = setupCommand('spt_x', appUrlFrom('http://10.0.0.5:3030', '/rootdesign'))
    expect(command).not.toContain('\n')
    expect(command).toContain('--server http://10.0.0.5:3030/rootdesign ')
    expect(appUrlFrom('http://host:8040', '')).toBe('http://host:8040')
  })
})
