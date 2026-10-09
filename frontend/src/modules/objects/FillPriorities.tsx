/**
 * 채울 곳 — **어디부터 채우나.**
 *
 * 아래 데이터 품질은 「무엇이 나쁜가」 다. 빈 칸은 늘 수천 개라 그것만으로는 무엇을 먼저 채울지
 * 정할 수 없다 — 지표가 묶는 칸 · 코어로 공개한 칸 · 뷰가 거르는 칸이 빈 것과 아무도 안 쓰는
 * 칸이 빈 것은 무게가 다르다. 서버가 쓰는 곳으로 가중해 줄을 세우고(ADR 0025), 여기는 위의 몇
 * 줄만 보인다 — 줄마다 「이것을 채우면 무엇이 좋아지나」 와, 빈 것만 거른 목록으로 가는 링크.
 *
 * **어림이면 어림이라고 적는다** — 큰 타입은 표본에서 센 수다(`estimated`).
 */

import { Link } from 'react-router-dom'
import { ListChecks } from 'lucide-react'

import { fillApi } from '@/modules/objects/api'
import type { FillPriority } from '@/modules/objects/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Badge } from '@/shared/components/ui/badge'
import { useResource } from '@/shared/hooks/useResource'

/** 화면에 싣는 줄 수 — 더 많이는 MCP(`fill_priorities`)가 준다. */
const SHOWN = 10

function amount(one: FillPriority): string {
  if (one.kind === 'empty_type') return '객체 없음'
  const about = one.estimated ? '약 ' : ''
  const missing = `${about}${one.missing.toLocaleString()}건`
  if (one.kind === 'waiting') return `${missing} 대기`
  return `${missing} / ${about}${one.total.toLocaleString()}건 비어 있음`
}

export function FillPriorities() {
  const report = useResource(() => fillApi.report({ limit: SHOWN }), [])
  const rows = report.data?.priorities ?? []

  return (
    <section id="fill" className="scroll-mt-4 space-y-3">
      <h2 className="flex items-center gap-2 text-base font-medium">
        <ListChecks className="text-muted-foreground size-4" />
        채울 곳
        <span className="text-muted-foreground text-sm font-normal">— 어디부터 채우나</span>
      </h2>
      <p className="text-muted-foreground text-sm">
        비어 있는 것을 그것을 사용하는 곳(필수 · 지표 · 코어 공개 · 뷰)으로 가중해 순서를
        정했습니다. 위의 것을 채울수록 더 많은 자리가 좋아집니다.
      </p>
      {report.error && <ErrorNotice error={report.error} />}
      {report.data && rows.length === 0 && (
        <p className="text-muted-foreground text-sm">
          채울 곳이 없습니다 — 필수 칸과 사용하는 칸이 모두 채워졌고, 객체가 없는 타입도
          없습니다.
        </p>
      )}
      {rows.length > 0 && (
        <ol className="divide-y rounded-md border text-sm">
          {rows.map((one) => (
            <li
              key={`${one.kind}:${one.type_slug}:${one.target}`}
              className="space-y-0.5 px-3 py-2"
            >
              <div className="flex items-baseline justify-between gap-3">
                <span className="flex min-w-0 flex-wrap items-baseline gap-1">
                  <Link to={`/o/${one.type_slug}`} className="font-medium hover:underline">
                    {one.type_label}
                  </Link>
                  <span className="text-muted-foreground">›</span>
                  <span>{one.target_label}</span>
                  {one.uses.map((use) => (
                    <Badge key={use} variant="secondary">
                      {use}
                    </Badge>
                  ))}
                </span>
                <Link
                  to={one.link}
                  className="text-muted-foreground shrink-0 text-xs hover:underline"
                >
                  {amount(one)}
                </Link>
              </div>
              <p className="text-muted-foreground text-xs">{one.gain}</p>
            </li>
          ))}
        </ol>
      )}
      {(report.data?.notes ?? []).map((note) => (
        <p key={note} className="text-muted-foreground text-xs">
          {note}
        </p>
      ))}
    </section>
  )
}
