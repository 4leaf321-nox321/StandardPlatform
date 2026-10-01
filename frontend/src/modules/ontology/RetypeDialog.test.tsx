/**
 * 종류 변경 창 — **계획을 먼저 보고, 변환할 수 없는 값은 값마다 정해야 적용된다**(ADR 0007).
 *
 * - 계획이 타입별 건수 · 변환할 수 없는 값(견본) · 경고를 다 보인다.
 * - 대체 값을 고치면 계획은 옛것이 된다 — 다시 봐야 적용 단추가 선다.
 * - 값 삭제는 `null` 로 나간다.
 * - 공개 타입이면 수신 시스템 확인 전에는 적용하지 않는다.
 * - 적용되면 창을 닫게 한다(속성 창이 옛 종류를 들고 있다).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PropertyDef, RetypeOut } from '@/modules/ontology/api'

const ontologyApi = vi.hoisted(() => ({
  retypeProperty: vi.fn(),
  retypeInterfaceProperty: vi.fn(),
}))
vi.mock('@/modules/ontology/api', async (original) => {
  const real = await original<typeof import('@/modules/ontology/api')>()
  return { ...real, ontologyApi }
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

async function open(onDone = vi.fn()) {
  const { RetypeDialog } = await import('@/modules/ontology/RetypeDialog')
  render(
    <MemoryRouter>
      <RetypeDialog
        owner={{ kind: 'type', row: TYPE as never }}
        property={WEIGHT}
        onClose={vi.fn()}
        onDone={onDone}
      />
    </MemoryRouter>,
  )
  return onDone
}

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
})
