/**
 * 연계 하나의 평가 — 축 다섯을 매기는 판.
 *
 * **축 종류마다 위젯이 다르다.** 넷을 하나로 뭉치면(「수준 하나 고르기」) 가상검증률이 값과
 * 수준으로 갈려 둘이 되고, 자동화는 선후 없는 항목에 억지 서열이 생긴다.
 *
 *   수치형(가상검증률)      숫자 + 문턱이 정한 수준을 배지로 보여 준다(고를 수 없다)
 *   수준 선택(적용 범위)     라디오
 *   선택형(자동화·시험 대체)  체크박스 — 서열은 켠 개수
 *   매트릭스(모델링 수준)    바탕 토글(형상 · 거동) + 불량 유형마다 시험 · 시장 재현
 *                           — **수준은 셈이 접는다**(사람이 고르지 않는다)
 *
 * ⚠️ **근거(글)는 필수다.** 수준만 남은 평가는 다음 사람이 확인할 방법이 없다. 등급 · 자료
 *    칸을 두었다가 걷었다(2026-09-24) — 칸이 늘수록 채우는 사람이 줄고, 안 채운 칸은
 *    「모름」 과 구별되지 않는다. 서버가 같은 규칙으로 한 번 더 막는다.
 */

import { Check, History } from 'lucide-react'
import { useState } from 'react'

import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Textarea } from '@/shared/components/ui/textarea'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

import { dtApi, type Assessment, type AssessmentBody, type AxisDef, type Defs, type Pair } from './api'

interface Props {
  pair: Pair
  defs: Defs
  /** 이 시험 항목의 불량 유형 — 모델링 수준의 셈 기준이다. */
  defectTypes: string[]
  /** 저장이 끝나면 부른다 — 평가 완료율이 바뀐다. */
  onSaved?: () => void
}

/** 수치형 축의 수준 — **화면도 서버와 같은 문턱으로 읽는다**(보여 주기만 한다). */
function rungForValue(value: number | null, defs: Defs): string | null {
  if (value === null || Number.isNaN(value)) return null
  let chosen: string | null = null
  for (const step of defs.accuracy_thresholds) if (value >= step.min) chosen = step.rung
  return chosen
}

function rungLabel(axis: AxisDef, key: string | null): string {
  if (!key) return '미평가'
  return axis.rungs.find((one) => one.key === key)?.label ?? key
}

