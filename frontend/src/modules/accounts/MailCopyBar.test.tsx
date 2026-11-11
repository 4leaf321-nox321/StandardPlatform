/**
 * 메일 주소 복사 — **조용히 실패하지 않는다.** 운영 서버는 http 라 브라우저가 클립보드 API 를
 * 안 준다. 옛 방식마저 막히면 주소를 골라진 채로 보여 주고 Ctrl+C 를 말한다 — 안 그러면
 * 사람은 「복사됐다」 고 믿고 빈 칸을 붙여 넣는다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const clipboard = vi.hoisted(() => ({ copyText: vi.fn() }))
vi.mock('@/shared/lib/clipboard', () => clipboard)

import { MailCopyBar } from '@/modules/accounts/MailCopyBar'

describe('MailCopyBar', () => {
  beforeEach(() => vi.clearAllMocks())

  it('고른 계정의 메일 주소를 「; 」 로 이어 복사하고, 메일이 아닌 아이디는 뺐다고 말한다', async () => {
    clipboard.copyText.mockResolvedValue(undefined)
    render(
      <MailCopyBar ids={['kim@corp.example', 'admin', 'lee@corp.example']} onClear={vi.fn()} />,
    )
    await userEvent.click(screen.getByRole('button', { name: /메일 주소 복사/ }))
    expect(clipboard.copyText).toHaveBeenCalledWith('kim@corp.example; lee@corp.example')
    const said = await screen.findByRole('status')
    expect(said).toHaveTextContent('메일 주소 2개를 복사했습니다')
    expect(said).toHaveTextContent('메일 주소가 아닌 아이디 1개는 뺐습니다(admin)')
  })

  it('자동 복사가 막히면 주소를 골라진 채로 보여 주고 Ctrl+C 를 말한다', async () => {
    clipboard.copyText.mockRejectedValue(new Error('copy failed'))
    render(<MailCopyBar ids={['kim@corp.example', 'lee@corp.example']} onClear={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /메일 주소 복사/ }))
    const box = await screen.findByRole<HTMLTextAreaElement>('textbox', {
      name: '복사할 메일 주소',
    })
    expect(box.value).toBe('kim@corp.example; lee@corp.example')
    await waitFor(() => expect(box.selectionEnd - box.selectionStart).toBe(box.value.length))
    expect(screen.getByRole('status')).toHaveTextContent('Ctrl+C')
  })

  it('메일 주소가 하나도 없으면 복사하지 않고 그렇다고 말한다', async () => {
    render(<MailCopyBar ids={['admin']} onClear={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /메일 주소 복사/ }))
    expect(clipboard.copyText).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('메일 주소가 없습니다')
  })

  it('고른 것이 없으면 누를 수 없다', () => {
    render(<MailCopyBar ids={[]} onClear={vi.fn()} />)
    expect(screen.getByRole('button', { name: /메일 주소 복사/ })).toBeDisabled()
  })
})
