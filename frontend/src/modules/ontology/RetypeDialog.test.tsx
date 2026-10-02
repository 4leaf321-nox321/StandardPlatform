/**
 * 종류 변경 창 — **계획을 먼저 보고, 변환할 수 없는 값은 값마다 정해야 적용된다**(ADR 0007).
 *
 * - 계획이 타입별 건수 · 변환할 수 없는 값(견본) · 경고를 다 보인다.
 * - 대체 값을 고치면 계획은 옛것이 된다 — 다시 봐야 적용 단추가 선다.
 * - 값 삭제는 `null` 로 나간다.
 * - 공개 타입이면 수신 시스템 확인 전에는 적용하지 않는다.
 * - 적용되면 창을 닫게 한다(속성 창이 옛 종류를 들고 있다).
 * - 글 ↔ 참조(ADR 0009): 참조로 바꿀 때는 가리킬 타입을 골라야 계획을 본다. 참조에서는 글 ·
 *   긴 글 · 선택으로만 간다.
 * - 값이 많은 타입은 작업으로 계획하고, 그 작업을 적용한다(진행률을 보이며 기다린다).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PropertyDef, RetypeOut } from '@/modules/ontology/api'
import { ApiError } from '@/shared/api/client'

const ontologyApi = vi.hoisted(() => ({
  retypeProperty: vi.fn(),
  retypeInterfaceProperty: vi.fn(),
  retypePropertyJob: vi.fn(),
}))
vi.mock('@/modules/ontology/api', async (original) => {
  const real = await original<typeof import('@/modules/ontology/api')>()
  return { ...real, ontologyApi }
})
const jobsApi = vi.hoisted(() => ({ apply: vi.fn(), waitFor: vi.fn() }))
vi.mock('@/modules/jobs/api', async (original) => {
  const real = await original<typeof import('@/modules/jobs/api')>()
  return { ...real, jobsApi }
})

const WEIGHT = {
  id: 'p1',
  owner_kind: 'type',
  owner_id: 't1',
  key: 'w',
  label: '무게',
  data_type: 'text',
  unit: '',
  help: '',
  required: false,
  multi: false,
  enum_options: null,
  ref_type_slug: null,
  min_value: null,
  max_value: null,
  decimals: null,
  pattern: null,
  default_value: null,
  unique: false,
  section: '',
  sort_order: 0,
} as unknown as PropertyDef

const TYPE = { id: 't1', slug: 'part', label: '부품' }

function plan(extra: Partial<RetypeOut> = {}): RetypeOut {
  return {
    applied: false,
    data_type_before: 'text',
    data_type_after: 'number',
    types: [
      {
        type_slug: 'part',
        type_label: '부품',
        key: 'w',
        via: '',
        with_value: 3,
        converted: 2,
        unchanged: 0,
        cleared: 0,
      },
    ],
    failures: [
      {
        value: '12 kg',
        count: 1,
        reason: '숫자가 아닙니다',
        to: null,
        samples: [{ type_slug: 'part', object_id: 'o1', label: '무거운 것' }],
      },
    ],
    failures_total: 1,
    mapped: [],
    errors: ['속성 part.w: 변환할 수 없는 값이 1종 1건 있습니다'],
    warnings: ['속성 part.w: 시각을 버리고 날짜만 남기는 값 1개.'],
    core_consumers: [],
    snapshot_id: null,
    ...extra,
  }
}

async function open(onDone = vi.fn(), property: PropertyDef = WEIGHT) {
  const { RetypeDialog } = await import('@/modules/ontology/RetypeDialog')
  render(
    <MemoryRouter>
      <RetypeDialog
        owner={{ kind: 'type', row: TYPE as never }}
        property={property}
        types={[{ slug: 'plm_model', label: '개발모델' }]}
        onClose={vi.fn()}
        onDone={onDone}
      />
    </MemoryRouter>,
  )
  return onDone
}

const MODEL_REF = {
  ...WEIGHT,
  key: 'model',
  label: '모델',
  data_type: 'object_ref',
  ref_type_slug: 'plm_model',
} as unknown as PropertyDef

beforeEach(() => {
  vi.clearAllMocks()
})

describe('종류 변경 창', () => {
  it('계획이 건수 · 변환할 수 없는 값과 견본 · 경고를 보이고, 그대로는 적용하지 않는다', async () => {
    ontologyApi.retypeProperty.mockResolvedValue(plan())
    await open()
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))

    await waitFor(() => expect(screen.getByText('12 kg')).toBeInTheDocument())
    expect(ontologyApi.retypeProperty).toHaveBeenCalledWith(
      'part',
      'w',
      expect.objectContaining({ data_type: 'text_long', apply: false }),
    )
    expect(screen.getByRole('link', { name: '무거운 것' })).toHaveAttribute('href', '/o/part/o1')
    expect(screen.getByText(/시각을 버리고/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /종류 변경 —/ })).not.toBeInTheDocument()
  })

  it('대체 값을 적으면 계획을 다시 보고, 그때 적용한다 — 값 삭제는 null 로', async () => {
    ontologyApi.retypeProperty
      .mockResolvedValueOnce(plan())
      .mockResolvedValueOnce(plan({ failures: [], failures_total: 0, errors: [], warnings: [] }))
      .mockResolvedValueOnce(plan({ applied: true, failures: [], errors: [] }))
    const onDone = await open()
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await waitFor(() => expect(screen.getByText('12 kg')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('checkbox', { name: '12 kg 값 삭제' }))
    // 고쳤으니 계획은 옛것이다.
    expect(screen.getByRole('button', { name: '계획 보기' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await waitFor(() =>
      expect(ontologyApi.retypeProperty).toHaveBeenLastCalledWith(
        'part',
        'w',
        expect.objectContaining({ mapping: { '12 kg': null }, apply: false }),
      ),
    )

    await userEvent.click(await screen.findByRole('button', { name: /종류 변경 — 저장값 2개/ }))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(ontologyApi.retypeProperty).toHaveBeenLastCalledWith(
      'part',
      'w',
      expect.objectContaining({ mapping: { '12 kg': null }, apply: true }),
    )
  })

  it('공개 타입이면 수신 시스템 확인 전에는 적용 단추가 없다', async () => {
    ontologyApi.retypeProperty.mockResolvedValue(
      plan({ failures: [], failures_total: 0, errors: [], core_consumers: ['ERP (미사용)'] }),
    )
    await open()
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await waitFor(() => expect(screen.getByText(/ERP \(미사용\)/)).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /종류 변경 —/ })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('checkbox', { name: /통보했습니다/ }))
    expect(screen.getByRole('button', { name: /종류 변경 —/ })).toBeInTheDocument()
  })

  it('글을 참조로 바꿀 때는 가리킬 타입을 골라야 계획을 보고, 그 타입으로 이름을 푼다', async () => {
    ontologyApi.retypeProperty.mockResolvedValue(
      plan({ data_type_after: 'object_ref', failures: [], errors: [], warnings: [] }),
    )
    await open()
    await userEvent.click(screen.getAllByRole('combobox')[0])
    await userEvent.click(await screen.findByRole('option', { name: '객체 참조' }))
    expect(screen.getByRole('button', { name: '계획 보기' })).toBeDisabled()

    await userEvent.click(screen.getAllByRole('combobox')[1])
    await userEvent.click(await screen.findByRole('option', { name: '개발모델' }))
    await userEvent.type(screen.getByLabelText(/상대 쪽에서 읽는 말/), '시장 서비스')
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await waitFor(() =>
      expect(ontologyApi.retypeProperty).toHaveBeenCalledWith(
        'part',
        'w',
        expect.objectContaining({
          data_type: 'object_ref',
          ref_type_slug: 'plm_model',
          inverse_label: '시장 서비스',
          apply: false,
        }),
      ),
    )
  })

  it('참조는 글로 가고, 가리키던 객체의 식별자가 남는다고 말한다', async () => {
    ontologyApi.retypeProperty.mockResolvedValue(
      plan({ data_type_before: 'object_ref', failures: [], errors: [], warnings: [] }),
    )
    await open(vi.fn(), MODEL_REF)
    expect(screen.getByText(/가리키던 객체의/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await waitFor(() =>
      expect(ontologyApi.retypeProperty).toHaveBeenCalledWith(
        'part',
        'model',
        expect.objectContaining({ data_type: 'text', ref_type_slug: null }),
      ),
    )
    await userEvent.click(screen.getAllByRole('combobox')[0])
    const offered = (await screen.findAllByRole('option')).map((one) => one.textContent)
    expect(offered).toEqual(['글 (한 줄)', '글 (여러 줄)', '선택'])
  })

  it('값이 많은 타입은 작업으로 계획하고, 그 작업을 적용한다', async () => {
    ontologyApi.retypeProperty.mockRejectedValue(
      new ApiError(409, { error: { code: 'APP-ONTOLOGY-0067', message: '작업으로 돌립니다' } }),
    )
    ontologyApi.retypePropertyJob.mockResolvedValue({ id: 'plan-job' })
    jobsApi.apply.mockResolvedValue({ id: 'apply-job' })
    jobsApi.waitFor.mockImplementation(async (id: string, onTick?: (job: unknown) => void) => {
      onTick?.({ progress: { stage: '계획', done: 5000, total: 2000000 } })
      const applied = id === 'apply-job'
      return {
        id,
        status: 'done',
        error: null,
        result: plan({ applied, failures: [], failures_total: 0, errors: [], warnings: [] }),
      }
    })
    const onDone = await open()
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await userEvent.click(await screen.findByRole('button', { name: /종류 변경 — 저장값/ }))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(ontologyApi.retypePropertyJob).toHaveBeenCalledWith(
      'part',
      'w',
      expect.objectContaining({ apply: false }),
    )
    expect(jobsApi.apply).toHaveBeenCalledWith('plan-job')
    // 적용은 계획 작업으로 — 요청 경로를 다시 부르지 않는다.
    expect(ontologyApi.retypeProperty).toHaveBeenCalledTimes(1)
  })
})
