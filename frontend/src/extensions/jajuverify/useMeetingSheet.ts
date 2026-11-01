/**
 * 회의 한 장의 상태와 할 일 — 세 화면(심사회 · 포스트모텀 · 설명회)이 함께 쓴다.
 *
 * ## 여럿이 함께 쓴다 — 판 번호로 맞춘다
 *
 * - 읽기는 한 번에(`jvApi.sheet`). 화면이 보이는 동안 `PULSE_MS` 마다 판 번호만 묻고(`pulse`), 그것이
 *   화면이 가진 것과 다를 때만 한 장을 다시 받는다. 탭이 숨으면 묻지 않고, 다시 보이면 곧 묻는다.
 *   **시각을 비교하지 않는다** — 이 개발 기계(WSL)의 벽시계는 뒤로 튀었다(ADR 0024).
 * - **내 쓰기 때문에 다시 읽지 않는다** — 쓰기의 응답이 그 쓰기의 판 번호를 싣는다. 화면은 「여기까지는
 *   다 안다」(`known`)를 들고, 내 쓰기의 번호가 그 바로 다음이면 앞으로 민다. 사이에 남의 쓰기가 끼면
 *   (번호가 건너뛰면) 밀지 않는다 — 그러면 다음 `pulse` 가 달라서 한 장을 다시 받는다.
 * - 다시 받은 한 장이 내 쓰기보다 옛것이면(그 쓰기 전에 읽기 시작한 것이면) 버린다 — 방금 저장한 글이
 *   옛 글로 잠깐 되돌아 보이지 않게. 다음 `pulse` 가 다시 받는다.
 * - **치고 있는 칸은 덮지 않는다** — 칸은 바깥 값이 바뀌어도 들어가 있거나 고치는 중이면 따라가지
 *   않고(`useAutoSave`), 본 값을 그대로 들고 저장할 때 서버가 충돌로 잡는다. 칸 저장 뒤에도 한 장을
 *   다시 읽지 않는다(그 줄만 응답으로 바꾼다) — 다시 읽으면 치고 있는 다른 칸이 흔들린다.
 *
 * ## 지금 수정하는 사람
 *
 * 칸에 들어가면 그 자리를 적고(`focus`), 머무는 동안 `PRESENCE_MS` 마다 다시 적고, 나가면 지운다
 * (`blur` — 다른 칸으로 바로 옮기면 지우지 않고 새 자리를 적는다). 남의 것은 `pulse` 가 준다.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { jvApi } from '@/extensions/jajuverify/api'
import type {
  Arranged,
  CellKey,
  Group,
  Meeting,
  Move,
  Presence,
  PresenceField,
  Row,
  ScheduleItem,
  Sheet,
} from '@/extensions/jajuverify/api'

/** 판 번호를 묻는 간격(ms) — 10~15초. */
export const PULSE_MS = 12_000
/** 칸에 머무는 동안 「수정 중」 을 다시 적는 간격(ms) — 서버는 30초 안의 것만 보인다. */
export const PRESENCE_MS = 10_000
/** 칸을 떠나고 「수정 중」 을 지우기까지(ms) — 곧바로 다른 칸에 들어가면 지우지 않는다. */
const LEAVE_MS = 400

/** 지금 수정하는 자리 — 줄의 칸, 묶음 이름(`group_id` + `title`), 회의 제목(`title`). */
export interface Target {
  row_id?: string | null
  group_id?: string | null
  field: PresenceField
}

export interface SheetActions {
  reload: () => Promise<void>
  saveTitle: (next: string, base: string) => Promise<void>
  saveHeldOn: (next: string, base: string) => Promise<void>
  saveCell: (row: Row, key: CellKey, next: string, base: string) => Promise<void>
  saveSchedule: (row: Row, next: ScheduleItem[], base: ScheduleItem[]) => Promise<void>
  /** 줄 추가 — `after` 를 주면 그 줄 바로 아래(그 줄의 묶음), 아니면 `groupId` 묶음의 맨 아래. */
  addRow: (after?: Row | null, groupId?: string | null) => Promise<void>
  /** 묶음 안에서 위 · 아래로 한 칸. */
  move: (row: Row, direction: 'up' | 'down') => Promise<void>
  removeRow: (row: Row) => Promise<void>
  removeMeeting: () => Promise<void>
  setDone: (row: Row, done: boolean) => Promise<void>
  /** 묶음 추가(맨 아래) — 고른 줄을 그 묶음으로. 실패하면 던진다(창이 말한다). */
  addGroup: (title: string, rowIds: string[]) => Promise<void>
  renameGroup: (group: Group, next: string, base: string) => Promise<void>
  moveGroup: (group: Group, direction: 'up' | 'down') => Promise<void>
  /** 묶음 삭제 — 묶음만 해제(줄은 묶음 없음으로) 또는 줄까지. 실패하면 던진다. */
  removeGroup: (group: Group, withRows: boolean) => Promise<void>
  /** 줄들을 그 묶음의 맨 아래로(null 이면 묶음 없음으로) — 지금 표의 차례대로. */
  moveRows: (rowIds: string[], groupId: string | null) => Promise<void>
  /** 확정 · 확정 해제 — 실패하면 던진다. */
  confirm: () => Promise<void>
  unconfirm: () => Promise<void>
  focus: (target: Target) => void
  blur: () => void
  /** 사진을 붙이거나 뗐다 — 한 장을 다시 읽는다. */
  photosChanged: () => void
}

