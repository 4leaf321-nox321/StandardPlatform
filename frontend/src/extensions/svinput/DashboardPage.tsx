/**
 * 집계 — 결과요약 페이지를 **자동 집계로 대체**한다. 단계 × 판정(PASS/FAIL) 막대 · 일정 상태 건수.
 * 숫자는 코어 `summary`(목록과 같은 거르기 위에서 센다)에서 오고, 그림은 `shared/charts` 가 그린다.
 */

import { useMemo } from 'react'

import { objectApi } from '@/modules/objects/api'
import { Chart } from '@/shared/charts'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { useResource } from '@/shared/hooks/useResource'

import { NotReady } from './NotReady'
import { useSetup } from './useSetup'

export default function DashboardPage() {
  const setup = useSetup()
  const stageType = setup.typeOf('stage_result')
  const stageKey = setup.fieldOf('stage_result', 'stage')
  const resultKey = setup.fieldOf('stage_result', 'result')
  const issueType = setup.typeOf('issue')
  const statusKey = setup.fieldOf('issue', 'status')

  const stageSlug = stageType?.slug ?? null
  const verdicts = useResource(
    () =>
      stageSlug && stageKey && resultKey
        ? objectApi.summary(
            stageSlug,
            {},
            { groupBy: `properties.${stageKey}`, splitBy: `properties.${resultKey}`, order: 'key' },
          )
        : Promise.resolve(null),
    [stageSlug, stageKey, resultKey],
  )
  const issueSlug = issueType?.slug ?? null
  const statuses = useResource(
    () =>
      issueSlug && statusKey
        ? objectApi.summary(issueSlug, {}, { groupBy: `properties.${statusKey}` })
        : Promise.resolve(null),
    [issueSlug, statusKey],
  )

  const verdictRows = useMemo(() => {
    const got = verdicts.data
    if (!got) return { data: [] as Record<string, unknown>[], splits: [] as string[] }
    return {
      splits: got.splits,
      data: got.buckets.map((bucket) => ({
        stage: bucket.label,
        ...Object.fromEntries(got.splits.map((split) => [split, 0])),
        ...Object.fromEntries(bucket.parts.map((part) => [part.label, part.count])),
      })),
    }
  }, [verdicts.data])

  if (setup.error) return <ErrorNotice error={setup.error} />
  if (!setup.loading && !setup.ready) return <NotReady status={setup.status} />

  return (
    <div className="space-y-6">
      <PageHeader
        title="자주검증 집계"
        description="문제점 · 단계별 판정에서 바로 센 값입니다 — 결과요약 페이지를 손으로 만들지 않습니다."
      />
      <section className="space-y-2">
        <h2 className="text-sm font-semibold">단계 × 판정</h2>
        {!stageType || !stageKey || !resultKey ? (
          <EmptyState
            title="단계별 판정 역할이 설정되지 않았습니다"
            hint="설정에서 단계별 판정 타입과 단계 · 판정 칸을 고르면 그림이 섭니다."
          />
        ) : verdicts.error ? (
          <ErrorNotice error={verdicts.error} />
        ) : (
          <Chart
            kind="bar"
            stacked
            data={verdictRows.data}
            x="stage"
            series={verdictRows.splits.map((split) => ({ key: split, label: split }))}
            emptyText="아직 단계별 판정 기록이 없습니다."
          />
        )}
      </section>
      <section className="space-y-2">
        <h2 className="text-sm font-semibold">문제점 일정 상태</h2>
        {!issueType || !statusKey ? (
          <EmptyState
            title="일정 상태 칸이 설정되지 않았습니다"
            hint="설정에서 문제점의 일정 상태 칸을 고르면 그림이 섭니다."
          />
        ) : statuses.error ? (
          <ErrorNotice error={statuses.error} />
        ) : (
          <Chart
            kind="pie"
            data={(statuses.data?.buckets ?? []).map((bucket) => ({
              status: bucket.label,
              count: bucket.count,
            }))}
            x="status"
            series={[{ key: 'count', label: '문제점' }]}
            emptyText="아직 문제점 기록이 없습니다."
          />
        )}
      </section>
    </div>
  )
}
