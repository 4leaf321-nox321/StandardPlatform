/**
 * 눈여겨볼 것의 줄들 — 화면 「눈여겨볼 것」 · 홈 카드 · 지표 상세가 같은 줄을 쓴다.
 *
 * 줄마다 **무엇이 · 얼마나 · 언제부터**(제목에 「몇 배 · 몇 건」 이 든다)와, 그 분석을 그 인자로 여는
 * 링크. 눌러서 본 것이 이 줄이 말한 것과 같아야 한다 — 그래서 링크는 서버가 짓는다.
 */

import { Link } from 'react-router-dom'

import type { Insight } from '@/modules/insights/api'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { shownDateTime } from '@/shared/lib/datetime'

export function InsightList({
  items,
  showMetric = true,
  onDismiss,
  busyId,
}: {
  items: Insight[]
  showMetric?: boolean
  /** 시스템 관리자에게만 준다 — 그만 보기 · 다시 보기. */
  onDismiss?: (one: Insight) => void
  busyId?: string | null
}) {
  return (
    <ul className="divide-y rounded-md border">
      {items.map((one) => (
        <li key={one.id} className="flex flex-wrap items-start gap-3 px-3 py-2 text-sm">
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">{one.kind_label}</Badge>
              {showMetric && (
                <Link to={`/metrics/${one.metric_slug}`} className="text-xs underline">
                  {one.metric_label}
                </Link>
              )}
              {one.past && <Badge variant="secondary">지남</Badge>}
              {one.dismissed && <Badge variant="secondary">그만 봄</Badge>}
            </div>
            <p className={one.past || one.dismissed ? 'text-muted-foreground' : undefined}>
              {one.title}
            </p>
            <p className="text-muted-foreground text-xs">
              처음 본 때 {shownDateTime(one.first_seen_at)} · {one.recipe_label}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Link to={one.link} className="text-xs underline">
              분석 열기
            </Link>
            {onDismiss && (
              <Button
                size="sm"
                variant="ghost"
                disabled={busyId === one.id}
                onClick={() => onDismiss(one)}
              >
                {one.dismissed ? '다시 보기' : '그만 보기'}
              </Button>
            )}
          </div>
        </li>
      ))}
    </ul>
  )
}
