/**
 * 객체를 고르는 칸의 후보 — **서버가 찾는다.**
 *
 * 처음 200개를 받아 화면에서 거르던 방식은 201번째부터 **없는 것으로 보인다.** 못 찾은
 * 사람은 없다고 결론 내리고 새로 만들고, 그러면 같은 것이 둘이 된다. 그래서 친 글자를
 * 서버로 보내 그 타입의 목록 API 로 찾는다(이름·식별자·검색 속성 — 목록 화면과 같은
 * 검색). 한 번에 50개만 받고, `total` 로 「더 있다」 를 말한다.
 *
 * 이미 골라 둔 값이 후보에 없을 수 있다(검색으로 좁혔거나, 51번째 이후거나). 그 값은
 * 상세를 한 번 읽어 **핀으로 꽂아 둔다** — 안 그러면 고른 것이 빈 칸으로 보인다.
 */

import { useEffect, useRef, useState } from 'react'

import { objectApi } from '@/modules/objects/api'
import type { PickerOption } from '@/shared/components/SearchablePicker'

export interface OptionSource {
  slug: string
  /** 여러 타입에서 고를 때 힌트에 붙는 이름. */
  label?: string
}

interface Options {
  /** 후보에서 뺄 객체(자기 자신). */
  exclude?: string | null
  /** 지금 골라 둔 값 — 후보에 없으면 핀으로 꽂는다. */
  value?: string | null
  /** 값을 `<타입slug>:<id>` 로 만들지. 여러 타입에서 고를 때 참이다. */
  composite?: boolean
  limit?: number
}

export const PICKER_LIMIT = 50
const DEBOUNCE_MS = 200

export function useObjectOptions(sources: OptionSource[], opts: Options = {}) {
  const { exclude = null, value = null, composite = false, limit = PICKER_LIMIT } = opts
  const [query, setQuery] = useState('')
  const [options, setOptions] = useState<PickerOption[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const [pinned, setPinned] = useState<PickerOption | null>(null)
  const pinCache = useRef(new Map<string, PickerOption>())
  const key = sources.map((one) => one.slug).join(',')

  // 후보 — 친 글자가 멈추고 잠깐 뒤에 한 번만 묻는다.
  useEffect(() => {
    if (!key) {
      setOptions([])
      setTotal(0)
      return
    }
    let cancelled = false
    const timer = window.setTimeout(() => {
      setLoading(true)
      setFailed(false)
      Promise.all(
        sources.map((source) =>
          objectApi.list(source.slug, { q: query || undefined, limit }).then((page) => ({
            source,
            page,
          })),
        ),
      )
        .then((pages) => {
          if (cancelled) return
          setTotal(pages.reduce((sum, one) => sum + one.page.total, 0))
          setOptions(
            pages.flatMap(({ source, page }) =>
              page.items
                .filter((row) => row.id !== exclude)
                .map((row) => ({
                  value: composite ? `${source.slug}:${row.id}` : row.id,
                  label: row.label,
                  hint: composite
                    ? `${source.label ?? source.slug}${row.key ? ` · ${row.key}` : ''}`
                    : (row.key ?? undefined),
                  keywords: row.key ?? undefined,
                  // 이미 못 고르는 줄은 **이유를 적는다.** 비활성만 시키고 말 안 하면 버그로 읽힌다.
                  disabledReason: row.status === 'deprecated' ? '안 쓰는 값' : undefined,
                })),
            ),
          )
        })
        .catch(() => {
          if (!cancelled) {
            setFailed(true)
            setOptions([])
          }
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    }, DEBOUNCE_MS)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
    // sources 는 매 렌더 새 배열이라 slug 목록으로 비교한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, query, exclude, composite, limit])

  // 골라 둔 값의 이름 — 후보에 없으면 상세를 한 번 읽는다.
  useEffect(() => {
    if (!value) {
      setPinned(null)
      return
    }
    // 후보 안에 있으면 그것이 이름이다 — 상세를 또 읽지 않는다. 뒤에 검색으로 후보가
    // 좁혀져도 기억해 둔 것이 남는다.
    const inOptions = options.find((one) => one.value === value)
    if (inOptions) pinCache.current.set(value, inOptions)
    const cached = pinCache.current.get(value)
    if (cached) {
      setPinned(cached)
      return
    }
    const [slug, id] = composite ? value.split(':') : [sources[0]?.slug, value]
    if (!slug || !id) {
      setPinned(null)
      return
    }
    let cancelled = false
    objectApi
      .profile(slug, id)
      .then((profile) => {
        if (cancelled) return
        const row = profile.object
        const option: PickerOption = {
          value,
          label: row.label,
          hint: composite
            ? `${profile.type_label}${row.key ? ` · ${row.key}` : ''}`
            : (row.key ?? undefined),
        }
        pinCache.current.set(value, option)
        setPinned(option)
      })
      .catch(() => {
        // 못 읽으면(지워졌거나 남의 부서) id 라도 보여 준다 — 빈 칸보다 낫다.
        if (!cancelled) setPinned({ value, label: value })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, composite, key, options])

  return { options, pinned, total, loading, failed, query, setQuery }
}
