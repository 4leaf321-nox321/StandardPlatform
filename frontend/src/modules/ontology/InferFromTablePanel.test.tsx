/**
 * 표에서 타입 생성 — **참조 후보**(ADR 0009).
 *
 * - 서버가 참조로 제안한 열은 후보 줄과 함께 오고, 그대로 계획을 보내면 참조 열로 간다.
 * - 고른 후보의 못 찾은 · 여럿에 맞는 값은 견본으로 보인다 — 넣을 때 그 행이 오류다.
 * - 제안되지 않은 열도 후보를 누르면 그 타입을 가리키는 참조 열이 된다. 왜 제안하지 않았는지도 보인다.
 * - 가리킬 타입이 없는 참조 열이 있으면 계획을 보내지 않는다.
 * - 정의의 적용은 「정의 계획 보기」 의 지문을 싣고, 그 사이 바뀌었으면(ONTOLOGY-0114) 계획을 비운다.
 * - 정의는 들어가고 행 추가만 실패했으면, 다시 클릭할 때 같은 정의는 다시 적용하지 않고 행만 넣는다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { InferColumn, InferResult, RefCandidate } from '@/modules/ontology/api'
import { ApiError } from '@/shared/api/client'

const ontologyApi = vi.hoisted(() => ({
  infer: vi.fn(),
  inferBuild: vi.fn(),
  importSchema: vi.fn(),
}))
vi.mock('@/modules/ontology/api', async (original) => {
  const real = await original<typeof import('@/modules/ontology/api')>()
  return { ...real, ontologyApi }
})
const objectApi = vi.hoisted(() => ({ importRows: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))
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
    // 표에서 만든 타입은 기본이 기록이다(ADR 0011) — 표는 대개 축을 가리키는 쪽이다.
    expect(ontologyApi.inferBuild.mock.calls[0][0]).toMatchObject({ usage: 'log' })
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

  it('정의의 적용은 계획의 지문을 싣고, 그 사이 바뀌었으면(ONTOLOGY-0114) 계획을 비운다', async () => {
    const PLANNED = { applied: false, changes: [], warnings: [], errors: [], fingerprint: 'fp-1' }
    ontologyApi.importSchema.mockResolvedValueOnce(PLANNED).mockRejectedValueOnce(
      new ApiError(409, {
        error: {
          code: 'APP-ONTOLOGY-0114',
          message: '미리 본 뒤 바뀌었습니다 — 다시 미리 보세요.',
        },
      }),
    )
    await open([NAME])
    await userEvent.click(screen.getByRole('button', { name: /정의 계획 보기/ }))
    expect(await screen.findByText(/정의 계획 —/)).toBeInTheDocument()
    expect(ontologyApi.importSchema).toHaveBeenLastCalledWith({}, true)

    await userEvent.click(screen.getByRole('button', { name: /타입 만들고 20행 추가/ }))
    await waitFor(() =>
      expect(ontologyApi.importSchema).toHaveBeenLastCalledWith({}, false, 'fp-1'),
    )
    expect(await screen.findByText(/미리 본 뒤 바뀌었습니다/)).toBeInTheDocument()
    // 옛 계획은 비운다 — 다시 「정의 계획 보기」 를 거쳐야 적용이 선다.
    expect(screen.queryByText(/정의 계획 —/)).toBeNull()
    expect(screen.getByRole('button', { name: /타입 만들고 20행 추가/ })).toBeDisabled()
  })

  it('행 추가만 실패했으면 다시 클릭할 때 정의는 다시 적용하지 않고 행만 넣는다', async () => {
    const PLANNED = {
      applied: false,
      changes: [{ kind: 'type', slug: 'svc_case', action: 'create', fields: [] }],
      warnings: [],
      errors: [],
      fingerprint: 'fp-1',
    }
    ontologyApi.importSchema
      .mockResolvedValueOnce(PLANNED)
      .mockResolvedValueOnce({ ...PLANNED, applied: true })
      // 이미 생긴 타입에 옛 지문으로 다시 적용하면 계획이 「그대로」 라 막힌다.
      .mockRejectedValueOnce(
        new ApiError(409, {
          error: {
            code: 'APP-ONTOLOGY-0114',
            message: '미리 본 뒤 바뀌었습니다 — 다시 미리 보세요.',
          },
        }),
      )
    objectApi.importRows
      .mockRejectedValueOnce(new Error('연결이 끊겼습니다'))
      .mockResolvedValueOnce({
        applied: true,
        counts: { create: 20, update: 0, error: 0 },
        errors: [],
        rows: [],
      })
    await open([NAME])
    await userEvent.click(screen.getByRole('button', { name: /정의 계획 보기/ }))
    await screen.findByText(/정의 계획 —/)
    const add = screen.getByRole('button', { name: /타입 만들고 20행 추가/ })
    await userEvent.click(add)
    expect(await screen.findByText('연결이 끊겼습니다')).toBeInTheDocument()
    expect(screen.getByText(/정의를 만들었습니다/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /타입 만들고 20행 추가/ }))
    expect(await screen.findByText(/행을 넣었습니다 — 새로 20/)).toBeInTheDocument()
    // 정의는 미리 보기 한 번 · 적용 한 번뿐이다 — 행만 다시 넣었다.
    expect(ontologyApi.importSchema).toHaveBeenCalledTimes(2)
    expect(objectApi.importRows).toHaveBeenCalledTimes(2)
    expect(screen.queryByText(/미리 본 뒤 바뀌었습니다/)).toBeNull()
  })
})
