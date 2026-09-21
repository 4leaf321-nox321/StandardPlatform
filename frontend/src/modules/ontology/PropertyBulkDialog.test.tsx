/**
 * 여러 속성 추가가 지키는 것 — **표를 정의 가져오기의 스키마로 옮기고, 미리 보고 적용한다.**
 *
 * 새 길을 만들지 않는 것이 요점이다. 검증 · 미리 보기 · 스냅샷은 전부 정의 가져오기의 것이고,
 * 이 창은 그 스키마를 만드는 껍데기다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ObjectType, PropertyDef } from '@/modules/ontology/api'

const ontologyApi = vi.hoisted(() => ({ importSchema: vi.fn() }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

const TYPE = {
  slug: 'part',
  label: '부품',
  properties: [{ key: 'weight' }],
} as unknown as ObjectType & { properties: PropertyDef[] }

const PLAN = {
  applied: false,
  changes: [{ kind: 'property', slug: 'part.grade', action: 'create', fields: ['label'] }],
  warnings: [],
  errors: [],
  snapshot_id: null,
}

async function mount() {
  const { PropertyBulkDialog } = await import('@/modules/ontology/PropertyBulkDialog')
  const onChanged = vi.fn()
  render(<PropertyBulkDialog type={TYPE} onClose={() => {}} onChanged={onChanged} />)
  return onChanged
}

describe('여러 속성 추가', () => {
  beforeEach(() => vi.clearAllMocks())

  it('붙여넣은 표가 정의 가져오기의 스키마가 된다 — 미리 보기가 먼저', async () => {
    ontologyApi.importSchema.mockResolvedValue(PLAN)
    await mount()

    const cell = screen.getByLabelText('1번 줄 key')
    cell.focus()
    // 종류는 값(text)으로도, 화면의 말(숫자)로도 적는다.
    await userEvent.paste('grade\t등급\tenum\t\tA;B;C\t\t예\t\t등급표 기준\nmass\t무게\t숫자\tkg')
    await userEvent.click(screen.getByRole('button', { name: /미리 보기 — 2개/ }))

    await waitFor(() => expect(ontologyApi.importSchema).toHaveBeenCalled())
    const [schema, dryRun] = ontologyApi.importSchema.mock.calls[0]
    expect(dryRun).toBe(true) // **미리 보기가 먼저다**
    const sent = (schema as { types: { slug: string; properties: Record<string, unknown>[] }[] })
      .types[0]
    expect(sent.slug).toBe('part')
    expect(sent.properties[0]).toMatchObject({
      key: 'grade',
      label: '등급',
      data_type: 'enum',
      enum_options: ['A', 'B', 'C'],
      required: true,
      multi: false,
      help: '등급표 기준',
    })
    expect(sent.properties[1]).toMatchObject({ key: 'mass', data_type: 'number', unit: 'kg' })
    // 순서는 이미 있는 속성 뒤로 — 새로 넣은 것이 위로 튀어 오르지 않는다.
    expect(sent.properties[0].sort_order).toBe(2)
  })

  it('오류가 있으면 적용이 안 선다', async () => {
    ontologyApi.importSchema.mockResolvedValue({ ...PLAN, errors: ['grade: 모르는 종류입니다'] })
    await mount()
    const cell = screen.getByLabelText('1번 줄 key')
    cell.focus()
    await userEvent.paste('grade\t등급\t없는종류')
    await userEvent.click(screen.getByRole('button', { name: /미리 보기/ }))

    await waitFor(() => expect(screen.getByText('grade: 모르는 종류입니다')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '적용' })).toBeDisabled()
  })

  it('미리 본 뒤에만 적용하고, 적용하면 목록을 다시 읽는다', async () => {
    ontologyApi.importSchema
      .mockResolvedValueOnce(PLAN)
      .mockResolvedValueOnce({ ...PLAN, applied: true })
    const onChanged = await mount()
    const cell = screen.getByLabelText('1번 줄 key')
    cell.focus()
    await userEvent.paste('grade\t등급\ttext')

    expect(screen.getByRole('button', { name: '적용' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: /미리 보기/ }))
    const apply = await screen.findByRole('button', { name: '적용' })
    await waitFor(() => expect(apply).toBeEnabled())
    await userEvent.click(apply)

    await waitFor(() =>
      expect(ontologyApi.importSchema).toHaveBeenLastCalledWith(expect.anything(), false),
    )
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(screen.getByText('적용했습니다.')).toBeInTheDocument()
  })
})
