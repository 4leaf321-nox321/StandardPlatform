/**
 * 자주검증(ADR 0044) — 서버와 주고받는 것. 백엔드 `app/extensions/jajuverify` 의 짝.
 *
 * 회의 · 묶음 · 줄은 확장의 전용 표이고, 칸의 사진은 **공통 첨부**(`owner_table=jv_rows`,
 * `owner_field` = 칸)로 붙는다 — 올리기 · 떼기는 첨부 API(`modules/files`)가 한다.
 *
 * 여럿이 함께 쓴다 — 칸을 저장할 때 「고치기 시작할 때 본 값」(`expected`)을 함께 보내고, 그사이 남이
 * 같은 칸을 고쳤으면 서버가 409 로 거절한다(`conflictOf` 가 그것을 `SaveConflictError` 로 바꾼다).
 * 바뀌었나는 판 번호로 묻는다(`pulse`).
 */

import { ApiError, api } from '@/shared/api/client'
import type { Page } from '@/shared/api/paging'
import { SaveConflictError } from '@/shared/hooks/useAutoSave'
import type { CellPhoto } from '@/modules/files/PhotoCell'

export type MeetingKind = 'review' | 'postmortem' | 'briefing'

/** 회의 종류 — 저장되는 키 → 화면의 이름. 차례가 메뉴의 차례다. */
export const KINDS: Record<MeetingKind, string> = {
  review: '심사회',
  postmortem: '포스트모텀',
  briefing: '설명회',
}

/** 글 칸 — 저장 칸이 곧 사진의 자리(`owner_field`)다. */
export type CellKey = 'topic' | 'problem' | 'action' | 'remark'
export const CELL_KEYS: CellKey[] = ['topic', 'problem', 'action', 'remark']

/** 표의 열 — 글(글 + 사진) · 일정(단계 목록). 열의 정의는 서버(`cells.COLUMNS`)가 준다. */
export type ColumnKey = CellKey | 'schedule'
export type ColumnKind = 'text' | 'schedule'

/** 지금 수정하는 칸 — 줄의 글 칸 넷과 일정, 묶음 · 회의의 이름. */
export type PresenceField = ColumnKey | 'title'

/** 사진이 붙는 첨부의 자리. */
export const OWNER_TABLE = 'jv_rows'

export interface Meeting {
  id: string
  kind: MeetingKind
  kind_label: string
  title: string
  held_on: string | null
  workspace_slug: string | null
  workspace_name: string | null
  author_name: string | null
  created_at: string
  updated_at: string
  /** 판 번호 — 이 회의의 쓰기마다 하나씩 오른다. */
  revision: number
  /** 회의 확정 — 채워져 있으면 확정을 해제할 때까지 아무도 수정하지 못한다. */
  confirmed_at: string | null
  confirmed_by_name: string | null
}

export interface MeetingBrief extends Meeting {
  row_count: number
  done_count: number
}

export interface Photo extends CellPhoto {
  /** 미리보기 경로(`/api` 아래) — 화면은 `attachmentPaths.thumbnail` 로 같은 것을 만든다. */
  thumbnail_path: string | null
}

/** 일정의 한 단계 — 날짜와 짧은 이름(비워도 된다). */
export interface ScheduleItem {
  date: string
  label: string
}

export interface Row {
  id: string
  /** 어느 묶음의 줄인가 — null 이면 묶음 없음(표 맨 위). */
  group_id: string | null
  position: number
  topic: string
  problem: string
  action: string
  remark: string
  /** 일정 — 날짜 차례로. */
  schedule: ScheduleItem[]
  /** 완료 예정일 — 일정의 가장 늦은 날(파생값). */
  due_on: string | null
  /** 칸마다 사진(정본) — 자리 · 폭은 칸 글의 표시 줄(`cellText`). */
  photos: Record<CellKey, Photo[]>
  /** 작성 완료. */
  done: boolean
  done_by_name: string | null
  done_at: string | null
  author_name: string | null
  updated_by_name: string | null
  created_at: string
  updated_at: string
  revision: number
}

