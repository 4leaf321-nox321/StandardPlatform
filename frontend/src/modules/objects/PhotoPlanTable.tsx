/**
 * 사진 일괄 업로드의 계획 표 — **대화상자와 「작업」 화면이 같은 것을 본다**(`ImportPlanTable`
 * 과 같은 까닭: 두 벌이면 한쪽만 고쳐진다).
 *
 * 파일마다 무엇이 되나 — 업로드 · 교체 · 건너뜀 · 못 찾음 · 여럿에 맞음 · 이미지 아님 · 너무 큼 ·
 * 권한 없음 · 읽지 않음. 일괄 입력과 달리 **못 붙는 파일이 있어도 붙는 것은 붙는다** — 그래서
 * 못 붙는 줄을 위에 올려 읽게 한다(적용한 뒤에는 그 파일만 고쳐 다시 올린다).
 */

import type { PhotoPlan, PhotoRow, PhotoStatus } from '@/modules/objects/api'
import { Badge } from '@/shared/components/ui/badge'

export const PHOTO_STATUS_LABEL: Record<PhotoStatus, string> = {
  attach: '업로드',
  replace: '교체',
  skip: '건너뜀',
  not_found: '못 찾음',
  ambiguous: '여럿에 맞음',
  not_image: '이미지 아님',
  too_large: '너무 큼',
  forbidden: '권한 없음',
  bad_entry: '읽지 않음',
}

const VARIANT: Record<PhotoStatus, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  attach: 'default',
  replace: 'secondary',
  skip: 'outline',
  not_found: 'destructive',
  ambiguous: 'destructive',
  not_image: 'destructive',
  too_large: 'destructive',
  forbidden: 'destructive',
  bad_entry: 'destructive',
}

/** 줄 차례 — 못 붙는 것이 위. 300장 중 3장이 못 찾음이면 그 셋이 먼저 보여야 한다. */
const ORDER: PhotoStatus[] = [
  'not_found',
  'ambiguous',
  'forbidden',
  'not_image',
  'too_large',
  'bad_entry',
  'replace',
  'attach',
  'skip',
]

/** 사진 일괄 업로드의 결과인가 — 작업 화면이 결과의 모양으로 가른다. */
export function photoPlanOf(result: Record<string, unknown> | null): PhotoPlan | null {
  return result && result.photos === true && Array.isArray(result.files)
    ? (result as unknown as PhotoPlan)
    : null
}

/** 적용할 것이 있나 — 붙는 줄(업로드 · 교체)이 하나라도 있으면. */
export function photoPlanWrites(plan: PhotoPlan): number {
  return (plan.tally.attach ?? 0) + (plan.tally.replace ?? 0)
}

function target(row: PhotoRow): string {
  if (!row.object_id) return '—'
  return row.object_key ? `${row.object_label} (${row.object_key})` : row.object_label
}

export function PhotoPlanTable({ plan }: { plan: PhotoPlan }) {
  const rows = [...plan.files].sort(
    (a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status) || a.name.localeCompare(b.name),
  )
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {ORDER.filter((status) => (plan.tally[status] ?? 0) > 0).map((status) => (
          <Badge key={status} variant={VARIANT[status]}>
            {PHOTO_STATUS_LABEL[status]} {plan.tally[status]}
          </Badge>
        ))}
        {plan.hidden > 0 && (
          <span className="text-muted-foreground text-xs">
            숨김 파일 {plan.hidden}개(__MACOSX · .DS_Store …)는 제외했습니다.
          </span>
        )}
        {plan.applied && (
          <span className="text-emerald-600 dark:text-emerald-400">적용했습니다.</span>
        )}
      </div>
      {rows.length === 0 ? (
        <p className="text-muted-foreground text-sm">zip 안에 사진이 없습니다.</p>
      ) : (
        <div className="max-h-72 overflow-auto rounded-md border">
          <table className="w-full text-xs">
            <thead className="bg-muted/50 sticky top-0">
              <tr>
                <th className="px-2 py-1 text-left">파일</th>
                <th className="px-2 py-1 text-left">결과</th>
                <th className="px-2 py-1 text-left">객체</th>
                <th className="px-2 py-1 text-left">메시지</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((one, at) => (
                <tr key={`${one.name}-${at}`} className="border-t align-top">
                  <td className="max-w-56 truncate px-2 py-1 font-mono" title={one.name}>
                    {one.name}
                  </td>
                  <td className="px-2 py-1">
                    <Badge variant={VARIANT[one.status]}>{PHOTO_STATUS_LABEL[one.status]}</Badge>
                  </td>
                  <td className="max-w-48 truncate px-2 py-1">{target(one)}</td>
                  <td
                    className={
                      VARIANT[one.status] === 'destructive'
                        ? 'text-destructive px-2 py-1'
                        : 'text-muted-foreground px-2 py-1'
                    }
                  >
                    {one.message}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
