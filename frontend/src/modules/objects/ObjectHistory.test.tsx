/**
 * 변경 이력이 지키는 것 — **칸별로 전→후를 말하고, 그 시점 값을 나란히 놓고, 되돌리기는
 * 서버의 말을 창 안에 그대로 띄운다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { HistoryEntry } from '@/modules/objects/api'
import type { PropertyDef } from '@/modules/ontology/api'
import { ApiError } from '@/shared/api/client'

const objectApi = vi.hoisted(() => ({
  history: vi.fn(),
  restore: vi.fn(),
  bulkEditUndo: vi.fn(),
}))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

const DEFS = [
  { key: 'weight', label: '무게', data_type: 'number', multi: false, unit: 'kg', decimals: null },
  { key: 'material', label: '재질', data_type: 'enum', multi: false, unit: '' },
] as PropertyDef[]

const ENTRIES: HistoryEntry[] = [
  {
    id: 'e2',
    at: '2026-08-01T10:00:00+09:00',
    actor_label: '박',
    action: 'object.update',
    reason: null,
    kind: 'object',
    changes: {
      'properties.weight': { before: 1.2, after: 0.8 },
      'properties.material': { before: '스틸', after: '알루미늄' },
    },
    relation: null,
    snapshot: {
      key: 'P-1',
      label: '볼트',
      status: 'active',
      properties: { weight: 0.8, material: '알루미늄' },
    },
  },
  {
    id: 'r1',
    at: '2026-06-01T10:00:00+09:00',
    actor_label: '김',
    action: 'object.relation.add',
    reason: null,
    kind: 'relation',
    changes: {},
    relation: { relation: 'supplied_by', outgoing: true, other_id: 'acme', other_label: 'ACME' },
    snapshot: null,
  },
  {
    id: 'e1',
    at: '2026-05-10T10:00:00+09:00',
    actor_label: '이',
    action: 'object.update',
    reason: null,
    kind: 'object',
    changes: { 'properties.weight': { before: 1, after: 1.2 } },
    relation: null,
    snapshot: {
      key: 'P-1',
      label: '볼트',
      status: 'active',
      properties: { weight: 1.2, material: '스틸' },
    },
  },
]

const CURRENT = {
  key: 'P-1',
  label: '볼트',
  status: 'active',
  properties: { weight: 0.8, material: '알루미늄' },
}

async function mount(canEdit = true) {
  const { ObjectHistory } = await import('@/modules/objects/ObjectHistory')
  const onRestored = vi.fn()
  render(
    <ObjectHistory
      typeSlug="part"
      objectId="bolt"
      defs={DEFS}
      current={CURRENT}
      refLabels={{}}
      canEdit={canEdit}
      onRestored={onRestored}
      reloadKey={1}
    />,
  )
  return onRestored
}

describe('변경 이력', () => {
  beforeEach(() => vi.clearAllMocks())

  it('칸별로 전→후를 적고, 관계 기록은 방향과 상대를 적는다', async () => {
    objectApi.history.mockResolvedValue(ENTRIES)
    await mount()
    await waitFor(() => expect(screen.getAllByText('고침')).toHaveLength(2))
    expect(screen.getByText(/→ 알루미늄/)).toBeInTheDocument()
    expect(screen.getByText('관계 맺음')).toBeInTheDocument()
    expect(screen.getByText(/→ ACME/)).toBeInTheDocument()
  })

  it('값 기록을 누르면 그때와 지금이 나란히 뜨고, 다른 칸 수를 말한다', async () => {
    objectApi.history.mockResolvedValue(ENTRIES)
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: /이 .*1\.2/ }))
    expect(await screen.findByText(/지금과 다른 칸 2개/)).toBeInTheDocument()
    // 표: 무게 그때 1.2 / 지금 0.8
    const row = screen.getByRole('cell', { name: '무게' }).closest('tr')!
    expect(row).toHaveTextContent('1.2')
    expect(row).toHaveTextContent('0.8')
  })

  it('되돌리기는 restore 를 부르고, 서버가 막으면 그 말이 창 안에 뜬다', async () => {
    objectApi.history.mockResolvedValue(ENTRIES)
    objectApi.restore.mockRejectedValueOnce(
      new ApiError(422, {
        error: {
          code: 'X-OBJECTS-0004',
          message: '가리키는 객체를 찾을 수 없습니다: acme',
          request_id: 'r',
          details: {},
        },
      }),
    )
    const onRestored = await mount()
    await userEvent.click(await screen.findByRole('button', { name: /이 .*1\.2/ }))
    await userEvent.click(await screen.findByRole('button', { name: '이 값으로 되돌리기' }))
    expect(await screen.findByText(/가리키는 객체를 찾을 수 없습니다/)).toBeInTheDocument()
    expect(onRestored).not.toHaveBeenCalled()
    // 창은 닫히지 않았다.
    expect(screen.getByRole('button', { name: '이 값으로 되돌리기' })).toBeInTheDocument()

    objectApi.restore.mockResolvedValueOnce({})
    await userEvent.click(screen.getByRole('button', { name: '이 값으로 되돌리기' }))
    await waitFor(() => expect(objectApi.restore).toHaveBeenLastCalledWith('part', 'bolt', 'e1'))
    expect(onRestored).toHaveBeenCalled()
  })

  it('가장 최근 기록은 지금 값과 같아 되돌리기가 안 서고, 고칠 수 없는 사람에겐 단추가 없다', async () => {
    objectApi.history.mockResolvedValue(ENTRIES)
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: /박.*0\.8/ }))
    expect(await screen.findByText(/가장 최근 기록이라 지금 값과 같습니다/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '이 값으로 되돌리기' })).toBeDisabled()
  })

  it('고칠 수 없는 사람에게는 되돌리기가 없다', async () => {
    objectApi.history.mockResolvedValue(ENTRIES)
    await mount(false)
    await userEvent.click(await screen.findByRole('button', { name: /이 .*1\.2/ }))
    await screen.findByText(/지금과 다른 칸/)
    expect(screen.queryByRole('button', { name: '이 값으로 되돌리기' })).not.toBeInTheDocument()
  })

  it('여럿 골라 고친 기록이면 함께 바뀐 것을 한 번에 되돌리는 길이 선다', async () => {
    // **한 건만 되돌리면 나머지는 틀린 값으로 남는다.** 틀린 값을 발견하는 자리가 대개
    // 이 이력이라 입구를 여기 둔다.
    objectApi.history.mockResolvedValue([
      { ...ENTRIES[2], batch: { id: 'batch-1', field_label: '무게', size: 40 } },
      ...ENTRIES,
    ])
    objectApi.bulkEditUndo.mockResolvedValue({
      applied: false,
      batch_id: null,
      field: 'properties.weight',
      field_label: '무게',
      counts: { change: 40, unchanged: 0, error: 0 },
      fields: [],
      rows: [],
    })
    await mount()
    const rows = await screen.findAllByTitle('그 시점의 값 보기')
    await userEvent.click(rows[0])
    await userEvent.click(await screen.findByRole('button', { name: /함께 바뀐 40건 되돌리기/ }))
    expect(await screen.findByRole('button', { name: /40건 되돌리기/ })).toBeEnabled()
    expect(objectApi.bulkEditUndo).toHaveBeenCalledWith('part', 'batch-1', false)
  })
})
