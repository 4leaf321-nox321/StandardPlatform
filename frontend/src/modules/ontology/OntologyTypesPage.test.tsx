/**
 * 타입 목록의 「속성」 · 「객체」 — 표 아래에 붙지 않고 **창으로** 뜬다. 타입이 많으면 표 아래에 붙은 것은
 * 누른 뒤 한참 내려야 보였고, 어느 타입의 것인지 표와 멀어졌다.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const TYPE = {
  slug: 'section',
  label: '절',
  icon: '',
  kind_class: 'record',
  nav_group_slug: 'docs',
  object_count: 12,
  is_active: true,
  managed_by: '',
  usage: '',
  core: false,
  properties: [{ key: 'section_no', label: '절 번호', data_type: 'text' }],
}

vi.mock('@/modules/ontology/OntologyLayout', () => ({
  useOntology: () => ({
    schema: { types: [TYPE], groups: [{ slug: 'docs' }], interfaces: [], relation_types: [] },
    reload: vi.fn(),
    setError: vi.fn(),
  }),
}))
vi.mock('@/modules/ontology/PropertyEditor', () => ({
  PropertyEditor: () => <p>속성 편집기</p>,
}))
vi.mock('@/modules/ontology/TypeExamples', () => ({
  TypeExamples: () => <p>예시 목록</p>,
}))
vi.mock('@/modules/ontology/TypeEditDialog', () => ({ TypeEditDialog: () => null }))

describe('타입 목록의 속성 · 객체', () => {
  it('속성을 클릭하면 창이 뜨고, 닫으면 사라진다', async () => {
    const { default: OntologyTypesPage } = await import('@/modules/ontology/OntologyTypesPage')
    render(<OntologyTypesPage />)
    await userEvent.click(screen.getByRole('button', { name: /속성 1/ }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('절 의 속성')).toBeInTheDocument()
    expect(within(dialog).getByText('속성 편집기')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('객체 수를 클릭하면 예시가 창으로 뜬다', async () => {
    const { default: OntologyTypesPage } = await import('@/modules/ontology/OntologyTypesPage')
    render(<OntologyTypesPage />)
    await userEvent.click(screen.getByTitle('이 타입의 예시 객체'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/절 의 예시/)).toBeInTheDocument()
    expect(within(dialog).getByText('예시 목록')).toBeInTheDocument()
  })
})
