/**
 * 규칙 기준 편집기(ADR 0015) — **차례 · 성격 이름 · 조건**의 표.
 *
 * 기록마다 위에서부터 **처음 맞는 규칙**의 이름이 그 기록의 값이고, 아무것도 안 맞으면 「기타」 다.
 * 그래서 차례가 뜻이다 — 줄을 끌거나 「위로 · 아래로」 로 바꾼다. 조건은 목록 화면의 조건 줄을
 * 그대로 쓴다(칸 · 연산 · 값을 따로 배우지 않는다). 「규칙 미리 보기」(정의 전체의 「계획 보기」 와
 * 같은 계획)를 누르면 규칙마다 붙인 기록 수 · 막힌 규칙(앞 규칙이 다 가져감) · 기타의 몫이 같은
 * 자리에 선다. 용어는 `docs/용어.md` 의 것.
 */

import { useState } from 'react'
import { ArrowDown, ArrowUp, GripVertical, Plus, Trash2 } from 'lucide-react'

import type { RulePlan } from '@/modules/metrics/api'
import { shownNumber } from '@/modules/metrics/metricDrill'
import { ConditionBar } from '@/modules/objects/ConditionBar'
import type { Condition, LinkedField } from '@/modules/objects/api'
import type { PropertyDef } from '@/modules/ontology/api'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'

/** 서버의 상한 — `spec.MAX_RULES`. */
export const MAX_RULES = 20

export interface RuleRow {
  label: string
  when: Condition[]
}

export interface RuleDimRow {
  kind: 'rules'
  name: string
  /** 기준의 이름(「성격」) — 비우면 `name`. */
  label: string
  rules: RuleRow[]
  /** 어느 규칙에도 안 맞는 기록의 이름. */
  other: string
}

export function emptyRuleDim(): RuleDimRow {
  return { kind: 'rules', name: '', label: '', rules: [{ label: '', when: [] }], other: '기타' }
}

/** 줄 하나를 `from` 에서 `to` 자리로. */
export function moved<T>(items: T[], from: number, to: number): T[] {
  if (to < 0 || to >= items.length || from === to) return items
  const copy = [...items]
  const [one] = copy.splice(from, 1)
  copy.splice(to, 0, one)
  return copy
}

interface Props {
  /** 몇 번째 기준인지 — 화면 낭독기의 이름에 쓴다. */
  index: number
  value: RuleDimRow
  onChange: (next: RuleDimRow) => void
  onRemove: () => void
  defs: PropertyDef[]
  linked: LinkedField[]
  /** 「계획 보기」 의 결과 중 이 기준의 것 — 없으면 아직 안 봤거나 정의가 바뀌었다. */
  plan: RulePlan | null
  onPreview?: () => void
  previewing?: boolean
}

function percent(part: number, whole: number): string {
  if (!whole) return '—'
  return `${shownNumber((part / whole) * 100, 1)}%`
}

