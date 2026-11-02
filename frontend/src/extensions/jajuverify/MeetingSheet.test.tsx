/**
 * 자주검증 회의 한 장이 지키는 것 — **문서처럼 그 자리에서 고치고, 여럿이 나눠 쓴다.**
 *
 * - 칸을 고치고 떠나면 그 칸 하나만 저장하고(「저장됨」 — 본 값을 `expected` 로 함께), 실패하면 글을
 *   지키고 「다시 시도」 를 낸다.
 * - 그사이 남이 같은 칸을 저장했으면 칸 아래 상자가 상대 글 · 내 글을 나란히 보이고 고르게 한다 —
 *   고르기 전에는 내 글이 사라지지 않는다.
 * - 자동 반영(판 번호가 바뀌면 한 장을 다시 받는다)은 **치고 있는 칸을 덮지 않는다** — 그 칸에는
 *   「다른 사람이 이 칸을 수정했습니다」 만 서고, 다른 칸은 새 값을 따른다.
 * - 남이 수정하는 칸에는 「김OO 수정 중」, 들어가면 안내(막지는 않는다).
 * - 묶음 — 머리줄 · 진행 · 접기, 줄을 골라 새 묶음 · 묶음에서 제외(배치를 통째로 보낸다).
 * - 사진은 **글 사이에 놓고 크기를 고른다** — 칸 글 안의 표시 줄(`![](photo:<id>?w=50)`)이 자리 · 폭.
 *   붙여넣기 · 끌어다 놓기는 커서 자리에, 폭 단추 · 위아래 · 크기 손잡이가 그 줄을 고친다. 표시가 없는
 *   사진은 끝에, 이 칸의 것이 아닌 표시는 그리지 않는다. 삭제는 표시 줄을 먼저 빼 저장한 뒤 뗀다.
 *   글이 함께 오면(엑셀 칸 복사) 글을 붙인다.
 * - 일정은 단계 목록(날짜 · 이름) — 목록 통째로 본 값과 함께 저장한다.
 * - 옮기기는 상대 동작 — 그사이 남이 지운 줄은 건너뛰고 말한다. 한 장은 본문 폭 전체를 쓴다.
 * - 줄 추가 · 위아래(묶음 안에서) · 삭제(무엇이 사라지는지 적은 확인) · 작성 완료.
 * - 확정된 회의는 글만 서고 「확정 · 날짜 · 이름」 과 「확정 해제」 가 선다. 작성 중이 남았는데 확정하면
 *   묻는다. 수정할 수 없는 사람에게는 글만 서고 그 까닭이 선다.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Group, Row, Sheet } from '@/extensions/jajuverify/api'
import { ApiError } from '@/shared/api/client'
import { SaveConflictError } from '@/shared/hooks/useAutoSave'

const jvApi = vi.hoisted(() => ({
  sheet: vi.fn(),
  pulse: vi.fn(),
  updateMeeting: vi.fn(),
  removeMeeting: vi.fn(),
  confirm: vi.fn(),
  unconfirm: vi.fn(),
  addRow: vi.fn(),
  updateRow: vi.fn(),
  setDone: vi.fn(),
  removeRow: vi.fn(),
  move: vi.fn(),
  addGroup: vi.fn(),
  updateGroup: vi.fn(),
  removeGroup: vi.fn(),
  touchPresence: vi.fn(),
  leavePresence: vi.fn(),
}))
vi.mock('@/extensions/jajuverify/api', async (original) => ({
  ...(await original<typeof import('@/extensions/jajuverify/api')>()),
  jvApi,
}))
const attachmentApi = vi.hoisted(() => ({
  list: vi.fn(),
  upload: vi.fn(),
  download: vi.fn(),
  remove: vi.fn(),
}))
vi.mock('@/modules/files/api', () => ({
  attachmentApi,
  attachmentPaths: {
    thumbnail: (id: string) => `/attachments/${id}/thumbnail`,
    content: (id: string) => `/attachments/${id}/content`,
  },
}))
// 첨부 상한은 서버에 묻는다 — 시험에서는 기본값으로(묻는 요청이 시험 밖으로 나가지 않게).
vi.mock('@/modules/files/limit', async (original) => ({
  ...(await original<typeof import('@/modules/files/limit')>()),
  useAttachmentLimit: () => 500 * 1024 * 1024,
}))
vi.mock('@/shared/hooks/useBlobUrl', () => ({
  useBlobUrl: (path: string | null) => ({ url: path ? `blob:${path}` : null, error: null }),
}))

function row(id: string, topic: string, extra: Partial<Row> = {}): Row {
  return {
    id,
    group_id: null,
    position: 1,
    topic,
    problem: '',
    action: '',
    remark: '',
    schedule: [],
    due_on: null,
    photos: { topic: [], problem: [], action: [], remark: [] },
    done: false,
    done_by_name: null,
    done_at: null,
    author_name: '홍길동',
    updated_by_name: null,
    created_at: '2026-10-30T00:00:00Z',
    updated_at: '2026-10-30T00:00:00Z',
    revision: 1,
    ...extra,
  }
}

function group(id: string, title: string, position = 1): Group {
  return {
    id,
    position,
    title,
    author_name: '홍길동',
    created_at: '2026-10-30T00:00:00Z',
    updated_at: '2026-10-30T00:00:00Z',
    revision: 1,
  }
}

const REFUSED = '이 회의의 표는 「품질팀」 의 구성원만 수정할 수 있습니다.'

function sheetOf(
  rows: Row[],
  options: {
    canWrite?: boolean
    canManage?: boolean
    groups?: Group[]
    revision?: number
    confirmed?: boolean
  } = {},
): Sheet {
  const canWrite = options.canWrite ?? true
  const canManage = options.canManage ?? canWrite
  return {
    meeting: {
      id: 'm1',
      kind: 'review',
      kind_label: '심사회',
      title: '1차 심사회',
      held_on: '2026-10-28',
      workspace_slug: 'hq',
      workspace_name: '품질팀',
      author_name: '홍길동',
      created_at: '2026-10-30T00:00:00Z',
      updated_at: '2026-10-30T00:00:00Z',
      revision: options.revision ?? 1,
      confirmed_at: options.confirmed ? '2026-10-31T01:00:00Z' : null,
      confirmed_by_name: options.confirmed ? '김부장' : null,
    },
    cells: [
      { key: 'topic', label: '항목', kind: 'text' },
      { key: 'problem', label: '문제점', kind: 'text' },
      { key: 'action', label: '개선대책', kind: 'text' },
      { key: 'schedule', label: '일정', kind: 'schedule' },
      { key: 'remark', label: '비고', kind: 'text' },
    ],
    groups: options.groups ?? [],
    rows,
    can_write: canWrite,
    write_refusal: canWrite
      ? null
      : options.confirmed
        ? '확정된 회의입니다 — 확정을 해제해야 수정할 수 있습니다.'
        : REFUSED,
    can_manage: canManage,
    manage_refusal: canManage
      ? null
      : '회의의 제목 · 일자 · 삭제 · 확정은 만든 사람 · 그 부서 관리자 · 시스템 관리자만 할 수 있습니다.',
    max_rows: 300,
    max_groups: 100,
  }
}

/** 사진 id — 칸 글의 표시(`![](photo:<id>?w=50)`)는 첨부 id(uuid)만 읽는다. */
const P1 = '11111111-1111-4111-8111-111111111111'
const P2 = '22222222-2222-4222-8222-222222222222'

