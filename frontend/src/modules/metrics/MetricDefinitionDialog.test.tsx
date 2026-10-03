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
})
