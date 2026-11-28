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
  kinds: vi.fn(),
  plan: vi.fn(),
  apply: vi.fn(),
  bulk: vi.fn(),
  batches: vi.fn(),
  undo: vi.fn(),
  accuracy: vi.fn(),
  auto: vi.fn(),
  setAuto: vi.fn(),
}))
vi.mock('@/modules/review/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/modules/review/api')>()
  return { ...real, reviewApi }
})
const jobsApi = vi.hoisted(() => ({ waitFor: vi.fn(), apply: vi.fn(), download: vi.fn() }))
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
    late: false,
    seconds: 0.4,
  },
]

function page(items: unknown[], shown: unknown[] = kinds) {
  return {
    kinds: shown,
    items,
    total: items.length,
    limit: 50,
    offset: 0,
    types: [{ value: 'tool', label: '툴', count: items.length }],
    workspaces: [{ value: 'team', label: '시험팀', count: items.length }],
    notes: [],
  }
}

/** 볼 수 있는 종류 — 「전체」 는 이 차례로 종류마다 따로 부른다(ADR 0075). */
const infos = [
  ['alias_candidate', '별칭 후보'],
  ['stale_lineage', '낡은 파생물'],
  ['proposed_relation', '제안된 관계'],
  ['broken_ref', '지워진 것을 가리키는 칸'],
].map(([kind, label]) => ({
  kind,
  label,
  description: '',
  link: '/quality',
  actionable: true,
  undo_note: '',
}))

interface Row {
  kind: string
  [key: string]: unknown
}
interface Summary {
  kind: string | null
  [key: string]: unknown
}

