/**
 * 표로 붙여넣기가 지키는 것 — **엑셀 범위가 붙인 칸부터 채워지고, 표는 그대로 탭 글자가 된다.**
 *
 * 형식이 둘이면 두 곳이 갈라진다. 표는 사람이 보기 좋으라고 있는 껍데기이고, 서버로는 늘
 * 「헤더 한 줄 + 탭으로 갈린 줄들」 이 간다.
 */

import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { PasteGrid, emptyRows, filledRows, toLines } from '@/shared/components/PasteGrid'
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
