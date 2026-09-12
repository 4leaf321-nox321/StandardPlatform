/**
 * 「아래 전부」 — 롤업 값. 저장된 것이 아니라 **볼 때마다 센 것**이다.
 *
 * 값 옆에 「N개 중 M개 값 없음」 을 반드시 적는다. 안 적으면 합계가 「전부의 합」 으로
 * 읽히고, 그것은 틀린 수다.
 */

import { Sigma } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { useResource } from '@/shared/hooks/useResource'

interface Props {
  typeSlug: string
  objectId: string
  /** 정의가 바뀌거나 상세가 다시 읽힐 때 다시 센다. */
  reloadKey: unknown
}

function shown(value: number | null): string {
  if (value === null) return '—'
  return Number.isInteger(value)
    ? value.toLocaleString()
    : value.toLocaleString(undefined, { maximumFractionDigits: 3 })
}

export function RollupPanel({ typeSlug, objectId, reloadKey }: Props) {
  const rollups = useResource(
    () => objectApi.rollup(typeSlug, objectId),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [typeSlug, objectId, reloadKey],
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
          트리 아래 {rows[0].descendants}개를 모은 값 — 볼 때마다 셉니다
        </span>
      </h2>
      <dl className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {rows.map((row) => (
          <div key={`${row.property}:${row.fn}`} className="rounded-md border p-3">
            <dt className="text-muted-foreground text-xs">{row.label}</dt>
            <dd className="text-lg font-semibold tabular-nums">{shown(row.value)}</dd>
            {/* **빈 것을 말한다.** 이것이 없으면 합계가 「전부의 합」 으로 읽힌다. */}
            <p className="text-muted-foreground text-xs">
              {row.descendants === 0
                ? '아래에 아무것도 없습니다'
                : row.missing > 0
                  ? `${row.descendants}개 중 ${row.missing}개는 값이 없습니다`
                  : `${row.descendants}개 전부 값이 있습니다`}
            </p>
          </div>
        ))}
      </dl>
    </section>
  )
}
