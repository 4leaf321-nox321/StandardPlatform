/**
 * 삭제 창이 지키는 것 — **걸린 것을 먼저 보여 주고, 고른 대로 보낸다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ObjectRow, References } from '@/modules/objects/api'

const objectApi = vi.hoisted(() => ({
  references: vi.fn(),
  remove: vi.fn(),
  merge: vi.fn(),
  list: vi.fn(),
}))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

const ACME = { id: 'acme', label: 'ACME', key: null, status: 'active', properties: {} } as ObjectRow

const NONE: References = {
  property_refs: [],
  relations: [],
  hidden_property_refs: 0,
  hidden_relations: 0,
  total: 0,
}
const SOME: References = {
  property_refs: [
    {
      object_id: 'bolt',
      label: '볼트',
      key: 'P-1',
      type_slug: 'part',
      type_label: '부품',
      property_key: 'vendor',
      property_label: '공급사',
    },
  ],
  relations: [
    {
      relation_id: 'r1',
      relation: 'supplied_by',
      outgoing: false,
      other_id: 'nut',
      other_label: '너트',
      other_type_slug: 'part',
    },
  ],
  hidden_property_refs: 2,
  hidden_relations: 0,
  total: 4,
}

async function mount() {
  const { ObjectDeleteDialog } = await import('@/modules/objects/ObjectDeleteDialog')
  const onDone = vi.fn()
  render(
    <MemoryRouter>
      <ObjectDeleteDialog
        typeSlug="vendor"
        typeLabel="공급사"
        object={ACME}
        onClose={() => {}}
        onDone={onDone}
      />
    </MemoryRouter>,
  )
  return onDone
}

describe('삭제 창', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // 병합 후보 — 창이 열리면 늘 찾는다. **값 없는 가짜로 두면** `undefined.then` 이 되어,
    // 시험은 다 통과하는데 처리 안 된 오류 하나가 실행 전체를 실패로 만든다(v0.1.1 릴리스).
    objectApi.list.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 })
  })

  it('걸린 것이 없으면 바로 지운다', async () => {
    objectApi.references.mockResolvedValue(NONE)
    objectApi.remove.mockResolvedValue(undefined)
    const onDone = await mount()
    // **병합 후보는 열리자마자 200ms 뒤에 찾는다**(`useObjectOptions`). 시험이 그보다 빨리
    // 끝나면 가짜 목록이 한 번도 안 불리고, 느린 CI 에서만 불려 터진다 — 그 자리를 여기서
    // 늘 밟게 한다.
    await new Promise((resolve) => setTimeout(resolve, 300))
    await waitFor(() =>
      expect(screen.getByText('이 객체를 가리키는 것이 없습니다.')).toBeInTheDocument(),
    )
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '삭제' }))
    await waitFor(() => expect(objectApi.remove).toHaveBeenCalledWith('vendor', 'acme', 'block'))
    expect(onDone).toHaveBeenCalledWith(null)
  })

  it('걸린 것이 있으면 세어 보여 주고, 볼 수 없는 것도 수로 말하고, 선택 전엔 못 지운다', async () => {
    objectApi.references.mockResolvedValue(SOME)
    await mount()
    await waitFor(() => expect(screen.getByText(/이 객체를 가리키는 것 4개/)).toBeInTheDocument())
    expect(screen.getByText(/속성 참조 3 · 관계 1/)).toBeInTheDocument()
    expect(screen.getByText(/볼 수 없는 부서의 것 2/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '볼트' })).toHaveAttribute('href', '/o/part/bolt')
    // 「그대로 두기」 가 기본이고, 그 상태에서는 삭제 단추가 안 선다.
    expect(screen.getByRole('radio', { name: /그대로 두기/ })).toBeChecked()
    expect(screen.getByRole('button', { name: '삭제' })).toBeDisabled()
  })

  it('「참조 해제 후 삭제」 는 detach 로 보낸다', async () => {
    objectApi.references.mockResolvedValue(SOME)
    objectApi.remove.mockResolvedValue(undefined)
    const onDone = await mount()
    await userEvent.click(
      await screen.findByRole('radio', { name: /참조를 참조를 비우고 관계를 해제한 뒤 삭제/ }),
    )
    await userEvent.click(screen.getByRole('button', { name: '참조 해제 후 삭제' }))
    await waitFor(() => expect(objectApi.remove).toHaveBeenCalledWith('vendor', 'acme', 'detach'))
    expect(onDone).toHaveBeenCalledWith(null)
  })

  it('「병합」 는 이긴 쪽을 고른 뒤에만 서고, 합친 곳으로 간다', async () => {
    objectApi.references.mockResolvedValue(SOME)
    objectApi.list.mockResolvedValue({
      items: [
        { id: 'acme', label: 'ACME', key: null, status: 'active', properties: {} },
        { id: 'other', label: 'OTHER', key: 'V-2', status: 'active', properties: {} },
      ],
      total: 2,
      limit: 200,
      offset: 0,
    })
    objectApi.merge.mockResolvedValue({
      into: 'other',
      property_refs: 1,
      relations_moved: 1,
      relations_dropped: 0,
    })
    const onDone = await mount()
    await userEvent.click(await screen.findByRole('radio', { name: /다른 공급사에 병합 후 삭제/ }))
    const apply = await screen.findByRole('button', { name: '병합 후 삭제' })
    expect(apply).toBeDisabled()
    await userEvent.click(await screen.findByRole('combobox'))
    const option = await screen.findByRole('button', { name: /OTHER/ })
    // 자기 자신은 후보에 없다.
    expect(screen.queryByRole('button', { name: /^ACME/ })).not.toBeInTheDocument()
    await userEvent.click(option)
    await userEvent.click(screen.getByRole('button', { name: '병합 후 삭제' }))
    await waitFor(() => expect(objectApi.merge).toHaveBeenCalledWith('vendor', 'acme', 'other'))
    expect(onDone).toHaveBeenCalledWith('other')
  })
})
