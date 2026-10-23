/**
 * 장표 그리기(ADR 0037) — 짝 영역(AS-IS / TO-BE)은 한 덩이라 기록마다 연결 항목 줄이 한 번이고
 * 좌우 칸이 같은 줄에 선다. 고장 난 영역만 까닭을 말하고, 글 영역은 「작성한 글」 로 갈린다.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { RenderedArea, SlideRecord } from '@/modules/slides/api'
import { SlideGrid, blocksOf } from '@/modules/slides/SlideGrid'

function record(id: string, values: Record<string, string>): SlideRecord {
  return {
    id,
    label: id,
    type_slug: 'change',
    values,
    links: [{ label: '부품', items: [{ id: 'p1', label: '커넥터', type_slug: 'part' }] }],
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
  it('짝 영역은 한 덩이 — 기록마다 연결 항목 줄 한 번, 좌우 칸이 같은 줄', () => {
    expect(blocksOf([tobe, asis]).map((group) => group.map((one) => one.key))).toEqual([
      ['asis', 'tobe'],
    ])
    draw([asis, tobe])
    const block = screen.getByRole('region', { name: 'AS-IS · TO-BE' })
    const rows = within(block).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getAllByText('커넥터')).toHaveLength(1)
    expect(within(rows[0]).getByText('2핀')).toBeInTheDocument()
    expect(within(rows[0]).getByText('4핀')).toBeInTheDocument()
    expect(within(rows[1]).getByText('PA66')).toBeInTheDocument()
    expect(within(rows[1]).getByText('PBT')).toBeInTheDocument()
    // 더 있으면 수를 말하고, 같은 조건의 목록으로 간다.
    expect(within(block).getByText('1건 더 있습니다.')).toBeInTheDocument()
    expect(within(block).getByRole('link', { name: /목록에서 보기/ })).toHaveAttribute(
      'href',
      '/o/change?f.model.eq=m1',
    )
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
})
