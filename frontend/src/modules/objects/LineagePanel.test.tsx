/**
 * 계보 패널(ADR 0041) — **왜 낡음인가를 말한다.**
 *
 * - 접어 둔 채로는 읽지 않고, 펼치면 계보를 부른다.
 * - 이 객체의 낡음 배지와 까닭, 지금 개정(개정 칸의 이름으로), 영향 범위(이것으로 만든 것 n개 ·
 *   낡음 m개).
 * - 위(원천) · 아래(파생물) 줄마다 관계 이름 · 상대 객체 링크 · 고정 개정 → 지금 개정 · 그 줄의
 *   낡음과 까닭. 볼 수 없는 객체는 링크 없이. 잘렸으면 「잘림」 과 전체 줄 수.
 * - 「다시 고정」 — 바로 붙은 원천 관계 중 낡은 것이 기본으로 골라져 있고, 근거 · 메모와 함께
 *   보낸다. 결과(바뀐 n개 · 그대로 m개)와 새 계보로 갱신한다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { LineageEnd, LineageLine, LineageOut } from '@/modules/objects/api'
import type { PropertyDef } from '@/modules/ontology/api'

const objectApi = vi.hoisted(() => ({ lineage: vi.fn(), repinLineage: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

function end(label: string, extra: Partial<LineageEnd> = {}): LineageEnd {
  return {
    id: `id-${label}`,
    label,
    key: null,
    type_slug: 'mesh',
    type_label: '메시',
    state: 'fresh',
    hidden: false,
    ...extra,
  }
}

const ME = end('해석 결과 R-1', { id: 'me', type_slug: 'result', type_label: '해석 결과' })

function line(extra: Partial<LineageLine> = {}): LineageLine {
  return {
    relation_id: 'rel-1',
    relation: 'made_from',
    relation_label: '만든 원천',
    depth: 1,
    source: end('메시 M-12'),
    derived: ME,
    revision_key: 'rev',
    pinned_revision: 'B',
    pinned_at: '2026-10-01T09:00:00+09:00',
    revision: 'C',
    source_updated_at: '2026-10-20T09:00:00+09:00',
    stale: true,
    reason: '메시 M-12 의 개정 B → C',
    ...extra,
  }
}

function out(extra: Partial<LineageOut> = {}): LineageOut {
  return {
    object_id: 'me',
    type_slug: 'result',
    label: '해석 결과 R-1',
    state: 'direct',
    reasons: ['메시 M-12 의 개정 B → C'],
    revision_key: 'run_no',
    revision: '7',
    up: [
      line(),
      line({
        relation_id: 'rel-2',
        source: end('조건 C-3', { type_slug: 'setup', type_label: '해석 조건' }),
        pinned_revision: '2',
        revision: '2',
        stale: false,
        reason: '',
      }),
      line({
        relation_id: 'rel-3',
        depth: 2,
        source: end('', { id: null, hidden: true, type_slug: '', type_label: '' }),
        derived: end('메시 M-12'),
        stale: false,
        reason: '',
      }),
    ],
    up_total: 3,
    down: [
      line({
        relation_id: 'rel-9',
        relation_label: '보고',
        source: ME,
        derived: end('보고서 D-1', {
          id: 'doc-1',
          type_slug: 'report',
          type_label: '보고서',
          state: 'upstream',
        }),
        revision_key: '',
        pinned_revision: null,
        revision: null,
        stale: false,
        reason: '',
      }),
    ],
    down_total: 1,
    truncated: false,
    impact: { derived: 4, stale: 2 },
    can_repin: true,
    kinds: ['made_from'],
    note: '',
    ...extra,
  }
}

const DEFS = [{ key: 'run_no', label: '실행 번호' }] as PropertyDef[]

async function mount(extra = {}) {
  const { LineagePanel } = await import('@/modules/objects/LineagePanel')
  render(
    <MemoryRouter>
      <LineagePanel typeSlug="result" objectId="me" defs={DEFS} {...extra} />
    </MemoryRouter>,
  )
}

async function expand() {
  await userEvent.click(screen.getByRole('button', { name: /계보/, expanded: false }))
}

describe('계보 패널', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    objectApi.lineage.mockResolvedValue(out())
  })

  it('접어 둔 채로는 읽지 않고, 펼치면 계보를 부른다', async () => {
    await mount()
    expect(objectApi.lineage).not.toHaveBeenCalled()
    await expand()
    await waitFor(() => expect(objectApi.lineage).toHaveBeenCalledWith('result', 'me'))
  })

  it('이 객체의 낡음 배지 · 까닭 · 지금 개정 · 영향 범위를 적는다', async () => {
    await mount()
    await expand()
    const reasons = await screen.findByRole('list', { name: '낡은 까닭' })
    expect(within(reasons).getByText('메시 M-12 의 개정 B → C')).toBeInTheDocument()
    // 바로 낡음의 배지는 「낡음」.
    expect(screen.getAllByText('낡음').length).toBeGreaterThan(0)
    // 개정 칸은 속성 이름으로.
    expect(screen.getByText(/지금 개정\(실행 번호\)/)).toBeInTheDocument()
    expect(screen.getByText('7')).toBeInTheDocument()
    expect(screen.getByText(/영향 범위/).parentElement).toHaveTextContent(
      '영향 범위 이것으로 만든 것 4개(낡음 2개)',
    )
  })

  it('위 · 아래 줄마다 관계 · 상대 링크 · 고정 개정 → 지금 개정 · 그 줄의 까닭을 적는다', async () => {
    await mount()
    await expand()
    const up = await screen.findByRole('list', { name: '위 (원천)' })
    const mesh = within(up).getByRole('link', { name: '메시 M-12' })
    expect(mesh).toHaveAttribute('href', '/o/mesh/id-메시 M-12')
    const first = mesh.closest('li') as HTMLElement
    expect(first).toHaveTextContent('만든 원천')
    expect(first).toHaveTextContent('고정 개정 B → 지금 C')
    expect(first).toHaveTextContent('낡음메시 M-12 의 개정 B → C')
    // 낡지 않은 줄은 배지가 없다.
    const setup = within(up).getByRole('link', { name: '조건 C-3' }).closest('li') as HTMLElement
    expect(setup).toHaveTextContent('고정 개정 2 → 지금 2')
    expect(within(setup).queryByText('낡음')).toBeNull()
    // 볼 수 없는 객체는 링크 없이, 두 걸음 떨어진 줄은 이 객체 쪽 끝을 함께.
    const hidden = within(up).getByText('(볼 수 없는 객체)').closest('li') as HTMLElement
    expect(within(hidden).queryByRole('link')).toBeNull()
    expect(hidden).toHaveTextContent('→ 메시 M-12')

    const down = screen.getByRole('list', { name: '아래 (파생물)' })
    const report = within(down).getByRole('link', { name: '보고서 D-1' })
    expect(report).toHaveAttribute('href', '/o/report/doc-1')
    const row = report.closest('li') as HTMLElement
    // 상대가 위에서 낡았으면 그 배지, 개정 칸이 없는 원천은 시각으로 판정한다.
    expect(within(row).getByText('위에서 낡음')).toBeInTheDocument()
    expect(row).toHaveTextContent('고정 시각')
    expect(row).toHaveTextContent('원천 수정 시각')
  })

  it('잘렸으면 「잘림」 과 전체 줄 수를 적는다', async () => {
    objectApi.lineage.mockResolvedValue(out({ down_total: 812, truncated: true }))
    await mount()
    await expand()
    expect(await screen.findByText('잘림')).toBeInTheDocument()
    expect(screen.getByText(/전체 812줄 중 1줄 표시/)).toBeInTheDocument()
  })

  it('이어진 것이 없으면 서버가 준 까닭을 적는다', async () => {
    objectApi.lineage.mockResolvedValue(
      out({
        state: 'fresh',
        reasons: [],
        up: [],
        up_total: 0,
        down: [],
        down_total: 0,
        impact: { derived: 0, stale: 0 },
        note: '이 객체는 계보 관계로 이어진 원천도 파생물도 없습니다.',
      }),
    )
    await mount()
    await expand()
    expect(await screen.findByText('이어진 원천 · 파생물이 없습니다')).toBeInTheDocument()
    expect(
      screen.getByText('이 객체는 계보 관계로 이어진 원천도 파생물도 없습니다.'),
    ).toBeInTheDocument()
    expect(screen.getByText('낡지 않음')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /다시 고정/ })).toBeNull()
  })

  it('다시 고정할 수 없는 사람에게는 단추가 없다', async () => {
    objectApi.lineage.mockResolvedValue(out({ can_repin: false }))
    await mount()
    await expand()
    await screen.findByRole('list', { name: '위 (원천)' })
    expect(screen.queryByRole('button', { name: /다시 고정/ })).toBeNull()
  })

  it('다시 고정 — 낡은 원천 관계가 기본으로 골라져 있고, 근거 · 메모와 함께 보낸다', async () => {
    const after = out({
      state: 'fresh',
      reasons: [],
      up: [line({ pinned_revision: 'C', stale: false, reason: '' })],
      up_total: 1,
    })
    objectApi.repinLineage.mockResolvedValue({
      changed: [
        {
          relation_id: 'rel-1',
          relation: 'made_from',
          source_id: 'id-메시 M-12',
          source_label: '메시 M-12',
          before: { revision: 'B', at: null },
          after: { revision: 'C', at: null },
        },
      ],
      unchanged: 1,
      lineage: after,
    })
    const onRepinned = vi.fn()
    await mount({ onRepinned })
    await expand()
    await userEvent.click(await screen.findByRole('button', { name: /다시 고정/ }))

    const dialog = await screen.findByRole('dialog')
    // 원천 객체는 안 바뀐다 — 위에서 낡음은 풀리지 않는다고 그 자리에 적는다.
    expect(dialog).toHaveTextContent('위에서 낡음은 풀리지 않습니다')
    // 바로 붙은 선(깊이 1)만 — 두 걸음 떨어진 선은 이 객체의 원천 관계가 아니다.
    const boxes = within(dialog).getAllByRole('checkbox')
    expect(boxes).toHaveLength(2)
    expect(within(dialog).getByRole('checkbox', { name: /메시 M-12/ })).toBeChecked()
    expect(within(dialog).getByRole('checkbox', { name: /조건 C-3/ })).not.toBeChecked()
    // 근거 — 기본은 「영향 없음 확인」.
    expect(within(dialog).getByRole('radio', { name: /영향 없음 확인/ })).toBeChecked()
    await userEvent.click(within(dialog).getByRole('checkbox', { name: /조건 C-3/ }))
    await userEvent.click(within(dialog).getByRole('radio', { name: /다시 만들었음/ }))
    await userEvent.type(within(dialog).getByLabelText('메모'), '새 메시로 다시 돌림')
    await userEvent.click(within(dialog).getByRole('button', { name: '2개 다시 고정' }))

    await waitFor(() =>
      expect(objectApi.repinLineage).toHaveBeenCalledWith('result', 'me', {
        relation_ids: ['rel-1', 'rel-2'],
        basis: 'rebuilt',
        note: '새 메시로 다시 돌림',
      }),
    )
    // 결과와 새 계보 — 다시 부르지 않는다.
    expect(await screen.findByRole('status')).toHaveTextContent(
      '다시 고정했습니다 — 바뀐 관계 1개 · 그대로 1개.',
    )
    expect(screen.getByText('낡지 않음')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: '낡은 까닭' })).toBeNull()
    expect(objectApi.lineage).toHaveBeenCalledTimes(1)
    expect(onRepinned).toHaveBeenCalled()
  })

  it('다시 고정이 거절되면 창을 닫지 않고 그 말을 보인다', async () => {
    objectApi.repinLineage.mockRejectedValue(new Error('이 객체를 수정할 권한이 없습니다'))
    await mount()
    await expand()
    await userEvent.click(await screen.findByRole('button', { name: /다시 고정/ }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: '1개 다시 고정' }))
    expect(await within(dialog).findByText(/권한이 없습니다/)).toBeInTheDocument()
    // 메모를 비웠으면 싣지 않는다.
    expect(objectApi.repinLineage.mock.calls[0][2]).toEqual({
      relation_ids: ['rel-1'],
      basis: 'checked',
    })
  })
})
