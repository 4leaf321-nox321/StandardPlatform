/**
 * 검토함이 지키는 것 — **한꺼번에 확인하기 쉽게, 그러나 누르기 전에 무엇이 되는지 보이고.**
 *
 * 키보드(j/k 이동 · x 선택 · a 추천대로 · i 무시 · 숫자로 후보), 「추천대로 모두」 의 미리 보기 →
 * 적용(막힌 것은 따로 센다), 고른 것의 무시, 큰 묶음은 작업으로.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const reviewApi = vi.hoisted(() => ({
  items: vi.fn(),
  plan: vi.fn(),
  apply: vi.fn(),
  batches: vi.fn(),
  undo: vi.fn(),
  accuracy: vi.fn(),
}))
vi.mock('@/modules/review/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/modules/review/api')>()
  return { ...real, reviewApi }
})
const jobsApi = vi.hoisted(() => ({ waitFor: vi.fn() }))
vi.mock('@/modules/jobs/api', () => ({ jobsApi }))

function item(id: string, extra: Record<string, unknown> = {}) {
  return {
    kind: 'alias_candidate',
    kind_label: '별칭 후보',
    id,
    question: `「${id}」 을(를) 무엇의 별칭으로 추가할까요?`,
    evidence: ['툴에서 4번 · 2명이 찾았으나 못 찾음'],
    choices: [
      { value: `${id}-a`, label: `${id} 후보 A`, detail: '툴', score: 0.95 },
      { value: `${id}-b`, label: `${id} 후보 B`, detail: '툴', score: 0.6 },
    ],
    recommended: `${id}-a`,
    recommended_label: `${id} 후보 A`,
    confidence: 0.95,
    reason: '이름이 비슷함 0.95',
    type_slug: 'tool',
    type_label: '툴',
    workspace_slug: 'team',
    workspace_name: '시험팀',
    rows: 1,
    group: id,
    group_label: id,
    blocked: null,
    link: '/quality#alias_candidates',
    ignorable: true,
    actionable: true,
    members: [],
    ...extra,
  }
}

const kinds = [
  {
    kind: 'alias_candidate',
    label: '별칭 후보',
    description: '여러 번 못 찾은 말',
    link: '/quality#alias_candidates',
    total: 2,
    matched: 2,
    scanned: 2,
    actionable: true,
    undo_note: '',
    notes: [],
  },
]

function page(items: unknown[]) {
  return {
    kinds,
    items,
    total: items.length,
    limit: 50,
    offset: 0,
    types: [{ value: 'tool', label: '툴', count: items.length }],
    workspaces: [{ value: 'team', label: '시험팀', count: items.length }],
    notes: [],
  }
}

function plan(extra: Record<string, unknown> = {}) {
  return {
    fingerprint: 'fp',
    mode: 'decisions',
    threshold: null,
    decisions: [{ kind: 'alias_candidate', item_id: 'r1', action: 'accept' }],
    counts: { apply: 1, blocked: 0 },
    by_kind: [
      {
        kind: 'alias_candidate',
        label: '별칭 후보',
        apply: 1,
        blocked: 0,
        rows: 1,
        undoable: true,
        undo_note: '',
      },
    ],
    outcomes: [
      {
        kind: 'alias_candidate',
        kind_label: '별칭 후보',
        item_id: 'r1',
        question: 'q',
        action: 'accept',
        choice: 'r1-a',
        choice_label: 'r1 후보 A',
        status: 'apply',
        effect: '「r1」 → r1 후보 A의 별칭',
        message: '',
        rows: 1,
        undoable: true,
        recommended: 'r1-a',
        confidence: 0.95,
        followed: true,
      },
    ],
    outcomes_omitted: 0,
    skipped: 0,
    rows: 1,
    as_job: false,
    notes: [],
    ...extra,
  }
}

async function open() {
  const { default: ReviewPage } = await import('@/modules/review/ReviewPage')
  render(
    <MemoryRouter initialEntries={['/review']}>
      <ReviewPage />
    </MemoryRouter>,
  )
  await screen.findByText('「r1」 을(를) 무엇의 별칭으로 추가할까요?')
}

function rowOf(id: string): HTMLElement {
  return screen.getByText(`「${id}」 을(를) 무엇의 별칭으로 추가할까요?`).closest('li')!
}

beforeEach(() => {
  vi.clearAllMocks()
  reviewApi.items.mockResolvedValue(page([item('r1'), item('r2')]))
  reviewApi.batches.mockResolvedValue([])
  reviewApi.accuracy.mockResolvedValue({ kinds: [], bands: [] })
})

describe('검토함', () => {
  it('한 줄에 물음 · 근거 · 후보 · 추천 · 확신도를 보인다', async () => {
    await open()
    const row = rowOf('r1')
    expect(within(row).getByText('툴에서 4번 · 2명이 찾았으나 못 찾음')).toBeInTheDocument()
    expect(within(row).getByText('확신도 0.95')).toBeInTheDocument()
    expect(within(row).getByText('r1 후보 A', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.getByLabelText('단축키')).toHaveTextContent('추천대로')
  })

  it('키보드 — j/k 로 오르내리고 x 로 고르고 a 로 추천대로(복원할 수 있는 한 줄은 바로)', async () => {
    reviewApi.plan.mockResolvedValue(plan())
    reviewApi.apply.mockResolvedValue({
      applied: true,
      batch_id: 'b1',
      job_id: null,
      counts: { done: 1, refused: 0 },
      outcomes: [],
      undoable: 1,
    })
    await open()
    expect(rowOf('r1')).toHaveAttribute('data-focused', 'true')
    fireEvent.keyDown(window, { key: 'j' })
    await waitFor(() => expect(rowOf('r2')).toHaveAttribute('data-focused', 'true'))
    fireEvent.keyDown(window, { key: 'k' })
    await waitFor(() => expect(rowOf('r1')).toHaveAttribute('data-focused', 'true'))
    fireEvent.keyDown(window, { key: 'x' })
    await waitFor(() =>
      expect(within(rowOf('r1')).getByRole('checkbox')).toBeChecked(),
    )

    fireEvent.keyDown(window, { key: 'a' })
    await waitFor(() =>
      expect(reviewApi.plan).toHaveBeenCalledWith({
        mode: 'decisions',
        decisions: [{ kind: 'alias_candidate', item_id: 'r1', action: 'accept' }],
      }),
    )
    // 복원할 수 있고 막힘이 없는 한 줄 — 미리 보기 창 없이 적용하고 「복원」 을 세운다.
    await waitFor(() => expect(reviewApi.apply).toHaveBeenCalledWith(plan(), 'one'))
    expect(await screen.findByText('1건을 처리했습니다.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '복원' })).toBeInTheDocument()
  })

  it('숫자로 후보를 고른다 — 지금 줄의 n 번째 후보로', async () => {
    reviewApi.plan.mockResolvedValue(plan({ counts: { apply: 1, blocked: 1 } }))
    await open()
    fireEvent.keyDown(window, { key: '2' })
    await waitFor(() =>
      expect(reviewApi.plan).toHaveBeenCalledWith({
        mode: 'decisions',
        decisions: [
          { kind: 'alias_candidate', item_id: 'r1', action: 'choose', value: 'r1-b' },
        ],
      }),
    )
    // 막히는 것이 있으면 바로 적용하지 않고 미리 보기를 띄운다.
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(reviewApi.apply).not.toHaveBeenCalled()
  })

  it('추천대로 모두 — 문턱과 거르기로 미리 보고, 막힌 것은 따로 센 뒤 적용한다', async () => {
    const recommended = plan({
      mode: 'recommended',
      threshold: 0.9,
      counts: { apply: 2, blocked: 1 },
      skipped: 3,
      by_kind: [
        {
          kind: 'duplicate',
          label: '병합 후보',
          apply: 2,
          blocked: 1,
          rows: 2,
          undoable: false,
          undo_note: '병합은 복원할 수 없습니다.',
        },
      ],
    })
    reviewApi.plan.mockResolvedValue(recommended)
    reviewApi.apply.mockResolvedValue({
      applied: true,
      batch_id: 'b2',
      job_id: null,
      counts: { done: 2, refused: 0 },
      outcomes: [],
      undoable: 0,
    })
    await open()
    await userEvent.clear(screen.getByLabelText('문턱'))
    await userEvent.type(screen.getByLabelText('문턱'), '0.85')
    await userEvent.click(screen.getByRole('button', { name: '추천대로 모두' }))
    await waitFor(() =>
      expect(reviewApi.plan).toHaveBeenCalledWith({
        mode: 'recommended',
        filters: expect.objectContaining({ kind: null }),
        threshold: 0.85,
      }),
    )
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('적용 2건')).toBeInTheDocument()
    expect(within(dialog).getByText('막힘 1건')).toBeInTheDocument()
    expect(within(dialog).getByText('문턱 아래 · 추천 없음 3건')).toBeInTheDocument()
    expect(within(dialog).getByText(/병합 후보은\(는\) 적용하면 복원할 수/)).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: '2건 적용' }))
    await waitFor(() => expect(reviewApi.apply).toHaveBeenCalledWith(recommended, 'recommended'))
    expect(await screen.findByText('2건을 처리했습니다.')).toBeInTheDocument()
    // 복원할 수 없는 묶음이라 「복원」 단추를 세우지 않는다.
    expect(screen.queryByRole('button', { name: '복원' })).not.toBeInTheDocument()
  })

  it('고른 것을 한 번에 무시 — 미리 보기를 거친다', async () => {
    reviewApi.plan.mockResolvedValue(
      plan({
        counts: { apply: 2, blocked: 0 },
        decisions: [
          { kind: 'alias_candidate', item_id: 'r1', action: 'ignore' },
          { kind: 'alias_candidate', item_id: 'r2', action: 'ignore' },
        ],
      }),
    )
    await open()
    const bar = screen.getByRole('toolbar', { name: '일괄 확인' })
    await userEvent.click(within(bar).getByRole('button', { name: '이 쪽 전부 선택' }))
    await userEvent.click(within(bar).getByRole('button', { name: '무시' }))
    await waitFor(() =>
      expect(reviewApi.plan).toHaveBeenCalledWith({
        mode: 'decisions',
        decisions: [
          { kind: 'alias_candidate', item_id: 'r1', action: 'ignore' },
          { kind: 'alias_candidate', item_id: 'r2', action: 'ignore' },
        ],
      }),
    )
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(reviewApi.apply).not.toHaveBeenCalled()
  })

  it('묶은 줄의 결정은 펼친 줄마다 간다', async () => {
    reviewApi.items.mockResolvedValue(
      page([item('r1', { id: 'group:x', members: ['m1', 'm2'], question: '「r1」 을(를) 무엇의 별칭으로 추가할까요?' })]),
    )
    reviewApi.plan.mockResolvedValue(plan({ counts: { apply: 2, blocked: 0 } }))
    await open()
    fireEvent.keyDown(window, { key: 'a' })
    await waitFor(() =>
      expect(reviewApi.plan).toHaveBeenCalledWith({
        mode: 'decisions',
        decisions: [
          { kind: 'alias_candidate', item_id: 'm1', action: 'accept' },
          { kind: 'alias_candidate', item_id: 'm2', action: 'accept' },
        ],
      }),
    )
  })

  it('큰 묶음은 작업으로 돌고 끝나면 결과를 말한다', async () => {
    const big = plan({ as_job: true, counts: { apply: 300, blocked: 0 } })
    reviewApi.plan.mockResolvedValue(big)
    reviewApi.apply.mockResolvedValue({
      applied: false,
      batch_id: null,
      job_id: 'job1',
      counts: { queued: 300 },
      outcomes: [],
      undoable: 0,
    })
    jobsApi.waitFor.mockResolvedValue({
      id: 'job1',
      status: 'done',
      error: null,
      result: {
        applied: true,
        batch_id: 'b9',
        counts: { done: 300, refused: 0 },
        rows: [{ status: 'done', undoable: true }],
      },
    })
    await open()
    await userEvent.click(screen.getByRole('button', { name: '추천대로 모두' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('작업으로 실행')).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: '300건 적용' }))
    await waitFor(() => expect(jobsApi.waitFor).toHaveBeenCalledWith('job1', expect.any(Function)))
    expect(await screen.findByText('300건을 처리했습니다.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '복원' })).toBeInTheDocument()
  })
})
