/**
 * 장표에서 연결 · 해제(ADR 0037 2단계) — 영역의 `link` 가 말하는 대로 **객체 API** 를 부른다.
 * 관계는 출발점에서 맺고 끊는다(출발점을 수정할 수 있는 사람이 맺는다). 칸이면 그 칸을 가진
 * 기록을 수정한다 — 여러 값 칸은 있던 값을 지키며 더하고 뺀다.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AreaLink, RenderedArea, SlideRecord } from '@/modules/slides/api'

const objectApi = vi.hoisted(() => ({
  addRelation: vi.fn(),
  removeRelation: vi.fn(),
  profile: vi.fn(),
  update: vi.fn(),
}))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

import { tie, untie } from '@/modules/slides/useRecordActions'

function area(link: Partial<AreaLink>): RenderedArea {
  return {
    key: 'risks',
    title: '잠재 리스크',
    row: 1,
    col: 0,
    row_span: 1,
    col_span: 1,
    kind: 'records',
    fill: 'follow',
    pair_of: null,
    type_slug: 'risk',
    type_label: '잠재 리스크',
    fields: [],
    text: '',
    records: [],
    total: 0,
    list_params: {},
    follow_title: '설명회',
    link: {
      kind: 'relation',
      relation: 'covers',
      direction: 'in',
      property: null,
      multi: false,
      source_type_slug: 'briefing',
      sources: [{ id: 'b1', label: '설명회', type_slug: 'briefing' }],
      can_link: true,
      ...link,
    },
    made: null,
    limit: 10,
    problem: null,
  }
}

function record(ties: SlideRecord['ties']): SlideRecord {
  return {
    id: 'r1',
    label: '접촉 불량',
    type_slug: 'risk',
    values: {},
    links: [],
    can_edit: true,
    ties,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('장표에서 연결 · 해제', () => {
  it('들어오는 관계는 따라갈 기록에서 맺고 끊는다', async () => {
    await tie(area({}), 'b1', 'r1')
    expect(objectApi.addRelation).toHaveBeenCalledWith('briefing', 'b1', {
      relation: 'covers',
      dst_object_id: 'r1',
    })
    await untie(area({}), record([{ id: 'rel9', source_id: 'b1' }]))
    expect(objectApi.removeRelation).toHaveBeenCalledWith('briefing', 'b1', 'rel9')
  })

  it('나가는 관계는 이 기록에서 맺고 끊는다', async () => {
    await tie(area({ direction: 'out' }), 'b1', 'r1')
    expect(objectApi.addRelation).toHaveBeenCalledWith('risk', 'r1', {
      relation: 'covers',
      dst_object_id: 'b1',
    })
    await untie(area({ direction: 'out' }), record([{ id: 'rel9', source_id: 'b1' }]))
    expect(objectApi.removeRelation).toHaveBeenCalledWith('risk', 'r1', 'rel9')
  })

  it('여러 값 칸은 있던 값을 지키며 더하고 뺀다', async () => {
    const link = {
      kind: 'ref' as const,
      relation: null,
      direction: null,
      property: 'changes',
      multi: true,
    }
    objectApi.profile.mockResolvedValue({ object: { properties: { changes: ['c0'] } } })
    await tie(area(link), 'b1', 'r1')
    expect(objectApi.update).toHaveBeenCalledWith('risk', 'r1', {
      properties: { changes: ['c0', 'b1'] },
    })
    objectApi.profile.mockResolvedValue({ object: { properties: { changes: ['c0', 'b1'] } } })
    await untie(area(link), record([{ id: 'b1', source_id: 'b1' }]))
    expect(objectApi.update).toHaveBeenLastCalledWith('risk', 'r1', {
      properties: { changes: ['c0'] },
    })
  })

  it('따라갈 기록의 칸이 가리키면 그 기록을 수정한다(한 값 칸은 비운다)', async () => {
    const link = {
      kind: 'backref' as const,
      relation: null,
      direction: null,
      property: 'part',
      multi: false,
    }
    objectApi.profile.mockResolvedValue({ object: { properties: { part: 'r1' } } })
    await untie(area(link), record([{ id: 'b1', source_id: 'b1' }]))
    expect(objectApi.profile).toHaveBeenCalledWith('briefing', 'b1')
    expect(objectApi.update).toHaveBeenCalledWith('briefing', 'b1', { properties: { part: null } })
  })
})
