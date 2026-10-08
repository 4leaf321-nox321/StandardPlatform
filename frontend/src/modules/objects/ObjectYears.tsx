/**
 * 연도 배정(`temporal_kind='yearly'`).
 *
 * **배정할 자리가 없으면 그 축은 늘 비어 보인다** — 목록이 「올해」 로 시작하는데
 * 아무 해도 배정돼 있지 않으면 아무것도 안 뜨고, 그것은 데이터가 없는 것으로 읽힌다.
 *
 * 구간(`lifecycle`)과 달리 **불연속이 가능하다** — 2024·2026 에는 쓰고 2025 에는
 * 안 쓰는 일이 실제로 있다.
 */

import { useEffect, useState } from 'react'
import { CalendarRange } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'

/** 고를 수 있는 해. 올해 기준 앞뒤로 조금씩. */
const YEARS = Array.from({ length: 12 }, (_, index) => new Date().getFullYear() + 1 - index)

interface Props {
  typeSlug: string
  objectId: string
  canEdit: boolean
}

export function ObjectYears({ typeSlug, objectId, canEdit }: Props) {
  const [years, setYears] = useState<number[]>([])
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  /**
   * 지금 배정을 **읽었나.** 저장은 통째 교체(PUT)라, 못 읽은 채 빈 목록에서 한 해를 눌러 저장하면
   * 기존 배정이 그 한 해로 바뀐다. 읽기 실패를 삼키던 때는 실패가 빈 목록으로 보였다(2026-10-08).
   */
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState<Error | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoaded(false)
    setLoadError(null)
    objectApi
      .years(typeSlug, objectId)
      .then((found) => {
        if (!cancelled) {
          setYears(found)
          setDirty(false)
          setLoaded(true)
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setLoadError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
        }
      })
    return () => {
      cancelled = true
    }
  }, [typeSlug, objectId, attempt])

  /** 고칠 수 있나 — 권한이 있고, 지금 배정을 읽은 뒤에만. */
  const editable = canEdit && loaded

  function toggle(one: number) {
    setYears((now) => (now.includes(one) ? now.filter((y) => y !== one) : [...now, one].sort()))
    setDirty(true)
  }

  async function save() {
    if (!loaded) return
    setError(null)
    setSaving(true)
    try {
      const saved = await objectApi.setYears(typeSlug, objectId, years)
      setYears(saved)
      setDirty(false)
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="space-y-3 rounded-md border p-4">
      <h2 className="flex items-center gap-2 text-base font-semibold">
        <CalendarRange className="size-4" />
        해당 연도
      </h2>

      {error && <ErrorNotice error={error} />}
      {loadError && (
        <div className="space-y-2">
          <ErrorNotice error={loadError} />
          <p className="text-muted-foreground text-xs">
            지금 배정을 읽지 못해 수정할 수 없습니다 — 저장은 통째로 바꾸므로, 읽지 못한 채
            저장하면 기존 배정이 지워집니다.{' '}
            <button
              type="button"
              className="underline"
              onClick={() => setAttempt((before) => before + 1)}
            >
              다시 읽기
            </button>
          </p>
        </div>
      )}

      <div className="flex flex-wrap gap-1.5">
        {YEARS.map((one) => (
          <button
            key={one}
            type="button"
            aria-pressed={years.includes(one)}
            disabled={!editable}
            onClick={() => toggle(one)}
            className={
              years.includes(one)
                ? 'bg-primary text-primary-foreground rounded px-2 py-1 text-xs'
                : 'text-muted-foreground hover:bg-accent rounded border px-2 py-1 text-xs disabled:opacity-50'
            }
          >
            {one}
          </button>
        ))}
      </div>

      <p className="text-muted-foreground text-xs">
        선택한 해의 목록에만 나옵니다. <b>하나도 선택하지 않으면 어느 해에도 안 나옵니다</b> —
        「전체 연도」 로 봐야 보입니다.
      </p>

      {canEdit && (
        <div className="flex justify-end">
          <Button size="sm" onClick={save} disabled={!dirty || saving || !loaded}>
            저장
          </Button>
        </div>
      )}
    </section>
  )
}
