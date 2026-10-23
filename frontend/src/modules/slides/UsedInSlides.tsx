/**
 * 객체 상세의 「장표」 — 이 객체를 주제로 한 장표와, **마지막으로 열었을 때** 이 기록이 실렸던
 * 장표(ADR 0037). 장표는 살아 있는 값이라 그 뒤에 빠졌을 수 있다 — 그래서 「확인」 시각을 함께 적는다.
 * 쓰인 데가 없으면 아무것도 표시하지 않는다.
 */

import { Link } from 'react-router-dom'

import { slideApi } from '@/modules/slides/api'
import { Badge } from '@/shared/components/ui/badge'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

export function UsedInSlides({ objectId }: { objectId: string }) {
  const used = useResource(() => slideApi.usedIn(objectId), [objectId])
  if (!used.data || used.data.length === 0) return null

  return (
    <section className="space-y-2 rounded-md border p-4" aria-label="장표">
      <h2 className="font-semibold">장표</h2>
      <p className="text-muted-foreground text-xs">
        이 객체를 주제로 한 장표와, 마지막으로 열었을 때 이 기록이 실렸던 장표입니다.
      </p>
      <ul className="space-y-1 text-sm">
        {used.data.map((one) => (
          <li key={`${one.role}-${one.slide.id}`} className="flex flex-wrap items-center gap-2">
            <Link to={`/slides/${one.slide.id}`} className="font-medium hover:underline">
              {one.slide.title}
            </Link>
            <span className="text-muted-foreground text-xs">{one.slide.template_name}</span>
            {one.role === 'subject' ? (
              <Badge variant="secondary" className="text-xs font-normal">
                주제
              </Badge>
            ) : (
              <>
                <Badge variant="outline" className="text-xs font-normal">
                  {one.areas.join(' · ')}
                </Badge>
                {one.seen_at && (
                  <span className="text-muted-foreground text-xs">
                    확인 {shownDateTime(one.seen_at)}
                  </span>
                )}
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
