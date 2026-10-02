/**
 * 표에서 타입 생성 — **참조 후보**(ADR 0009).
 *
 * - 서버가 참조로 제안한 열은 후보 줄과 함께 오고, 그대로 계획을 보내면 참조 열로 간다.
 * - 고른 후보의 못 찾은 · 여럿에 맞는 값은 견본으로 보인다 — 넣을 때 그 행이 오류다.
 * - 제안되지 않은 열도 후보를 누르면 그 타입을 가리키는 참조 열이 된다. 왜 제안하지 않았는지도 보인다.
 * - 가리킬 타입이 없는 참조 열이 있으면 계획을 보내지 않는다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { InferColumn, InferResult, RefCandidate } from '@/modules/ontology/api'

const ontologyApi = vi.hoisted(() => ({
  infer: vi.fn(),
  inferBuild: vi.fn(),
  importSchema: vi.fn(),
}))
vi.mock('@/modules/ontology/api', async (original) => {
  const real = await original<typeof import('@/modules/ontology/api')>()
  return { ...real, ontologyApi }
})
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { home_workspace_slug: 'hq', memberships: [] } }),
}))

import { InferFromTablePanel } from './InferFromTablePanel'

function candidate(extra: Partial<RefCandidate>): RefCandidate {
  return {
    target_slug: 'plm_model',
    target_label: '개발모델',
    target_kind: 'type',
    checked: 20,
    one: 19,
    many: 0,
    none: 1,
    one_values: 5,
    many_samples: [],
    none_samples: ['X-9'],
    short: false,
    ...extra,
  }
}

function column(extra: Partial<InferColumn>): InferColumn {
  return {
    header: '',
    role: 'property',
    key: '',
    label: '',
    data_type: 'text',
    multi: false,
    enum_options: [],
    decimals: null,
    filled: 20,
    distinct: 5,
    samples: [],
    note: '',
    ref_type_slug: null,
    ref_candidates: [],
    ref_note: '',
    ...extra,
  }
}

function result(columns: InferColumn[]): InferResult {
  return { rows: 20, columns, raw_rows: [] }
}

const NAME = column({ header: '이름', role: 'label', label: '이름' })

async function open(columns: InferColumn[]) {
  ontologyApi.infer.mockResolvedValue(result(columns))
  const { container } = render(
    <InferFromTablePanel
      groups={[]}
      types={[
        { slug: 'plm_model', label: '개발모델' },
        { slug: 'plm_task', label: '과제' },
      ]}
      interfaces={[]}
      onChanged={vi.fn()}
    />,
  )
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  await userEvent.upload(input, new File(['x'], 'cases.csv', { type: 'text/csv' }))
  await userEvent.type(await screen.findByLabelText('타입 slug'), 'svc_case')
}

/** 계획 단추를 눌러 서버에 간 열 — 이름으로. */
async function sentColumns(): Promise<Record<string, InferColumn>> {
  await userEvent.click(screen.getByRole('button', { name: /정의 계획 보기/ }))
  await waitFor(() => expect(ontologyApi.inferBuild).toHaveBeenCalled())
  const body = ontologyApi.inferBuild.mock.calls[0][0] as { columns: InferColumn[] }
  return Object.fromEntries(body.columns.map((one) => [one.header, one]))
}

describe('표에서 타입 생성 — 참조 후보', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ontologyApi.inferBuild.mockResolvedValue({ schema: {}, import_rows: [] })
    ontologyApi.importSchema.mockResolvedValue({
      applied: false,
      changes: [],
      warnings: [],
      errors: [],
    })
  })

  it('제안된 참조 열은 후보와 못 찾은 견본을 보이고 그대로 참조로 보낸다', async () => {
    await open([
      NAME,
      column({
        header: '모델',
        key: 'model',
        data_type: 'object_ref',
        ref_type_slug: 'plm_model',
        note: '개발모델 의 식별자 · 이름으로 95% 가 풀립니다 — 참조로 둡니다',
        ref_candidates: [candidate({})],
      }),
    ])
    expect(screen.getByText(/개발모델 — 하나로 95%/)).toHaveTextContent('못 찾음 1')
    expect(screen.getByText(/못 찾음: X-9 — 그 행은 넣을 때 오류입니다/)).toBeInTheDocument()

    const sent = await sentColumns()
    expect(sent['모델']).toMatchObject({ data_type: 'object_ref', ref_type_slug: 'plm_model' })
  })

  it('제안되지 않은 열도 후보를 누르면 그 타입을 가리키는 참조 열이 된다', async () => {
    await open([
      NAME,
      column({
        header: '과제',
        key: 'task',
        data_type: 'enum',
        enum_options: ['T-1', 'T-2'],
        ref_candidates: [
          candidate({
            target_slug: 'plm_task',
            target_label: '과제',
            one: 12,
            many: 3,
            none: 5,
            many_samples: ['같은 이름'],
          }),
        ],
        ref_note: '',
      }),
      column({
        header: '판',
        key: 'rev',
        data_type: 'number',
        ref_candidates: [candidate({ one: 20, none: 0, short: true, none_samples: [] })],
        ref_note: '맞은 값이 전부 짧은 숫자라 우연일 수 있어 제안하지 않습니다',
      }),
    ])
    expect(screen.getByText(/짧은 숫자라 우연일 수 있어/)).toBeInTheDocument()
    // 고르기 전에는 막힐 값을 안 보인다 — 아직 이 열을 잇기로 한 것이 아니다.
    expect(screen.queryByText(/여럿에 맞음: 같은 이름/)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /과제 — 하나로 60%/ }))
    expect(screen.getByText(/여럿에 맞음: 같은 이름 — 넣을 때 거절됩니다/)).toBeInTheDocument()

    const sent = await sentColumns()
    expect(sent['과제']).toMatchObject({
      data_type: 'object_ref',
      ref_type_slug: 'plm_task',
      enum_options: [],
    })
    expect(sent['판']).toMatchObject({ data_type: 'number' })
  })

  it('가리킬 타입이 없는 참조 열이 있으면 계획을 보내지 않는다', async () => {
    await open([NAME, column({ header: '모델', key: 'model', data_type: 'object_ref' })])
    expect(screen.getByText(/참조 열이 가리킬 타입을 고르세요 — 모델/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /정의 계획 보기/ })).toBeDisabled()
  })
})