export function RuleDimensionEditor({
  index,
  value,
  onChange,
  onRemove,
  defs,
  linked,
  plan,
  onPreview,
  previewing = false,
}: Props) {
  const [dragging, setDragging] = useState<number | null>(null)
  const rules = value.rules
  const setRules = (next: RuleRow[]) => onChange({ ...value, rules: next })
  const setRule = (at: number, next: RuleRow) =>
    setRules(rules.map((one, i) => (i === at ? next : one)))
  // 계획은 지금 줄과 같은 차례일 때만 줄마다 붙인다 — 바꾼 뒤에는 「계획 보기」 를 다시 누른다.
  const counts = plan && plan.rules.length === rules.length ? plan.rules : null
  const where = `기준 ${index + 1}`

  return (
    <section
      aria-label={`규칙 기준 ${value.label || value.name || index + 1}`}
      className="space-y-2 rounded-md border p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">규칙 기준</span>
        <Input
          aria-label={`${where} 이름`}
          className="w-36"
          placeholder="이름 (영문)"
          value={value.name}
          onChange={(event) => onChange({ ...value, name: event.target.value })}
        />
        <Input
          aria-label={`${where} 표시 이름`}
          className="w-36"
          placeholder="표시 이름 — 성격"
          value={value.label}
          onChange={(event) => onChange({ ...value, label: event.target.value })}
        />
        <span className="ml-auto" />
        {onPreview && (
          <Button size="sm" variant="outline" disabled={previewing} onClick={onPreview}>
            {previewing ? '세는 중…' : '규칙 미리 보기'}
          </Button>
        )}
        <Button size="sm" variant="ghost" aria-label={`${where} 삭제`} onClick={onRemove}>
          <Trash2 className="size-4" />
        </Button>
      </div>
      <p className="text-muted-foreground text-xs">
        기록마다 위에서부터 <strong>처음 맞는 규칙</strong>의 이름이 값입니다 — 차례가 뜻입니다. 한
        규칙의 조건은 모두 맞아야(그리고) 하고, 같은 칸의 「또는」 은 「그 중 하나」 로 적습니다.
      </p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-14">차례</TableHead>
            <TableHead className="w-40">성격 이름</TableHead>
            <TableHead>조건 — 모두 맞으면</TableHead>
            <TableHead className="w-36 text-right">미리 보기</TableHead>
            <TableHead className="w-28" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {rules.map((rule, at) => {
            const count = counts?.[at] ?? null
            const name = rule.label || `${at + 1}`
            return (
              <TableRow
                key={at}
                draggable
                aria-label={`규칙 ${at + 1}`}
                className={dragging === at ? 'opacity-50' : undefined}
                onDragStart={() => setDragging(at)}
                onDragEnd={() => setDragging(null)}
                onDragOver={(event) => event.preventDefault()}
                onDrop={() => {
                  if (dragging !== null) setRules(moved(rules, dragging, at))
                  setDragging(null)
                }}
              >
                <TableCell className="text-muted-foreground whitespace-nowrap">
                  <GripVertical className="mr-1 inline size-3.5 cursor-grab" aria-hidden />
                  {at + 1}
                </TableCell>
                <TableCell>
                  <Input
                    aria-label={`규칙 ${at + 1} 이름`}
                    placeholder="SW · NTF …"
                    value={rule.label}
                    onChange={(event) => setRule(at, { ...rule, label: event.target.value })}
                  />
                </TableCell>
                <TableCell>
                  <ConditionBar
                    defs={defs}
                    linked={linked}
                    conditions={rule.when}
                    allowExclude={false}
                    onChange={(next) => setRule(at, { ...rule, when: next })}
                  />
                </TableCell>
                <TableCell className="text-right text-xs">
                  {count ? (
                    <div aria-label={`규칙 ${at + 1} 미리 보기`}>
                      <div>
                        {shownNumber(count.won)}건 · {percent(count.won, plan?.seen ?? 0)}
                      </div>
                      {count.blocked && (
                        <div className="text-amber-700 dark:text-amber-400">
                          막힌 규칙 — 맞는 {shownNumber(count.matched)}건을 앞 규칙이 가져감
                        </div>
                      )}
                      {count.matched === 0 && (
                        <div className="text-amber-700 dark:text-amber-400">맞는 기록 없음</div>
                      )}
                      {!count.blocked && count.matched > count.won && (
                        <div className="text-muted-foreground">
                          맞는 {shownNumber(count.matched)}건 중{' '}
                          {count.taken_by
                            .map((one) => `규칙 ${one.rule}이 ${shownNumber(one.count)}`)
                            .join(' · ')}
                        </div>
                      )}
                    </div>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="whitespace-nowrap text-right">
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`규칙 ${at + 1} 위로`}
                    disabled={at === 0}
                    onClick={() => setRules(moved(rules, at, at - 1))}
                  >
                    <ArrowUp className="size-4" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`규칙 ${at + 1} 아래로`}
                    disabled={at === rules.length - 1}
                    onClick={() => setRules(moved(rules, at, at + 1))}
                  >
                    <ArrowDown className="size-4" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`규칙 ${name} 삭제`}
                    disabled={rules.length === 1}
                    onClick={() => setRules(rules.filter((_, i) => i !== at))}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </TableCell>
              </TableRow>
            )
          })}
          <TableRow>
            <TableCell className="text-muted-foreground">—</TableCell>
            <TableCell>
              <Input
                aria-label={`${where} 기타 이름`}
                value={value.other}
                onChange={(event) => onChange({ ...value, other: event.target.value })}
              />
            </TableCell>
            <TableCell className="text-muted-foreground text-xs">어느 규칙에도 안 맞음</TableCell>
            <TableCell className="text-right text-xs">
              {plan ? (
                <div aria-label="기타 미리 보기">
                  {shownNumber(plan.other_count)}건 · {percent(plan.other_count, plan.seen)}
                </div>
              ) : (
                <span className="text-muted-foreground">—</span>
              )}
            </TableCell>
            <TableCell />
          </TableRow>
        </TableBody>
      </Table>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={rules.length >= MAX_RULES}
          onClick={() => setRules([...rules, { label: '', when: [] }])}
        >
          <Plus className="mr-1 size-3.5" />
          규칙 추가
        </Button>
        <span className="text-muted-foreground text-xs">
          {rules.length} / {MAX_RULES}
          {plan &&
            ` · ${plan.sampled ? `무작위 표본 ${shownNumber(plan.seen)}건에서 센 수` : `거르기를 통과한 기록 ${shownNumber(plan.seen)}건`}`}
          {plan && !counts && ' · 규칙을 수정했습니다 — 「규칙 미리 보기」 로 다시 셉니다'}
        </span>
      </div>
    </section>
  )
}
