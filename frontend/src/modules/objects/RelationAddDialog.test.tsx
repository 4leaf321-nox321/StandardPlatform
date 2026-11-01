/**
 * 잇기 창 — **끝에 인터페이스가 적힌 관계는 그것을 구현한 타입이 후보다**(ADR 0006).
 *
 * - 출발에 인터페이스가 적혔으면, 구현 타입의 객체에서 그 관계를 고를 수 있다.
 * - 도착에 인터페이스가 적혔으면, 후보는 구현 타입들의 객체다(다른 타입은 아니다).
 * - 구현한 타입이 없는 인터페이스는 「아무 타입이나」 가 아니다 — 이을 수 없다고 말한다.
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RelationType } from '@/modules/ontology/api'

const ontologyApi = vi.hoisted(() => ({ schema: vi.fn() }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

const objectApi = vi.hoisted(() => ({ addRelation: vi.fn(), proposeRelation: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

/** 후보를 찾는 쪽 — 어느 타입들에 물었는지 보고, 시험이 준 후보를 낸다. */
const asked = vi.hoisted(() => ({
  sources: [] as { slug: string }[],
  options: [] as { value: string; label: string }[],
}))
vi.mock('@/modules/objects/useObjectOptions', () => ({
  useObjectOptions: (sources: { slug: string }[]) => {
    asked.sources = sources
    return {
      options: asked.options,
      pinned: [],
      total: 0,
      loading: false,
      failed: false,
      setQuery: vi.fn(),
    }
  },
}))

function type(slug: string, label: string) {
  return { slug, label, kind_class: 'record' }
}

function iface(slug: string, label: string, implementers: string[]) {
  return {
    id: slug,
    slug,
    label,
    icon: '',
    description: '',
    sort_order: 0,
    extends_slugs: [],
    list_view: {},
    implementers,
    object_count: 0,
    properties: [],
  }
}

function kind(slug: string, label: string, src: string[] | null, dst: string[] | null) {
  return {
    slug,
    label,
    inverse_label: '',
    is_active: true,
    src_type_slugs: src,
    dst_type_slugs: dst,
  } as unknown as RelationType
}

async function open(relationTypes: RelationType[], objectTypeSlug = 'lab') {
  const { RelationAddDialog } = await import('@/modules/objects/RelationAddDialog')
  render(
    <RelationAddDialog
      typeSlug={objectTypeSlug}
      objectId="o1"
      objectLabel="한국교정원"
      objectTypeSlug={objectTypeSlug}
      relationTypes={relationTypes}
      onClose={vi.fn()}
      onAdded={vi.fn()}
    />,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  asked.sources = []
  asked.options = []
  ontologyApi.schema.mockResolvedValue({
    types: [type('lab', '교정기관'), type('tester', '시험장비'), type('part', '부품')],
    interfaces: [iface('equip', '설비', ['tester']), iface('lonely', '외톨이', [])],
    relation_types: [],
  })
})

describe('잇기 창의 인터페이스 끝', () => {
  it('도착이 인터페이스면 구현 타입에서만 고른다', async () => {
    await open([kind('calibrates', '교정', ['lab'], ['equip'])])
    await waitFor(() => expect(asked.sources.map((one) => one.slug)).toEqual(['tester']))
    expect(
      screen.getByText('시험장비 만 선택할 수 있습니다.', { exact: false }),
    ).toBeInTheDocument()
  })

  it('출발이 인터페이스면 구현 타입의 객체에서 그 관계를 고를 수 있다', async () => {
    await open([kind('owned_by', '소유', ['equip'], ['lab'])], 'tester')
    await waitFor(() => expect(screen.getByText(/「소유」/)).toBeInTheDocument())
    expect(screen.queryByText(/출발할 수 있는 관계 종류가 없습니다/)).not.toBeInTheDocument()
  })

  it('구현한 타입이 없는 인터페이스는 아무 타입이나가 아니다', async () => {
    await open([kind('uses', '사용', ['lab'], ['lonely'])])
    await waitFor(() =>
      expect(screen.getByText(/해당하는 타입이 없어 이을 수 없습니다/)).toBeInTheDocument(),
    )
    expect(asked.sources).toEqual([])
    expect(screen.queryByText(/아무 타입이나/)).not.toBeInTheDocument()
  })
})

describe('잇기 창의 유효 기간(ADR 0039)', () => {
  it('유효 기간을 받는 종류일 때만 시작일 · 끝날 칸이 선다', async () => {
    await open([kind('calibrates', '교정', ['lab'], null)])
    await waitFor(() => expect(screen.getByText(/「교정」/)).toBeInTheDocument())
    expect(screen.queryByLabelText('시작일 (포함)')).not.toBeInTheDocument()
    cleanup()

    await open([
      { ...kind('bom_of', '구성', ['lab'], null), effectivity: true } as unknown as RelationType,
    ])
    expect(await screen.findByLabelText('시작일 (포함)')).toBeInTheDocument()
    expect(screen.getByLabelText('끝날 (제외)')).toBeInTheDocument()
    expect(screen.getByText(/끝날은 그날부터 무효입니다/)).toBeInTheDocument()
  })

  it('시작일이 끝날보다 늦으면 저장 전에 말한다', async () => {
    await open([
      { ...kind('bom_of', '구성', ['lab'], null), effectivity: true } as unknown as RelationType,
    ])
    fireEvent.change(await screen.findByLabelText('시작일 (포함)'), {
      target: { value: '2026-04-01' },
    })
    fireEvent.change(screen.getByLabelText('끝날 (제외)'), { target: { value: '2026-03-01' } })
    expect(screen.getByText('시작일은 끝날보다 앞이어야 합니다.')).toBeInTheDocument()
  })
})

