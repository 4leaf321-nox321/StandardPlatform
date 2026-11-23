/**
 * 묶음의 **삭제 표식**(`tombstones`) — 원천에서 사라진 것을 어떻게 하나(사용 중지 · 삭제 · 병합 ·
 * 관계 해제). 화면의 말은 용어집(「삭제 표식」 · 「삭제」 · 「관계 해제」)을 따른다.
 *
 * 예전에는 「작업」 화면이 묶음의 객체 · 관계 단계만 그리고 무덤은 안 그렸다. datarelay 의 「보낸 것
 * 걷어내기」 는 무덤만 담은 묶음을 올리므로, 적용하는 사람이 무엇이 사라지는지 읽을 자리가 없었다.
 * **「삭제」 는 「사용 중지」 와 따로 센다**(ADR 0090) — 삭제한 것은 목록에서 제외되고 복원할 수
 * 없다.
 */

import { Badge } from '@/shared/components/ui/badge'

export interface TombstoneRow {
  row: number
  action: string
  label: string
  message: string
}

export interface TombstonePlan {
  rows: TombstoneRow[]
  counts: Record<string, number>
  rows_omitted?: number
}

/** 보이는 차례 — 사라지는 것부터. */
export const GRAVE_LABEL: Record<string, string> = {
  delete: '삭제',
  deprecate: '사용 중지',
  merge: '병합',
  unlink: '관계 해제',
  error: '오류',
  unchanged: '그대로',
}

/** 표에 그리는 줄의 상한 — 나머지는 셈으로만. */
export const GRAVE_ROWS_MAX = 200

export function tombstonesOf(result: unknown): TombstonePlan | null {
  const plan = (result as { tombstones?: TombstonePlan | null } | null)?.tombstones
  return plan && Array.isArray(plan.rows) ? plan : null
}

/** 묶음 결과의 셈(`tombstones_<동작>`)을 한 줄로 — 작업 목록의 요약에 붙인다. */
export function graveSummary(counts: Record<string, number>): string {
  return Object.entries(GRAVE_LABEL)
    .filter(([action]) => action !== 'unchanged' && (counts[`tombstones_${action}`] ?? 0) > 0)
    .map(([action, label]) => `${label} ${counts[`tombstones_${action}`]}`)
    .join(' · ')
}

export function TombstonePlanView({ plan }: { plan: TombstonePlan }) {
  const shown = plan.rows.filter((one) => one.action !== 'unchanged').slice(0, GRAVE_ROWS_MAX)
  const totals = Object.entries(GRAVE_LABEL).filter(([action]) => (plan.counts[action] ?? 0) > 0)
  const deleting = plan.counts.delete ?? 0
  return (
    <section aria-label="삭제 표식" className="space-y-1.5 text-xs">
      <p>
        <b>삭제 표식</b>{' '}
        <span className="text-muted-foreground">
          {totals.length
            ? totals.map(([action, label]) => `${label} ${plan.counts[action]}`).join(' · ')
            : '없음'}
        </span>
      </p>
      {deleting > 0 && (
        <p className="text-destructive">
          「삭제」 {deleting}건은 사용 중지가 아니라 <b>삭제</b>입니다 — 목록에서 제외되고 복원할 수
          없습니다(이력 · 감사 기록은 남습니다).
        </p>
      )}
      {shown.length > 0 && (
        <table>
          <tbody>
            {shown.map((one) => (
              <tr key={one.row} className="align-top">
                <td className="py-0.5 pr-2">
                  <Badge
                    variant={
                      one.action === 'delete' || one.action === 'error'
                        ? 'destructive'
                        : 'secondary'
                    }
                  >
                    {GRAVE_LABEL[one.action] ?? one.action}
                  </Badge>
                </td>
                <td className="py-0.5 pr-3 font-mono">{one.label}</td>
                <td className="text-muted-foreground py-0.5">{one.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
