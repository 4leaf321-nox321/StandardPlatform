/**
 * 표로 붙여넣기가 지키는 것 — **엑셀 범위가 붙인 칸부터 채워지고, 표는 그대로 탭 글자가 된다.**
 *
 * 형식이 둘이면 두 곳이 갈라진다. 표는 사람이 보기 좋으라고 있는 껍데기이고, 서버로는 늘
 * 「헤더 한 줄 + 탭으로 갈린 줄들」 이 간다.
 */

import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import {
  PasteGrid,
  emptyRows,
  filledRows,
  isChecked,
  toLines,
  unknownParts,
} from '@/shared/components/PasteGrid'
import type { GridColumn } from '@/shared/components/PasteGrid'

const COLUMNS: GridColumn[] = [
  { key: 'key', header: 'key', label: '식별자', required: true },
  { key: 'label', header: 'label', label: '이름', required: true },
  { key: 'weight', header: 'weight', label: '무게', help: 'kg' },
]

function Harness({ onChange }: { onChange?: (rows: string[][]) => void }) {
  const [rows, setRows] = useState<string[][]>(emptyRows(COLUMNS))
  return (
    <PasteGrid
      columns={COLUMNS}
      rows={rows}
      onRows={(next) => {
        setRows(next)
        onChange?.(next)
      }}
    />
  )
}

/** 엑셀에서 복사한 것처럼 — 탭과 줄바꿈이 섞인 글자. */
async function pasteInto(label: string, text: string) {
  const cell = screen.getByLabelText(label)
  cell.focus()
  await userEvent.paste(text)
}

describe('표로 붙여넣기', () => {
  it('엑셀 범위를 붙이면 그 칸부터 여러 줄이 채워진다', async () => {
    render(<Harness />)
    await pasteInto('1번 줄 key', 'P-1\t볼트\t1.5\nP-2\t너트\t0.5')

    expect(screen.getByLabelText('1번 줄 key')).toHaveValue('P-1')
    expect(screen.getByLabelText('1번 줄 label')).toHaveValue('볼트')
    expect(screen.getByLabelText('2번 줄 weight')).toHaveValue('0.5')
    // 마지막에 빈 줄이 하나 남는다 — 「줄 추가」 를 누르러 가지 않아도 계속 친다.
    expect(screen.getByLabelText('3번 줄 key')).toHaveValue('')
  })

  it('가운데 칸에 붙이면 그 자리부터 — 열을 넘어가는 것은 버린다', async () => {
    render(<Harness />)
    await pasteInto('1번 줄 label', '볼트\t1.5\t넘침')
    expect(screen.getByLabelText('1번 줄 key')).toHaveValue('')
    expect(screen.getByLabelText('1번 줄 label')).toHaveValue('볼트')
    expect(screen.getByLabelText('1번 줄 weight')).toHaveValue('1.5')
  })

  it('한 칸짜리 붙여넣기는 그 칸에 그대로 들어간다', async () => {
    render(<Harness />)
    await pasteInto('1번 줄 label', '볼트')
    expect(screen.getByLabelText('1번 줄 label')).toHaveValue('볼트')
  })

  it('줄을 지울 수 있다', async () => {
    render(<Harness />)
    await pasteInto('1번 줄 key', 'P-1\t볼트\nP-2\t너트')
    await userEvent.click(screen.getByRole('button', { name: '1번 줄 제거' }))
    expect(screen.getByLabelText('1번 줄 key')).toHaveValue('P-2')
  })

  it('머리에는 이름이 크게, 서버로 가는 열 이름은 그 아래 회색으로', () => {
    // `weight` 만 있으면 그것이 「무게」 인지 「무게중심」 인지 표가 말해 주지 못하고,
    // 「무게」 만 있으면 엑셀에서 채워 올 때 어떤 열 이름으로 적어야 할지 알 수 없다.
    render(<Harness />)
    const head = screen.getByRole('columnheader', { name: /무게/ })
    expect(head).toHaveTextContent('무게')
    expect(head).toHaveTextContent('weight')
    expect(head).toHaveTextContent('kg')
    // 칸의 이름표는 **서버로 가는 이름**이다 — 붙여넣기 시험이 그것으로 칸을 찾는다.
    expect(screen.getByLabelText('1번 줄 weight')).toBeInTheDocument()
  })

  it('서버로는 헤더 한 줄 + 탭 글자로 간다 — 다 빈 줄은 빠진다', () => {
    const rows = [
      ['P-1', '볼트', '1.5'],
      ['', '', ''],
      ['P-2', '너트', ''],
    ]
    expect(toLines(COLUMNS, rows)).toEqual(['key\tlabel\tweight', 'P-1\t볼트\t1.5', 'P-2\t너트\t'])
    expect(filledRows(rows)).toBe(2)
  })
})

