/**
 * 값마다 훑기(④ · ⑩) — 「전작보다 빨리 늘고 있는 **증상**은?」 「계절을 빼면 늘고 있는
 * **증상**은?」 처럼 물음의 주어가 기준의 값일 때.
 *
 * 같은 분석을 값마다 돌려 줄을 세운다 — 여럿을 함께 보므로 서버가 유의수준 · 벌점을 값의 수로
 * 맞춘다(주의에 적힌다). 줄의 「이 값만 보기」 는 그 값으로 거른 단건 분석으로 간다.
 */

import type { Metric } from '@/modules/metrics/api'
import { DrillLink, interval } from '@/modules/metrics/analysis/common'
import type {
  ChangesScanResult,
  SprtDecision,
  SprtScanResult,
} from '@/modules/metrics/analysis/types'
import { shownNumber } from '@/modules/metrics/metricDrill'
import { Button } from '@/shared/components/ui/button'
import { Label } from '@/shared/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'

const NONE = '__none__'

/** 「이 값만 보기」 — 훑기에서 고른 값 하나로 거른 단건 분석. */
export interface Focus {
  name: string
  value: string
  label: string
}

/** 값마다 훑을 기준 — 모델 기준 · 이미 거른 기준은 뺀다. */
export function ByPicker({
  id,
  metric,
  value,
  onChange,
  exclude,
}: {
  id: string
  metric: Metric
  value: string
  onChange: (next: string) => void
  exclude: string[]
}) {
  const choices = metric.dims.filter((one) => !exclude.includes(one.name) && !one.grain)
  if (choices.length === 0) return null
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>값마다 훑기</Label>
      <Select value={value || NONE} onValueChange={(next) => onChange(next === NONE ? '' : next)}>
        <SelectTrigger id={id} className="w-48">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NONE}>훑지 않음</SelectItem>
          {choices.map((one) => (
            <SelectItem key={one.name} value={one.name}>
              {one.label}마다
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

/** 고른 값 하나만 보는 중이라고 말하고, 훑기로 돌아가는 단추. */
export function FocusNote({ focus, onBack }: { focus: Focus; onBack: () => void }) {
  return (
    <p className="flex flex-wrap items-center gap-2 text-sm">
      <span>
        <strong>{focus.label}</strong> 만 보는 중입니다.
      </span>
      <Button size="sm" variant="outline" onClick={onBack}>
        훑기로 돌아가기
      </Button>
    </p>
  )
}

const DECISION_SHORT: Record<SprtDecision, string> = {
  worse: '나쁨',
  not_worse: '나쁘지 않음',
  continue: '아직',
}

export function SprtScanTable({
  data,
  onFocus,
}: {
  data: SprtScanResult
  onFocus: (focus: Focus) => void
}) {
  const worse = data.items.filter((one) => one.decision === 'worse')
  return (
    <section className="space-y-2" aria-label="값마다 훑기">
      <p className="text-sm">
        {data.target_label} vs {data.reference_label} — {data.by_label} {data.scanned}개 중{' '}
        {worse.length === 0
          ? '전작보다 나빠진 것이 없습니다(아직인 것은 문제없다는 뜻이 아닙니다).'
          : `${worse.length}개가 전작보다 ${data.rho}배 쪽입니다.`}{' '}
        <span className="text-muted-foreground">
          값마다 유의수준 {shownNumber(data.alpha_each, 4)}(α/{data.scanned}).
        </span>
      </p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{data.by_label}</TableHead>
            <TableHead>결론</TableHead>
            <TableHead className="text-right">관측</TableHead>
            <TableHead className="text-right">기대</TableHead>
            <TableHead className="text-right">표준화 비(95% 구간)</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.map((one) => (
            <TableRow key={one.key}>
              <TableCell>{one.label}</TableCell>
              <TableCell
                className={
                  one.decision === 'worse'
                    ? 'text-destructive font-semibold'
                    : one.decision === 'not_worse'
                      ? 'text-emerald-700 dark:text-emerald-400'
                      : undefined
                }
              >
                {DECISION_SHORT[one.decision]}
                {one.decided_at && ` (${one.decided_at})`}
              </TableCell>
              <TableCell className="text-right">{shownNumber(one.observed)}</TableCell>
              <TableCell className="text-right">{shownNumber(one.expected, 1)}</TableCell>
              <TableCell className="text-right">
                {one.new
                  ? '전작에 없던 값'
                  : `${shownNumber(one.smr, 2)} (${interval([one.smr_low ?? 0, one.smr_high ?? 0])})`}
              </TableCell>
              <TableCell className="text-right">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => onFocus({ name: data.by, value: one.key, label: one.label })}
                >
                  이 값만 보기
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {data.skipped.length > 0 && (
        <p className="text-muted-foreground text-xs">
          견주지 못한 값 {data.skipped.length}개 — {data.skipped.join(' · ')}
        </p>
      )}
    </section>
  )
}

const DIRECTION_TEXT = { up: '올라감', down: '내려감', flat: '그대로' } as const

export function ChangesScanTable({
  data,
  onFocus,
}: {
  data: ChangesScanResult
  onFocus: (focus: Focus) => void
}) {
  const rising = data.items.filter((one) => one.direction === 'up')
  return (
    <section className="space-y-2" aria-label="값마다 훑기">
      <p className="text-sm">
        계절을 빼고 보면 {data.by_label} {data.scanned}개 중{' '}
        {rising.length === 0
          ? '수준이 오른 것이 없습니다.'
          : `${rising.length}개가 마지막 변화에서 올랐습니다.`}
      </p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{data.by_label}</TableHead>
            <TableHead>마지막 변화</TableHead>
            <TableHead className="text-right">비(95% 구간)</TableHead>
            <TableHead className="text-right">
              지금 수준{data.kind === 'rate' ? `(${shownNumber(data.per)}대당)` : ''}
            </TableHead>
            <TableHead className="text-right">건수</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.map((one) => (
            <TableRow key={one.key ?? '-'}>
              <TableCell>{one.label}</TableCell>
              <TableCell
                className={one.direction === 'up' ? 'text-destructive font-semibold' : undefined}
              >
                {one.note ??
                  (one.last
                    ? `${one.last.label}부터 ${DIRECTION_TEXT[one.direction]}${one.last.provisional ? '(잠정)' : ''}`
                    : '바뀐 곳 없음')}
              </TableCell>
              <TableCell className="text-right">
                {one.last
                  ? `${shownNumber(one.last.ratio, 2)}배 (${interval(one.last.ratio_ci)})`
                  : '—'}
              </TableCell>
              <TableCell className="text-right">{shownNumber(one.level_now, 2)}</TableCell>
              <TableCell className="text-right">
                <DrillLink drill={one.drill} count={one.total} />
              </TableCell>
              <TableCell className="text-right">
                {one.key !== null && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      onFocus({ name: data.by, value: one.key ?? '', label: one.label })
                    }
                  >
                    이 값만 보기
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  )
}