/** 그 자리를 지금 수정하는 다른 사람의 이름. */
export function editorsAt(
  presence: Presence[],
  where: { row_id?: string | null; group_id?: string | null; field: string },
): string[] {
  return presence
    .filter(
      (one) =>
        (one.row_id ?? null) === (where.row_id ?? null) &&
        (one.group_id ?? null) === (where.group_id ?? null) &&
        one.field === where.field,
    )
    .map((one) => one.name)
}

function asError(caught: unknown): Error {
  return caught instanceof Error ? caught : new Error('알 수 없는 오류')
}

export function useMeetingSheet(meetingId: string) {
  const [sheet, setSheet] = useState<Sheet | null>(null)
  const sheetRef = useRef<Sheet | null>(null)
  sheetRef.current = sheet
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<Error | null>(null)
  const [error, setError] = useState<Error | null>(null)
  /** 방금 더한 줄 — 그 줄의 항목 칸에 들어간다. */
  const [fresh, setFresh] = useState<string | null>(null)
  const [presence, setPresence] = useState<Presence[]>([])
  /** 이 번호까지의 쓰기는 화면에 다 들어 있다. */
  const known = useRef(0)
  /** 응답으로 이미 반영한 내 쓰기 중 `known` 보다 뒤의 것. */
  const mine = useRef<Set<number>>(new Set())

  /** 받은 한 장을 쓴다 — 내가 이미 아는 것보다 옛것이면 버린다(false). */
  const adopt = useCallback((next: Sheet): boolean => {
    const ceiling = Math.max(known.current, ...mine.current)
    if (next.meeting.revision < ceiling) return false
    known.current = next.meeting.revision
    mine.current = new Set([...mine.current].filter((one) => one > next.meeting.revision))
    setSheet(next)
    return true
  }, [])

  /** 내 쓰기의 판 번호 — 바로 다음 번호들이면 「다 안다」 를 앞으로 민다. */
  const wrote = useCallback((revision: number) => {
    if (revision <= known.current) return
    mine.current.add(revision)
    while (mine.current.has(known.current + 1)) {
      known.current += 1
      mine.current.delete(known.current)
    }
  }, [])

  const reload = useCallback(async () => {
    try {
      adopt(await jvApi.sheet(meetingId))
      setLoadError(null)
    } catch (caught) {
      setLoadError(asError(caught))
    } finally {
      setLoading(false)
    }
  }, [meetingId, adopt])

  useEffect(() => {
    known.current = 0
    mine.current = new Set()
    setSheet(null)
    setLoading(true)
    setPresence([])
    void reload()
  }, [reload])

  // --- 바뀌었나 -------------------------------------------------------------------
  const busy = useRef(false)
  const tick = useCallback(async () => {
    if (busy.current || document.visibilityState === 'hidden' || !sheetRef.current) return
    busy.current = true
    try {
      const got = await jvApi.pulse(meetingId)
      setPresence(got.presence)
      if (got.revision !== known.current) await reload()
    } catch {
      // 다음 차례에 다시 묻는다 — 한 번 못 물은 것으로 화면을 흔들지 않는다.
    } finally {
      busy.current = false
    }
  }, [meetingId, reload])

  const loaded = sheet !== null
  useEffect(() => {
    if (!loaded) return
    void tick()
    const timer = window.setInterval(() => void tick(), PULSE_MS)
    const onVisible = () => {
      if (document.visibilityState === 'visible') void tick()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [loaded, tick])

  // --- 지금 수정하는 사람 -------------------------------------------------------------
  const target = useRef<Target | null>(null)
  const beat = useRef<number | null>(null)
  const leaving = useRef<number | null>(null)
  const touch = useCallback(
    (where: Target) => {
      jvApi.touchPresence(meetingId, where).catch(() => undefined)
    },
    [meetingId],
  )
  useEffect(
    () => () => {
      if (beat.current !== null) window.clearInterval(beat.current)
      if (leaving.current !== null) window.clearTimeout(leaving.current)
      if (target.current) jvApi.leavePresence(meetingId).catch(() => undefined)
      target.current = null
    },
    [meetingId],
  )

  // --- 쓰기 -------------------------------------------------------------------------
  const fail = useCallback((caught: unknown) => setError(asError(caught)), [])

  const patchRow = useCallback((row: Row) => {
    setSheet(
      (now) => now && { ...now, rows: now.rows.map((one) => (one.id === row.id ? row : one)) },
    )
  }, [])

  /** 그사이 사라져 건너뛴 것을 알린다 — 옮기기는 거절하지 않으니, 말하지 않으면 모른다. */
  const [notice, setNotice] = useState<string | null>(null)

  /** 바뀐 한 장을 돌려주는 쓰기 — 옛것이면(그사이 내 다른 쓰기가 더 뒤면) 다시 읽는다. */
  const settle = useCallback(
    async (got: Sheet | Arranged) => {
      const skipped = 'skipped' in got ? got.skipped.length : 0
      setNotice(
        skipped
          ? `그사이 다른 사람이 삭제한 줄 · 묶음 ${skipped}개는 건너뛰었습니다.`
          : 'anchor_missing' in got && got.anchor_missing
            ? '기준 줄이 그사이 삭제되어 묶음의 맨 아래에 놓았습니다.'
            : null,
      )
      if (!adopt(got)) await reload()
    },
    [adopt, reload],
  )

  /** 옮기기 — **상대 동작**이라 그사이 남이 줄을 더하거나 지워도 거절되지 않는다(건너뛴 것만 알린다). */
  const arrange = useCallback(
    async (move: Move) => {
      setError(null)
      try {
        await settle(await jvApi.move(meetingId, move))
      } catch (caught) {
        fail(caught)
        await reload()
      }
    },
    [meetingId, settle, fail, reload],
  )

  const current = () => sheetRef.current

  const actions: SheetActions = {
    reload,
    saveTitle: async (next, base) => {
      const got: Meeting = await jvApi.updateMeeting(meetingId, {
        title: next,
        expected: { title: base },
      })
      wrote(got.revision)
      setSheet((now) => now && { ...now, meeting: got })
    },
    saveHeldOn: async (next, base) => {
      const got = await jvApi.updateMeeting(meetingId, {
        held_on: next || null,
        expected: { held_on: base || null },
      })
      wrote(got.revision)
      setSheet((now) => now && { ...now, meeting: got })
    },
    saveCell: async (row, key, next, base) => {
      const got = await jvApi.updateRow(row.id, { [key]: next, expected: { [key]: base } })
      wrote(got.revision)
      patchRow(got)
    },
    saveSchedule: async (row, next, base) => {
      const got = await jvApi.updateRow(row.id, { schedule: next, expected: { schedule: base } })
      wrote(got.revision)
      patchRow(got)
    },
    addRow: async (after, groupId) => {
      setError(null)
      try {
        const made = await jvApi.addRow(
          meetingId,
          after ? { after_id: after.id } : { group_id: groupId ?? null },
        )
        wrote(made.revision)
        setFresh(made.id)
        await reload()
      } catch (caught) {
        fail(caught)
      }
    },
    move: async (row, direction) => {
      await arrange({ rows: [row.id], step: direction })
    },
    removeRow: async (row) => {
      await jvApi.removeRow(row.id)
      await reload()
    },
    removeMeeting: async () => {
      await jvApi.removeMeeting(meetingId)
    },
    setDone: async (row, done) => {
      setError(null)
      try {
        const got = await jvApi.setDone(row.id, done)
        wrote(got.revision)
        patchRow(got)
      } catch (caught) {
        fail(caught)
      }
    },
    addGroup: async (title, rowIds) => {
      await settle(await jvApi.addGroup(meetingId, { title, row_ids: rowIds }))
    },
    renameGroup: async (group, next, base) => {
      const got = await jvApi.updateGroup(group.id, { title: next, expected: { title: base } })
      wrote(got.revision)
      setSheet(
        (now) =>
          now && { ...now, groups: now.groups.map((one) => (one.id === got.id ? got : one)) },
      )
    },
    moveGroup: async (group, direction) => {
      await arrange({ group: group.id, step: direction })
    },
    removeGroup: async (group, withRows) => {
      await settle(await jvApi.removeGroup(group.id, withRows))
    },
    moveRows: async (rowIds, groupId) => {
      if (rowIds.length) await arrange({ rows: rowIds, to: { group: groupId, at: 'end' } })
    },
    confirm: async () => {
      await settle(await jvApi.confirm(meetingId))
    },
    unconfirm: async () => {
      await settle(await jvApi.unconfirm(meetingId))
    },
    focus: (where) => {
      if (!current()?.can_write) return
      if (leaving.current !== null) {
        window.clearTimeout(leaving.current)
        leaving.current = null
      }
      target.current = where
      touch(where)
      if (beat.current !== null) window.clearInterval(beat.current)
      beat.current = window.setInterval(() => {
        if (target.current) touch(target.current)
      }, PRESENCE_MS)
    },
    blur: () => {
      if (!target.current) return
      target.current = null
      if (beat.current !== null) {
        window.clearInterval(beat.current)
        beat.current = null
      }
      if (leaving.current !== null) window.clearTimeout(leaving.current)
      leaving.current = window.setTimeout(() => {
        leaving.current = null
        jvApi.leavePresence(meetingId).catch(() => undefined)
      }, LEAVE_MS)
    },
    photosChanged: () => void reload(),
  }

  return {
    sheet,
    loading: loading && !sheet,
    error: error ?? loadError,
    notice,
    fresh,
    presence,
    actions,
  }
}
