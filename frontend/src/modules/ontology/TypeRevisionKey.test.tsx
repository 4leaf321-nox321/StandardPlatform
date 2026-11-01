/**
 * 타입의 「개정 칸」(ADR 0041) — **값이 하나인 글자 · 선택 · 숫자 속성만** 후보이고, **바꾼 때만**
 * `revision_key` 를 보낸다(안 보낸 것과 비운 것은 다른 말이다). 비우면 수정 시각으로 판정한다는 것을
 * 그 자리에 적는다. 고를 것이 없으면 왜 없는지 말한다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PropertyDef, RelationType } from '@/modules/ontology/api'

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

const PROPERTIES = [
  prop('rev', '개정'),
  prop('grade', '등급', { data_type: 'enum', enum_options: ['A', 'B'] }),
  prop('version', '판 번호', { data_type: 'number' }),
  prop('lots', '로트', { data_type: 'number', multi: true }),
  prop('tags', '태그', { multi: true }),
  prop('made', '만든 날', { data_type: 'date' }),
  prop('memo', '메모', { data_type: 'text_long' }),
  prop('ok', '승인', { data_type: 'bool' }),
]

const TYPE = {
  id: 't1',
  slug: 'drawing',
  label: '도면',
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
  revision_key: '',
  is_active: true,
  object_count: 3,
  core: false,
  properties: PROPERTIES,
}

const LINEAGE_KIND = { slug: 'made_from', lineage_source: 'dst' } as RelationType

async function open(overrides: Record<string, unknown> = {}, relationTypes = [LINEAGE_KIND]) {
  const { TypeEditDialog } = await import('@/modules/ontology/TypeEditDialog')
  render(
    <TypeEditDialog
      type={{ ...TYPE, ...overrides } as never}
      groups={[]}
      relationTypes={relationTypes}
      onClose={vi.fn()}
      onChanged={vi.fn()}
    />,
  )
}

describe('타입의 개정 칸', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ontologyApi.updateType.mockResolvedValue({})
  })

  it('값이 하나인 글자 · 선택 · 숫자만 후보이고, 고르면 그 키를 보낸다', async () => {
    await open()
    const field = screen.getByRole('combobox', { name: '개정 칸' })
    expect(field).toHaveTextContent('없음')
    await userEvent.click(field)
    expect(await screen.findByRole('option', { name: '개정' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '등급' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '판 번호' })).toBeInTheDocument()
    // 여러 값 · 날짜 · 긴 글 · 예/아니오는 후보가 아니다.
    for (const name of ['로트', '태그', '만든 날', '메모', '승인']) {
      expect(screen.queryByRole('option', { name })).toBeNull()
    }
    await userEvent.click(screen.getByRole('option', { name: '판 번호' }))
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(ontologyApi.updateType).toHaveBeenCalledWith(
        'drawing',
        expect.objectContaining({ revision_key: 'version' }),
      ),
    )
  })

  it('비우면 수정 시각으로 판정한다고 그 자리에 적는다', async () => {
    await open()
    expect(
      screen.getByText(/비우면 수정 시각으로 판정해 이름 · 별칭만 수정해도 낡음이 됩니다/),
    ).toBeInTheDocument()
  })

  it('바꾸지 않으면 revision_key 를 보내지 않는다', async () => {
    await open({ revision_key: 'rev' })
    expect(screen.getByRole('combobox', { name: '개정 칸' })).toHaveTextContent('개정')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(ontologyApi.updateType).toHaveBeenCalled())
    expect(ontologyApi.updateType.mock.calls[0][1]).not.toHaveProperty('revision_key')
  })

  it('없음으로 바꾸면 빈 글자를 보낸다', async () => {
    await open({ revision_key: 'rev' })
    await userEvent.click(screen.getByRole('combobox', { name: '개정 칸' }))
    await userEvent.click(await screen.findByRole('option', { name: /없음/ }))
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(ontologyApi.updateType).toHaveBeenCalledWith(
        'drawing',
        expect.objectContaining({ revision_key: '' }),
      ),
    )
  })

  it('고를 속성이 없으면 고르개를 막고 까닭을 적는다', async () => {
    await open({ properties: [prop('made', '만든 날', { data_type: 'date' })] })
    expect(screen.getByRole('combobox', { name: '개정 칸' })).toBeDisabled()
    expect(screen.getByText(/값이 하나인 속성이 없습니다/)).toBeInTheDocument()
  })

  it('계보 관계 종류가 없는 설치에서는 아직 쓰이지 않는다고 말한다', async () => {
    await open({}, [])
    expect(screen.getByText(/아직 계보 관계 종류가 없습니다/)).toBeInTheDocument()
  })

  it('투영 타입에는 칸이 없다', async () => {
    await open({ kind_class: 'system', system_source: 'workspace', properties: [] })
    expect(screen.queryByRole('combobox', { name: '개정 칸' })).toBeNull()
  })
})