const PHOTO = {
  id: P1,
  name: '캡처.png',
  is_image: true,
  width: 640,
  height: 480,
  size_bytes: 2048,
  thumbnail_path: `/attachments/${P1}/thumbnail`,
}

async function open(sheet: Sheet) {
  jvApi.sheet.mockResolvedValue(sheet)
  jvApi.pulse.mockResolvedValue({
    revision: sheet.meeting.revision,
    confirmed: false,
    presence: [],
  })
  const { default: ReviewSheet } = await import('@/extensions/jajuverify/ReviewSheet')
  render(
    <MemoryRouter initialEntries={['/ext/jajuverify/meetings/review/m1']}>
      <Routes>
        <Route path="ext/jajuverify/meetings/review/:meetingId" element={<ReviewSheet />} />
      </Routes>
    </MemoryRouter>,
  )
  await screen.findByRole('columnheader', { name: '문제점' })
  // 처음 그린 뒤 곧 한 번 묻는다(지금 수정하는 사람).
  await waitFor(() => expect(jvApi.pulse).toHaveBeenCalled())
}

/**
 * 덮어쓰기 보호의 상자. **역할로 찾으면 표 전체의 접근성 트리를 매번 다시 훑는다** — 회의 표는 칸이 많아 한
 * 번에 수백 ms 라, 시험을 여럿 함께 돌리면 기본 1초 안에 못 찾고 가끔 떨어졌다(2026-11-03, 밀기 전 점검 세 번에
 * 두 번). 기다리는 시간만 늘린다 — 찾는 것은 그대로다.
 */
const conflictBox = () => screen.findByRole('alert', {}, { timeout: 5000 })