export function AssessmentPanel({ pair, defs, defectTypes, onSaved }: Props) {
  const rows = useResource(() => dtApi.assessments(pair.id), [pair.id])
  const past = useResource(() => dtApi.history(pair.id), [pair.id])
  const [open, setOpen] = useState<string | null>(null)

  const saved = new Map((rows.data ?? []).map((one) => [one.axis, one]))

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-semibold">
          {pair.subject_label} · {pair.agent_label}
        </h2>
        <p className="text-muted-foreground text-sm">
          축마다 수준과 근거를 적습니다. 가상검증률의 수준은 값이 정합니다.
        </p>
      </div>

      <ErrorNotice error={rows.error} />

      <ul className="space-y-2">
        {defs.axes.map((axis) => {
          const one = saved.get(axis.key)
          return (
            <li key={axis.key} className="rounded-md border">
              <button
                type="button"
                className="hover:bg-muted/50 flex w-full items-center gap-2 p-3 text-left"
                onClick={() => setOpen(open === axis.key ? null : axis.key)}
              >
                <span className="text-sm font-medium">{axis.label}</span>
                {one ? (
                  <Badge variant="secondary">{summary(axis, one, defs)}</Badge>
                ) : (
                  <Badge variant="outline">미평가</Badge>
                )}
                {one && (
                  <span className="text-muted-foreground ml-auto text-xs">
                    {one.assessed_by_label} · {shownDateTime(one.assessed_at)}
                  </span>
                )}
              </button>
              {open === axis.key && (
                <AxisForm
                  axis={axis}
                  defs={defs}
                  current={one}
                  pairId={pair.id}
                  defectTypes={defectTypes}
                  onDone={() => {
                    rows.reload()
                    past.reload()
                    onSaved?.()
                  }}
                />
              )}
            </li>
          )
        })}
      </ul>

      <section className="space-y-2">
        <h3 className="flex items-center gap-1 text-sm font-semibold">
          <History className="size-4" /> 변경 이력
        </h3>
        {(past.data ?? []).length === 0 ? (
          <p className="text-muted-foreground text-sm">아직 기록이 없습니다.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {(past.data ?? []).map((row, index) => (
              <li key={index} className="flex flex-wrap items-baseline gap-2">
                <span className="text-muted-foreground text-xs">
                  {shownDateTime(row.changed_at)}
                </span>
                <b>{row.axis_label}</b>
                <span className="text-muted-foreground">
                  {String(row.snapshot.note ?? '')}
                </span>
                <span className="text-muted-foreground ml-auto text-xs">
                  {row.changed_by_label}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

/** 접힌 줄에 한눈에 보일 한 마디. */
function summary(axis: AxisDef, one: Assessment, defs: Defs): string {
  if (axis.kind === 'value') {
    return `${one.value ?? '—'}${axis.unit ?? ''} · ${rungLabel(axis, one.rung)}`
  }
  if (axis.kind === 'rung') return rungLabel(axis, one.rung)
  if (axis.kind === 'set') {
    return one.rungs.map((key) => rungLabel(axis, key)).join(' · ') || '미평가'
  }
  void defs
  const marked = Object.keys(one.defects).length
  return `${rungLabel(axis, one.rung)}${marked ? ` · 불량 ${marked}종` : ''}`
}

/** 오늘의 연월 — 재현을 표시한 시점. 원본도 연월까지만 둔다. */
function thisMonth(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

function AxisForm({
  axis,
  defs,
  current,
  pairId,
  defectTypes,
  onDone,
}: {
  axis: AxisDef
  defs: Defs
  current?: Assessment
  pairId: string
  defectTypes: string[]
  onDone: () => void
}) {
  const [value, setValue] = useState<string>(current?.value?.toString() ?? '')
  const [rung, setRung] = useState<string | null>(current?.rung ?? null)
  const [rungs, setRungs] = useState<string[]>(current?.rungs ?? [])
  const [defects, setDefects] = useState<Record<string, Record<string, string>>>(
    current?.defects ?? {},
  )
  const [note, setNote] = useState(current?.note ?? '')
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<Error | null>(null)

  // 「수동」 처럼 아무것도 안 켠 상태를 뜻하는 칸은 목록에서 뺀다(정의의 `hide_empty`).
  const options = axis.hide_empty ? axis.rungs.slice(1) : axis.rungs

  async function save() {
    setBusy(true)
    setFailed(null)
    try {
      const body: AssessmentBody = {
        note,
        ...(axis.kind === 'value' ? { value: value === '' ? null : Number(value) } : {}),
        ...(axis.kind === 'rung' ? { rung } : {}),
        ...(axis.kind === 'set' ? { rungs } : {}),
        ...(axis.kind === 'matrix' ? { rungs, defects } : {}),
      }
      await dtApi.save(pairId, axis.key, body)
      onDone()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3 border-t p-3">
      {axis.question && <p className="text-muted-foreground text-sm">{axis.question}</p>}

      {axis.kind === 'value' && (
        <div className="flex items-end gap-3">
          <div className="space-y-1">
            <span className="text-muted-foreground text-xs">값 ({axis.unit})</span>
            <Input
              type="number"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              className="w-28"
            />
          </div>
          <div className="space-y-1">
            <span className="text-muted-foreground text-xs">수준</span>
            <p className="text-sm">
              <Badge variant="secondary">
                {rungLabel(axis, rungForValue(value === '' ? null : Number(value), defs))}
              </Badge>
              <span className="text-muted-foreground ml-2 text-xs">
                값이 문턱을 넘으면 올라갑니다 — 직접 고르지 않습니다.
              </span>
            </p>
          </div>
        </div>
      )}

      {axis.kind === 'rung' && (
        <div className="space-y-1">
          {options.map((one) => (
            <label key={one.key} className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                className="mt-0.5"
                checked={rung === one.key}
                onChange={() => setRung(one.key)}
              />
              <span>
                {one.label}
                {one.description && (
                  <span className="text-muted-foreground ml-1 text-xs">{one.description}</span>
                )}
              </span>
            </label>
          ))}
        </div>
      )}

      {axis.kind === 'set' && (
        <div className="space-y-1">
          {options.map((one) => (
            <label key={one.key} className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={rungs.includes(one.key)}
                onChange={(event) =>
                  setRungs((was) =>
                    event.target.checked
                      ? [...was, one.key]
                      : was.filter((key) => key !== one.key),
                  )
                }
              />
              <span>
                {one.label}
                {one.description && (
                  <span className="text-muted-foreground ml-1 text-xs">{one.description}</span>
                )}
              </span>
            </label>
          ))}
          <p className="text-muted-foreground text-xs">
            선후가 없는 항목입니다 — 서열은 켠 개수로 읽습니다.
          </p>
        </div>
      )}

      {axis.kind === 'matrix' && (
        <div className="space-y-3">
          <div className="space-y-1">
            {(axis.base ?? []).map((one) => (
              <label key={one.key} className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={rungs.includes(one.key)}
                  onChange={(event) =>
                    setRungs((was) =>
                      event.target.checked
                        ? [...was, one.key]
                        : was.filter((key) => key !== one.key),
                    )
                  }
                />
                <span>
                  {one.label}
                  {one.description && (
                    <span className="text-muted-foreground ml-1 text-xs">{one.description}</span>
                  )}
                </span>
              </label>
            ))}
          </div>

          {/* **불량 유형은 시험 항목이 든 목록이 기준이다.** 여기 비어 있으면 그 시험 항목에
              불량 유형이 안 적힌 것이다 — 기준 정보 화면에서 먼저 적는다. */}
          {defectTypes.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              이 시험 항목에 불량 유형이 없습니다 — 기준 정보 화면에서 먼저 입력합니다.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground text-xs">
                  <th className="py-1 text-left font-medium">불량 유형</th>
                  {(axis.columns ?? []).map((col) => (
                    <th key={col.key} className="py-1 text-left font-medium">
                      {col.short ?? col.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {defectTypes.map((name) => (
                  <tr key={name} className="border-t">
                    <td className="py-1">{name}</td>
                    {(axis.columns ?? []).map((col) => (
                      <td key={col.key} className="py-1">
                        <input
                          type="checkbox"
                          checked={Boolean(defects[name]?.[col.key])}
                          onChange={(event) =>
                            setDefects((was) => {
                              const marks = { ...was[name] }
                              if (event.target.checked) marks[col.key] = thisMonth()
                              else delete marks[col.key]
                              const next = { ...was, [name]: marks }
                              if (Object.keys(marks).length === 0) delete next[name]
                              return next
                            })
                          }
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-muted-foreground text-xs">
            수준은 이 표를 세어 정해집니다 — 형상 · 거동 · 일부 유형 시험 재현 · 전 유형 · 시장.
          </p>
        </div>
      )}

      <div className="space-y-1">
        <span className="text-muted-foreground text-xs">
          근거 <b>(필수)</b>
          {axis.evidence_label && ` — ${axis.evidence_label}`}
        </span>
        <Textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          rows={2}
          placeholder="무엇을 보고 이렇게 매겼는지 적습니다 — 비교 건수 · 문서번호 · 확인한 화면."
        />
      </div>

      <div className="flex justify-end">
        <Button onClick={() => void save()} disabled={busy || !note.trim()}>
          <Check className="size-4" /> 저장
        </Button>
      </div>

      <ErrorNotice error={failed} />
    </div>
  )
}
