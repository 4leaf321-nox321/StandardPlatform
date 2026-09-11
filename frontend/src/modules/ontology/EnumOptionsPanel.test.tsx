/**
 * 고를 값 다루기가 지키는 것 — **몇 개가 함께 바뀌는지 먼저 말하고, 승격은 계획을 보고 누른다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ObjectType, PropertyDef } from '@/modules/ontology/api'

const ontologyApi = vi.hoisted(() => ({ renameOption: vi.fn(), promoteProperty: vi.fn() }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

const TYPE = { slug: 'part', label: '부품' } as ObjectType
const PROPERTY = { key: 'material', label: '재질', data_type: 'enum', enum_options: ['스틸', '고무'] } as PropertyDef
const BOOKS = [{ slug: 'material_book', label: '재질', kind_class: 'reference', is_active: true, object_count: 2 }] as ObjectType[]

async function mount() {
  const { EnumOptionsPanel } = await import('@/modules/ontology/EnumOptionsPanel')
  const onChanged = vi.fn()
  const onPromoted = vi.fn()
  render(<EnumOptionsPanel type={TYPE} property={PROPERTY} types={BOOKS} onChanged={onChanged} onPromoted={onPromoted} />)
  return { onChanged, onPromoted }
}

describe('고를 값 다루기', () => {
  beforeEach(() => vi.clearAllMocks())

  it('이름 바꾸기 — 몇 개가 함께 바뀌는지 본 뒤에 바꾼다', async () => {
    ontologyApi.renameOption
      .mockResolvedValueOnce({ applied: false, from_value: '스틸', to_value: '강', objects_with_value: 7, errors: [] })
      .mockResolvedValueOnce({ applied: true, from_value: '스틸', to_value: '강', objects_with_value: 7, errors: [] })
    const { onChanged } = await mount()
    await userEvent.click(screen.getAllByRole('button', { name: /이름 바꾸기/ })[0])
    const input = await screen.findByLabelText('새 이름')
    await userEvent.clear(input)
    await userEvent.type(input, '강')
    await userEvent.click(screen.getByRole('button', { name: /몇 개가 바뀌는지 보기/ }))
    expect(await screen.findByText(/저장된 값 7개/)).toBeInTheDocument()
    expect(ontologyApi.renameOption).toHaveBeenCalledWith('part', 'material', { from: '스틸', to: '강', apply: false })
    await userEvent.click(screen.getByRole('button', { name: /바꾸기 — 저장값 7개 포함/ }))
    await waitFor(() => expect(ontologyApi.renameOption).toHaveBeenLastCalledWith('part', 'material', { from: '스틸', to: '강', apply: true }))
    expect(onChanged).toHaveBeenCalled()
  })

  it('승격 — 계획(값마다 새로/재사용, 옮길 수)을 보고 누른다', async () => {
    const plan = {
      applied: false, target_slug: 'material_book', target_label: '재질', target_new: false,
      options: [
        { value: '스틸', action: 'reuse', object_id: 'x', objects_with_value: 7 },
        { value: '고무', action: 'create', object_id: null, objects_with_value: 2 },
      ],
      errors: [], warnings: [], snapshot_id: null,
    }
    ontologyApi.promoteProperty.mockResolvedValueOnce(plan).mockResolvedValueOnce({ ...plan, applied: true, snapshot_id: 's' })
    const { onPromoted } = await mount()
    await userEvent.click(screen.getByRole('button', { name: /코드표로 승격/ }))
    await userEvent.click(await screen.findByRole('button', { name: '계획 보기' }))
    expect(await screen.findByText('있는 것에 붙임')).toBeInTheDocument()
    expect(screen.getByText('새로 만듦')).toBeInTheDocument()
    expect(ontologyApi.promoteProperty).toHaveBeenCalledWith('part', 'material', { target_type_slug: 'material_book', apply: false })
    await userEvent.click(screen.getByRole('button', { name: /승격 — 저장값 9개 옮김/ }))
    await waitFor(() => expect(onPromoted).toHaveBeenCalled())
  })

  it('계획에 오류가 있으면 승격 단추가 안 선다', async () => {
    ontologyApi.promoteProperty.mockResolvedValue({
      applied: false, target_slug: 'material_book', target_label: '재질', target_new: false,
      options: [], errors: ['고를 값에 없는 저장값이 있습니다: 청동'], warnings: [], snapshot_id: null,
    })
    await mount()
    await userEvent.click(screen.getByRole('button', { name: /코드표로 승격/ }))
    await userEvent.click(await screen.findByRole('button', { name: '계획 보기' }))
    expect(await screen.findByText(/청동/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /승격 —/ })).not.toBeInTheDocument()
  })
})
