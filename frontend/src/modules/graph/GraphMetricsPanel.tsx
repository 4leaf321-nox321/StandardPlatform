/**
 * 객체 상세의 「그래프 지표」(ADR 0067) — 밤에 센 기대는 수 · 다리 역할 · 무리 · 비슷한 것.
 *
 * 셈은 밤에 워커가 한다 — 여기서는 읽기만(가볍다). 판이 없거나, 이 객체에 센 값이 하나도 없거나(기록
 * 타입 · 아무것과도 안 이어진 것), 볼 수 없으면(부서로 좁힌 설치) **아무것도 안 그린다** — 빈 칸이
 * 상세마다 서면 잡음이다.
 */

import { Link } from 'react-router-dom'

import { graphCalcApi } from '@/modules/graph/calc'
import type { CalcObject } from '@/modules/graph/calc'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

const number = new Intl.NumberFormat('ko-KR')

function share(rank: number, of: number): string {
  if (of <= 0) return ''
  const top = (rank / of) * 100
  return top < 1 ? ' · 상위 1% 안' : ` · 상위 ${Math.ceil(top)}%`
}

export function GraphMetricsPanel({
  objectId,
  relationLabels = {},
}: {
  objectId: string
  /** 관계 slug → 이름(없으면 slug 그대로). */
  relationLabels?: Record<string, string>
}) {
  const found = useResource<CalcObject | null>(
    () => graphCalcApi.object(objectId).catch(() => null),
    [objectId],
  )
  const data = found.data
  if (!data?.run) return null
  const depends = data.scores.filter((one) => one.metric === 'depends_top')
  const bridge = data.scores.find((one) => one.metric === 'bridge')
  if (!depends.length && !bridge && !data.cluster && !data.similar.length) return null
  const name = (slug: string) => relationLabels[slug] ?? slug

  return (
    <section className="space-y-3 rounded-md border p-4" aria-label="그래프 지표">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold">그래프 지표</h2>
        <span className="text-muted-foreground text-xs">
          {shownDateTime(data.run.computed_at)} 기준 · 밤에 센 값({data.run.engine.toUpperCase()})
        </span>
      </div>
      <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
        {depends.map((one) => (
          <div key={one.relation} className="contents">
            <dt className="text-muted-foreground">기대는 수 · {name(one.relation)}</dt>
            <dd>
              맨 위 <strong>{number.format(one.value)}</strong>개가 기댑니다{' '}
              <span className="text-muted-foreground text-xs">
                ({number.format(one.of)}개 중 {number.format(one.rank)}위{share(one.rank, one.of)})
              </span>
            </dd>
          </div>
        ))}
        {bridge && (
          <>
            <dt className="text-muted-foreground">다리 역할</dt>
            <dd>
              {number.format(bridge.rank)}위{' '}
              <span className="text-muted-foreground text-xs">
                ({number.format(bridge.of)}개 중{share(bridge.rank, bridge.of)} · 가장 큰 다리 대비{' '}
                {Math.round(bridge.value * 100)}%)
              </span>
            </dd>
          </>
        )}
        {data.cluster && (
          <>
            <dt className="text-muted-foreground">무리</dt>
            <dd>
              무리 {data.cluster.cluster}
              {data.cluster.label && ` 「${data.cluster.label}」`}{' '}
              <span className="text-muted-foreground text-xs">
                ({data.cluster.size != null && `${number.format(data.cluster.size)}개 · `}이 안{' '}
                {number.format(data.cluster.rank)}번째로 이음이 많음)
              </span>
            </dd>
          </>
        )}
      </dl>
      {data.similar.length > 0 && (
        <div className="space-y-1">
          <h3 className="text-sm font-medium">구성이 비슷한 것</h3>
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {data.similar.map((one) => (
              <li key={`${one.relation}:${one.id}`}>
                <Link to={`/o/${one.type_slug}/${one.id}`} className="hover:underline">
                  {one.label}
                </Link>{' '}
                <span className="text-muted-foreground text-xs">
                  {Math.round(one.score * 100)}%
                </span>
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground text-xs">
            끝까지 펼친 구성이 겹치는 정도(같은 타입끼리) — 밤에 센 값이라 그 뒤에 바뀐 구성은 안
            들었습니다.
          </p>
        </div>
      )}
    </section>
  )
}