/** 제안 모드로 연다 — 수정 권한(`canLink`) · 알림을 시험이 정한다. */
async function openWith(extra: { canLink?: boolean } = {}) {
  const { RelationAddDialog } = await import('@/modules/objects/RelationAddDialog')
  const onAdded = vi.fn()
  const onProposed = vi.fn()
  render(
    <RelationAddDialog
      typeSlug="lab"
      objectId="o1"
      objectLabel="한국교정원"
      objectTypeSlug="lab"
      relationTypes={[kind('calibrates', '교정', ['lab'], ['tester'])]}
      onClose={vi.fn()}
      onAdded={onAdded}
      onProposed={onProposed}
      {...extra}
    />,
  )
  return { onAdded, onProposed }
}

async function pickTarget() {
  await userEvent.click(await screen.findByRole('combobox', { name: '이을 객체' }))
  await userEvent.click(await screen.findByRole('button', { name: /장비 A/ }))
}

describe('제안으로 추가(ADR 0042)', () => {
  beforeEach(() => {
    asked.options = [{ value: 'tester:t1', label: '장비 A' }]
  })

  it('수정할 수 없으면 제안으로만 추가하고 근거 · 확신도를 함께 보낸다', async () => {
    objectApi.proposeRelation.mockResolvedValue({
      index: 0,
      status: 'created',
      message: '제안했습니다',
      updated: false,
    })
    const { onAdded, onProposed } = await openWith({ canLink: false })
    expect(await screen.findByText('한국교정원 에서 관계 제안')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '연결' })).not.toBeInTheDocument()
    await pickTarget()
    await userEvent.type(screen.getByLabelText('근거'), '교정 성적서 2쪽')
    await userEvent.type(screen.getByLabelText('확신도 (선택)'), '0.8')
    await userEvent.click(screen.getByRole('button', { name: '제안으로 추가' }))
    await waitFor(() =>
      expect(objectApi.proposeRelation).toHaveBeenCalledWith('lab', 'o1', {
        relation: 'calibrates',
        dst_object_id: 't1',
        evidence_note: '교정 성적서 2쪽',
        confidence: 0.8,
        origin: 'user',
        again: false,
      }),
    )
    expect(objectApi.addRelation).not.toHaveBeenCalled()
    // 결과를 창에 보인다 — 닫지 않고, 목록만 다시 읽는다.
    expect(await screen.findByText('제안함')).toBeInTheDocument()
    expect(onProposed).toHaveBeenCalled()
    expect(onAdded).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '닫기' })).toBeInTheDocument()
  })

  it('거절된 적 있으면 그 까닭을 보이고 「다시 제안」 이 again 으로 다시 보낸다', async () => {
    objectApi.proposeRelation
      .mockResolvedValueOnce({
        index: 0,
        status: 'rejected_before',
        message: '같은 제안이 2026-10-30 10:00 에 manager 이(가) 거절했습니다 — 까닭: 단종',
        updated: false,
      })
      .mockResolvedValueOnce({
        index: 0,
        status: 'created',
        message: '제안했습니다',
        updated: false,
      })
    await openWith({ canLink: false })
    await pickTarget()
    await userEvent.click(screen.getByRole('button', { name: '제안으로 추가' }))
    expect(await screen.findByText('거절된 적 있음')).toBeInTheDocument()
    expect(screen.getByText(/까닭: 단종/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '다시 제안' }))
    await waitFor(() => expect(objectApi.proposeRelation).toHaveBeenCalledTimes(2))
    expect(objectApi.proposeRelation.mock.calls[1][2]).toMatchObject({ again: true })
    expect(await screen.findByText('제안함')).toBeInTheDocument()
  })

  it('이미 연결된 것 · 이미 열린 제안도 결과로 말한다', async () => {
    objectApi.proposeRelation.mockResolvedValue({
      index: 0,
      status: 'connected',
      message: '이미 교정(으)로 이어져 있습니다 — 제안을 만들지 않았습니다.',
      updated: false,
    })
    const { onProposed } = await openWith({ canLink: false })
    await pickTarget()
    await userEvent.click(screen.getByRole('button', { name: '제안으로 추가' }))
    expect(await screen.findByText('이미 연결됨')).toBeInTheDocument()
    expect(onProposed).not.toHaveBeenCalled()
  })

  it('수정할 수 있어도 「제안으로 추가」 를 선택할 수 있다', async () => {
    objectApi.proposeRelation.mockResolvedValue({
      index: 0,
      status: 'open',
      message: '같은 제안이 이미 열려 있습니다.',
      updated: false,
    })
    await openWith()
    expect(await screen.findByRole('button', { name: '연결' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: /제안으로 추가/ }))
    await pickTarget()
    await userEvent.click(screen.getByRole('button', { name: '제안으로 추가' }))
    expect(await screen.findByText('이미 열린 제안')).toBeInTheDocument()
    expect(objectApi.addRelation).not.toHaveBeenCalled()
  })

  it('확신도가 0 ~ 1 밖이면 보내기 전에 말한다', async () => {
    await openWith({ canLink: false })
    await pickTarget()
    await userEvent.type(screen.getByLabelText('확신도 (선택)'), '3')
    expect(screen.getByText(/확신도는 0 ~ 1 사이의 수입니다/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '제안으로 추가' })).toBeDisabled()
  })
})
