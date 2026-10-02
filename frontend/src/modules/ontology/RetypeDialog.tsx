/**
 * 종류 변경 — 속성 종류를 바꾸면서 **저장된 값도 새 종류로** 변환한다(ADR 0007).
 *
 * 계획을 먼저 본다: 몇 개가 변환되고, 무엇이 변환되지 않는지(값마다 건수 · 견본 · 까닭).
 * **변환할 수 없는 값이 하나라도 남아 있으면 적용하지 않는다** — 값마다 대체 값을 적거나 값 삭제를
 * 고른 뒤 계획을 다시 보면 적용 단추가 선다. 조용히 비우거나 짐작으로 맞추는 것은 없다.
 *
 * 인터페이스의 공통 속성이면 구현 타입 전부의 저장값을 한 번에 바꾼다.
 *
 * **글 ↔ 참조**(ADR 0009) — 이미 넣은 기록을 축에 잇는다. 글 → 참조는 값마다 일괄 입력과 같은
 * 이름 풀이(식별자 → 다른 이름 → 이름 → id)로 바꾸고, 참조 → 글은 상대의 식별자(없으면 이름)가
 * 된다. 값이 있는 객체가 많은 타입(기록)은 서버가 작업으로 돌린다 — 이 창은 진행률을 보이며
 * 기다리고, 계획을 본 뒤 그 작업을 적용한다.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Loader2 } from 'lucide-react'

import { jobsApi } from '@/modules/jobs/api'
import type { Job, JobProgress } from '@/modules/jobs/api'
import { ontologyApi } from '@/modules/ontology/api'
import type { DataType, PropertyDef, RetypeOut, RetypeRequest } from '@/modules/ontology/api'
import { DATA_TYPE_LABELS } from '@/modules/ontology/PropertyEditDialog'
import type { PropertyOwner } from '@/modules/ontology/PropertyEditDialog'
import { ApiError } from '@/shared/api/client'
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
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'

/** 종류 변경이 오가는 값의 종류 — 서버의 `conversion.SUPPORTED` 와 같다. */
export const RETYPE_KINDS: DataType[] = [
  'text',
  'text_long',
  'url',
  'number',
  'date',
  'datetime',
  'bool',
  'enum',
]

/** 객체 참조와 오가는 종류 — 서버의 `conversion.LINKABLE`. 숫자 · 날짜를 거치면 식별자 「007」
 *  이 「7」 이 된다. */
export const LINKABLE_KINDS: DataType[] = ['text', 'text_long', 'enum']

/** 이 종류에서 바꿀 수 있는 종류 — 없으면 종류 변경을 못 한다(파일). */
export function retypeChoices(from: DataType): DataType[] {
  if (from === 'object_ref') return LINKABLE_KINDS
  if (!RETYPE_KINDS.includes(from)) return []
  const linkable = LINKABLE_KINDS.includes(from) ? (['object_ref'] as DataType[]) : []
  return [...RETYPE_KINDS, ...linkable].filter((one) => one !== from)
}

interface Target {
  slug: string
  label: string
}

interface Props {
  owner: PropertyOwner
  property: PropertyDef
  /** 「객체 참조」 로 바꿀 때 가리킬 수 있는 타입 · 인터페이스. */
  types?: Target[]
  interfaces?: (Target & { implementers: string[] })[]
  onClose: () => void
  /** 적용됐다 — 속성 창을 닫고 정의를 다시 읽는다(창이 들고 있는 정의는 옛 종류다). */
  onDone: () => void
}

/** 「1, 2, 3」 → 목록. 빈 것은 뺀다. */
function listOf(text: string): string[] {
  return text
    .split(',')
    .map((one) => one.trim())
    .filter(Boolean)
}

function numberOf(text: string): number | null {
  if (text.trim() === '') return null
  const value = Number(text)
  return Number.isFinite(value) ? value : null
}

