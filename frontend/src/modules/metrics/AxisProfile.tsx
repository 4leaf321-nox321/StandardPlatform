/**
 * 축 객체의 상세에 서는 「기록 요약」(ADR 0022) — 이 객체의 타입을 기준(축)으로 가진 지표를 찾아,
 * 그 기준이 이 객체인 기록의 한 장 요약을 보인다. 그런 지표가 없으면 아무것도 안 그린다.
 *
 * 축 이름은 모른다 — 지표의 기준이 가리키는 타입(`target`)으로 찾는다. 그래서 어느 쌍둥이의
 * 어느 축 타입(부품 · 고장 메커니즘 · 해석법 …)에서도 선다.
 */

import { useState } from 'react'

import { metricsApi } from '@/modules/metrics/api'
import { ProfileSummary } from '@/modules/metrics/analysis/ProfileSummary'
import type { ProfileResult } from '@/modules/metrics/analysis/types'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { useResource } from '@/shared/hooks/useResource'

export function AxisProfile({ typeSlug, objectId }: { typeSlug: string; objectId: string }) {
  const metrics = useResource(() => metricsApi.list(), [])
  const choices = (metrics.data ?? []).flatMap((metric) =>
    metric.analyses.some((one) => one.recipe === 'profile' && one.ok)
      ? metric.dims
          .filter((dim) => dim.target === typeSlug)
          .map((dim) => ({ metric, dim, key: `${metric.slug}:${dim.name}` }))
      : [],
  )
  const [picked, setPicked] = useState<string | null>(null)
  const chosen = choices.find((one) => one.key === picked) ?? choices[0]
  const result = useResource<ProfileResult | null>(
    () =>
      chosen
        ? metricsApi.analysis<ProfileResult>(chosen.metric.slug, 'profile', {
            dim: chosen.dim.name,
            value: objectId,
          })
        : Promise.resolve(null),
    [chosen?.key, objectId],
  )
  if (!chosen) return null
  return (
    <section className="space-y-3 rounded-md border p-4" aria-label="기록 요약">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold">기록 요약 — {chosen.metric.label}</h2>
        {choices.length > 1 && (
          <Select value={chosen.key} onValueChange={setPicked}>
            <SelectTrigger className="w-64" aria-label="요약할 지표">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {choices.map((one) => (
                <SelectItem key={one.key} value={one.key}>
                  {one.metric.label} · {one.dim.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
      <ErrorNotice error={result.error} />
      {result.data && <ProfileSummary data={result.data} compact />}
    </section>
  )
}
