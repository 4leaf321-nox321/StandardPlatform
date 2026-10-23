/**
 * 장표 그리기(ADR 0037) — 짝 영역(AS-IS / TO-BE)은 한 덩이라 기록마다 연결 항목 줄이 한 번이고
 * 좌우 칸이 같은 줄에 선다. 고장 난 영역만 까닭을 말하고, 글 영역은 「작성한 글」 로 갈린다.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { RenderedArea, SlideRecord } from '@/modules/slides/api'
import { SlideGrid, blocksOf, canUnlink, columnsOf, primaryOf } from '@/modules/slides/SlideGrid'

function record(id: string, values: Record<string, string>): SlideRecord {
  return {
    id,
    label: id,
    type_slug: 'change',
    values,
    links: [{ label: '부품', items: [{ id: 'p1', label: '커넥터', type_slug: 'part' }] }],
    can_edit: false,
    ties: [],
  }
}

function area(extra: Partial<RenderedArea>): RenderedArea {
  return {
    key: 'k',
    title: '제목',
    row: 0,
    col: 0,
    row_span: 1,
    col_span: 1,
    kind: 'records',
    fill: 'condition',
    pair_of: null,
    type_slug: 'change',
    type_label: '변경점',
    fields: [],
    text: '',
    records: [],
    total: 0,
    list_params: {},
    follow_title: null,
    link: null,
    made: null,
    limit: 10,
    problem: null,
    ...extra,
  }
}

const asis = area({
  key: 'asis',
  title: 'AS-IS',
  fields: [{ key: 'before', label: '변경 전' }],
  records: [record('핀 수', { before: '2핀' }), record('재질', { before: 'PA66' })],
  total: 3,
  list_params: { 'f.model.eq': 'm1' },
})
const tobe = area({
  key: 'tobe',
  title: 'TO-BE',
  col: 1,
  pair_of: 'asis',
  fields: [{ key: 'after', label: '변경 후' }],
  records: [record('핀 수', { after: '4핀' }), record('재질', { after: 'PBT' })],
})

function draw(areas: RenderedArea[], extra: Partial<Parameters<typeof SlideGrid>[0]> = {}) {
  return render(
    <MemoryRouter>
      <SlideGrid rows={2} cols={2} areas={areas} {...extra} />
    </MemoryRouter>,
  )
}

describe('장표 그리기', () => {
  it('짝 영역은 표 하나 — 머리글 · 기록마다 연결 항목 줄 한 번 · 좌우 칸이 같은 줄', () => {
    expect(blocksOf([tobe, asis]).map((group) => group.map((one) => one.key))).toEqual([
      ['asis', 'tobe'],
    ])
    draw([asis, tobe])
    const block = screen.getByRole('region', { name: 'AS-IS · TO-BE' })
    // 머리글이 표의 첫 줄이다 — 표와 같은 열을 써서 구분선과 어긋나지 않는다.
    expect(within(block).getAllByRole('table')).toHaveLength(1)
    expect(within(block).getByRole('columnheader', { name: 'AS-IS' })).toBeInTheDocument()
    expect(within(block).getByRole('columnheader', { name: 'TO-BE' })).toBeInTheDocument()
    const pin = within(block).getByRole('rowgroup', { name: '핀 수' })
    expect(within(pin).getAllByText('커넥터')).toHaveLength(1)
    const [line, values] = within(pin).getAllByRole('row')
    expect(line).toHaveTextContent('부품: 커넥터')
    expect(within(values).getByText('2핀')).toBeInTheDocument()
    expect(within(values).getByText('4핀')).toBeInTheDocument()
    const material = within(block).getByRole('rowgroup', { name: '재질' })
    expect(within(material).getByText('PA66')).toBeInTheDocument()
    expect(within(material).getByText('PBT')).toBeInTheDocument()
    // 더 있으면 수를 말하고, 같은 조건의 목록으로 간다.
    expect(within(block).getByText('1건 더 있습니다.')).toBeInTheDocument()
    expect(within(block).getByRole('link', { name: /목록에서 보기/ })).toHaveAttribute(
      'href',
      '/o/change?f.model.eq=m1',
    )
  })

  it('짝의 칸 수가 달라도 줄이 맞는다 — 적은 쪽은 빈 칸', () => {
    const left = { ...asis, fields: [asis.fields[0], { key: 'label', label: '이름' }] }
    const right = {
      ...tobe,
      fields: [
        { key: 'after', label: '변경 후' },
        { key: 'when', label: '적용 시점' },
        { key: 'state', label: '진행 상태' },
      ],
    }
    // 열 — 좌우가 너비의 반씩, 칸 이름 열과 값 열로.
    const widths = columnsOf([left, right]).map((one) => Math.round(one.width))
    expect(widths).toEqual([16, 34, 16, 34])
    draw([left, right])
    const pin = screen.getByRole('rowgroup', { name: '핀 수' })
    const rows = within(pin).getAllByRole('row')
    // 연결 항목 줄 하나 + 칸 줄 셋(많은 쪽) — 왼쪽 셋째 줄은 비어 있다.
    expect(rows).toHaveLength(4)
    expect(within(rows[3]).getByText('진행 상태')).toBeInTheDocument()
    expect(within(rows[3]).getAllByRole('rowheader')[0]).toHaveTextContent('')
  })

  it('따라오는 영역은 무엇을 따라오는지 말하고, 비면 그 까닭을 말한다', () => {
    draw([
      asis,
      tobe,
      area({ key: 'risks', title: '잠재리스크', row: 1, fill: 'follow', follow_title: 'AS-IS' }),
    ])
    expect(screen.getByText('「AS-IS」 따라옴')).toBeInTheDocument()
    expect(screen.getByText('「AS-IS」 의 기록과 이어진 기록이 없습니다.')).toBeInTheDocument()
  })

  it('고장 난 영역만 까닭을 말한다', () => {
    draw([
      asis,
      tobe,
      area({ key: 'risks', title: '잠재리스크', row: 1, problem: '없는 칸입니다: gone' }),
    ])
    expect(screen.getByRole('alert')).toHaveTextContent('없는 칸입니다: gone')
    expect(screen.getByText('2핀')).toBeInTheDocument()
  })

  it('글 영역은 「작성한 글」 로 갈리고 작성자만 수정한다', async () => {
    const save = vi.fn().mockResolvedValue(undefined)
    const opinion = area({ key: 'opinion', title: '종합 의견', kind: 'text', text: '대책 확인' })
    const { unmount } = draw([opinion])
    expect(screen.getByText('작성한 글')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /수정/ })).not.toBeInTheDocument()
    unmount()

    draw([opinion], { editable: true, onSaveText: save })
    await userEvent.click(screen.getByRole('button', { name: /수정/ }))
    const box = screen.getByRole('textbox', { name: '종합 의견 글' })
    await userEvent.clear(box)
    await userEvent.type(box, '대책 완료')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    expect(save).toHaveBeenCalledWith('opinion', '대책 완료')
  })

  it('수정할 수 있는 기록의 값 칸만 클릭으로 수정한다', async () => {
    const onEditValue = vi.fn()
    const mine = { ...asis, records: [{ ...asis.records[0], can_edit: true }, asis.records[1]] }
    draw([mine, tobe], { onEditValue })
    await userEvent.click(screen.getByRole('button', { name: '핀 수 변경 전 수정' }))
    expect(onEditValue).toHaveBeenCalledWith(mine, mine.records[0], {
      key: 'before',
      label: '변경 전',
    })
    // 남의 기록(can_edit 거짓)은 버튼이 아니다.
    expect(screen.queryByRole('button', { name: '재질 변경 전 수정' })).not.toBeInTheDocument()
  })

  it('따라오는 영역 — 연결 · 해제 · 새 기록이 권한대로 선다', async () => {
    const onLink = vi.fn()
    const onUnlink = vi.fn()
    const onCreate = vi.fn()
    const risks = area({
      key: 'risks',
      title: '잠재리스크',
      row: 1,
      fill: 'follow',
      follow_title: 'AS-IS',
      type_label: '잠재 리스크',
      fields: [{ key: 'label', label: '이름' }],
      records: [
        { ...record('접촉 불량', { label: '접촉 불량' }), ties: [{ id: 'x', source_id: 's' }] },
      ],
      link: {
        kind: 'relation',
        relation: 'affects',
        direction: 'in',
        property: null,
        multi: false,
        source_type_slug: 'change',
        sources: [{ id: 's', label: '핀 수', type_slug: 'change' }],
        can_link: true,
      },
    })
    draw([risks], { onLink, onUnlink, onCreate, canCreate: () => true })
    await userEvent.click(screen.getByRole('button', { name: /연결$/ }))
    expect(onLink).toHaveBeenCalledWith(risks)
    await userEvent.click(screen.getByRole('button', { name: '접촉 불량 연결 해제' }))
    expect(onUnlink).toHaveBeenCalledWith(risks, risks.records[0])
    await userEvent.click(screen.getByRole('button', { name: /새 잠재 리스크/ }))
    expect(onCreate).toHaveBeenCalledWith(risks)
  })

  it('따라갈 기록을 수정할 수 없으면 연결 · 해제가 없다', () => {
    const risks = area({
      key: 'risks',
      fill: 'follow',
      fields: [{ key: 'label', label: '이름' }],
      records: [
        { ...record('접촉 불량', {}), can_edit: true, ties: [{ id: 'x', source_id: 's' }] },
      ],
      link: {
        kind: 'relation',
        relation: 'affects',
        direction: 'in',
        property: null,
        multi: false,
        source_type_slug: 'change',
        sources: [{ id: 's', label: '핀 수', type_slug: 'change' }],
        can_link: false,
      },
    })
    draw([risks], { onLink: vi.fn(), onUnlink: vi.fn() })
    expect(screen.queryByRole('button', { name: /연결$/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /연결 해제/ })).not.toBeInTheDocument()
  })

  it('이 기록 쪽을 고치는 연결도 `can_link` 가 거짓이면 해제가 없다', () => {
    // 허브가 관리하는 관계 — 기록은 내 것이어도 그 관계는 끊지 못한다.
    const risks = area({
      key: 'risks',
      fill: 'follow',
      fields: [{ key: 'label', label: '이름' }],
      records: [
        { ...record('접촉 불량', {}), can_edit: true, ties: [{ id: 'x', source_id: 's' }] },
      ],
      link: {
        kind: 'relation',
        relation: 'affects',
        direction: 'out',
        property: null,
        multi: false,
        source_type_slug: 'change',
        sources: [{ id: 's', label: '핀 수', type_slug: 'change' }],
        can_link: false,
      },
    })
    expect(canUnlink(risks, risks.records[0])).toBe(false)
    expect(
      canUnlink({ ...risks, link: { ...risks.link!, can_link: true } }, risks.records[0]),
    ).toBe(true)
    draw([risks], { onLink: vi.fn(), onUnlink: vi.fn() })
    expect(screen.queryByRole('button', { name: /연결 해제/ })).not.toBeInTheDocument()
  })

  it('글 영역 — 기록으로 저장과 만든 기록', async () => {
    const onMakeRecord = vi.fn()
    const opinion = area({
      key: 'opinion',
      title: '종합 의견',
      kind: 'text',
      text: '대책 확인',
      made: { id: 'm1', label: '대책 확인', type_slug: 'risk' },
    })
    draw([opinion], { editable: true, onSaveText: vi.fn(), onMakeRecord })
    await userEvent.click(screen.getByRole('button', { name: /기록으로 저장/ }))
    expect(onMakeRecord).toHaveBeenCalledWith(opinion)
    expect(screen.getByRole('link', { name: '대책 확인' })).toHaveAttribute('href', '/o/risk/m1')
  })

  it('AI 가 쓴 글은 「AI 초안」 으로 표시된다', () => {
    draw([area({ key: 'opinion', title: '종합 의견', kind: 'text', text: '초안', ai_draft: true })])
    expect(screen.getByText('AI 초안')).toBeInTheDocument()
  })

  it('짝이 원본보다 왼쪽이어도 원본의 채우는 법으로 그린다 — 「기록 선택」 이 원본을 연다', async () => {
    const onPick = vi.fn()
    const primary = { ...asis, col: 1, fill: 'pick' as const }
    const pair = { ...tobe, col: 0 }
    expect(primaryOf([pair, primary])).toBe(primary)
    draw([pair, primary], { editable: true, onPick })
    await userEvent.click(screen.getByRole('button', { name: /기록 선택/ }))
    expect(onPick).toHaveBeenCalledWith(primary)
  })
})
