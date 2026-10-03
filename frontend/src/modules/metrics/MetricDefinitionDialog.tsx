/**
 * 지표 정의 — **계획을 보고 저장한다.**
 *
 * 원천 기록 타입 · 집계 · 시간 칸 · 코호트 칸 · 기준 · 거르기 · 분모 · 닫힘 일수 · 주기.
 * 「계획 보기」 가 정의를 실제로 지어 보고 오류 전부 · 경고 · 어림한 셀 수를 말한다 — 저장은
 * 계획이 통과한 뒤에만, 그리고 저장하자마자 센다. 용어는 `docs/용어.md` 의 것만 쓴다.
 */

import { useEffect, useMemo, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'

import { jobsApi } from '@/modules/jobs/api'
import { metricsApi } from '@/modules/metrics/api'
import type { Grain, Metric, MetricPlan, MetricSpec } from '@/modules/metrics/api'
import { GRAIN_LABELS, MEASURE_LABELS, shownNumber } from '@/modules/metrics/metricDrill'
import { ConditionBar } from '@/modules/objects/ConditionBar'
import { objectApi } from '@/modules/objects/api'
import type { Condition, ConditionOp, LinkedField } from '@/modules/objects/api'
import { ontologyApi } from '@/modules/ontology/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { Textarea } from '@/shared/components/ui/textarea'
import { useResource } from '@/shared/hooks/useResource'

const NONE = '__none__'
const GRAINS = ['day', 'week', 'month', 'quarter', 'year'] as const
const MEASURES = ['count', 'sum', 'avg', 'min', 'max'] as const
/** 자기 객체의 고정 축 — 서버의 `FIXED_FIELDS` 와 같다. */
const FIXED: [string, string][] = [
  ['label', '이름'],
  ['key', '식별자'],
  ['status', '상태'],
  ['workspace', '소유 부서'],
  ['created_year', '만든 해'],
]

interface DimRow {
  name: string
  address: string
  grain: string
}

interface Props {
  existing: Metric | null
  onClose: () => void
  onSaved: (metric: Metric) => void
}

function slugOf(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 32)
}