/** 탭이 다시 보였다 — 판 번호를 곧 묻는다(시계를 기다리지 않는다). */
async function poke() {
  await act(async () => {
    document.dispatchEvent(new Event('visibilitychange'))
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  jvApi.touchPresence.mockResolvedValue(undefined)
  jvApi.leavePresence.mockResolvedValue(undefined)
})

describe('칸 — 그 자리에서 고치고 자동 저장', () => {
  it('고치고 떠나면 그 칸 하나만 본 값과 함께 저장하고 「저장됨」 을 보인다 — 줄바꿈 그대로', async () => {
    jvApi.updateRow.mockResolvedValue(row('r1', '힌지', { problem: '열림 소음\n둘째 줄' }))
    await open(sheetOf([row('r1', '힌지')]))
    const cell = screen.getByLabelText('1번 줄 문제점')
    await userEvent.type(cell, '열림 소음{Enter}둘째 줄')
    await userEvent.tab()
    await waitFor(() =>
      expect(jvApi.updateRow).toHaveBeenCalledWith('r1', {
        problem: '열림 소음\n둘째 줄',
        expected: { problem: '' },
      }),
    )
    expect(await screen.findByText('저장됨')).toBeInTheDocument()
    // 다른 칸은 안 보낸다(부분 수정) — 칸 하나 = 요청 하나.
    expect(jvApi.updateRow).toHaveBeenCalledTimes(1)
  })

  it('실패하면 친 글을 지키고 까닭과 「다시 시도」 를 낸다', async () => {
    jvApi.updateRow
      .mockRejectedValueOnce(new Error('연결이 끊겼습니다'))
      .mockResolvedValueOnce(row('r1', '힌지', { remark: '확인 필요' }))
    await open(sheetOf([row('r1', '힌지')]))
    const cell = screen.getByLabelText('1번 줄 비고')
    await userEvent.type(cell, '확인 필요')
    await userEvent.tab()
    expect(await screen.findByText(/저장 실패 — 연결이 끊겼습니다/)).toBeInTheDocument()
    expect(cell).toHaveValue('확인 필요')
    await userEvent.click(screen.getByRole('button', { name: '다시 시도' }))
    await waitFor(() => expect(jvApi.updateRow).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('저장됨')).toBeInTheDocument()
  })

  it('일정은 단계를 더하고 날짜 · 이름을 적으면 목록 통째로 본 값과 함께 저장한다', async () => {
    jvApi.updateRow.mockResolvedValue(row('r1', '힌지'))
    await open(
      sheetOf([row('r1', '힌지', { schedule: [{ date: '2026-11-30', label: '재시험' }] })]),
    )
    expect(screen.getByLabelText('1번 줄 일정 1번째 날짜')).toHaveValue('2026-11-30')
    await userEvent.click(screen.getByRole('button', { name: '1번 줄 일정 단계 추가' }))
    fireEvent.change(screen.getByLabelText('1번 줄 일정 2번째 날짜'), {
      target: { value: '2026-11-15' },
    })
    await userEvent.type(screen.getByLabelText('1번 줄 일정 2번째 단계'), '설계 변경')
    await userEvent.tab()
    await waitFor(() =>
      expect(jvApi.updateRow).toHaveBeenLastCalledWith('r1', {
        // 날짜 차례로 맞춰 보낸다 — 서버도 그 차례로 저장한다.
        schedule: [
          { date: '2026-11-15', label: '설계 변경' },
          { date: '2026-11-30', label: '재시험' },
        ],
        expected: { schedule: [{ date: '2026-11-30', label: '재시험' }] },
      }),
    )
  })

  it('일정 단계를 빼면 그 단계 없이 저장한다', async () => {
    jvApi.updateRow.mockResolvedValue(row('r1', '힌지'))
    await open(sheetOf([row('r1', '힌지', { schedule: [{ date: '2026-11-30', label: '' }] })]))
    await userEvent.click(screen.getByRole('button', { name: '1번 줄 일정 1번째 단계 삭제' }))
    await userEvent.click(screen.getByRole('button', { name: '1번 줄 일정 단계 추가' }))
    await userEvent.click(screen.getByRole('button', { name: '1번 줄 일정 1번째 단계 삭제' }))
    await userEvent.tab()
    await waitFor(() =>
      expect(jvApi.updateRow).toHaveBeenCalledWith('r1', {
        schedule: [],
        expected: { schedule: [{ date: '2026-11-30', label: '' }] },
      }),
    )
  })
})

describe('여럿이 함께 — 덮어쓰기 보호 · 자동 반영 · 수정 중', () => {
  it('같은 칸을 남이 먼저 저장했으면 상자가 두 글을 나란히 보이고, 고르기 전에는 내 글이 그대로다', async () => {
    jvApi.updateRow
      .mockRejectedValueOnce(
        new SaveConflictError('김철수 님이 이 칸을 먼저 수정했습니다', '남의 글', '김철수', null),
      )
      .mockResolvedValueOnce(row('r1', '힌지', { problem: '내 수정' }))
    await open(sheetOf([row('r1', '힌지')]))
    const cell = screen.getByLabelText('1번 줄 문제점')
    await userEvent.type(cell, '내 수정')
    await userEvent.tab()
    const box = await conflictBox()
    expect(within(box).getByText(/김철수 님이 먼저 수정했습니다/)).toBeInTheDocument()
    expect(within(box).getByText('남의 글')).toBeInTheDocument()
    expect(within(box).getByText('내 수정')).toBeInTheDocument()
    expect(cell).toHaveValue('내 수정')
    // 고르기 전에는 다시 저장하지 않는다 — 같은 거절을 되풀이할 뿐이다.
    await userEvent.type(cell, '!')
    await userEvent.tab()
    expect(jvApi.updateRow).toHaveBeenCalledTimes(1)
    // 「내 글로 덮어쓰기」 — 본 값을 서버의 지금 값으로 다시 보낸다.
    await userEvent.click(within(box).getByRole('button', { name: '내 글로 덮어쓰기' }))
    await waitFor(() =>
      expect(jvApi.updateRow).toHaveBeenLastCalledWith('r1', {
        problem: '내 수정!',
        expected: { problem: '남의 글' },
      }),
    )
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  })

  it('「상대 글 사용」 은 내 글을 버리고 서버의 글을 들인다', async () => {
    jvApi.updateRow.mockRejectedValueOnce(
      new SaveConflictError('먼저 수정했습니다', '남의 글', '김철수', '2026-10-31T01:32:00Z'),
    )
    await open(sheetOf([row('r1', '힌지')]))
    const cell = screen.getByLabelText('1번 줄 비고')
    await userEvent.type(cell, '내 글')
    await userEvent.tab()
    const box = await conflictBox()
    await userEvent.click(within(box).getByRole('button', { name: '상대 글 사용' }))
    expect(cell).toHaveValue('남의 글')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(jvApi.updateRow).toHaveBeenCalledTimes(1)
  })

  it('자동 반영은 치고 있는 칸을 덮지 않고 「다른 사람이 수정했습니다」 만 세우며, 다른 칸은 따른다', async () => {
    await open(sheetOf([row('r1', '힌지')]))
    const problem = screen.getByLabelText('1번 줄 문제점')
    await userEvent.type(problem, '내가 치는 중')
    // 그사이 다른 사람이 같은 칸과 비고를 저장했다 — 판 번호가 바뀌었다.
    jvApi.pulse.mockResolvedValue({ revision: 2, confirmed: false, presence: [] })
    jvApi.sheet.mockResolvedValue(
      sheetOf([row('r1', '힌지', { problem: '남의 글', remark: '남의 비고' })], { revision: 2 }),
    )
    await poke()
    await waitFor(() => expect(screen.getByLabelText('1번 줄 비고')).toHaveValue('남의 비고'))
    expect(problem).toHaveValue('내가 치는 중')
    expect(screen.getByText(/다른 사람이 이 칸을 수정했습니다/)).toBeInTheDocument()
    // 판 번호가 그대로면 다시 받지 않는다.
    const loads = jvApi.sheet.mock.calls.length
    await poke()
    await waitFor(() => expect(jvApi.pulse).toHaveBeenCalledTimes(3))
    expect(jvApi.sheet).toHaveBeenCalledTimes(loads)
  })

  it('남이 수정하는 칸에 「김철수 수정 중」, 들어가면 안내하고 내 자리를 적는다', async () => {
    await open(sheetOf([row('r1', '힌지')]))
    jvApi.pulse.mockResolvedValue({
      revision: 1,
      confirmed: false,
      presence: [{ user_id: 'u2', name: '김철수', row_id: 'r1', group_id: null, field: 'problem' }],
    })
    await poke()
    expect(await screen.findByText('김철수 수정 중')).toBeInTheDocument()
    await userEvent.click(screen.getByLabelText('1번 줄 문제점'))
    expect(screen.getByText('김철수 님이 이 칸을 수정하는 중입니다.')).toBeInTheDocument()
    expect(jvApi.touchPresence).toHaveBeenCalledWith('m1', { row_id: 'r1', field: 'problem' })
    // 막지는 않는다 — 그대로 입력된다.
    await userEvent.type(screen.getByLabelText('1번 줄 문제점'), '같이 씀')
    expect(screen.getByLabelText('1번 줄 문제점')).toHaveValue('같이 씀')
  })

  it('서버의 409(덮어쓰기 충돌)는 지금 값 · 누가 · 언제를 든 충돌로 읽힌다', async () => {
    const { conflictOf } = await import('@/extensions/jajuverify/api')
    const raised = new ApiError(409, {
      error: {
        code: 'APP-JAJUVERIFY-0010',
        message: '김철수 님이 이 칸을 먼저 수정했습니다',
        details: {
          row_id: 'r1',
          field: 'problem',
          current: '남의 글',
          updated_by: '김철수',
          updated_at: '2026-10-31T01:32:00Z',
        },
      },
    })
    const read = conflictOf(raised)
    expect(read).toBeInstanceOf(SaveConflictError)
    expect(read).toMatchObject({ current: '남의 글', by: '김철수', at: '2026-10-31T01:32:00Z' })
    // 다른 409(확정된 회의 · 배치가 맞지 않음)는 그대로 둔다.
    const other = new ApiError(409, {
      error: { code: 'APP-JAJUVERIFY-0009', message: '확정된 회의입니다', details: {} },
    })
    expect(conflictOf(other)).toBe(other)
  })
})

describe('묶음 — 머리줄 · 묶기 · 옮기기', () => {
  const design = group('g1', '설계 부문')
  const rows = [
    row('r1', '맨 위 줄'),
    row('r2', '하우징', { group_id: 'g1', done: true }),
    row('r3', '힌지', { group_id: 'g1' }),
  ]

  it('묶음 없는 줄이 맨 위, 그 아래 머리줄과 그 줄 — 머리줄에 진행이 서고 접으면 줄이 숨는다', async () => {
    await open(sheetOf(rows, { groups: [design] }))
    expect(screen.getByLabelText('1번 묶음 이름')).toHaveValue('설계 부문')
    expect(screen.getByText('줄 2 · 작성 완료 1')).toBeInTheDocument()
    // 번호는 표 전체로 이어 매긴다.
    expect(screen.getByLabelText('3번 줄 항목')).toHaveValue('힌지')
    await userEvent.click(screen.getByRole('button', { name: '1번 묶음 접기' }))
    expect(screen.getByLabelText('3번 줄 항목').closest('tr')).toHaveClass('hidden')
    // 인쇄에는 편다.
    expect(screen.getByLabelText('3번 줄 항목').closest('tr')).toHaveClass('print:table-row')
    // 맨 위 줄(묶음 없음)은 그대로 보인다.
    expect(screen.getByLabelText('1번 줄 항목').closest('tr')).not.toHaveClass('hidden')
  })

  it('줄을 골라 「새 묶음」 — 이름을 묻고, 고른 줄을 표의 차례대로 보낸다', async () => {
    jvApi.addGroup.mockResolvedValue(sheetOf(rows, { groups: [design], revision: 2 }))
    await open(sheetOf([row('r1', 'A'), row('r2', 'B'), row('r3', 'C')]))
    await userEvent.click(screen.getByLabelText('3번 줄 선택'))
    await userEvent.click(screen.getByLabelText('1번 줄 선택'))
    const bar = screen.getByRole('toolbar', { name: '선택한 줄' })
    expect(within(bar).getByText('2줄 선택됨')).toBeInTheDocument()
    await userEvent.click(within(bar).getByRole('button', { name: /새 묶음/ }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('묶음 이름'), '설계 부문')
    await userEvent.click(within(dialog).getByRole('button', { name: '생성' }))
    await waitFor(() =>
      expect(jvApi.addGroup).toHaveBeenCalledWith('m1', {
        title: '설계 부문',
        row_ids: ['r1', 'r3'],
      }),
    )
    await waitFor(() => expect(screen.queryByRole('toolbar', { name: '선택한 줄' })).toBeNull())
  })

  it('「묶음에서 제외」 · 줄의 위 · 아래는 상대 동작으로 보낸다(위 · 아래는 묶음 안에서만)', async () => {
    jvApi.move.mockResolvedValue({
      ...sheetOf(rows, { groups: [design], revision: 2 }),
      skipped: [],
      anchor_missing: false,
      changed: true,
      group_id: null,
    })
    await open(sheetOf(rows, { groups: [design] }))
    // 묶음의 첫 줄은 위로 못 간다 — 이웃 묶음으로 넘기지 않는다.
    expect(screen.getByRole('button', { name: '2번 줄 위로' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: '2번 줄 아래로' }))
    await waitFor(() =>
      expect(jvApi.move).toHaveBeenCalledWith('m1', { rows: ['r2'], step: 'down' }),
    )
    await userEvent.click(screen.getByLabelText('3번 줄 선택'))
    await userEvent.click(screen.getByRole('button', { name: '묶음에서 제외' }))
    await waitFor(() =>
      expect(jvApi.move).toHaveBeenLastCalledWith('m1', {
        rows: ['r3'],
        to: { group: null, at: 'end' },
      }),
    )
  })

  it('묶음 삭제는 「묶음만 해제」 와 「줄까지 삭제」 중에서 고른다', async () => {
    jvApi.removeGroup.mockResolvedValue(sheetOf(rows, { revision: 2 }))
    await open(sheetOf(rows, { groups: [design] }))
    await userEvent.click(screen.getByRole('button', { name: '1번 묶음 삭제' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/「설계 부문」 묶음을 삭제합니다/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: /줄까지 삭제 — 2개/ })).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: /묶음만 해제/ }))
    await waitFor(() => expect(jvApi.removeGroup).toHaveBeenCalledWith('g1', false))
  })
})