describe('PasteGrid — 등록된 목록', () => {
  const COLUMNS: GridColumn[] = [
    { key: 'subject', header: '시험 항목', options: ['낙하 시험', '굽힘 시험'] },
    { key: 'levels', header: '자동화', options: ['전처리 자동화', '실행 자동화'], multi: true },
    { key: 'note', header: '근거' },
  ]

  it('목록이 있는 열은 드롭다운으로 고른다', () => {
    render(<PasteGrid columns={COLUMNS} rows={[['', '', '']]} onRows={vi.fn()} />)
    // `<datalist>` 라 붙여넣기 · 손 입력을 막지 않는다 — 목록은 거기 붙는다.
    const cell = screen.getByLabelText('1번 줄 시험 항목')
    const listId = cell.getAttribute('list')
    expect(listId).toBeTruthy()
    const list = document.getElementById(listId as string)
    expect([...(list?.querySelectorAll('option') ?? [])].map((one) => one.getAttribute('value'))).toEqual(
      ['낙하 시험', '굽힘 시험'],
    )
  })

  it('등록된 이름이 아니면 그 칸이 표시된다 — 저장을 눌러 보고 알지 않는다', () => {
    render(
      <PasteGrid
        columns={COLUMNS}
        rows={[
          ['낙하 시험', '전처리 자동화 · 실행 자동화', '맞는 줄'],
          ['낙하시험', '없는 항목', '틀린 줄'],
        ]}
        onRows={vi.fn()}
      />,
    )
    expect(screen.getByLabelText('2번 줄 시험 항목')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('1번 줄 시험 항목')).toHaveAttribute('aria-invalid', 'false')
    // 여럿을 적는 열은 **토큰마다** 본다.
    expect(screen.getByLabelText('1번 줄 자동화')).toHaveAttribute('aria-invalid', 'false')
    expect(screen.getByLabelText('2번 줄 자동화')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText(/등록된 이름이 아닌 칸이 2개/)).toBeInTheDocument()
  })
})

describe('PasteGrid — 체크 열', () => {
  // 여러 항목을 고르는 축(자동화 · 시험 대체)은 **항목마다 한 열**이다. 한 칸에
  // 「전처리 자동화 · 실행 자동화」 를 적게 하면 이름을 정확히 외워야 한다.
  const COLUMNS: GridColumn[] = [
    { key: 'subject', header: '시험 항목' },
    { key: 'pick:전처리 자동화', header: '전처리 자동화', check: true },
    { key: 'pick:실행 자동화', header: '실행 자동화', check: true },
    { key: 'note', header: '근거' },
  ]

  function CheckHarness({ start }: { start: string[][] }) {
    const [rows, setRows] = useState(start)
    return <PasteGrid columns={COLUMNS} rows={rows} onRows={setRows} />
  }

  it('클릭으로 켜고 끈다 — 켠 칸은 O 로 남는다', async () => {
    const onRows = vi.fn()
    render(<PasteGrid columns={COLUMNS} rows={[['낙하 시험', '', '', '']]} onRows={onRows} />)
    await userEvent.click(screen.getByLabelText('1번 줄 전처리 자동화'))
    // 뒤에 빈 줄 하나가 따라온다(계속 칠 자리) — 고쳐진 줄만 본다.
    expect((onRows.mock.calls[0][0] as string[][])[0]).toEqual(['낙하 시험', 'O', '', ''])
  })

  it('지금 켜진 항목은 체크된 채로 시작한다', () => {
    render(<PasteGrid columns={COLUMNS} rows={[['낙하 시험', 'O', '', '']]} onRows={vi.fn()} />)
    expect(screen.getByLabelText('1번 줄 전처리 자동화')).toBeChecked()
    expect(screen.getByLabelText('1번 줄 실행 자동화')).not.toBeChecked()
  })

  it('엑셀에서 붙인 O · 예 · v 를 켜진 것으로 읽는다 — 사람마다 다른 글자를 쓴다', async () => {
    render(<CheckHarness start={[['', '', '', '']]} />)
    const cell = screen.getByLabelText('1번 줄 시험 항목')
    cell.focus()
    await userEvent.paste('낙하 시험\tO\t예\t시험 성적서')
    expect(screen.getByLabelText('1번 줄 전처리 자동화')).toBeChecked()
    expect(screen.getByLabelText('1번 줄 실행 자동화')).toBeChecked()
    expect(screen.getByLabelText('1번 줄 근거')).toHaveValue('시험 성적서')
  })

  it('체크 열은 「등록된 이름」 검사에서 빠진다 — 켜짐 · 꺼짐으로만 읽는다', () => {
    expect(isChecked('O')).toBe(true)
    expect(isChecked(' 예 ')).toBe(true)
    expect(isChecked('')).toBe(false)
    expect(unknownParts({ key: 'a', header: 'A', check: true, options: ['O'] }, 'v')).toEqual([])
  })
})

describe('unknownParts', () => {
  it('목록이 없는 열은 늘 맞다고 본다 — 자유롭게 적는 칸이다', () => {
    expect(unknownParts({ key: 'note', header: '근거' }, '아무 글')).toEqual([])
  })

  it('여럿 적는 칸은 토큰마다 가른다', () => {
    const column: GridColumn = { key: 'a', header: 'A', options: ['가', '나'], multi: true }
    expect(unknownParts(column, '가 · 나')).toEqual([])
    expect(unknownParts(column, '가 · 다')).toEqual(['다'])
  })
})
