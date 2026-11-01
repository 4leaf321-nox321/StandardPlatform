/**
 * BOM 전개(ADR 0040) — **세지 않은 것 · 1개로 센 것을 말한다.**
 *
 * - 접어 둔 채로는 세지 않고, 펼치면 「정리」 를 부른다 — 보기를 바꾸면 그 보기를 부른다.
 * - 표는 파일과 같은 열 · 같은 순서다(보기마다).
 * - 「수량 없음」 · 「볼 수 없는 객체」 · 「잘림」 을 수와 함께 적는다. 수량 칸이 없으면 모두 1개로
 *   센다고 말한다.
 * - 가린 줄(볼 수 없는 · 삭제된 객체)은 회색 글자이고 링크가 없다.
 * - 「어디에 쓰였나」 는 맨 위 객체마다 그것 하나에 든 이 객체의 총수량을 적는다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ExplodeLine, ExplodeOut } from '@/modules/objects/api'

const objectApi = vi.hoisted(() => ({ explode: vi.fn(), exportExplode: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

function line(label: string, extra: Partial<ExplodeLine> = {}): ExplodeLine {
  return {
    id: `id-${label}`,
    label,
    key: null,
    type_slug: 'part',
    status: 'active',
    hidden: false,
    deleted: false,
    level: 1,
    total: 1,
    quantity: null,
    quantity_missing: false,
    depth_max: null,
    paths: null,
    missing: null,
    ...extra,
  }
}

function out(extra: Partial<ExplodeOut> = {}): ExplodeOut {
  return {
    mode: 'summary',
    root: {
      id: 'me',
      label: '제품',
      key: 'P-1',
      type_slug: 'part',
      status: 'active',
      hidden: false,
      deleted: false,
    },
    as_of: '2026-10-30',
    relation: 'bom_of',
    relation_label: '구성',
    parent_end: 'src',
    quantity_key: 'qty',
    quantity_label: '수량',
    lines: [],
    tops: [],
    totals: { objects: 0, lines: 0, lines_total: 0, links: 0, missing: 0, hidden: 0, deleted: 0 },
    truncated: false,
    ...extra,
  }
}

async function mount(extra = {}) {
  const { ExplodePanel } = await import('@/modules/objects/ExplodePanel')
  render(
    <MemoryRouter>
      <ExplodePanel
        typeSlug="part"
        objectId="me"
        relationLabel="구성"
        typeLabels={{ part: '부품' }}
        {...extra}
      />
    </MemoryRouter>,
  )
}

function headers(): string[] {
  return within(screen.getAllByRole('table')[0])
    .getAllByRole('columnheader')
    .map((one) => one.textContent ?? '')
}

describe('BOM 전개', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('접어 둔 채로는 세지 않고, 펼치면 정리를 부른다', async () => {
    objectApi.explode.mockResolvedValue(
      out({
        lines: [line('볼트', { total: 24, depth_max: 3, paths: 2, missing: 0, key: 'B-1' })],
        totals: {
          objects: 1,
          lines: 1,
          lines_total: 1,
          links: 3,
          missing: 0,
          hidden: 0,
          deleted: 0,
        },
      }),
    )
    await mount()
    expect(objectApi.explode).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: /BOM 전개/ }))
    await waitFor(() =>
      expect(objectApi.explode).toHaveBeenCalledWith('part', 'me', { mode: 'summary', asOf: null }),
    )
    expect(await screen.findByRole('link', { name: '볼트' })).toHaveAttribute(
      'href',
      '/o/part/id-볼트',
    )
    // 파일과 같은 열 · 같은 순서.
    expect(headers()).toEqual([
      '가장 얕은 단계',
      '식별자',
      '이름',
      '타입',
      '총수량',
      '가장 깊은 단계',
      '경로 수',
      '수량 없음',
    ])
    expect(screen.getByText('부품')).toBeInTheDocument()
    expect(screen.getByText('24')).toBeInTheDocument()
  })

  it('보기를 바꾸면 그 보기를 부르고, 단계별은 수량 · 누적 수량 열이다', async () => {
    objectApi.explode.mockImplementation((_t: string, _i: string, { mode }: { mode: string }) =>
      Promise.resolve(
        mode === 'indented'
          ? out({
              mode: 'indented',
              lines: [
                line('어셈블리', { level: 1, quantity: 2, total: 2 }),
                line('볼트', { level: 2, quantity: 4, total: 8 }),
              ],
            })
          : out(),
      ),
    )
    await mount({ asOf: '2026-03-01' })
    await userEvent.click(screen.getByRole('button', { name: /BOM 전개/ }))
    await userEvent.click(await screen.findByRole('button', { name: '단계별' }))
    await waitFor(() =>
      expect(objectApi.explode).toHaveBeenLastCalledWith('part', 'me', {
        mode: 'indented',
        asOf: '2026-03-01',
      }),
    )
    expect(await screen.findByRole('link', { name: '볼트' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '단계별' })).toHaveAttribute('aria-pressed', 'true')
    expect(headers()).toEqual(['단계', '식별자', '이름', '타입', '수량', '누적 수량'])
    expect(screen.getByText('8')).toBeInTheDocument()
  })

  it('수량 없음 · 볼 수 없는 객체 · 잘림을 수와 함께 적고, 가린 줄에는 링크가 없다', async () => {
    objectApi.explode.mockResolvedValue(
      out({
        mode: 'indented',
        lines: [
          line('어셈블리', { quantity: null, quantity_missing: true, total: 1 }),
          line('(볼 수 없는 객체)', {
            id: null,
            type_slug: null,
            hidden: true,
            level: 2,
            quantity: 3,
            total: 3,
          }),
        ],
        totals: {
          objects: 1,
          lines: 2,
          lines_total: 7300,
          links: 9,
          missing: 2,
          hidden: 1,
          deleted: 0,
        },
        truncated: true,
      }),
    )
    await mount()
    await userEvent.click(screen.getByRole('button', { name: /BOM 전개/ }))
    expect(await screen.findByText('수량 없음')).toBeInTheDocument()
    expect(screen.getByText(/2개 — 수량 칸 「수량」 에 값이 없거나/)).toBeInTheDocument()
    expect(screen.getByText('잘림')).toBeInTheDocument()
    expect(screen.getByText(/전체 7,300줄 중 2줄 표시/)).toBeInTheDocument()
    expect(screen.getByText(/「정리」 는 객체마다 한 줄입니다/)).toBeInTheDocument()
    expect(screen.getByText('볼 수 없는 객체')).toBeInTheDocument()
    // 줄의 수량 칸 — 1개로 셌다는 표시.
    expect(screen.getByText('(없음)')).toBeInTheDocument()
    // 가린 줄: 이름은 보이되 링크가 아니다.
    expect(screen.getByText('(볼 수 없는 객체)')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '(볼 수 없는 객체)' })).toBeNull()
    expect(screen.getByRole('link', { name: '어셈블리' })).toBeInTheDocument()
  })

  it('수량 칸이 없으면 모든 관계를 1개로 센다고 말한다', async () => {
    objectApi.explode.mockResolvedValue(
      out({ quantity_key: '', quantity_label: '', lines: [line('볼트', { total: 3, paths: 3 })] }),
    )
    await mount()
    await userEvent.click(screen.getByRole('button', { name: /BOM 전개/ }))
    expect(
      await screen.findByText(/수량 칸이 정해지지 않아 모든 관계를 1개로 셉니다/),
    ).toBeInTheDocument()
    // 수량 칸이 없으면 빠진 것이 아니다 — 「수량 없음」 으로 세지 않는다.
    expect(screen.queryByText(/에 값이 없거나/)).toBeNull()
  })

  it('아래에 아무것도 없으면 기준일과 함께 이유를 말한다', async () => {
    objectApi.explode.mockResolvedValue(out())
    await mount()
    await userEvent.click(screen.getByRole('button', { name: /BOM 전개/ }))
    expect(await screen.findByText('아래에 아무것도 없습니다')).toBeInTheDocument()
    expect(screen.getByText(/기준일 2026-10-30에 유효한 「구성」 관계로/)).toBeInTheDocument()
  })

  it('어디에 쓰였나는 맨 위 객체마다 그것 하나에 든 총수량을 적는다', async () => {
    objectApi.explode.mockImplementation((_t: string, _i: string, { mode }: { mode: string }) =>
      Promise.resolve(
        mode === 'where_used'
          ? out({
              mode: 'where_used',
              root: { ...out().root, label: '볼트' },
              lines: [
                line('어셈블리', { level: 1, quantity: 4, total: 4 }),
                line('제품', { level: 2, quantity: 2, total: 8 }),
              ],
              tops: [{ ...line('제품'), total: 8, paths: 1, level: 2 }],
            })
          : out(),
      ),
    )
    await mount()
    await userEvent.click(screen.getByRole('button', { name: /BOM 전개/ }))
    await userEvent.click(await screen.findByRole('button', { name: '어디에 쓰였나' }))
    const tops = await screen.findByRole('table', { name: '맨 위 객체' })
    expect(headers()).toEqual(['단계', '식별자', '이름', '타입', '수량', '하나에 든 볼트'])
    expect(within(tops).getByRole('link', { name: '제품' })).toBeInTheDocument()
    expect(within(tops).getByText('총 8개')).toBeInTheDocument()
  })

  it('유효 기간을 받는 트리에서만 기준일 고르개를 세우고, 다운로드는 지금 보기를 받는다', async () => {
    objectApi.explode.mockResolvedValue(out({ lines: [line('볼트')] }))
    objectApi.exportExplode.mockResolvedValue(undefined)
    const onAsOf = vi.fn()
    await mount({ periodic: true, onAsOf, asOf: '2026-03-01' })
    await userEvent.click(screen.getByRole('button', { name: /BOM 전개/ }))
    expect(await screen.findByLabelText('기준일')).toHaveValue('2026-03-01')
    await userEvent.click(await screen.findByRole('button', { name: /다운로드/ }))
    await waitFor(() =>
      expect(objectApi.exportExplode).toHaveBeenCalledWith(
        'part',
        'me',
        { mode: 'summary', asOf: '2026-03-01' },
        'csv',
        'part-explode-summary.csv',
      ),
    )
  })

  it('유효 기간을 안 받는 트리에는 기준일 고르개가 없다', async () => {
    objectApi.explode.mockResolvedValue(out({ lines: [line('볼트')] }))
    await mount({ periodic: false, onAsOf: vi.fn() })
    await userEvent.click(screen.getByRole('button', { name: /BOM 전개/ }))
    await screen.findByRole('link', { name: '볼트' })
    expect(screen.queryByLabelText('기준일')).toBeNull()
  })
})