export function MetricDefinitionDialog({ existing, onClose, onSaved }: Props) {
  const schema = useResource(() => ontologyApi.schema(), [])
  const metrics = useResource(() => metricsApi.list(), [])
  const spec0 = existing?.spec
  const [slug, setSlug] = useState(existing?.slug ?? '')
  // 이름에서 slug 를 짓되, 사람이 slug 를 손댄 뒤에는 따라가지 않는다.
  const [slugTouched, setSlugTouched] = useState(Boolean(existing))
  const [label, setLabel] = useState(existing?.label ?? '')
  const [description, setDescription] = useState(existing?.description ?? '')
  const [source, setSource] = useState(existing?.source_type_slug ?? '')
  const [measure, setMeasure] = useState<string>(spec0?.measure ?? 'count')
  const [measureField, setMeasureField] = useState(spec0?.measure_field ?? '')
  const [timeAddress, setTimeAddress] = useState(spec0?.time?.address ?? '')
  const [grain, setGrain] = useState<string>(spec0?.time?.grain ?? 'month')
  const [cohortAddress, setCohortAddress] = useState(spec0?.cohort?.address ?? '')
  const [dims, setDims] = useState<DimRow[]>(
    (spec0?.dimensions ?? []).map((one) => ({
      name: one.name,
      address: one.address,
      grain: one.grain ?? '',
    })),
  )
  const [filters, setFilters] = useState<Condition[]>(
    (spec0?.filters ?? []).map((one) => ({
      field: one.field,
      op: one.op as ConditionOp,
      value: one.value,
    })),
  )
  const [denMetric, setDenMetric] = useState(spec0?.denominator?.metric ?? '')
  const [denOn, setDenOn] = useState<string[]>(spec0?.denominator?.on ?? [])
  const [denTime, setDenTime] = useState<string>(
    spec0?.denominator ? (spec0.denominator.time ?? NONE) : 'period',
  )
  const [denPer, setDenPer] = useState(String(spec0?.denominator?.per ?? 100))
  const [settleDays, setSettleDays] = useState(String(spec0?.settle_days ?? 0))
  const [visitKey, setVisitKey] = useState(spec0?.visits?.key ?? '')
  const [visitDays, setVisitDays] = useState(String(spec0?.visits?.within_days ?? 90))
  const [intervalHours, setIntervalHours] = useState(String(existing?.interval_hours ?? 24))
  const [plan, setPlan] = useState<MetricPlan | null>(null)
  const [busy, setBusy] = useState<'plan' | 'save' | null>(null)
  const [error, setError] = useState<Error | null>(null)

  const types = useMemo(
    () =>
      (schema.data?.types ?? []).filter(
        (one) => one.kind_class !== 'system' && one.is_active !== false,
      ),
    [schema.data],
  )
  // 원천은 기록 타입이 보통이다 — 처음 열 때 기록 타입을 먼저 고른다.
  useEffect(() => {
    if (!source && types.length > 0) {
      const log = types.find((one) => one.usage === 'log')
      setSource((log ?? types[0]).slug)
    }
  }, [source, types])
  const sourceType = types.find((one) => one.slug === source)
  const defs = useMemo(() => sourceType?.properties ?? [], [sourceType])
  const dateDefs = defs.filter(
    (one) => (one.data_type === 'date' || one.data_type === 'datetime') && !one.multi,
  )
  const numberDefs = defs.filter((one) => one.data_type === 'number' && !one.multi)
  // 시리얼 — 값 하나로 같은 제품을 가리키는 칸.
  const serialDefs = defs.filter(
    (one) => ['text', 'number', 'enum'].includes(one.data_type) && !one.multi,
  )
  const linked = useResource<LinkedField[]>(
    () => (source ? objectApi.fields(source) : Promise.resolve([])),
    [source],
  )
  // 시간 칸이 비었으면 첫 날짜 칸 — 정의의 대부분이 시간을 갖는다.
  useEffect(() => {
    if (!timeAddress && dateDefs.length > 0 && !existing) {
      setTimeAddress(`properties.${dateDefs[0].key}`)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dateDefs.length, existing])

  const addressOptions = useMemo(() => {
    const own = defs
      .filter((one) => !['text_long', 'file'].includes(one.data_type))
      .map((one) => ({ value: `properties.${one.key}`, label: one.label }))
    const fixed = FIXED.map(([value, text]) => ({ value, label: text }))
    const beyond = (linked.data ?? []).map((one) => ({ value: one.field, label: one.label }))
    const visits = visitKey
      ? [
          { value: 'visit.number', label: '방문 차례(1 · 2 · 3 · 4+)' },
          { value: 'visit.repeat', label: `${visitDays}일 안 재방문(예 · 아니오 · 아직 열림)` },
        ]
      : []
    return [...own, ...fixed, ...beyond, ...visits]
  }, [defs, linked.data, visitKey, visitDays])

  const denominatorCandidates = (metrics.data ?? []).filter(
    (one) => one.slug !== slug && !one.spec.denominator,
  )
  const denominator = denominatorCandidates.find((one) => one.slug === denMetric)

  function buildSpec(): MetricSpec {
    return {
      measure: measure as MetricSpec['measure'],
      measure_field: measure === 'count' ? null : measureField || null,
      time: timeAddress ? { address: timeAddress, grain: grain as Grain } : null,
      cohort:
        cohortAddress && timeAddress ? { address: cohortAddress, grain: grain as Grain } : null,
      dimensions: dims
        .filter((one) => one.name.trim() && one.address.trim())
        .map((one) => ({
          name: one.name.trim(),
          address: one.address.trim(),
          grain: one.grain ? (one.grain as Grain) : null,
        })),
      filters: filters.map((one) => ({ field: one.field, op: one.op, value: one.value })),
      denominator: denMetric
        ? {
            metric: denMetric,
            on: denOn.filter((name) => dims.some((dim) => dim.name === name)),
            time: denTime === NONE ? null : (denTime as 'period' | 'cohort'),
            per: Number(denPer) || 1,
          }
        : null,
      settle_days: Number(settleDays) || 0,
      visits:
        visitKey && timeAddress
          ? { key: visitKey, within_days: Number(visitDays) || 90 }
          : null,
    }
  }

  async function seePlan() {
    setError(null)
    setBusy('plan')
    try {
      setPlan(
        await metricsApi.plan({ slug: slug || null, source_type_slug: source, spec: buildSpec() }),
      )
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(null)
    }
  }

  async function save() {
    setError(null)
    setBusy('save')
    try {
      const spec = buildSpec()
      const saved = existing
        ? await metricsApi.update(
            existing.slug,
            { label, description, spec, interval_hours: Number(intervalHours) || 0 },
            true,
          )
        : await metricsApi.create(
            {
              slug,
              label,
              description,
              source_type_slug: source,
              spec,
              interval_hours: Number(intervalHours) || 0,
            },
            true,
          )
      if (saved.job) await jobsApi.waitFor(saved.job.id)
      onSaved(saved.metric)
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(null)
    }
  }

  const invalidate = () => setPlan(null)

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{existing ? '지표 수정' : '새 지표'}</DialogTitle>
          <DialogDescription>
            정의 하나가 계산 · 저장 · 조회 · 건 보기를 만듭니다. 「계획 보기」 로 오류와 어림한 셀
            수를 본 뒤 저장합니다.
          </DialogDescription>
        </DialogHeader>
        {(schema.error || linked.error) && <ErrorNotice error={schema.error ?? linked.error} />}
        {error && <ErrorNotice error={error} />}

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="metric-label">이름</Label>
            <Input
              id="metric-label"
              value={label}
              onChange={(event) => {
                setLabel(event.target.value)
                if (!slugTouched) setSlug(slugOf(event.target.value))
              }}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="metric-slug">slug</Label>
            <Input
              id="metric-slug"
              value={slug}
              disabled={Boolean(existing)}
              onChange={(event) => {
                setSlug(event.target.value)
                setSlugTouched(true)
              }}
              placeholder="소문자 · 숫자 · 밑줄"
            />
          </div>
          <div className="space-y-1 md:col-span-2">
            <Label htmlFor="metric-description">설명</Label>
            <Textarea
              id="metric-description"
              rows={2}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="metric-source">원천 기록 타입</Label>
            <Select
              value={source}
              disabled={Boolean(existing)}
              onValueChange={(next) => {
                setSource(next)
                setTimeAddress('')
                setCohortAddress('')
                setDims([])
                setFilters([])
                invalidate()
              }}
            >
              <SelectTrigger id="metric-source">
                <SelectValue placeholder="타입" />
              </SelectTrigger>
              <SelectContent>
                {types.map((one) => (
                  <SelectItem key={one.slug} value={one.slug}>
                    {one.label}
                    {one.usage === 'log' ? ' (기록)' : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="metric-measure">집계</Label>
            <div className="flex gap-2">
              <Select
                value={measure}
                onValueChange={(next) => {
                  setMeasure(next)
                  invalidate()
                }}
              >
                <SelectTrigger id="metric-measure" className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MEASURES.map((one) => (
                    <SelectItem key={one} value={one}>
                      {MEASURE_LABELS[one]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {measure !== 'count' && (
                <Select
                  value={measureField || NONE}
                  onValueChange={(next) => {
                    setMeasureField(next === NONE ? '' : next)
                    invalidate()
                  }}
                >
                  <SelectTrigger aria-label="숫자 칸">
                    <SelectValue placeholder="숫자 칸" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>숫자 칸 고르기</SelectItem>
                    {numberDefs.map((one) => (
                      <SelectItem key={one.key} value={`properties.${one.key}`}>
                        {one.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="metric-time">시간 칸 · 기간 단위</Label>
            <div className="flex gap-2">
              <Select
                value={timeAddress || NONE}
                onValueChange={(next) => {
                  setTimeAddress(next === NONE ? '' : next)
                  if (next === NONE) setCohortAddress('')
                  invalidate()
                }}
              >
                <SelectTrigger id="metric-time">
                  <SelectValue placeholder="날짜 칸" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>시간 없음</SelectItem>
                  {dateDefs.map((one) => (
                    <SelectItem key={one.key} value={`properties.${one.key}`}>
                      {one.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={grain}
                onValueChange={(next) => {
                  setGrain(next)
                  invalidate()
                }}
              >
                <SelectTrigger aria-label="기간 단위" className="w-24">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {GRAINS.map((one) => (
                    <SelectItem key={one} value={one}>
                      {GRAIN_LABELS[one]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="metric-cohort">코호트 칸 (선택 · 같은 단위)</Label>
            <Select
              value={cohortAddress || NONE}
              disabled={!timeAddress}
              onValueChange={(next) => {
                setCohortAddress(next === NONE ? '' : next)
                invalidate()
              }}
            >
              <SelectTrigger id="metric-cohort">
                <SelectValue placeholder="없음" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>없음</SelectItem>
                {dateDefs
                  .filter((one) => `properties.${one.key}` !== timeAddress)
                  .map((one) => (
                    <SelectItem key={one.key} value={`properties.${one.key}`}>
                      {one.label}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">기준 (여섯까지)</span>
            <Button
              size="sm"
              variant="outline"
              disabled={dims.length >= 6}
              onClick={() => {
                setDims([...dims, { name: '', address: '', grain: '' }])
                invalidate()
              }}
            >
              <Plus className="mr-1 size-3.5" />
              기준 추가
            </Button>
          </div>
          <datalist id="metric-address-options">
            {addressOptions.map((one) => (
              <option key={one.value} value={one.value}>
                {one.label}
              </option>
            ))}
          </datalist>
          {dims.map((dim, index) => (
            <div key={index} className="flex flex-wrap items-center gap-2">
              <Input
                aria-label={`기준 ${index + 1} 이름`}
                className="w-36"
                placeholder="이름 (영문)"
                value={dim.name}
                onChange={(event) => {
                  const next = [...dims]
                  next[index] = { ...dim, name: event.target.value }
                  setDims(next)
                  invalidate()
                }}
              />
              <Input
                aria-label={`기준 ${index + 1} 주소`}
                className="flex-1"
                list="metric-address-options"
                placeholder="주소 — properties.<칸> · ref.<칸>.<칸> · out.<관계>"
                value={dim.address}
                onChange={(event) => {
                  const next = [...dims]
                  next[index] = { ...dim, address: event.target.value }
                  setDims(next)
                  invalidate()
                }}
              />
              <Select
                value={dim.grain || NONE}
                onValueChange={(value) => {
                  const next = [...dims]
                  next[index] = { ...dim, grain: value === NONE ? '' : value }
                  setDims(next)
                  invalidate()
                }}
              >
                <SelectTrigger aria-label={`기준 ${index + 1} 기간 단위`} className="w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>단위 없음</SelectItem>
                  {GRAINS.map((one) => (
                    <SelectItem key={one} value={one}>
                      {GRAIN_LABELS[one]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`기준 ${index + 1} 지우기`}
                onClick={() => {
                  setDims(dims.filter((_, at) => at !== index))
                  invalidate()
                }}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          ))}
        </section>

        <section className="space-y-2">
          <span className="text-sm font-medium">거르기</span>
          <ConditionBar
            defs={defs}
            linked={linked.data ?? undefined}
            conditions={filters}
            onChange={(next) => {
              setFilters(next)
              invalidate()
            }}
          />
        </section>

        <section className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="metric-denominator">분모 지표 (비율)</Label>
            <Select
              value={denMetric || NONE}
              onValueChange={(next) => {
                setDenMetric(next === NONE ? '' : next)
                invalidate()
              }}
            >
              <SelectTrigger id="metric-denominator">
                <SelectValue placeholder="없음" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>없음</SelectItem>
                {denominatorCandidates.map((one) => (
                  <SelectItem key={one.slug} value={one.slug}>
                    {one.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {denominator && (
            <>
              <div className="space-y-1">
                <Label htmlFor="metric-den-time">분모의 시간축</Label>
                <div className="flex gap-2">
                  <Select
                    value={denTime}
                    onValueChange={(next) => {
                      setDenTime(next)
                      invalidate()
                    }}
                  >
                    <SelectTrigger id="metric-den-time">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="period">기간끼리</SelectItem>
                      <SelectItem value="cohort" disabled={!cohortAddress}>
                        분모의 기간 = 분자의 코호트
                      </SelectItem>
                      <SelectItem value={NONE}>기간 없이 전부 합</SelectItem>
                    </SelectContent>
                  </Select>
                  <Input
                    aria-label="곱할 수"
                    className="w-24"
                    type="number"
                    value={denPer}
                    onChange={(event) => {
                      setDenPer(event.target.value)
                      invalidate()
                    }}
                  />
                </div>
              </div>
              <div className="space-y-1 md:col-span-2">
                <span className="text-sm">
                  짝지을 기준 — 분모 「{denominator.label}」 의 기준:{' '}
                  {denominator.dims.map((one) => one.name).join(', ') || '없음'}
                </span>
                <div className="flex flex-wrap gap-1">
                  {dims
                    .filter((one) => one.name.trim())
                    .map((one) => {
                      const on = denOn.includes(one.name)
                      return (
                        <Button
                          key={one.name}
                          size="sm"
                          variant={on ? 'default' : 'outline'}
                          aria-pressed={on}
                          onClick={() => {
                            setDenOn(
                              on ? denOn.filter((name) => name !== one.name) : [...denOn, one.name],
                            )
                            invalidate()
                          }}
                        >
                          {one.name}
                        </Button>
                      )
                    })}
                </div>
              </div>
            </>
          )}
          <div className="space-y-1">
            <Label htmlFor="metric-visit-key">방문 — 시리얼 칸 (선택)</Label>
            <Select
              value={visitKey || NONE}
              disabled={!timeAddress}
              onValueChange={(next) => {
                setVisitKey(next === NONE ? '' : next)
                invalidate()
              }}
            >
              <SelectTrigger id="metric-visit-key">
                <SelectValue placeholder="없음" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>없음</SelectItem>
                {serialDefs.map((one) => (
                  <SelectItem key={one.key} value={`properties.${one.key}`}>
                    {one.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {visitKey && (
            <div className="space-y-1">
              <Label htmlFor="metric-visit-days">재방문 일수</Label>
              <Input
                id="metric-visit-days"
                type="number"
                min={1}
                value={visitDays}
                onChange={(event) => {
                  setVisitDays(event.target.value)
                  invalidate()
                }}
              />
            </div>
          )}
          <div className="space-y-1">
            <Label htmlFor="metric-settle">닫힘 일수</Label>
            <Input
              id="metric-settle"
              type="number"
              value={settleDays}
              onChange={(event) => setSettleDays(event.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="metric-interval">주기 (시간, 0 은 손으로만)</Label>
            <Input
              id="metric-interval"
              type="number"
              value={intervalHours}
              onChange={(event) => setIntervalHours(event.target.value)}
            />
          </div>
        </section>

        {plan && (
          <section
            className={`space-y-1 rounded-md border p-3 text-sm ${plan.ok ? '' : 'border-destructive'}`}
            aria-label="계획"
          >
            {plan.errors.map((one) => (
              <p key={one} className="text-destructive">
                {one}
              </p>
            ))}
            {plan.warnings.map((one) => (
              <p key={one} className="text-amber-700 dark:text-amber-400">
                {one}
              </p>
            ))}
            {plan.ok && (
              <p>
                거르기를 통과한 기록 {shownNumber(plan.rows)}건 · 어림한 셀{' '}
                {shownNumber(plan.estimated_cells)}개
                {plan.period_from && ` · 기간 ${plan.period_from} ~ ${plan.period_to}`}
                {plan.overlap && ' · 겹침'}
              </p>
            )}
            {plan.dims.length > 0 && (
              <p className="text-muted-foreground text-xs">
                기준:{' '}
                {plan.dims
                  .map(
                    (one) =>
                      `${one.name} = ${one.label}(${one.kind}${one.multi ? ', 여러 값' : ''}${
                        one.distinct !== null && one.distinct !== undefined
                          ? `, ${shownNumber(one.distinct)}가지`
                          : ''
                      })`,
                  )
                  .join(' · ')}
              </p>
            )}
          </section>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            닫기
          </Button>
          <Button variant="outline" disabled={busy !== null || !source} onClick={seePlan}>
            계획 보기
          </Button>
          <Button disabled={busy !== null || !plan?.ok || !label || !slug} onClick={save}>
            {busy === 'save' ? '저장하고 세는 중…' : '저장하고 계산'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
