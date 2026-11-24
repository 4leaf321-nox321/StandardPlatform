/**
 * 키 · slug 변경 창(ADR 0093) — **계획을 먼저 보이고, 막는 것이 있으면 변경하지 않는다.**
 *
 * 지키는 것: 바뀌는 자리 · 알아 둘 것 · 막는 것을 보이는가, 막는 것이 있으면 변경 단추가 서지
 * 않는가, 공개 타입은 통보 확인을 받는가, 값이 많은 키는 작업으로 계획하고 그 작업을 적용하는가.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { RenameDialog } from '@/modules/ontology/RenameDialog'
import { ApiError } from '@/shared/api/client'

const ontologyApi = vi.hoisted(() => ({
  renameKey: vi.fn(),
  renameKeyJob: vi.fn(),
  renameType: vi.fn(),
}))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))
const jobsApi = vi.hoisted(() => ({ apply: vi.fn(), waitFor: vi.fn() }))
vi.mock('@/modules/jobs/api', () => ({ jobsApi }))

function plan(over: Record<string, unknown> = {}) {
  return {
    kind: 'property',
    slug: 'part',
    key: 'w',
    to: 'weight',
    label: '부품 · 무게',
    applied: false,
    objects: 3,
    places: [
      { what: '객체 값', count: 3 },
      { what: '저장된 뷰(조건 · 통계 기준)', count: 1 },
    ],
    warnings: ['바깥에 적힌 것은 바꾸지 않습니다 — 정제 키트의 정의'],
    errors: [],
    core_consumers: [],
    snapshot_id: null,
    ...over,
  }
}

function open(kind: 'property' | 'type' = 'property') {
  const onDone = vi.fn()
  render(
    <RenameDialog
      kind={kind}
      slug="part"
      propertyKey={kind === 'property' ? 'w' : undefined}
      label="부품 · 무게"
      onDone={onDone}
      onClose={vi.fn()}
    />,
  )
  return onDone
}

describe('키 · slug 변경', () => {
  beforeEach(() => vi.clearAllMocks())

  it('계획을 보이고 변경하면 새 키를 넘긴다', async () => {
    ontologyApi.renameKey
      .mockResolvedValueOnce(plan())
      .mockResolvedValueOnce(plan({ applied: true, snapshot_id: 's1' }))
    const onDone = open()
    await userEvent.type(screen.getByLabelText('새 키'), 'weight')
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    expect(ontologyApi.renameKey).toHaveBeenCalledWith('part', 'w', {
      to: 'weight',
      apply: false,
      accept_core: false,
    })
    expect(await screen.findByText('객체 값 3')).toBeInTheDocument()
    expect(screen.getByText(/정제 키트의 정의/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '키 변경' }))
    await waitFor(() => expect(onDone).toHaveBeenCalledWith('weight'))
  })

  it('막는 것이 있으면 변경 단추가 서지 않는다', async () => {
    ontologyApi.renameKey.mockResolvedValue(
      plan({ errors: ['부품에 이미 키 「weight」 인 속성이 있습니다.'] }),
    )
    open()
    await userEvent.type(screen.getByLabelText('새 키'), 'weight')
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    expect(await screen.findByText(/이미 키 「weight」/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '키 변경' })).toBeNull()
  })

  it('공개 타입은 통보 확인을 받아야 변경한다 — slug', async () => {
    ontologyApi.renameType
      .mockResolvedValueOnce(plan({ kind: 'type', key: null, core_consumers: ['MatNexus'] }))
      .mockResolvedValueOnce(plan({ kind: 'type', key: null, applied: true, to: 'component' }))
    const onDone = open('type')
    await userEvent.type(screen.getByLabelText('새 slug'), 'component')
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    expect(await screen.findByText(/MatNexus/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'slug 변경' })).toBeNull()
    await userEvent.click(screen.getByRole('checkbox'))
    await userEvent.click(screen.getByRole('button', { name: 'slug 변경' }))
    expect(ontologyApi.renameType).toHaveBeenLastCalledWith('part', {
      to: 'component',
      apply: true,
      accept_core: true,
    })
    await waitFor(() => expect(onDone).toHaveBeenCalledWith('component'))
  })

  it('값이 많은 키는 작업으로 계획하고 그 작업을 적용한다', async () => {
    ontologyApi.renameKey
      .mockResolvedValueOnce(plan())
      .mockRejectedValueOnce(
        new ApiError(409, { error: { code: 'APP-ONTOLOGY-0105', message: '작업으로' } }),
      )
    ontologyApi.renameKeyJob.mockResolvedValue({ id: 'j1' })
    jobsApi.apply.mockResolvedValue({ id: 'j2' })
    jobsApi.waitFor
      .mockResolvedValueOnce({ status: 'done', result: plan() })
      .mockResolvedValueOnce({ status: 'done', result: plan({ applied: true }) })
    const onDone = open()
    await userEvent.type(screen.getByLabelText('새 키'), 'weight')
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await userEvent.click(await screen.findByRole('button', { name: '키 변경' }))
    await waitFor(() => expect(ontologyApi.renameKeyJob).toHaveBeenCalled())
    expect(ontologyApi.renameKeyJob.mock.calls[0][2].apply).toBe(false)
    await userEvent.click(await screen.findByRole('button', { name: '키 변경' }))
    await waitFor(() => expect(jobsApi.apply).toHaveBeenCalledWith('j1'))
    await waitFor(() => expect(onDone).toHaveBeenCalledWith('weight'))
  })
})