describe('칸의 사진 — 글 사이에 놓고 크기를 고른다', () => {
  const uploaded = (id: string) => ({ id, is_image: true, original_name: 'image.png' })
  const placed = (text: string, extra: Partial<Row> = {}) =>
    row('r1', '힌지', {
      problem: text,
      photos: { topic: [], problem: [PHOTO], action: [], remark: [] },
      ...extra,
    })

  it('붙여 넣은 캡처는 그 칸의 사진으로 올라가고, 커서 자리에 표시 줄이 들어가 저장된다', async () => {
    attachmentApi.upload.mockResolvedValue(uploaded(P2))
    jvApi.updateRow.mockResolvedValue(row('r1', '힌지'))
    await open(sheetOf([row('r1', '힌지', { problem: '첫 줄\n둘째 줄' })]))
    const cell = screen.getByLabelText('1번 줄 문제점') as HTMLTextAreaElement
    cell.focus()
    cell.setSelectionRange(3, 3) // 「첫 줄」 끝
    const shot = new File(['x'], 'image.png', { type: 'image/png' })
    fireEvent.paste(cell, { clipboardData: { files: [shot], getData: () => '' } })
    await waitFor(() => expect(attachmentApi.upload).toHaveBeenCalledTimes(1))
    expect(attachmentApi.upload.mock.calls[0][0]).toMatchObject({
      ownerTable: 'jv_rows',
      ownerId: 'r1',
      ownerField: 'problem',
      file: shot,
    })
    await waitFor(() =>
      expect(jvApi.updateRow).toHaveBeenCalledWith('r1', {
        problem: `첫 줄\n![](photo:${P2}?w=50)\n둘째 줄`,
        expected: { problem: '첫 줄\n둘째 줄' },
      }),
    )
    // 올린 뒤 한 장을 다시 읽는다 — 미리보기가 그 자리에 선다.
    await waitFor(() => expect(jvApi.sheet).toHaveBeenCalledTimes(2))
  })

  it('글이 함께 오면(엑셀 칸 복사) 사진이 아니라 글을 붙인다', async () => {
    await open(sheetOf([row('r1', '힌지')]))
    fireEvent.paste(screen.getByLabelText('1번 줄 문제점'), {
      clipboardData: {
        files: [new File(['x'], 'image.png', { type: 'image/png' })],
        getData: (kind: string) => (kind === 'text/plain' ? '엑셀의 글' : ''),
      },
    })
    expect(attachmentApi.upload).not.toHaveBeenCalled()
  })

  it('칸으로 끌어다 놓은 파일은 그 칸에 올라간다', async () => {
    attachmentApi.upload.mockResolvedValue(uploaded(P2))
    jvApi.updateRow.mockResolvedValue(row('r1', '힌지'))
    await open(sheetOf([row('r1', '힌지')]))
    const cell = screen.getByLabelText('1번 줄 비고')
    const file = new File(['y'], '현장.jpg', { type: 'image/jpeg' })
    fireEvent.dragOver(cell, { dataTransfer: { types: ['Files'], files: [file] } })
    fireEvent.drop(cell, { dataTransfer: { types: ['Files'], files: [file] } })
    await waitFor(() => expect(attachmentApi.upload).toHaveBeenCalledTimes(1))
    expect(attachmentApi.upload.mock.calls[0][0]).toMatchObject({
      ownerTable: 'jv_rows',
      ownerField: 'remark',
      file,
    })
    // 커서가 없으면 끝에.
    await waitFor(() =>
      expect(jvApi.updateRow).toHaveBeenCalledWith('r1', {
        remark: `![](photo:${P2}?w=50)`,
        expected: { remark: '' },
      }),
    )
  })

  it('표시 줄의 자리에 글 조각 사이로 서고, 폭 단추 · 위아래 · 크기 손잡이가 표시 줄을 고친다', async () => {
    jvApi.updateRow.mockImplementation(async (_id: string, body: { problem: string }) =>
      placed(body.problem),
    )
    await open(sheetOf([placed(`앞 글\n![](photo:${P1}?w=50)\n뒤 글`)]))
    expect(screen.getByLabelText('1번 줄 문제점')).toHaveValue('앞 글')
    expect(screen.getByLabelText('1번 줄 문제점 — 2번째 글 조각')).toHaveValue('뒤 글')
    const frame = screen.getByRole('button', { name: '캡처.png 크게 보기' }).parentElement!
    expect(frame.style.width).toBe('50%')

    await userEvent.click(screen.getByRole('button', { name: '캡처.png 폭 75%' }))
    await waitFor(() =>
      expect(jvApi.updateRow).toHaveBeenLastCalledWith('r1', {
        problem: `앞 글\n![](photo:${P1}?w=75)\n뒤 글`,
        expected: { problem: `앞 글\n![](photo:${P1}?w=50)\n뒤 글` },
      }),
    )
    await userEvent.click(screen.getByRole('button', { name: '캡처.png 아래로' }))
    await waitFor(() =>
      expect(jvApi.updateRow).toHaveBeenLastCalledWith('r1', {
        problem: `앞 글\n뒤 글\n![](photo:${P1}?w=75)`,
        expected: { problem: `앞 글\n![](photo:${P1}?w=75)\n뒤 글` },
      }),
    )
    // 크기 손잡이는 키보드로도 — 10% 씩.
    const handle = screen.getByRole('slider', { name: '캡처.png 크기' })
    handle.focus()
    await userEvent.keyboard('{ArrowLeft}')
    await waitFor(() =>
      expect(jvApi.updateRow).toHaveBeenLastCalledWith('r1', {
        problem: `앞 글\n뒤 글\n![](photo:${P1}?w=70)`,
        expected: { problem: `앞 글\n뒤 글\n![](photo:${P1}?w=75)` },
      }),
    )
  })

  it('표시가 없는 사진은 끝에 서고, 이 칸의 것이 아닌 표시는 그리지 않는다', async () => {
    await open(sheetOf([placed(`글\n![](photo:${P2}?w=30)`)]))
    // P2 는 이 칸의 첨부가 아니다 — 그리지 않는다. P1 은 표시가 없어 끝에 기본 폭으로.
    expect(screen.getAllByRole('img')).toHaveLength(1)
    expect(
      screen.getByRole('button', { name: '캡처.png 크게 보기' }).parentElement!.style.width,
    ).toBe('50%')
  })

  it('사진은 누르면 크게 보고, 삭제는 표시 줄을 먼저 빼 저장한 뒤 첨부를 뗀다', async () => {
    attachmentApi.remove.mockResolvedValue(undefined)
    jvApi.updateRow.mockResolvedValue(placed('앞 글'))
    await open(sheetOf([placed(`앞 글\n![](photo:${P1}?w=50)`)]))
    const thumb = screen.getByRole('img', { name: '캡처.png' })
    expect(thumb.getAttribute('src')).toBe(`blob:/attachments/${P1}/thumbnail`)
    await userEvent.click(screen.getByRole('button', { name: '캡처.png 크게 보기' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('img').getAttribute('src')).toBe(
      `blob:/attachments/${P1}/content`,
    )
    await userEvent.keyboard('{Escape}')
    await userEvent.click(screen.getByRole('button', { name: '캡처.png 삭제' }))
    await waitFor(() => expect(attachmentApi.remove).toHaveBeenCalledWith(P1))
    expect(jvApi.updateRow).toHaveBeenCalledWith('r1', {
      problem: '앞 글',
      expected: { problem: `앞 글\n![](photo:${P1}?w=50)` },
    })
    expect(jvApi.updateRow.mock.invocationCallOrder[0]).toBeLessThan(
      attachmentApi.remove.mock.invocationCallOrder[0],
    )
  })

  it('표시를 뺀 저장이 거절되면(충돌) 사진을 떼지 않는다', async () => {
    jvApi.updateRow.mockRejectedValueOnce(
      new SaveConflictError('먼저 수정했습니다', '남의 글', '김철수', null),
    )
    await open(sheetOf([placed(`앞 글\n![](photo:${P1}?w=50)`)]))
    await userEvent.click(screen.getByRole('button', { name: '캡처.png 삭제' }))
    expect(await conflictBox()).toBeInTheDocument()
    expect(attachmentApi.remove).not.toHaveBeenCalled()
  })
})

describe('줄 — 추가 · 차례 · 삭제 · 작성 완료', () => {
  it('줄 추가는 묶음 없음의 맨 아래, 줄의 「+」 는 그 줄 아래에 — 새 줄의 항목 칸으로 들어간다', async () => {
    jvApi.addRow.mockResolvedValue(row('r2', '', { revision: 2 }))
    await open(sheetOf([row('r1', '힌지')]))
    await userEvent.click(screen.getByRole('button', { name: '줄 추가' }))
    await waitFor(() => expect(jvApi.addRow).toHaveBeenCalledWith('m1', { group_id: null }))
    await userEvent.click(screen.getByRole('button', { name: '1번 줄 아래에 줄 추가' }))
    await waitFor(() => expect(jvApi.addRow).toHaveBeenLastCalledWith('m1', { after_id: 'r1' }))
  })

  it('아래로 — 「이 줄을 한 칸 아래로」 를 보내고 받은 한 장으로 다시 그린다', async () => {
    const rows = [row('r1', '첫째'), row('r2', '둘째'), row('r3', '셋째')]
    jvApi.move.mockResolvedValue({
      ...sheetOf([rows[1], rows[0], rows[2]], { revision: 2 }),
      skipped: [],
      anchor_missing: false,
      changed: true,
      group_id: null,
    })
    await open(sheetOf(rows))
    expect(screen.getByRole('button', { name: '1번 줄 위로' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: '1번 줄 아래로' }))
    await waitFor(() =>
      expect(jvApi.move).toHaveBeenCalledWith('m1', { rows: ['r1'], step: 'down' }),
    )
    await waitFor(() => expect(screen.getByLabelText('1번 줄 항목')).toHaveValue('둘째'))
  })

  it('그사이 남이 지운 줄은 건너뛰고 그 말을 표시한다 — 거절하지 않는다', async () => {
    const rows = [row('r1', '첫째'), row('r2', '둘째')]
    jvApi.move.mockResolvedValue({
      ...sheetOf([rows[1], rows[0]], { revision: 3 }),
      skipped: ['r9'],
      anchor_missing: false,
      changed: true,
      group_id: null,
    })
    await open(sheetOf(rows))
    await userEvent.click(screen.getByRole('button', { name: '1번 줄 아래로' }))
    expect(await screen.findByText(/삭제한 줄 · 묶음 1개는 건너뛰었습니다/)).toBeInTheDocument()
  })

  it('한 장은 본문 폭 전체를 쓰고 열은 다섯이다(일정이 따로 선다)', async () => {
    await open(sheetOf([row('r1', '힌지')]))
    const table = screen.getByRole('table')
    expect(table.closest('.w-full')).not.toBeNull()
    expect(document.querySelector('[class*="max-w-"]')).toBeNull()
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((one) => one.textContent),
    ).toEqual(['번호', '항목', '문제점', '개선대책', '일정', '비고'])
  })

  it('작성 완료를 켜고 끈다 — 그 줄만 응답으로 바꾼다', async () => {
    jvApi.setDone.mockResolvedValue(
      row('r1', '힌지', { done: true, done_by_name: '홍길동', revision: 2 }),
    )
    await open(sheetOf([row('r1', '힌지')]))
    const toggle = screen.getByRole('button', { name: '1번 줄 작성 완료' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(toggle)
    await waitFor(() => expect(jvApi.setDone).toHaveBeenCalledWith('r1', true))
    await waitFor(() => expect(toggle).toHaveAttribute('aria-pressed', 'true'))
    expect(screen.getByText('줄 1 · 작성 완료 1')).toBeInTheDocument()
    // 내 쓰기 때문에 다시 읽지 않는다.
    expect(jvApi.sheet).toHaveBeenCalledTimes(1)
  })

  it('삭제는 무엇이 사라지는지 적은 확인을 거친다', async () => {
    jvApi.removeRow.mockResolvedValue(undefined)
    await open(
      sheetOf([
        row('r1', '도어 힌지', { photos: { topic: [PHOTO], problem: [], action: [], remark: [] } }),
      ]),
    )
    await userEvent.click(screen.getByRole('button', { name: '1번 줄 삭제' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/「도어 힌지」 줄을 삭제합니다/)).toBeInTheDocument()
    expect(within(dialog).getByText(/사진 1장/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: '삭제' }))
    await waitFor(() => expect(jvApi.removeRow).toHaveBeenCalledWith('r1'))
  })
})

describe('확정', () => {
  it('확정된 회의는 글만 서고 「확정 · 날짜 · 이름」 과 「확정 해제」 가 선다', async () => {
    jvApi.unconfirm.mockResolvedValue(sheetOf([row('r1', '힌지')], { revision: 3 }))
    await open(
      sheetOf([row('r1', '힌지', { done: true })], {
        canWrite: false,
        canManage: true,
        confirmed: true,
        revision: 2,
      }),
    )
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.getByText('확정')).toBeInTheDocument()
    expect(screen.getByText(/김부장/)).toBeInTheDocument()
    expect(screen.getByText(/확정된 회의입니다/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /회의 삭제/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /줄 추가/ })).toBeNull()
    expect(screen.queryByLabelText('1번 줄 선택')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /확정 해제/ }))
    await waitFor(() => expect(jvApi.unconfirm).toHaveBeenCalledWith('m1'))
    expect(await screen.findByLabelText('1번 줄 항목')).toHaveValue('힌지')
  })

  it('작성 중인 줄이 남았는데 확정하면 묻는다', async () => {
    jvApi.confirm.mockResolvedValue(
      sheetOf([row('r1', 'A'), row('r2', 'B')], {
        canWrite: false,
        canManage: true,
        confirmed: true,
        revision: 2,
      }),
    )
    await open(sheetOf([row('r1', 'A'), row('r2', 'B', { done: true }), row('r3', 'C')]))
    await userEvent.click(screen.getByRole('button', { name: /^확정$/ }))
    const dialog = await screen.findByRole('dialog')
    expect(
      within(dialog).getByText(/작성 중 2개가 남았습니다 — 그래도 확정할까요/),
    ).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: '확정' }))
    await waitFor(() => expect(jvApi.confirm).toHaveBeenCalledWith('m1'))
    await waitFor(() => expect(screen.queryByRole('textbox')).toBeNull())
  })
})

describe('수정할 수 없는 사람', () => {
  it('글과 사진만 서고, 입력 칸 · 단추 대신 까닭이 선다', async () => {
    await open(
      sheetOf(
        [row('r1', '힌지', { photos: { topic: [PHOTO], problem: [], action: [], remark: [] } })],
        { canWrite: false },
      ),
    )
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.getByText('힌지')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: '캡처.png' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /줄 추가/ })).toBeNull()
    expect(screen.queryByRole('button', { name: '캡처.png 삭제' })).toBeNull()
    expect(screen.queryByRole('button', { name: /회의 삭제/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /^확정$/ })).toBeNull()
    expect(screen.getByText(REFUSED)).toBeInTheDocument()
    // 붙여 넣어도 안 올린다.
    fireEvent.paste(screen.getByText('힌지'), {
      clipboardData: {
        files: [new File(['x'], 'a.png', { type: 'image/png' })],
        getData: () => '',
      },
    })
    expect(attachmentApi.upload).not.toHaveBeenCalled()
  })
})