export interface Group {
  id: string
  position: number
  title: string
  author_name: string | null
  created_at: string
  updated_at: string
  revision: number
}

export interface Sheet {
  meeting: Meeting
  /** 표의 열 — 차례대로(이름 · 종류는 서버가 준다). */
  cells: { key: ColumnKey; label: string; kind: ColumnKind }[]
  /** 묶음 — 차례대로. */
  groups: Group[]
  /** 줄 — 묶음 없는 것부터, 묶음의 차례대로(서버가 정렬했다). */
  rows: Row[]
  /** 줄 · 묶음 · 사진 · 작성 완료를 수정할 수 있나 — 그 부서의 구성원 · 확정 전(서버가 판정했다). */
  can_write: boolean
  write_refusal: string | null
  /** 제목 · 일자 · 삭제 · 확정 · 확정 해제 — 만든 사람 · 부서 관리자 · 시스템 관리자. */
  can_manage: boolean
  manage_refusal: string | null
  max_rows: number
  max_groups: number
}

export interface Presence {
  user_id: string
  name: string
  row_id: string | null
  group_id: string | null
  field: string | null
}

export interface Pulse {
  revision: number
  confirmed: boolean
  /** 30초 안에 칸을 수정하던 다른 사람(나는 빠진다). */
  presence: Presence[]
}

export type RowPatch = Partial<Pick<Row, ColumnKey>>

/** 옮기기의 결과 — 바뀐 한 장과 그사이 사라져 건너뛴 것. */
export interface Arranged extends Sheet {
  skipped: string[]
  anchor_missing: boolean
  changed: boolean
  group_id: string | null
}

/**
 * 옮기기 — **상대 동작**(서버가 그때의 차례에 대고 적용한다 — 차례 때문에 부딪치지 않는다).
 * 줄 한 칸 · 묶음 한 칸 · 줄을 그 묶음의 앞 · 뒤 · 맨 위 · 맨 아래로.
 */
export type Move =
  | { rows: [string]; step: 'up' | 'down' }
  | { group: string; step: 'up' | 'down' }
  | {
      rows: string[]
      to: { group: string | null; before?: string; after?: string; at?: 'start' | 'end' }
    }

const ROOT = '/ext/jajuverify'

/** 덮어쓰기 충돌의 오류 번호(백엔드 `code("JAJUVERIFY", 10)`). */
const CONFLICT = /-JAJUVERIFY-0010$/

/**
 * 같은 칸을 그사이 남이 고쳤다는 409 → `SaveConflictError`(지금 값 · 누가 · 언제). 다른 오류는
 * 그대로 둔다. 날짜 칸은 비어 있음을 `''` 로 읽는다(입력 칸의 값).
 */
export function conflictOf(caught: unknown): unknown {
  if (!(caught instanceof ApiError) || caught.status !== 409 || !CONFLICT.test(caught.code)) {
    return caught
  }
  const details = caught.details
  // 글 칸은 글, 일정은 단계 목록 — 받는 칸이 제 모양으로 읽는다.
  return new SaveConflictError<unknown>(
    caught.message,
    details.current ?? null,
    typeof details.updated_by === 'string' ? details.updated_by : null,
    typeof details.updated_at === 'string' ? details.updated_at : null,
  )
}

/** 부른 일이 충돌로 거절되면 `SaveConflictError` 로 던진다. */
async function guarded<T>(call: Promise<T>): Promise<T> {
  try {
    return await call
  } catch (caught) {
    throw conflictOf(caught)
  }
}

