/**
 * 이 객체를 가리키는 **기록**(ADR 0011) — 타입 · 칸마다의 수. 축의 상세는 기록을 줄로 싣지 않는다
 * (인기 모델은 시장 서비스 건 10만 건이 가리킨다). 누르면 그 칸으로 걸러진 목록으로 간다 — 거기서
 * 정렬 · 거르기 · 통계를 쓴다.
 */

import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'

import type { LogCount } from '@/modules/objects/api'

export function LogCounts({ objectId, counts }: { objectId: string; counts: LogCount[] }) {
  if (counts.length === 0) return null
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold">가리키는 기록</h2>
      <ul className="flex flex-wrap gap-2">
        {counts.map((one) => (
          <li key={`${one.type_slug}.${one.key}`}>
            <Link
              to={`/o/${one.type_slug}?${new URLSearchParams({ [`f.${one.key}.eq`]: objectId })}`}
              className="hover:bg-muted/50 inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm"
            >
              <span>{one.type_label}</span>
              <span className="font-medium tabular-nums">{one.count.toLocaleString()}건</span>
              <span className="text-muted-foreground text-xs">「{one.label}」 칸</span>
              <ArrowRight className="size-3.5" />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}
