/**
 * 객체 생성의 소유 부서 — **내가 관리자인 부서로 보낸다.**
 *
 * 대표 소속을 고정으로 보내던 때, B 의 관리자인데 대표 소속 A 에서는 멤버인 사람은 늘 403 을
 * 봤다(서버의 `require_manager`, 2026-10-08).
 *
 * 파일 칸 — 고른 파일은 **객체를 만든 뒤** 같은 업로드 API 로 올린다. 업로드가 실패해도 객체는
 * 남으므로 창을 닫지 않고 그 사실과 상세로 가는 길을 보인다(2026-10-08).
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ObjectType, PropertyDef } from '@/modules/ontology/api'

const objectApi = vi.hoisted(() => ({ create: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))
const attachmentApi = vi.hoisted(() => ({ upload: vi.fn() }))
vi.mock('@/modules/files/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/files/api')>()),
  attachmentApi,
}))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({
    user: {
      home_workspace_slug: 'hq',
      is_system_admin: false,
      memberships: [
        { slug: 'hq', name: '본사', path: '본사', role: 'member' },
        { slug: 'lab', name: '시험팀', path: '본사 / 시험팀', role: 'manager' },
      ],
    },
  }),
}))

const TYPE = { slug: 'part', label: '부품', key_policy: 'none' } as unknown as ObjectType

describe('객체 생성', () => {
  it('대표 소속에서 멤버뿐이면 관리하는 부서의 것으로 만든다', async () => {
    objectApi.create.mockResolvedValue({ id: 'o1' })
    const { ObjectCreateDialog } = await import('@/modules/objects/ObjectCreateDialog')
    const onCreated = vi.fn()
    render(<ObjectCreateDialog type={TYPE} defs={[]} onClose={vi.fn()} onCreated={onCreated} />)
    expect(screen.getByRole('combobox', { name: '소유 부서' })).toHaveTextContent('시험팀')
    await userEvent.type(screen.getByLabelText(/이름/), '볼트')
    await userEvent.click(screen.getByRole('button', { name: '생성' }))
    await waitFor(() =>
      expect(objectApi.create).toHaveBeenCalledWith(
        'part',
        expect.objectContaining({ label: '볼트', workspace_slug: 'lab' }),
      ),
    )
    expect(onCreated).toHaveBeenCalled()
  })
})

const PHOTO = {
  key: 'photo',
  label: '사진',
  data_type: 'file',
  accept: 'image',
  required: false,
  multi: false,
} as unknown as PropertyDef

function pick(files: File[]) {
  fireEvent.change(screen.getByLabelText('사진'), { target: { files } })
}

describe('생성 화면의 파일 칸', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    objectApi.create.mockResolvedValue({ id: 'o1', label: '볼트' })
  })

  it('고른 파일은 객체를 만든 뒤 그 칸에 업로드한다', async () => {
    attachmentApi.upload.mockResolvedValue({ id: 'a1' })
    const { ObjectCreateDialog } = await import('@/modules/objects/ObjectCreateDialog')
    const onCreated = vi.fn()
    render(
      <ObjectCreateDialog type={TYPE} defs={[PHOTO]} onClose={vi.fn()} onCreated={onCreated} />,
    )
    await userEvent.type(screen.getByLabelText(/이름/), '볼트')
    pick([new File(['png'], '앞면.png', { type: 'image/png' })])
    expect(screen.getByText('앞면.png')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '생성' }))
    await waitFor(() => expect(onCreated).toHaveBeenCalled())
    expect(objectApi.create).toHaveBeenCalledTimes(1)
    // 파일은 properties 에 안 실린다 — 첨부다.
    expect(objectApi.create.mock.calls[0][1].properties).toEqual({})
    expect(attachmentApi.upload).toHaveBeenCalledWith(
      expect.objectContaining({ ownerTable: 'objects', ownerId: 'o1', ownerField: 'photo' }),
    )
  })

  it('업로드가 실패하면 객체는 남았다고 알리고 상세로 안내한다', async () => {
    attachmentApi.upload.mockRejectedValue(new Error('이 칸에는 이미지만 업로드할 수 있습니다'))
    const { ObjectCreateDialog } = await import('@/modules/objects/ObjectCreateDialog')
    const onCreated = vi.fn()
    const onOpen = vi.fn()
    render(
      <ObjectCreateDialog
        type={TYPE}
        defs={[PHOTO]}
        onClose={vi.fn()}
        onCreated={onCreated}
        onOpen={onOpen}
      />,
    )
    await userEvent.type(screen.getByLabelText(/이름/), '볼트')
    pick([new File(['%PDF'], '성적서.png', { type: 'image/png' })])
    await userEvent.click(screen.getByRole('button', { name: '생성' }))
    expect(await screen.findByText(/만들었지만 파일 1개를 업로드하지 못했습니다/)).toBeVisible()
    expect(screen.getByText(/성적서.png: 이 칸에는 이미지만/)).toBeInTheDocument()
    // 다시 「생성」 할 자리는 없다 — 누르면 같은 것이 둘이 된다.
    expect(screen.queryByRole('button', { name: '생성' })).not.toBeInTheDocument()
    expect(onCreated).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '상세 열기' }))
    expect(onCreated).toHaveBeenCalled()
    expect(onOpen).toHaveBeenCalledWith('o1')
  })

  it('한 파일의 상한을 넘으면 만들기 전에 막는다', async () => {
    const { ObjectCreateDialog } = await import('@/modules/objects/ObjectCreateDialog')
    render(
      <ObjectCreateDialog type={TYPE} defs={[PHOTO]} onClose={vi.fn()} onCreated={vi.fn()} />,
    )
    await userEvent.type(screen.getByLabelText(/이름/), '볼트')
    const big = new File(['x'], '큰사진.png', { type: 'image/png' })
    Object.defineProperty(big, 'size', { value: 60 * 1024 * 1024 })
    pick([big])
    await userEvent.click(screen.getByRole('button', { name: '생성' }))
    expect(await screen.findByText(/50MB 까지입니다 — 큰사진.png/)).toBeInTheDocument()
    expect(objectApi.create).not.toHaveBeenCalled()
  })
})
