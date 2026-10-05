/**
 * 한 장 요약(분석 탭) — 기준 하나와 그 값 하나를 고른다(ADR 0022).
 */

import { useMemo, useState } from 'react'

import { metricsApi } from '@/modules/metrics/api'
import type { AnalysisViewProps } from '@/modules/metrics/analysis/SprtView'
import { ProfileSummary } from '@/modules/metrics/analysis/ProfileSummary'
import type { ProfileResult } from '@/modules/metrics/analysis/types'
import { shownNumber } from '@/modules/metrics/metricDrill'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Label } from '@/shared/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { useResource } from '@/shared/hooks/useResource'

export function ProfileView({ metric, read, initial = {} }: AnalysisViewProps) {
  const choices = metric.dims.filter((one) => !one.grain)
  const [dim, setDim] = useState(initial.dim ?? choices[0]?.name ?? '')
  const [value, setValue] = useState(initial.value ?? '')
  const values = useResource(
    () => (dim ? metricsApi.dims(metric.slug, dim) : Promise.resolve(null)),
    [metric.slug, dim],
  )
  const options = useMemo(
    () =>
      (values.data?.values ?? [])
        .filter((one) => one.value !== null)
        .map((one) => ({
          value: one.value as string,
          label: one.label,
          hint: `${shownNumber(one.count)}건`,
        })),
    [values.data],
  )
  // 요약할 기준의 거르기는 뺀다 — 그 값이 곧 요약할 값이다.
  const filters = Object.fromEntries(
    Object.entries(read.filters ?? {}).filter(([name]) => name !== dim),
  )
  const key = JSON.stringify([dim, value, filters, read.period_from, read.period_to])
  const result = useResource<ProfileResult | null>(
    () =>
      dim && value
        ? metricsApi.analysis<ProfileResult>(
            metric.slug,
            'profile',
            { dim, value },
            {
              ...read,
              filters,
            },
          )
        : Promise.resolve(null),
    [metric.slug, key],
  )
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="profile-dim">기준</Label>
          <Select
            value={dim}
            onValueChange={(next) => {
              setDim(next)
              setValue('')
            }}
          >
            <SelectTrigger id="profile-dim" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {choices.map((one) => (
                <SelectItem key={one.name} value={one.name}>
                  {one.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="profile-value">값</Label>
          <SearchablePicker
            id="profile-value"
            options={options}
            value={value}
            loading={values.loading}
            onChange={setValue}
            placeholder="값 고르기"
          />
        </div>
      </div>
      {!value && <p className="text-muted-foreground text-sm">요약할 값을 고릅니다.</p>}
      {result.error && <ErrorNotice error={result.error} />}
      {result.data && <ProfileSummary data={result.data} />}
    </div>
  )
}
