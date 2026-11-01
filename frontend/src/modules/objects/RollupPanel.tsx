/**
 * 「아래 전부」 — 롤업 값. 저장된 것이 아니라 **볼 때마다 센 것**이다.
 *
 * 값 옆에 「N개 중 M개 값 없음」 을 반드시 적는다. 안 적으면 합계가 「전부의 합」 으로
 * 읽히고, 그것은 틀린 수다.
 *
 * 「수량 반영」 · 「말단만」(ADR 0040)이면 그것도 적는다 — 같은 「무게 합계」 라도 총수량만큼 셌는지,
 * 말단만 모았는지에 따라 다른 수다. 모은 대상의 수(`descendants`)도 롤업마다 다르다.
 */

import { Sigma } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import type { Rollup } from '@/modules/objects/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { useResource } from '@/shared/hooks/useResource'

interface Props {
  typeSlug: string
  objectId: string
  /** 정의가 바뀌거나 상세가 다시 읽힐 때 다시 센다. */
  reloadKey: unknown
  /** 기준일(ADR 0039, 빈 글자 = 오늘) — 그날 유효한 관계로 펼친다. 상세의 관련 객체와 같은 날. */
  asOf?: string
}

/** 정수는 그대로, 소수는 3자리까지 — 롤업 · 전개가 같은 모양으로 적는다. */
export function shown(value: number | null): string {
  if (value === null) return '—'
  return Number.isInteger(value)
    ? value.toLocaleString()
    : value.toLocaleString(undefined, { maximumFractionDigits: 3 })
}

/**
 * 머리말의 「트리 아래 N개」 — **롤업마다 센 대상이 다르면 하나로 적지 않는다**(「말단만」 이면
 * 말단의 수다). 그때는 카드마다 적힌 수를 읽는다.
 */
function headline(rows: Rollup[]): string {
  const counts = new Set(rows.map((one) => one.descendants))
  if (counts.size !== 1) return '트리 아래를 모은 값'
  const leaves = rows.every((one) => one.leaves_only)
  return `트리 아래 ${leaves ? '말단 ' : ''}${rows[0].descendants.toLocaleString()}개를 모은 값`
}

function coverage(row: Rollup): string {
  const what = row.leaves_only ? '말단 ' : ''
  if (row.descendants === 0) {
    return row.leaves_only ? '아래에 말단이 없습니다' : '아래에 아무것도 없습니다'
  }
  return row.missing > 0
    ? `${what}${row.descendants}개 중 ${row.missing}개는 값이 없습니다`
    : `${what}${row.descendants}개 전부 값이 있습니다`
}

export function RollupPanel({ typeSlug, objectId, reloadKey, asOf = '' }: Props) {
  const rollups = useResource(
    () => objectApi.rollup(typeSlug, objectId, asOf || null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [typeSlug, objectId, reloadKey, asOf],
  )
  if (rollups.error) return <ErrorNotice error={rollups.error} />
  const rows = rollups.data ?? []
  if (rows.length === 0) return null

  return (
    <section className="space-y-2">
      <h2 className="flex items-center gap-2 text-base font-semibold">
        <Sigma className="size-4" />
        아래 전부
        <span className="text-muted-foreground text-sm font-normal">
          {headline(rows)} — 볼 때마다 셉니다
          {asOf && ` · 기준일 ${asOf}`}
        </span>
      </h2>
      <dl className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {rows.map((row) => (
          <div
            key={`${row.property}:${row.fn}:${row.weighted}:${row.leaves_only}`}
            className="rounded-md border p-3"
          >
            <dt className="text-muted-foreground flex flex-wrap items-center gap-1 text-xs">
              {row.label}
              {row.weighted && <StatusBadge kind="rollup" value="weighted" />}
              {row.leaves_only && <StatusBadge kind="rollup" value="leaves_only" />}
            </dt>
            {/* **못 낸 값은 까닭을 값 자리에 적는다** — 「—」 만 두면 값이 없는 것으로 읽힌다. */}
            {row.note ? (
              <dd className="text-muted-foreground py-1 text-sm">{row.note}</dd>
            ) : (
              <dd className="text-lg font-semibold tabular-nums">{shown(row.value)}</dd>
            )}
            {/* **빈 것을 말한다.** 이것이 없으면 합계가 「전부의 합」 으로 읽힌다. */}
            <p className="text-muted-foreground text-xs">{coverage(row)}</p>
            {row.weighted && row.quantity_missing > 0 && (
              <p className="text-muted-foreground text-xs">
                수량 없음 {row.quantity_missing.toLocaleString()}개(1개로 셈)
              </p>
            )}
          </div>
        ))}
      </dl>
    </section>
  )
}
