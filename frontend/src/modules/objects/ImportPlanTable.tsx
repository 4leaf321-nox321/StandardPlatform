/**
 * 가져오기 계획 표 — **대화상자와 「작업」 화면이 같은 것을 본다.**
 *
 * 두 벌로 두면 한쪽만 고쳐지고, 그때 같은 계획이 자리에 따라 다르게 보인다. 읽는 규칙도
 * 여기 하나다: 「그대로」 는 행이 많을 때 빼고, 그 사실을 표 아래에 적는다.
 */

import type { ImportPlan, ImportRow } from '@/modules/objects/api'
import { Badge } from '@/shared/components/ui/badge'

const ACTION_LABEL: Record<ImportRow['action'], string> = {
  create: '새로',
  update: '고침',
  unchanged: '그대로',
  unlink: '끊음',
  delete: '지움',
  error: '오류',
}

const ACTION_VARIANT: Record<
  ImportRow['action'],
  'default' | 'secondary' | 'outline' | 'destructive'
> = {
  create: 'default',
  update: 'secondary',
  unchanged: 'outline',
  unlink: 'destructive',
  delete: 'destructive',
  error: 'destructive',
}

/** 「그대로」 는 표에서 뺀다 — 300행 중 297행이 그대로면 나머지 셋이 안 보인다. */
export const SHOW_UNCHANGED_BELOW = 20

/** 표에 그리는 줄의 상한. 5만 줄을 한 번에 그리면 브라우저가 멈춘다(2026-10-09) — 서버도 계획을
 *  이만큼만 싣고(`rows_omitted`) 전체는 CSV 로 준다. */
export const RENDER_MAX = 500

/** 서버가 응답에서 뺀 줄 수 — 결과에 줄이 많으면 앞(오류 먼저)만 싣는다. */
export function omittedRows(plan: ImportPlan): number {
  return (plan as ImportPlan & { rows_omitted?: number }).rows_omitted ?? 0
}

/** 이 계획을 적용할 수 있나 — 오류가 하나라도 있으면 아무것도 안 들어간다. */
export function planIsClean(plan: ImportPlan): boolean {
  return plan.errors.length === 0 && plan.counts.error === 0
}

/** 바뀌는 것이 있나 — 없으면 적용해도 아무 일이 없다. */
export function planChangesSomething(plan: ImportPlan): boolean {
  return (
    plan.counts.create + plan.counts.update + (plan.counts.unlink ?? 0) + (plan.counts.delete ?? 0) >
    0
  )
}

export function ImportPlanTable({
  plan,
  onDownloadRows,
}: {
  plan: ImportPlan
  /** 모든 줄을 CSV 로 받기 — 표에 다 못 그릴 때 그 아래에 선다. */
  onDownloadRows?: () => void
}) {
  const listed = plan.rows.filter(
    (one) => one.action !== 'unchanged' || plan.rows.length <= SHOW_UNCHANGED_BELOW,
  )
  const rows = listed.slice(0, RENDER_MAX)
  const rest = listed.length - rows.length + omittedRows(plan)
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge>새로 {plan.counts.create}</Badge>
        <Badge variant="secondary">고침 {plan.counts.update}</Badge>
        <Badge variant="outline">그대로 {plan.counts.unchanged}</Badge>
        {(plan.counts.unlink ?? 0) > 0 && (
          <Badge variant="destructive">끊음 {plan.counts.unlink}</Badge>
        )}
        {(plan.counts.delete ?? 0) > 0 && (
          <Badge variant="destructive">지움 {plan.counts.delete}</Badge>
        )}
        {plan.counts.error > 0 && <Badge variant="destructive">오류 {plan.counts.error}</Badge>}
        {plan.applied && (
          <span className="text-emerald-600 dark:text-emerald-400">적용했습니다.</span>
        )}
      </div>
      {plan.errors.length > 0 && (
        <ul className="text-destructive space-y-0.5 text-sm">
          {plan.errors.map((one) => (
            <li key={one}>{one}</li>
          ))}
        </ul>
      )}
      {rows.length > 0 && (
        <div className="max-h-72 overflow-auto rounded-md border">
          <table className="w-full text-xs">
            <thead className="bg-muted/50 sticky top-0">
              <tr>
                <th className="px-2 py-1 text-right">행</th>
                <th className="px-2 py-1 text-left">결과</th>
                <th className="px-2 py-1 text-left">이름</th>
                <th className="px-2 py-1 text-left">바뀌는 칸 · 메시지</th>
              </tr>
            </thead>
            <tbody>
              {/* **행 번호를 키로 쓰지 않는다.** 「끊음」 줄은 파일에서 온 것이 아니라
                  번호가 0 이고, 여럿이면 키가 겹친다. */}
              {rows.map((one, at) => (
                <tr key={`${one.row}-${at}`} className="border-t">
                  <td className="text-muted-foreground px-2 py-1 text-right tabular-nums">
                    {one.row || '—'}
                  </td>
                  <td className="px-2 py-1">
                    <Badge variant={ACTION_VARIANT[one.action]}>{ACTION_LABEL[one.action]}</Badge>
                  </td>
                  <td className="max-w-48 truncate px-2 py-1">
                    {one.label}
                    {one.key && (
                      <span className="text-muted-foreground ml-1 font-mono">{one.key}</span>
                    )}
                  </td>
                  <td className="px-2 py-1">
                    {one.action === 'error' ? (
                      <span className="text-destructive">{one.message}</span>
                    ) : (
                      <span className="text-muted-foreground">
                        {one.changes.join(', ') || one.message}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {plan.rows.length > SHOW_UNCHANGED_BELOW && plan.counts.unchanged > 0 && (
        <p className="text-muted-foreground text-xs">
          「그대로」 {plan.counts.unchanged}행은 표에서 뺐습니다.
        </p>
      )}
      {rest > 0 && (
        <p className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
          <span>
            나머지 {rest.toLocaleString()}줄은 여기 그리지 않았습니다 — 오류 줄을 먼저
            보입니다.
          </span>
          {onDownloadRows && (
            <button type="button" className="text-primary underline" onClick={onDownloadRows}>
              모든 줄 받기(CSV)
            </button>
          )}
        </p>
      )}
    </div>
  )
}
