/**
 * 인터페이스(ADR 0006) — **고르기 전에 무엇이 되는지, 잠긴 칸은 왜 잠겼는지 화면이 말한다.**
 *
 * - 타입 창에서 구현 인터페이스를 고르면 저장 전에 미리 보기가 서고, 모양이 안 맞으면 서버가 적은
 *   충돌 문구가 그대로 보이며 저장 단추가 막힌다.
 * - 타입 쪽에서 공통 속성을 열면 모양 칸이 잠기고 어느 인터페이스에서 고치는지 적힌다.
 * - 인터페이스의 공통 속성에는 타입마다 정하는 칸(유일 · 기본값)이 없고, 파일 종류도 없다.
 * - 인터페이스 목록의 열에는 「타입」 이 있고, 트리 · 필터 칸은 없다(서버가 받는 범위와 같다).
 * - 관계 종류의 끝에 인터페이스를 고를 수 있고, 구현한 타입이 없는 것을 고르면 그 자리에서
 *   「아무것도 못 잇는다」 고 말한다.
 * - 참조 속성의 대상이 인터페이스면 그것을 구현한 타입 전부에서 고른다고 적는다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const ontologyApi = vi.hoisted(() => ({
  updateType: vi.fn(),
  implementPlan: vi.fn(),
  updateProperty: vi.fn(),
  createInterfaceProperty: vi.fn(),
  propertyUsage: vi.fn(),
  updateInterface: vi.fn(),
  createRelationType: vi.fn(),
}))
vi.mock('@/modules/ontology/api', async (original) => {
  const real = await original<typeof import('@/modules/ontology/api')>()
  return { ...real, ontologyApi }
})

const TYPE = {
  id: 't1',
  slug: 'tester',
  label: '시험장비',
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
  is_active: true,
  object_count: 0,
  core: false,
  interface_slugs: [],
  properties: [],
}

const IFACE = {
  id: 'i1',
  slug: 'equip',
  label: '설비',
  icon: 'Shapes',
  description: '',
  sort_order: 0,
  extends_slugs: [],
  list_view: {},
  implementers: [],
  object_count: 0,
}

const COUNTRY = {
  id: 'p1',
  owner_kind: 'type',
  owner_id: 't1',
  key: 'country',
  label: '국가',
  data_type: 'enum' as const,
  unit: '',
  help: '',
  required: false,
  multi: false,
  enum_options: ['KR', 'US'],
  ref_type_slug: null,
  min_value: null,
  max_value: null,
  decimals: null,
  pattern: null,
  default_value: null,
  unique: false,
  section: '',
  sort_order: 10,
}

async function openType() {
  const { TypeEditDialog } = await import('@/modules/ontology/TypeEditDialog')
  render(
    <TypeEditDialog
      type={TYPE as never}
      groups={[]}
      interfaces={[IFACE]}
      relationTypes={[]}
      onClose={vi.fn()}
      onChanged={vi.fn()}
    />,
  )
}

describe('타입 창의 구현 인터페이스', () => {
  it('고르면 저장 전에 만들 속성을 보여 주고, 저장하면 구현이 나간다', async () => {
    ontologyApi.implementPlan.mockResolvedValue({
      creates: [{ key: 'maker', interface: 'equip', changed: [] }],
      adopts: [],
      syncs: [],
      conflicts: [],
      warnings: [],
    })
    ontologyApi.updateType.mockResolvedValue({})
    await openType()

    await userEvent.click(screen.getByRole('checkbox', { name: '설비' }))
    await waitFor(() => expect(screen.getByText(/새로 만들 속성/)).toBeInTheDocument())
    expect(ontologyApi.implementPlan).toHaveBeenCalledWith('tester', ['equip'])

    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(ontologyApi.updateType).toHaveBeenCalledWith(
        'tester',
        expect.objectContaining({ interface_slugs: ['equip'] }),
      ),
    )
  })

  it('모양이 안 맞으면 서버가 적은 차이가 그대로 보이고 저장이 막힌다', async () => {
    const said =
      '타입 tester.country: 인터페이스 equip 의 공통 속성과 모양이 다릅니다 — 종류: 인터페이스는 enum, 이 타입은 text.'
    ontologyApi.implementPlan.mockResolvedValue({
      creates: [],
      adopts: [],
      syncs: [],
      conflicts: [said],
      warnings: [],
    })
    await openType()

    await userEvent.click(screen.getByRole('checkbox', { name: '설비' }))
    await waitFor(() => expect(screen.getByText(said)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '저장' })).toBeDisabled()
  })

  it('구현을 안 바꾸면 보내지 않는다 — 보내면 구현을 다시 맞추고 기록을 남긴다', async () => {
    ontologyApi.updateType.mockResolvedValue({})
    await openType()
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(ontologyApi.updateType).toHaveBeenCalled())
    const body = ontologyApi.updateType.mock.calls.at(-1)?.[1] as Record<string, unknown>
    expect(body).not.toHaveProperty('interface_slugs')
  })
})

describe('공통 속성을 여는 창', () => {
  it('타입 쪽에서 열면 모양 칸이 잠기고 어느 인터페이스에서 고치는지 적힌다', async () => {
    const { PropertyEditDialog } = await import('@/modules/ontology/PropertyEditDialog')
    render(
      <PropertyEditDialog
        owner={{ kind: 'type', row: TYPE as never }}
        property={{ ...COUNTRY, interface_slug: 'equip' }}
        types={[]}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    )
    expect(screen.getByText(/공통 속성/, { selector: 'b' })).toBeInTheDocument()
    expect(screen.getByLabelText('선택할 값 (쉼표로)')).toBeDisabled()
    expect(screen.getByLabelText('단위')).toBeDisabled()
    // 이름은 타입마다 — 고칠 수 있다.
    expect(screen.getByLabelText('이름')).not.toBeDisabled()
    // 삭제 대신 이유가 선다.
    expect(screen.queryByRole('button', { name: '삭제' })).not.toBeInTheDocument()
    expect(screen.getByText(/구현을 해제하면/)).toBeInTheDocument()
  })

  it('인터페이스의 공통 속성에는 유일 · 기본값 칸이 없고 공통 속성 길로 나간다', async () => {
    ontologyApi.createInterfaceProperty.mockResolvedValue({})
    const { PropertyEditDialog } = await import('@/modules/ontology/PropertyEditDialog')
    render(
      <PropertyEditDialog
        owner={{ kind: 'interface', row: IFACE }}
        types={[]}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    )
    expect(screen.queryByLabelText('기본값')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /유일해야 함/ })).not.toBeInTheDocument()

    await userEvent.type(screen.getByLabelText('키'), 'maker')
    await userEvent.type(screen.getByLabelText('이름'), '제조사')
    await userEvent.click(screen.getByRole('button', { name: '추가' }))
    await waitFor(() =>
      expect(ontologyApi.createInterfaceProperty).toHaveBeenCalledWith(
        'equip',
        expect.objectContaining({ key: 'maker', unique: false, default_value: null }),
      ),
    )
  })
})

describe('인터페이스 목록 화면', () => {
  it('열에 「타입」 을 담을 수 있고, 트리 · 필터 칸은 없으며, 저장하면 list_view 로 나간다', async () => {
    ontologyApi.updateInterface.mockResolvedValue({})
    const { InterfaceEditDialog } = await import('@/modules/ontology/InterfaceEditDialog')
    const equip = { ...IFACE, properties: [{ ...COUNTRY, owner_kind: 'interface' }] }
    render(
      <InterfaceEditDialog
        iface={equip as never}
        interfaces={[equip as never]}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    )
    expect(screen.getByText('인터페이스 목록 화면')).toBeInTheDocument()
    // 기본 열이 무엇인지 말한다 — 타입 목록의 기본과 다르다.
    expect(screen.getByText(/이름 · 타입 · 식별자/)).toBeInTheDocument()
    expect(screen.queryByText('목록 왼쪽 트리')).not.toBeInTheDocument()
    expect(screen.queryByText('목록 위에 세울 필터 칸')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '타입' }))
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(ontologyApi.updateInterface).toHaveBeenCalledWith(
        'equip',
        expect.objectContaining({ list_view: { columns: ['type'] } }),
      ),
    )
  })
})

describe('관계 종류의 끝에 인터페이스', () => {
  it('고르면 그 slug 로 나가고, 구현한 타입이 없으면 고른 자리에서 말한다', async () => {
    ontologyApi.createRelationType.mockResolvedValue({})
    const { RelationTypeEditDialog } = await import('@/modules/ontology/RelationTypeEditDialog')
    render(
      <RelationTypeEditDialog
        types={[TYPE as never]}
        interfaces={[
          { ...IFACE, implementers: ['tester'] },
          { ...IFACE, slug: 'lonely', label: '외톨이' },
        ]}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    )
    await userEvent.type(screen.getByLabelText('slug'), 'calibrates')
    await userEvent.type(screen.getByLabelText('이름 (→ 방향)'), '교정')
    // 두 번째 고르개가 도착이다.
    await userEvent.click(screen.getAllByRole('checkbox', { name: /설비/ })[1])
    expect(screen.queryByText(/아무것도 잇지 못합니다/)).not.toBeInTheDocument()
    await userEvent.click(screen.getAllByRole('checkbox', { name: /외톨이/ })[1])
    expect(screen.getByText(/외톨이을\(를\) 구현한 타입이 없어/)).toBeInTheDocument()
    await userEvent.click(screen.getAllByRole('checkbox', { name: /외톨이/ })[1])

    await userEvent.click(screen.getByRole('button', { name: '생성' }))
    await waitFor(() =>
      expect(ontologyApi.createRelationType).toHaveBeenCalledWith(
        expect.objectContaining({ slug: 'calibrates', dst_type_slugs: ['equip'] }),
      ),
    )
  })
})

describe('참조 대상에 인터페이스', () => {
  it('대상이 인터페이스면 구현한 타입 전부에서 고른다고 적는다', async () => {
    const { PropertyEditDialog } = await import('@/modules/ontology/PropertyEditDialog')
    render(
      <PropertyEditDialog
        owner={{ kind: 'type', row: TYPE as never }}
        property={{
          ...COUNTRY,
          key: 'equipment',
          label: '사용 설비',
          data_type: 'object_ref',
          enum_options: [],
          ref_type_slug: 'equip',
        }}
        types={[TYPE as never]}
        interfaces={[{ ...IFACE, implementers: ['tester'] }]}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    )
    expect(screen.getByText(/구현한 타입 전부의 객체에서 고릅니다/)).toBeInTheDocument()
  })
})
