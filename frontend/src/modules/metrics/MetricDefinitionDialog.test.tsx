/**
 * 정의 대화상자가 지키는 것 — **계획을 먼저 보고, 통과한 뒤에만 저장하고 센다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const metricsApi = vi.hoisted(() => ({
  list: vi.fn(),
  plan: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
}))
const jobsApi = vi.hoisted(() => ({ waitFor: vi.fn() }))
const ontologyApi = vi.hoisted(() => ({ schema: vi.fn() }))
const objectApi = vi.hoisted(() => ({ fields: vi.fn() }))
vi.mock('@/modules/metrics/api', () => ({ metricsApi }))
vi.mock('@/modules/jobs/api', () => ({ jobsApi }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))
vi.mock('@/modules/objects/ConditionBar', () => ({ ConditionBar: () => <div>조건 막대</div> }))

const SCHEMA = {
  groups: [],
  relation_types: [],
  data_types: [],
  types: [
    {
      slug: 'plm_model',
      label: '개발모델',
      kind_class: 'reference',
      is_active: true,
      usage: 'axis',
      properties: [],
    },
    {
      slug: 'svc_case',
      label: '시장 서비스',
      kind_class: 'record',
      is_active: true,
      usage: 'log',
      properties: [
        {
          id: 'p1',
          owner_kind: 'type',
          owner_id: 't',
          key: 'received',
          label: '접수일',
          data_type: 'date',
          unit: '',
          help: '',
          required: false,
          multi: false,
          enum_options: null,
          ref_type_slug: null,
        },
        {
          id: 'p2',
          owner_kind: 'type',
          owner_id: 't',
          key: 'symptom',
          label: '증상',
          data_type: 'enum',
          unit: '',
          help: '',
          required: false,
          multi: false,
          enum_options: ['소음'],
          ref_type_slug: null,
        },
      ],
    },
  ],
}

async function mount(onSaved = vi.fn()) {
  ontologyApi.schema.mockResolvedValue(SCHEMA)
  objectApi.fields.mockResolvedValue([
    {
      field: 'ref.model.series',
      label: '모델 › 시리즈',
      heading: '모델',
      data_type: 'enum',
      multi: false,
      enum_options: null,
      ref_type_slug: null,
    },
  ])
  metricsApi.list.mockResolvedValue([])
  const { MetricDefinitionDialog } = await import('@/modules/metrics/MetricDefinitionDialog')
  render(<MetricDefinitionDialog existing={null} onClose={vi.fn()} onSaved={onSaved} />)
  return onSaved
}

describe('지표 정의', () => {
  it('끊긴 질문의 초안으로 채워 열고, 그 정의로 계획을 본다', async () => {
    // ADR 0032 — 저장하지 않은 초안. 사람이 계획을 보고 고쳐 저장한다.
    ontologyApi.schema.mockResolvedValue(SCHEMA)
    objectApi.fields.mockResolvedValue([])
    metricsApi.list.mockResolvedValue([])
    metricsApi.plan.mockResolvedValue({
      ok: true,
      errors: [],
      warnings: [],
      rows: 8,
      estimated_cells: 24,
      overlap: false,
      dims: [],
      period_from: null,
      period_to: null,
    })
    const { MetricDefinitionDialog } = await import('@/modules/metrics/MetricDefinitionDialog')
    render(
      <MetricDefinitionDialog
        existing={null}
        draft={{
          slug: 'cut_1a2b3c4d',
          label: '시장 서비스 — symptom (끊긴 질문)',
          description: '끊긴 질문에서 지은 초안',
          source_type_slug: 'svc_case',
          spec: {
            measure: 'count',
            dimensions: [{ name: 'symptom', address: 'properties.symptom' }],
            filters: [],
            settle_days: 0,
          },
        }}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    )
    expect(await screen.findByRole('heading', { name: '새 지표' })).toBeInTheDocument()
    expect(screen.getByLabelText('이름')).toHaveValue('시장 서비스 — symptom (끊긴 질문)')
    expect(screen.getByLabelText('기준 1 주소')).toHaveValue('properties.symptom')
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await waitFor(() => expect(metricsApi.plan).toHaveBeenCalled())
    const sent = metricsApi.plan.mock.calls.at(-1)?.[0]
    expect(sent.slug).toBe('cut_1a2b3c4d')
    expect(sent.source_type_slug).toBe('svc_case')
    // 초안에 시간 칸이 없으면 첫 날짜 칸을 저절로 고르지 않는다 — 초안이 정한 대로.
    expect(sent.spec.time ?? null).toBeNull()
    expect(sent.spec.dimensions).toEqual([
      expect.objectContaining({ name: 'symptom', address: 'properties.symptom' }),
    ])
    // 뒤의 시험이 첫 계획 호출을 읽는다 — 이 시험의 호출을 남기지 않는다.
    metricsApi.plan.mockClear()
  })

  it('기록 타입과 첫 날짜 칸을 미리 고르고, 계획의 오류를 보여 준다', async () => {
    metricsApi.plan.mockResolvedValue({
      ok: false,
      errors: ['기준 이름으로 쓸 수 없는 예약어입니다: period'],
      warnings: [],
      rows: null,
      estimated_cells: null,
      overlap: false,
      dims: [],
      period_from: null,
      period_to: null,
    })
    await mount()
    await waitFor(() => expect(screen.getByText('시장 서비스 (기록)')).toBeInTheDocument())
    await userEvent.type(screen.getByLabelText('이름'), '월별 인입')
    await userEvent.click(screen.getByRole('button', { name: '기준 추가' }))
    await userEvent.type(screen.getByLabelText('기준 1 이름'), 'period')
    await userEvent.type(screen.getByLabelText('기준 1 주소'), 'properties.symptom')
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await waitFor(() => expect(metricsApi.plan).toHaveBeenCalled())
    const sent = metricsApi.plan.mock.calls[0][0]
    expect(sent.source_type_slug).toBe('svc_case')
    expect(sent.spec.time).toEqual({ address: 'properties.received', grain: 'month' })
    expect(sent.spec.dimensions).toEqual([
      { name: 'period', address: 'properties.symptom', grain: null },
    ])
    await waitFor(() =>
      expect(screen.getByText('기준 이름으로 쓸 수 없는 예약어입니다: period')).toBeInTheDocument(),
    )
    // 계획이 통과하지 않으면 저장은 눌리지 않는다.
    expect(screen.getByRole('button', { name: '저장하고 계산' })).toBeDisabled()
  })

  it('계획이 통과하면 저장하고 세며, 작업이 끝나기를 기다린다', async () => {
    metricsApi.plan.mockResolvedValue({
      ok: true,
      errors: [],
      warnings: ['여러 값 기준이 있어 겹침'],
      rows: 8,
      estimated_cells: 24,
      overlap: true,
      dims: [
        {
          name: 'symptom',
          address: 'properties.symptom',
          label: '증상',
          kind: 'enum',
          multi: false,
          grain: null,
          distinct: 3,
        },
      ],
      period_from: '2026-01-01',
      period_to: '2026-04-01',
    })
    metricsApi.create.mockResolvedValue({ metric: { slug: 'cases' }, job: { id: 'j1' } })
    jobsApi.waitFor.mockResolvedValue({ id: 'j1', status: 'done' })
    const onSaved = await mount()
    await waitFor(() => expect(screen.getByText('시장 서비스 (기록)')).toBeInTheDocument())
    await userEvent.type(screen.getByLabelText('이름'), 'cases')
    await userEvent.click(screen.getByRole('button', { name: '기준 추가' }))
    await userEvent.type(screen.getByLabelText('기준 1 이름'), 'symptom')
    await userEvent.type(screen.getByLabelText('기준 1 주소'), 'properties.symptom')
    await userEvent.click(screen.getByRole('button', { name: '계획 보기' }))
    await waitFor(() => expect(screen.getByText(/기록 8건 · 어림한 셀 24개/)).toBeInTheDocument())
    expect(screen.getByText('여러 값 기준이 있어 겹침')).toBeInTheDocument()
    expect(screen.getByText(/symptom = 증상\(enum, 3가지\)/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '저장하고 계산' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ slug: 'cases' }))
    expect(metricsApi.create).toHaveBeenCalledWith(
      expect.objectContaining({ slug: 'cases', label: 'cases', source_type_slug: 'svc_case' }),
      true,
    )
    expect(jobsApi.waitFor).toHaveBeenCalledWith('j1')
  })

  it('규칙 기준 — 불러와 차례를 바꾸면 그 차례로 계획을 보고, 미리 보기가 그 자리에 선다', async () => {
    ontologyApi.schema.mockResolvedValue(SCHEMA)
    objectApi.fields.mockResolvedValue([])
    metricsApi.list.mockResolvedValue([])
    metricsApi.plan.mockResolvedValue({
      ok: true,
      errors: [],
      warnings: [],
      rows: 10,
      estimated_cells: 6,
      overlap: false,
      dims: [],
      period_from: null,
      period_to: null,
      rules: [
        {
          name: 'nature',
          label: '성격',
          fingerprint: 'a'.repeat(64),
          rules: [
            { label: '고객과실', matched: 3, won: 3, taken_by: [], blocked: false },
            {
              label: 'SW',
              matched: 1,
              won: 0,
              taken_by: [{ rule: 1, count: 1 }],
              blocked: true,
            },
          ],
          other: '기타',
          other_count: 6,
          seen: 10,
          sampled: false,
        },
      ],
    })
    const existing = {
      slug: 'nature_monthly',
      label: '성격',
      description: '',
      source_type_slug: 'svc_case',
      interval_hours: 24,
      spec: {
        measure: 'count',
        time: { address: 'properties.received', grain: 'month' },
        dimensions: [
          {
            name: 'nature',
            label: '성격',
            rules: [
              { label: 'SW', when: [{ field: 'action', op: 'starts', value: 'SW' }] },
              { label: '고객과실', when: [{ field: 'diag', op: 'in', value: 'C01|C02' }] },
            ],
            else: '기타',
          },
        ],
        filters: [],
        settle_days: 0,
      },
    }
    const { MetricDefinitionDialog } = await import('@/modules/metrics/MetricDefinitionDialog')
    render(
      <MetricDefinitionDialog
        existing={existing as unknown as import('@/modules/metrics/api').Metric}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    )
    expect(await screen.findByLabelText('규칙 1 이름')).toHaveValue('SW')
    await userEvent.click(screen.getByRole('button', { name: '규칙 2 위로' }))
    expect(screen.getByLabelText('규칙 1 이름')).toHaveValue('고객과실')
    await userEvent.click(screen.getByRole('button', { name: '규칙 미리 보기' }))
    await waitFor(() => expect(metricsApi.plan).toHaveBeenCalled())
    const sent = metricsApi.plan.mock.calls.at(-1)?.[0]
    expect(sent.spec.dimensions).toEqual([
      {
        name: 'nature',
        label: '성격',
        rules: [
          { label: '고객과실', when: [{ field: 'diag', op: 'in', value: 'C01|C02' }] },
          { label: 'SW', when: [{ field: 'action', op: 'starts', value: 'SW' }] },
        ],
        else: '기타',
      },
    ])
    expect(await screen.findByLabelText('규칙 1 미리 보기')).toHaveTextContent('3건 · 30%')
    expect(screen.getByLabelText('규칙 2 미리 보기')).toHaveTextContent('막힌 규칙')
    expect(screen.getByLabelText('기타 미리 보기')).toHaveTextContent('6건 · 60%')
    // 규칙을 바꾸면 미리 보기는 사라진다 — 지난 계획으로 판단하지 않게.
    await userEvent.click(screen.getByRole('button', { name: '규칙 1 아래로' }))
    expect(screen.queryByLabelText('규칙 1 미리 보기')).not.toBeInTheDocument()
  })

  it('규칙 기준 추가는 빈 규칙 하나와 「기타」 로 선다', async () => {
    metricsApi.plan.mockResolvedValue({
      ok: false,
      errors: ['규칙 1 의 이름(성격)이 비었습니다.'],
      warnings: [],
      rows: null,
      estimated_cells: null,
      overlap: false,
      dims: [],
      period_from: null,
      period_to: null,
      rules: [],
    })
    await mount()
    await waitFor(() => expect(screen.getByText('시장 서비스 (기록)')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: '규칙 기준 추가' }))
    expect(screen.getByLabelText('기준 1 기타 이름')).toHaveValue('기타')
    await userEvent.type(screen.getByLabelText('기준 1 이름'), 'nature')
    await userEvent.click(screen.getByRole('button', { name: '규칙 미리 보기' }))
    await waitFor(() => expect(metricsApi.plan).toHaveBeenCalled())
    expect(metricsApi.plan.mock.calls.at(-1)?.[0].spec.dimensions).toEqual([
      { name: 'nature', label: null, rules: [{ label: '', when: [] }], else: '기타' },
    ])
    expect(await screen.findByText('규칙 1 의 이름(성격)이 비었습니다.')).toBeInTheDocument()
  })
})
