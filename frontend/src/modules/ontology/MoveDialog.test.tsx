/**
 * 객체를 다른 타입으로 옮기기 창(ADR 0094) — **칸 대응을 보이고 사람이 고친 것만 보낸다.**
 *
 * 지키는 것: 대상을 골라야 계획을 보는가, 칸마다 대응 · 값 건수 · 막는 까닭을 보이는가, 막는 것이
 * 있으면 옮기기 단추가 서지 않는가, 줄의 「버림」 이 mapping 의 null 로 가는가, 겹치는 객체 병합을
 * 보내는가, 옮긴 뒤 알리는가, 적용이 미리 본 계획의 지문을 싣고 「미리 본 뒤 바뀜」(ONTOLOGY-0114)
 * 이면 계획을 비우는가.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MoveDialog } from '@/modules/ontology/MoveDialog'
import { ApiError } from '@/shared/api/client'

const ontologyApi = vi.hoisted(() => ({ moveType: vi.fn(), moveTypeJob: vi.fn() }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))
const jobsApi = vi.hoisted(() => ({ apply: vi.fn(), waitFor: vi.fn() }))
vi.mock('@/modules/jobs/api', () => ({ jobsApi }))

function plan(over: Record<string, unknown> = {}) {
  return {
    source: 'vendor',
    target: 'supplier',
    label: '협력사 → 공급사',
    applied: false,
    objects: 300,
    live: 290,
    duplicates: 0,
    fields: [
      {
        source: 'w',
        label: '무게',
        target: 'weight',
        action: 'map',
        values: 120,
        note: '',
        blocked: false,
      },
      {
        source: 'memo',
        label: '메모',
        target: 'memo',
        action: 'create',
        values: 40,
        note: '',
        blocked: false,
      },
    ],
    places: [{ what: '옮길 객체(지운 것 포함)', count: 300 }],
    warnings: ['옮긴 뒤 「협력사」 타입은 빈 채로 남습니다'],
    errors: [],
    core_consumers: [],
    snapshot_id: null,
    moved: 0,
    merged: 0,
    ...over,
  }
}

function open() {
  const onDone = vi.fn()
  render(
    <MoveDialog
      type={{ slug: 'vendor', label: '협력사' }}
      targets={[{ slug: 'supplier', label: '공급사' }]}
      onDone={onDone}
      onClose={vi.fn()}
    />,
  )
  return onDone
}

describe('객체를 다른 타입으로 옮기기', () => {
  beforeEach(() => vi.clearAllMocks())

  it('대상을 골라 칸 대응을 보고, 고친 줄만 보내며 옮긴다', async () => {
    ontologyApi.moveType
      .mockResolvedValueOnce(plan())
      .mockResolvedValueOnce(plan())
      .mockResolvedValueOnce(plan({ applied: true, moved: 300 }))
    const onDone = open()
    expect(screen.getByRole('button', { name: '계획 보기' })).toBeDisabled()
    await userEvent.selectOptions(screen.getByLabelText('대상 타입'), 'supplier')
    await userEvent.click(screen.getByRole('checkbox', { name: /겹치는 객체 병합/ }))
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    expect(await screen.findByText(/계획대로 — 다른 키로 weight/)).toBeInTheDocument()
    expect(ontologyApi.moveType.mock.calls[0][1]).toMatchObject({
      to: 'supplier',
      mapping: {},
      merge_duplicates: true,
      apply: false,
    })
    // 메모는 버린다 — 사람이 고른 것만 mapping 에 실린다.
    await userEvent.selectOptions(screen.getByLabelText('메모 대응'), '\u0000drop')
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    expect(ontologyApi.moveType.mock.calls[1][1].mapping).toEqual({ memo: null })
    await userEvent.click(await screen.findByRole('button', { name: /옮기기 — 객체 300건/ }))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(ontologyApi.moveType.mock.calls[2][1].apply).toBe(true)
  })

  it('막는 것이 있으면 옮기기 단추가 서지 않고 줄에 까닭을 적는다', async () => {
    ontologyApi.moveType.mockResolvedValue(
      plan({
        fields: [
          {
            source: 'country',
            label: '국가',
            target: 'country',
            action: 'same',
            values: 3,
            note: '모양이 다릅니다(글 (한 줄) → 숫자)',
            blocked: true,
          },
        ],
        errors: ['속성 「국가」(country): 모양이 다릅니다(글 (한 줄) → 숫자)'],
      }),
    )
    open()
    await userEvent.selectOptions(screen.getByLabelText('대상 타입'), 'supplier')
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    expect(await screen.findByText('지금은 옮길 수 없습니다')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /옮기기 — / })).toBeNull()
  })

  it('옮기기는 미리 본 계획의 지문을 싣는다 — 계획 보기에는 싣지 않는다', async () => {
    ontologyApi.moveType
      .mockResolvedValueOnce(plan({ fingerprint: 'fp-1' }))
      .mockResolvedValueOnce(plan({ applied: true, moved: 300, fingerprint: 'fp-1' }))
    const onDone = open()
    await userEvent.selectOptions(screen.getByLabelText('대상 타입'), 'supplier')
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await userEvent.click(await screen.findByRole('button', { name: /옮기기 — 객체 300건/ }))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(ontologyApi.moveType.mock.calls[0][1]).not.toHaveProperty('expected_fingerprint')
    expect(ontologyApi.moveType.mock.calls[1][1]).toMatchObject({
      apply: true,
      expected_fingerprint: 'fp-1',
    })
  })

  it('미리 본 뒤 바뀌었으면(ONTOLOGY-0114) 그 말을 보이고 계획을 비워 다시 보게 한다', async () => {
    ontologyApi.moveType
      .mockResolvedValueOnce(plan({ fingerprint: 'fp-1' }))
      .mockRejectedValueOnce(
        new ApiError(409, {
          error: {
            code: 'APP-ONTOLOGY-0114',
            message: '미리 본 뒤 바뀌었습니다 — 다시 미리 보세요.',
          },
        }),
      )
      .mockResolvedValueOnce(plan({ duplicates: 2, fingerprint: 'fp-2' }))
    const onDone = open()
    await userEvent.selectOptions(screen.getByLabelText('대상 타입'), 'supplier')
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await userEvent.click(await screen.findByRole('button', { name: /옮기기 — 객체 300건/ }))
    expect(await screen.findByText(/미리 본 뒤 바뀌었습니다/)).toBeInTheDocument()
    // 옛 칸 대응 · 단추는 비운다 — 「계획 보기」 로 새 계획을 읽은 뒤에 옮긴다.
    expect(screen.queryByText(/계획대로 — 다른 키로 weight/)).toBeNull()
    expect(screen.queryByRole('button', { name: /옮기기 — / })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await waitFor(() => expect(ontologyApi.moveType).toHaveBeenCalledTimes(3))
    expect(ontologyApi.moveType.mock.calls[2][1].apply).toBe(false)
    expect(onDone).not.toHaveBeenCalled()
  })
})