export const jvApi = {
  meetings: (kind: MeetingKind, options: { q?: string; offset?: number; limit?: number } = {}) => {
    const params = new URLSearchParams({ kind })
    if (options.q) params.set('q', options.q)
    if (options.offset) params.set('offset', String(options.offset))
    if (options.limit) params.set('limit', String(options.limit))
    return api.get<Page<MeetingBrief>>(`${ROOT}/meetings?${params.toString()}`)
  },
  create: (body: {
    kind: MeetingKind
    title: string
    held_on: string | null
    workspace_slug: string
  }) => api.post<Meeting>(`${ROOT}/meetings`, body),
  sheet: (meetingId: string) => api.get<Sheet>(`${ROOT}/meetings/${meetingId}`),
  /** 바뀌었나 — 판 번호 · 확정 · 지금 수정하는 다른 사람. */
  pulse: (meetingId: string) => api.get<Pulse>(`${ROOT}/meetings/${meetingId}/pulse`),
  /** 제목 · 일자 — **보낸 칸만.** 일자를 비우려면 `held_on: null`. `expected` 는 본 값. */
  updateMeeting: (
    meetingId: string,
    body: {
      title?: string
      held_on?: string | null
      expected?: { title?: string; held_on?: string | null }
    },
  ) => guarded(api.patch<Meeting>(`${ROOT}/meetings/${meetingId}`, body)),
  removeMeeting: (meetingId: string) => api.delete<void>(`${ROOT}/meetings/${meetingId}`),
  /** 회의 확정 · 확정 해제 — 바뀐 한 장을 돌려준다. */
  confirm: (meetingId: string) => api.post<Sheet>(`${ROOT}/meetings/${meetingId}/confirm`, {}),
  unconfirm: (meetingId: string) => api.delete<Sheet>(`${ROOT}/meetings/${meetingId}/confirm`),
  /** 줄 추가 — `after_id` 바로 아래(그 줄의 묶음), 아니면 `group_id` 묶음의 맨 아래. */
  addRow: (
    meetingId: string,
    body: { after_id?: string | null; group_id?: string | null } & RowPatch = {},
  ) => api.post<Row>(`${ROOT}/meetings/${meetingId}/rows`, body),
  /** 칸 하나씩 — **보낸 칸만**(자동 저장). `expected` 는 그 칸의 본 값. */
  updateRow: (rowId: string, body: RowPatch & { expected?: RowPatch }) =>
    guarded(api.patch<Row>(`${ROOT}/rows/${rowId}`, body)),
  /** 작성 완료를 켜고 끈다. */
  setDone: (rowId: string, done: boolean) => api.put<Row>(`${ROOT}/rows/${rowId}/done`, { done }),
  removeRow: (rowId: string) => api.delete<void>(`${ROOT}/rows/${rowId}`),
  /** 옮기기(상대 동작) — 바뀐 한 장과 건너뛴 것을 돌려준다. */
  move: (meetingId: string, move: Move) =>
    api.post<Arranged>(`${ROOT}/meetings/${meetingId}/move`, move),
  /** 묶음 추가(맨 아래) — `row_ids` 의 줄을 그 묶음으로. 바뀐 한 장을 돌려준다. */
  addGroup: (meetingId: string, body: { title: string; row_ids: string[] }) =>
    api.post<Arranged>(`${ROOT}/meetings/${meetingId}/groups`, body),
  /** 묶음 이름 — `expected` 는 본 값. */
  updateGroup: (groupId: string, body: { title: string; expected?: { title: string } }) =>
    guarded(api.patch<Group>(`${ROOT}/groups/${groupId}`, body)),
  /** 묶음 삭제 — 묶음만 해제(줄은 묶음 없음으로) 또는 줄까지. 바뀐 한 장을 돌려준다. */
  removeGroup: (groupId: string, withRows: boolean) =>
    api.delete<Sheet>(`${ROOT}/groups/${groupId}?with_rows=${withRows ? 'true' : 'false'}`),
  /** 지금 이 칸을 수정하는 중 · 떠났다. */
  touchPresence: (
    meetingId: string,
    body: { row_id?: string | null; group_id?: string | null; field: PresenceField },
  ) => api.put<void>(`${ROOT}/meetings/${meetingId}/presence`, body),
  leavePresence: (meetingId: string) => api.delete<void>(`${ROOT}/meetings/${meetingId}/presence`),
}

/** 화면 주소 — 목록 · 한 장. */
export const paths = {
  list: (kind: MeetingKind) => `/ext/jajuverify/meetings/${kind}`,
  sheet: (kind: MeetingKind, id: string) => `/ext/jajuverify/meetings/${kind}/${id}`,
}
