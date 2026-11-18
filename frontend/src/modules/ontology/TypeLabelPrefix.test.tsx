/**
 * 타입의 「이름 앞에 붙일 값(앞머리)」(ADR 0088) — 기본 칸과 속성(파일 · 문서 칸 제외) 중 하나이고 이름
 * 자신은 후보가 아니다. **바꾼 때만** `label_prefix` 를 보낸다(안 보낸 것과 비운 것은 다른 말이다).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PropertyDef } from '@/modules/ontology/api'

const ontologyApi = vi.hoisted(() => ({ updateType: vi.fn(), propertyUsage: vi.fn() }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

function prop(key: string, label: string, extra: Partial<PropertyDef> = {}): PropertyDef {
  return {
    key,
    label,
    data_type: 'text',
    multi: false,
    unit: '',
    enum_options: null,
    section: '',
    sort_order: 0,
    ...extra,
  } as PropertyDef
}

const TYPE = {
  id: 't1',
  slug: 'section',
  label: '절',
  icon: '',
  description: '',
  sort_order: 0,
  nav_group_id: null,
  nav_group_slug: null,
  kind_class: 'record' as const,
  system_source: '',
  entry_policy: 'open' as const,
  key_policy: 'optional' as const,
  key_scope: 'global' as const,
  temporal_kind: 'evergreen' as const,
  list_view: {},
  form_view: {},
  detail_view: {},
  title_template: '',
  label_prefix: '',
  is_active: true,
  object_count: 3,
  core: false,
  properties: [prop('section_no', '절 번호'), prop('pic', '그림', { data_type: 'file' })],
}

async function open(overrides: Record<string, unknown> = {}) {
  const { TypeEditDialog } = await import('@/modules/ontology/TypeEditDialog')
  render(
    <TypeEditDialog
      type={{ ...TYPE, ...overrides } as never}
      groups={[]}
      relationTypes={[]}
      onClose={vi.fn()}
      onChanged={vi.fn()}
    />,
  )
}

describe('타입의 앞머리(ADR 0088)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ontologyApi.updateType.mockResolvedValue({})
  })

  it('속성 · 기본 칸이 후보이고 이름 · 파일 칸은 아니며, 고르면 그 칸을 보낸다', async () => {
    await open()
    const field = screen.getByRole('combobox', { name: '이름 앞에 붙일 값(앞머리)' })
    expect(field).toHaveTextContent('없음')
    await userEvent.click(field)
    expect(await screen.findByRole('option', { name: '절 번호' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '식별자' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: '이름' })).toBeNull()
    expect(screen.queryByRole('option', { name: '그림' })).toBeNull()
    await userEvent.click(screen.getByRole('option', { name: '절 번호' }))
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(ontologyApi.updateType).toHaveBeenCalledWith(
        'section',
        expect.objectContaining({ label_prefix: 'properties.section_no' }),
      ),
    )
  })

  it('바꾸지 않으면 보내지 않고, 없음으로 바꾸면 빈 글자를 보낸다', async () => {
    await open({ label_prefix: 'properties.section_no' })
    const field = screen.getByRole('combobox', { name: '이름 앞에 붙일 값(앞머리)' })
    expect(field).toHaveTextContent('절 번호')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(ontologyApi.updateType).toHaveBeenCalled())
    expect(ontologyApi.updateType.mock.calls[0][1]).not.toHaveProperty('label_prefix')

    ontologyApi.updateType.mockClear()
    await userEvent.click(field)
    await userEvent.click(await screen.findByRole('option', { name: '없음' }))
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(ontologyApi.updateType).toHaveBeenCalledWith(
        'section',
        expect.objectContaining({ label_prefix: '' }),
      ),
    )
  })
})
