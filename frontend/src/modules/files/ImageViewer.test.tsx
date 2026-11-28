/**
 * 크게 보기의 「원본 다운로드」 — **실패를 말한다.** 전에는 거절(404 · 401)이 처리되지 않아 클릭해도
 * 아무것도 안 떴다(2026-11-28). 오류는 그 사진의 것이라 다음 사진으로 넘기면 표시하지 않는다.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { Attachment } from '@/modules/files/api'

const attachmentApi = vi.hoisted(() => ({ download: vi.fn() }))
vi.mock('@/modules/files/api', () => ({
  attachmentApi,
  attachmentPaths: { content: (id: string) => `/attachments/${id}/content` },
}))
vi.mock('@/shared/hooks/useBlobUrl', () => ({
  useBlobUrl: (path: string | null) => ({ url: path ? `blob:${path}` : null, error: null }),
}))

import { ImageViewer } from '@/modules/files/ImageViewer'

function photo(id: string, name: string): Attachment {
  return {
    id,
    owner_table: 'object_instances',
    owner_id: 'o1',
    owner_field: null,
    original_name: name,
    content_type: 'image/png',
    size_bytes: 2048,
    sha256: 'x',
    created_at: '2026-11-28T09:00:00+09:00',
    is_image: true,
  }
}

describe('크게 보기의 원본 다운로드', () => {
  it('실패하면 그 사진 아래에 오류를 표시한다', async () => {
    attachmentApi.download.mockRejectedValue(new Error('첨부를 찾을 수 없습니다.'))
    const images = [photo('a', '앞.png'), photo('b', '뒤.png')]
    const onIndex = vi.fn()
    const { rerender } = render(
      <ImageViewer images={images} index={0} onIndex={onIndex} onClose={() => undefined} />,
    )

    await userEvent.click(screen.getByRole('button', { name: '원본 다운로드' }))
    expect(attachmentApi.download).toHaveBeenCalledWith('a', '앞.png')
    expect(await screen.findByText('첨부를 찾을 수 없습니다.')).toBeInTheDocument()

    rerender(<ImageViewer images={images} index={1} onIndex={onIndex} onClose={() => undefined} />)
    expect(screen.queryByText('첨부를 찾을 수 없습니다.')).toBeNull()
  })
})