/** 종류마다 따로 묻는 것에 그 종류의 줄 · 요약만 돌려준다 — 종류 없이 물으면 전부. */
function serve(all: Row[], shown: Summary[] = kinds, extra: Record<string, unknown> = {}) {
  reviewApi.items.mockImplementation((filters: { kind?: string | null }) => {
    const kind = filters.kind ?? null
    const mine = all.filter((one) => !kind || one.kind === kind)
    const summary = shown.filter((one) => !kind || one.kind === kind)
    const fallback = {
      ...kinds[0],
      kind,
      label: kind,
      total: mine.length,
      matched: mine.length,
      scanned: mine.length,
    }
    return Promise.resolve({ ...page(mine, summary.length ? summary : [fallback]), ...extra })
  })
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
    unread: 0,
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
  reviewApi.kinds.mockResolvedValue(infos)
  serve([item('r1'), item('r2')])
  reviewApi.batches.mockResolvedValue([])
  reviewApi.accuracy.mockResolvedValue({ kinds: [], bands: [] })
  reviewApi.auto.mockResolvedValue([])
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
    await waitFor(() => expect(within(rowOf('r1')).getByRole('checkbox')).toBeChecked())

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
        decisions: [{ kind: 'alias_candidate', item_id: 'r1', action: 'choose', value: 'r1-b' }],
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
    expect(within(dialog).getByText('문턱 아래 · 확신도 없음 3건')).toBeInTheDocument()
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
    serve([
      item('r1', {
        id: 'group:x',
        members: ['m1', 'm2'],
        question: '「r1」 을(를) 무엇의 별칭으로 추가할까요?',
      }),
    ])
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

describe('확신도가 없는 추천(낡은 파생물, ADR 0041)', () => {
  it('추천은 있는데 확신도가 없으면 「추천 없음」 이 아니라 「확신도 없음」 으로 적는다', async () => {
    serve([
      item('r1'),
      item('r2', {
        kind: 'stale_lineage',
        kind_label: '낡은 파생물',
        choices: [
          { value: 'checked', label: '영향 없음 확인', detail: '', score: null },
          { value: 'rebuilt', label: '다시 만들었음', detail: '', score: null },
        ],
        recommended: 'checked',
        recommended_label: '영향 없음 확인',
        confidence: null,
        reason: '메시 M-12 의 개정 B → C',
        ignorable: false,
      }),
    ])
    await open()
    const row = rowOf('r2')
    const badge = within(row).getByText('확신도 없음')
    expect(badge).toHaveAttribute(
      'title',
      expect.stringContaining('「추천대로 모두」 에 들지 않습니다'),
    )
    expect(within(row).queryByText('추천 없음')).toBeNull()
    // 추천 단추는 그대로 강조되고, 점수 없는 후보에는 수가 안 붙는다.
    expect(within(row).getByRole('button', { name: /영향 없음 확인/ })).toHaveTextContent(
      /^1영향 없음 확인$/,
    )
  })
})

describe('제안된 관계(ADR 0042)', () => {
  it('확신도가 높으면 「확정」 을 추천하고, 없으면 추천 없음 — 근거 · 출처가 보인다', async () => {
    const proposal = (id: string, dst: string, extra: Record<string, unknown>) =>
      item(id, {
        kind: 'proposed_relation',
        kind_label: '제안된 관계',
        question: `「볼트」 —공급받음→ 「${dst}」 을(를) 이을까요?`,
        evidence: ['부품 → 공급사', `근거: ${dst} 공급 계약서`, 'AI(MCP) 「대화 #12」 · member'],
        choices: [
          { value: 'accept', label: '확정', detail: '「관계 추가」 와 같은 검사', score: null },
          { value: 'reject', label: '거절', detail: '같은 제안을 다시 만들지 않음', score: null },
        ],
        group: '',
        group_label: '',
        link: '/o/part/bolt',
        ...extra,
      })
    serve([
      item('r1'),
      proposal('p1', 'ACME', {
        recommended: 'accept',
        recommended_label: '확정',
        confidence: 0.95,
        reason: '제안한 쪽의 확신도 0.95 — 「확정」 을 앞에 둡니다.',
      }),
      proposal('p2', 'BETA', {
        recommended: null,
        recommended_label: null,
        confidence: null,
        reason: '확신도가 없는 제안이라 추천하지 않습니다 — 근거를 보고 고르세요.',
      }),
    ])
    await open()
    const high = screen.getByText('「볼트」 —공급받음→ 「ACME」 을(를) 이을까요?').closest('li')!
    expect(within(high).getByText('확신도 0.95')).toBeInTheDocument()
    expect(within(high).getByText(/근거: ACME 공급 계약서/)).toBeInTheDocument()
    expect(within(high).getByText(/AI\(MCP\) 「대화 #12」/)).toBeInTheDocument()
    expect(within(high).getByRole('button', { name: /확정/ })).toBeInTheDocument()
    expect(within(high).getByRole('button', { name: /거절/ })).toBeInTheDocument()
    const low = screen.getByText('「볼트」 —공급받음→ 「BETA」 을(를) 이을까요?').closest('li')!
    expect(within(low).getByText('추천 없음')).toBeInTheDocument()
  })
})

describe('종류마다 시간 상한(ADR 0073)', () => {
  it('시간 안에 다 못 읽은 종류는 「없다」 가 아니라고 말하고 그 종류만 볼 길을 준다', async () => {
    // 운영에서 별칭 후보 하나가 60초를 넘겨 검토함 전체가 504 였다 — 이제 서버가 종류마다
    // 시간을 두고, 못 읽은 종류는 `late` 로 온다(ADR 0073).
    const slow = [
      {
        ...kinds[0],
        total: 24000,
        matched: 1,
        scanned: 18,
        late: true,
        seconds: 8.4,
        notes: [
          '아직 미리 계산하지 않은 182건은 그 자리에서 계산하는 데 시간이 걸려 이번에 건너뛰었습니다',
        ],
      },
      {
        kind: 'broken_ref',
        label: '지워진 것을 가리키는 칸',
        description: '',
        link: '/quality#broken_refs',
        total: 0,
        matched: 0,
        scanned: 0,
        actionable: true,
        undo_note: '',
        notes: ['「지워진 것을 가리키는 칸」 은(는) 12초 안에 읽지 못해 이번에는 건너뛰었습니다'],
        late: true,
        seconds: 12.0,
      },
    ]
    serve([item('r1')], slow)
    await open()
    const notice = screen.getByRole('status', { name: '시간 안에 다 읽지 못한 종류' })
    expect(notice).toHaveTextContent('시간이 걸려 18건만 읽었습니다.')
    expect(notice).toHaveTextContent('시간 안에 읽지 못해 이번에는 건너뛰었습니다.')
    expect(notice).toHaveTextContent('없는 것이 아닙니다.')
    expect(notice).toHaveTextContent('12초 안에 읽지 못해')
    // 칩 — 건너뛴 종류는 수를 모른다(0 이 아니다).
    expect(screen.getByRole('button', { name: /지워진 것을 가리키는 칸.*\?/ })).toBeInTheDocument()
    expect(screen.getAllByLabelText('시간 안에 다 읽지 못함')).toHaveLength(2)

    const buttons = within(notice).getAllByRole('button', { name: '이 종류만 보기' })
    await userEvent.click(buttons[1])
    await waitFor(() =>
      expect(reviewApi.items).toHaveBeenLastCalledWith(
        expect.objectContaining({ kind: 'broken_ref' }),
        expect.anything(),
      ),
    )
  })

  it('항목이 없어도 늦은 종류가 있으면 「검토할 항목이 없습니다」 라고 하지 않는다', async () => {
    serve([], [{ ...kinds[0], total: 0, matched: 0, scanned: 0, late: true, notes: [] }], {
      notes: ['시간 안에 다 읽지 못한 종류가 있습니다(별칭 후보) — 없는 것이 아닙니다.'],
    })
    const { default: ReviewPage } = await import('@/modules/review/ReviewPage')
    render(
      <MemoryRouter initialEntries={['/review']}>
        <ReviewPage />
      </MemoryRouter>,
    )
    expect(await screen.findByText('시간 안에 다 읽지 못했습니다')).toBeInTheDocument()
    expect(screen.queryByText('검토할 항목이 없습니다')).not.toBeInTheDocument()
  })
})

describe('검토함이 많을 때(ADR 0075)', () => {
  it('「전체」 는 종류마다 따로 부르고 먼저 온 종류부터 그린다 — 느린 종류는 「읽는 중」', async () => {
    const never = new Promise(() => {})
    reviewApi.items.mockImplementation((filters: { kind?: string | null }) =>
      filters.kind === 'broken_ref'
        ? never
        : Promise.resolve(
            page(filters.kind === 'alias_candidate' ? [item('r1'), item('r2')] : [], [
              {
                ...kinds[0],
                kind: filters.kind,
                total: filters.kind === 'alias_candidate' ? 2 : 0,
                matched: filters.kind === 'alias_candidate' ? 2 : 0,
              },
            ]),
          ),
    )
    await open()
    expect(reviewApi.items).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'alias_candidate' }),
      expect.objectContaining({ limit: 20, offset: 0 }),
    )
    expect(reviewApi.items).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'broken_ref' }),
      expect.anything(),
    )
    // 느린 종류는 칩의 수 대신 「읽는 중」, 자리에도 「읽는 중…」 — 나머지는 이미 섰다.
    expect(screen.getByText('지워진 것을 가리키는 칸 — 읽는 중…')).toBeInTheDocument()
    expect(rowOf('r2')).toBeInTheDocument()
  })

  it('종류의 줄이 더 있으면 「이 종류 N건 모두 보기」 로 그 종류만 쪽을 넘겨 본다', async () => {
    serve([item('r1'), item('r2')], [{ ...kinds[0], total: 45, matched: 45, scanned: 45 }], {
      total: 45,
    })
    await open()
    await userEvent.click(screen.getByRole('button', { name: '이 종류 45건 모두 보기' }))
    await waitFor(() =>
      expect(reviewApi.items).toHaveBeenLastCalledWith(
        expect.objectContaining({ kind: 'alias_candidate' }),
        expect.objectContaining({ limit: 50 }),
      ),
    )
  })

  it('「추천 없음」 — 추천이 없는 줄만 거르고, 글자가 다른 이름일 수 있다고 말한다', async () => {
    await open()
    await userEvent.click(screen.getByRole('button', { name: '추천 없음' }))
    await waitFor(() =>
      expect(reviewApi.items).toHaveBeenLastCalledWith(
        expect.objectContaining({ recommendation: 'without' }),
        expect.anything(),
      ),
    )
    expect(screen.getByRole('note')).toHaveTextContent('「앤시스」 → Ansys')
  })

  it('상한 밖이 있으면 「전부 작업으로」 — 작업이 미리 보고, 적용은 그 작업의 적용으로', async () => {
    reviewApi.plan.mockResolvedValue(
      plan({ mode: 'recommended', threshold: 0.9, counts: { apply: 2, blocked: 0 }, unread: 5 }),
    )
    reviewApi.bulk.mockResolvedValue({ job_id: 'plan-job' })
    const whole = plan({
      mode: 'recommended',
      threshold: 0.9,
      decisions: [],
      counts: { apply: 7, blocked: 0 },
      as_job: true,
    })
    jobsApi.waitFor.mockImplementation((id: string) =>
      Promise.resolve(
        id === 'plan-job'
          ? { id, status: 'done', error: null, result: whole }
          : {
              id,
              status: 'done',
              error: null,
              result: {
                applied: true,
                batch_id: 'b7',
                counts: { done: 7, refused: 0 },
                undoable: 7,
              },
            },
      ),
    )
    jobsApi.apply.mockResolvedValue({ id: 'apply-job' })
    await open()
    await userEvent.click(screen.getByRole('button', { name: '추천대로 모두' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('note', { name: '보지 않은 것' })).toHaveTextContent('5건')
    await userEvent.click(within(dialog).getByRole('button', { name: '전부 작업으로' }))
    await waitFor(() =>
      expect(reviewApi.bulk).toHaveBeenCalledWith({
        mode: 'recommended',
        filters: expect.objectContaining({ kind: null }),
        threshold: 0.9,
        action: undefined,
      }),
    )
    const full = await screen.findByRole('dialog')
    expect(within(full).getByText('추천대로 모두 — 확신도 0.90 이상 — 전부')).toBeInTheDocument()
    jobsApi.download.mockResolvedValue(undefined)
    await userEvent.click(within(full).getByRole('button', { name: '결정 전부 다운로드(CSV)' }))
    expect(jobsApi.download).toHaveBeenCalledWith('plan-job', '검토함-전부-미리보기.csv')
    await userEvent.click(within(full).getByRole('button', { name: '7건 적용' }))
    await waitFor(() => expect(jobsApi.apply).toHaveBeenCalledWith('plan-job'))
    expect(await screen.findByText('7건을 처리했습니다.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '복원' })).toBeInTheDocument()
    expect(reviewApi.apply).not.toHaveBeenCalled()
  })

  it('자동 확인 — 켰나만이 아니라 지금 돌 수 있나와 그 까닭을 보이고, 시스템 관리자가 켠다', async () => {
    reviewApi.auto.mockResolvedValue([
      {
        kind: 'alias_candidate',
        label: '별칭 후보',
        enabled: false,
        threshold: 0.95,
        enabled_by_name: null,
        enabled_at: null,
        last_run_at: null,
        last_job_id: null,
        last_note: '',
        decided: 12,
        followed: 12,
        rate: 1,
        ready: false,
        why: '꺼져 있습니다.',
        min_decided: 30,
        min_rate: 0.95,
        max_per_night: 1000,
        hour: 6,
        can_edit: true,
      },
    ])
    reviewApi.setAuto.mockResolvedValue({})
    await open()
    await userEvent.click(screen.getByText(/자동 확인\(밤마다 추천대로\)/))
    expect(
      await screen.findByText(/확신도 0.95 이상에서 사람이 하나씩 정한 결정 12건 · 추천대로 100%/),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '켜기' }))
    await waitFor(() =>
      expect(reviewApi.setAuto).toHaveBeenCalledWith('alias_candidate', {
        enabled: true,
        threshold: 0.95,
      }),
    )
  })
})