export function RetypeDialog({
  owner,
  property,
  types = [],
  interfaces = [],
  onClose,
  onDone,
}: Props) {
  const choices = retypeChoices(property.data_type)
  const [dataType, setDataType] = useState<DataType>(choices[0])
  const [refType, setRefType] = useState('')
  const [inverseLabel, setInverseLabel] = useState('')
  const [options, setOptions] = useState((property.enum_options ?? []).join(', '))
  const [decimals, setDecimals] = useState('')
  const [minValue, setMinValue] = useState('')
  const [maxValue, setMaxValue] = useState('')
  const [unit, setUnit] = useState(property.unit ?? '')
  /** {변환할 수 없는 값: 대체 값 | null(값 삭제)} — 적은 것만 보낸다. */
  const [mapping, setMapping] = useState<Record<string, string | null>>({})
  const [acceptCore, setAcceptCore] = useState(false)
  const [plan, setPlan] = useState<RetypeOut | null>(null)
  /** 계획이 작업으로 섰으면 그 작업 — 적용은 그 작업을 적용한다(지문이 지킨다). */
  const [planJob, setPlanJob] = useState<string | null>(null)
  const [progress, setProgress] = useState<JobProgress | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  /** 무엇이든 고치면 계획은 옛것이다 — 다시 보게 한다. */
  const stale = () => {
    setPlan(null)
    setPlanJob(null)
  }

  function body(apply: boolean): RetypeRequest {
    return {
      data_type: dataType,
      enum_options: dataType === 'enum' ? listOf(options) : null,
      decimals: dataType === 'number' ? numberOf(decimals) : null,
      min_value: dataType === 'number' ? numberOf(minValue) : null,
      max_value: dataType === 'number' ? numberOf(maxValue) : null,
      unit: unit.trim(),
      ref_type_slug: dataType === 'object_ref' ? refType || null : null,
      inverse_label:
        dataType === 'object_ref' && owner.kind === 'type' ? inverseLabel.trim() : null,
      mapping,
      accept_core: acceptCore,
      apply,
    }
  }

  /** 작업이 끝날 때까지 — 진행률을 보이며. 실패면 그 이유로 던진다. */
  async function follow(started: Job): Promise<RetypeOut> {
    const done = await jobsApi.waitFor(started.id, (job) => setProgress(job.progress))
    setProgress(null)
    if (done.status !== 'done' || !done.result) {
      throw new Error(done.error ?? '작업이 끝나지 않았습니다.')
    }
    return done.result as unknown as RetypeOut
  }

  async function run(apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      let result: RetypeOut
      if (apply && planJob) {
        result = await follow(await jobsApi.apply(planJob))
      } else {
        const interfaceOwned = owner.kind === 'interface'
        const call = interfaceOwned
          ? ontologyApi.retypeInterfaceProperty
          : ontologyApi.retypeProperty
        try {
          result = await call(owner.row.slug, property.key, body(apply))
        } catch (caught) {
          // 값이 있는 객체가 많다 — 요청 안에서 안 끝나 작업으로 계획한다(적용은 그 작업으로).
          if (!(caught instanceof ApiError && caught.code.endsWith('ONTOLOGY-0067'))) throw caught
          const job = interfaceOwned
            ? ontologyApi.retypeInterfacePropertyJob
            : ontologyApi.retypePropertyJob
          const started = await job(owner.row.slug, property.key, body(false))
          result = await follow(started)
          setPlanJob(started.id)
        }
      }
      setPlan(result)
      if (result.applied) onDone()
    } catch (caught) {
      setProgress(null)
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const moved = plan ? plan.types.reduce((sum, one) => sum + one.converted + one.cleared, 0) : 0
  const needsCore = Boolean(plan && plan.core_consumers.length > 0)
  const canApply =
    plan !== null && !plan.applied && plan.errors.length === 0 && (!needsCore || acceptCore)
  const ready =
    (dataType !== 'enum' || listOf(options).length > 0) &&
    (dataType !== 'object_ref' || Boolean(refType))

  function setOne(value: string, next: string | null | undefined) {
    setMapping((before) => {
      const copy = { ...before }
      if (next === undefined) delete copy[value]
      else copy[value] = next
      return copy
    })
    stale()
  }

  /** 고를 값으로 바꿀 때 — 변환할 수 없는 값(목록에 없는 값)을 고를 값에 더한다. */
  function addFailuresToOptions() {
    if (!plan) return
    const now = listOf(options)
    const more = plan.failures.map((one) => one.value).filter((one) => !now.includes(one))
    setOptions([...now, ...more].join(', '))
    stale()
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>「{property.label}」 종류 변경</DialogTitle>
          <DialogDescription>
            지금은 <b>{DATA_TYPE_LABELS[property.data_type]}</b>입니다. 저장된 값을 새 종류로
            변환합니다
            {owner.kind === 'interface' ? ' — 구현 타입 전부의 값을 한 번에 변환합니다' : ''}. 적용
            직전 정의를 스냅샷으로 남기고, 객체마다 이력에 바뀌기 전 값이 남습니다.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm">
          <div className="space-y-1.5">
            <Label htmlFor="retype-kind">새 종류</Label>
            <Select
              value={dataType}
              onValueChange={(next) => {
                setDataType(next as DataType)
                stale()
              }}
            >
              <SelectTrigger id="retype-kind" size="sm" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {choices.map((one) => (
                  <SelectItem key={one} value={one}>
                    {DATA_TYPE_LABELS[one]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {dataType === 'object_ref' && (
            <div className="space-y-1.5">
              <Label htmlFor="retype-ref">가리킬 타입</Label>
              <Select
                value={refType}
                onValueChange={(next) => {
                  setRefType(next)
                  stale()
                }}
              >
                <SelectTrigger id="retype-ref" size="sm" className="w-full">
                  <SelectValue placeholder="선택하세요" />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectLabel>타입</SelectLabel>
                    {types.map((one) => (
                      <SelectItem key={one.slug} value={one.slug}>
                        {one.label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                  {interfaces.length > 0 && (
                    <SelectGroup>
                      <SelectLabel>인터페이스 — 구현한 타입 중에서</SelectLabel>
                      {interfaces.map((one) => (
                        <SelectItem key={one.slug} value={one.slug}>
                          {one.label} (구현 타입 {one.implementers.length})
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  )}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">
                값마다 그 타입에서 식별자 → 다른 이름 → 이름 차례로 찾습니다 — 일괄 입력과 같은
                규칙입니다. 이름이 여럿에 맞거나 못 찾은 값은 대체 값(식별자 · 이름 · id)을 적거나
                값 삭제를 선택합니다.
              </p>
              {owner.kind === 'type' && (
                <>
                  <Label htmlFor="retype-inverse">상대 쪽에서 읽는 말 (선택)</Label>
                  <Input
                    id="retype-inverse"
                    value={inverseLabel}
                    placeholder="예: 시장 서비스"
                    onChange={(event) => {
                      setInverseLabel(event.target.value)
                      stale()
                    }}
                  />
                </>
              )}
            </div>
          )}
          {property.data_type === 'object_ref' && (
            <p className="text-muted-foreground text-xs">
              가리키던 객체의 <b>식별자</b>(없으면 이름)가 글로 남습니다 — 다시 객체 참조로 바꾸면
              같은 객체로 풀립니다.
            </p>
          )}

          {dataType === 'enum' && (
            <div className="space-y-1.5">
              <Label htmlFor="retype-options">선택할 값 (쉼표로)</Label>
              <Input
                id="retype-options"
                value={options}
                placeholder="한국, 미국, 일본"
                onChange={(event) => {
                  setOptions(event.target.value)
                  stale()
                }}
              />
            </div>
          )}

          {dataType === 'number' && (
            <div className="grid grid-cols-4 gap-2">
              <div className="space-y-1">
                <Label htmlFor="retype-min">최소</Label>
                <Input
                  id="retype-min"
                  inputMode="decimal"
                  value={minValue}
                  onChange={(event) => {
                    setMinValue(event.target.value)
                    stale()
                  }}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="retype-max">최대</Label>
                <Input
                  id="retype-max"
                  inputMode="decimal"
                  value={maxValue}
                  onChange={(event) => {
                    setMaxValue(event.target.value)
                    stale()
                  }}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="retype-decimals">소수 자릿수</Label>
                <Input
                  id="retype-decimals"
                  inputMode="numeric"
                  value={decimals}
                  onChange={(event) => {
                    setDecimals(event.target.value)
                    stale()
                  }}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="retype-unit">단위</Label>
                <Input
                  id="retype-unit"
                  value={unit}
                  onChange={(event) => {
                    setUnit(event.target.value)
                    stale()
                  }}
                />
              </div>
            </div>
          )}

          {plan && (
            <div className="space-y-3">
              <table className="w-full text-xs">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1 text-left font-normal">타입</th>
                    <th className="py-1 text-right font-normal">값 있음</th>
                    <th className="py-1 text-right font-normal">변환</th>
                    <th className="py-1 text-right font-normal">그대로</th>
                    <th className="py-1 text-right font-normal">비움</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.types.map((one) => (
                    <tr key={`${one.type_slug}.${one.key}`} className="border-t">
                      <td className="py-1">{one.type_label}</td>
                      <td className="py-1 text-right tabular-nums">{one.with_value}</td>
                      <td className="py-1 text-right tabular-nums">{one.converted}</td>
                      <td className="py-1 text-right tabular-nums">{one.unchanged}</td>
                      <td className="py-1 text-right tabular-nums">{one.cleared}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {plan.failures.length > 0 && (
                <div className="space-y-1.5">
                  <p className="font-medium">
                    변환할 수 없는 값 {plan.failures_total}종 — 값마다 대체 값을 적거나 값 삭제를
                    선택한 뒤 계획을 다시 보세요.
                  </p>
                  {dataType === 'enum' && (
                    <Button size="sm" variant="outline" onClick={addFailuresToOptions}>
                      이 값들을 선택할 값에 추가
                    </Button>
                  )}
                  <table className="w-full text-xs">
                    <thead className="text-muted-foreground">
                      <tr>
                        <th className="py-1 text-left font-normal">값</th>
                        <th className="py-1 text-right font-normal">건수</th>
                        <th className="py-1 text-left font-normal">대체 값</th>
                        <th className="py-1 text-left font-normal">값 삭제</th>
                      </tr>
                    </thead>
                    <tbody>
                      {plan.failures.map((one) => (
                        <tr key={one.value} className="border-t align-top">
                          <td className="py-1">
                            <span className="font-medium">{one.value}</span>
                            <span className="text-muted-foreground block">{one.reason}</span>
                            <span className="text-muted-foreground block">
                              {one.samples.map((sample, index) => (
                                <span key={`${sample.object_id ?? 'default'}-${index}`}>
                                  {index > 0 && ' · '}
                                  {sample.object_id ? (
                                    <Link
                                      to={`/o/${sample.type_slug}/${sample.object_id}`}
                                      className="underline"
                                    >
                                      {sample.label}
                                    </Link>
                                  ) : (
                                    sample.label
                                  )}
                                </span>
                              ))}
                            </span>
                          </td>
                          <td className="py-1 text-right tabular-nums">{one.count}</td>
                          <td className="py-1">
                            <Input
                              aria-label={`${one.value} 대체 값`}
                              className="h-7"
                              placeholder={dataType === 'object_ref' ? '식별자 · 이름 · id' : ''}
                              disabled={mapping[one.value] === null}
                              value={mapping[one.value] ?? ''}
                              onChange={(event) =>
                                setOne(one.value, event.target.value || undefined)
                              }
                            />
                          </td>
                          <td className="py-1">
                            <input
                              type="checkbox"
                              aria-label={`${one.value} 값 삭제`}
                              className="size-4"
                              checked={mapping[one.value] === null}
                              onChange={(event) =>
                                setOne(one.value, event.target.checked ? null : undefined)
                              }
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {plan.errors.length > 0 && (
                <ul className="text-destructive space-y-0.5">
                  {plan.errors.map((one) => (
                    <li key={one}>{one}</li>
                  ))}
                </ul>
              )}
              {plan.warnings.length > 0 && (
                <ul className="space-y-0.5 text-amber-700 dark:text-amber-400">
                  {plan.warnings.map((one) => (
                    <li key={one}>{one}</li>
                  ))}
                </ul>
              )}
              {needsCore && (
                <label className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4"
                    checked={acceptCore}
                    onChange={(event) => {
                      setAcceptCore(event.target.checked)
                      // 작업의 계획은 확인을 들고 간다 — 확인이 바뀌면 그 계획으로는 적용 못 한다.
                      if (planJob) stale()
                    }}
                  />
                  <span>
                    이 타입은 외부에 공개 중입니다 — 속성 종류는 수신 시스템과의 약속입니다. 수신
                    시스템({plan.core_consumers.join(', ')})에 통보했습니다.
                  </span>
                </label>
              )}
            </div>
          )}
          {progress && (
            <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
              <Loader2 className="size-3.5 animate-spin" />
              값이 많아 작업으로 돕니다 — {progress.stage || '대기'}{' '}
              {progress.total > 0 &&
                `${progress.done.toLocaleString()} / ${progress.total.toLocaleString()}`}
            </p>
          )}
          {error && <ErrorNotice error={error} />}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            취소
          </Button>
          {!canApply ? (
            <Button disabled={!ready || busy} onClick={() => void run(false)}>
              {busy && <Loader2 className="mr-1 size-3.5 animate-spin" />}
              계획 보기
            </Button>
          ) : (
            <Button disabled={busy} onClick={() => void run(true)}>
              {busy ? (
                <Loader2 className="mr-1 size-3.5 animate-spin" />
              ) : (
                <ArrowRight className="mr-1 size-3.5" />
              )}
              종류 변경 — 저장값 {moved}개
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
