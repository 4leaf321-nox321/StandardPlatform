/**
 * 기록 타입의 「지도의 허브 칸」 — 전체 지도(ADR 0046)에서 이 타입의 기록을 **어느 기준정보 곁에** 둘지.
 *
 * 타입 편집 창의 다른 칸과 달리 **고르면 바로 저장한다** — 설정이 지도 쪽 표(`graph_map_hub_keys`)에
 * 있고, 지도는 다음 전체 배치에서 이것을 쓴다(지금 판은 그대로다). 그래서 그 말을 함께 적는다.
 */

import { useEffect, useState } from 'react'

import { graphApi } from '@/modules/graph/api'
import type { MapHubKey } from '@/modules/graph/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Label } from '@/shared/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'

const AUTO = '__auto__'
const NONE = '__none__'

export function MapHubKeyField({ typeSlug, canEdit }: { typeSlug: string; canEdit: boolean }) {
  const [row, setRow] = useState<MapHubKey | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    let live = true
    graphApi
      .hubKeys()
      .then((rows) => {
        if (live) setRow(rows.find((one) => one.type_slug === typeSlug) ?? null)
      })
      .catch((caught: unknown) => {
        if (live) setError(caught instanceof Error ? caught : new Error(String(caught)))
      })
    return () => {
      live = false
    }
  }, [typeSlug])

  if (error) return <ErrorNotice error={error} />
  if (!row) return null

  const value = row.mode === 'auto' ? AUTO : row.mode === 'none' ? NONE : (row.key ?? AUTO)
  const label = (key: string | null) =>
    row.candidates.find((one) => one.key === key)?.label ?? key ?? '없음'
  const save = (next: string) => {
    setSaving(true)
    setError(null)
    const body =
      next === AUTO
        ? ({ mode: 'auto' } as const)
        : next === NONE
          ? ({ mode: 'none' } as const)
          : ({ mode: 'key', key: next } as const)
    graphApi
      .setHubKey(typeSlug, body)
      .then(setRow)
      .catch((caught: unknown) =>
        setError(caught instanceof Error ? caught : new Error(String(caught))),
      )
      .finally(() => setSaving(false))
  }

  return (
    <div className="space-y-1.5 border-t pt-3">
      <Label htmlFor="type-edit-map-hub">지도의 허브 칸</Label>
      <Select value={value} onValueChange={save} disabled={!canEdit || saving}>
        <SelectTrigger id="type-edit-map-hub" aria-label="지도의 허브 칸">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={AUTO}>
            자동{row.auto_key ? ` (지금: ${label(row.auto_key)})` : ' (알맞은 칸 없음 → 미지정)'}
          </SelectItem>
          {row.candidates.map((one) => (
            <SelectItem key={one.key} value={one.key}>
              {one.label} → {one.target_label} ({one.hubs.toLocaleString()}개)
              {one.required ? '' : ' · 필수 아님'}
            </SelectItem>
          ))}
          <SelectItem value={NONE}>미지정 — 허브 없이 모두 「미지정」 자리에</SelectItem>
        </SelectContent>
      </Select>
      <p className="text-muted-foreground text-xs">
        전체 지도에서 이 타입의 기록을 이 칸이 가리키는 기준정보 곁 원반에 놓습니다. 자동은 필수 ·
        값 하나 · 기준정보를 가리키는 칸 중 가리킬 수 있는 기준정보가 가장 많은 칸입니다. 고르면
        바로 저장되고, <b>다음 「전체 다시 배치」 부터</b> 지도에 반영됩니다
        {row.in_layout ? ` — 지금 판은 「${label(row.used_key)}」 입니다` : ''}.
        {row.stale && ' 전에 고른 칸이 더 이상 알맞지 않아 자동으로 돌아갔습니다.'}
      </p>
    </div>
  )
}
