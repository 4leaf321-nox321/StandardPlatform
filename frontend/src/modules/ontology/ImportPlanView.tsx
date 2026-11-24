/**
 * 정의 가져오기의 계획 — 오류 · 경고 · 바뀌는 것. 가져오기 화면과 정의 제안(ADR 0096)이 같은 것을
 * 쓴다(두 벌이면 한쪽만 고쳐진다).
 */

import { AlertTriangle } from 'lucide-react'

import type { ImportPlan } from '@/modules/ontology/api'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'

const ACTION_LABELS: Record<string, string> = {
  create: '새로 만듦',
  update: '고침',
  unchanged: '그대로',
}

const KIND_LABELS: Record<string, string> = {
  group: '묶음',
  interface: '인터페이스',
  interface_property: '공통 속성',
  type: '타입',
  property: '속성',
  relation_type: '관계 종류',
}

export function ImportPlanView({ plan }: { plan: ImportPlan }) {
  const changed = plan.changes.filter((one) => one.action !== 'unchanged')
  return (
    <>
      {plan.errors.length > 0 && (
        <div className="border-destructive/40 bg-destructive/5 space-y-1 rounded border p-3">
          <p className="text-destructive text-sm font-medium">
            이대로면 실패합니다 — <b>아무것도 안 바뀝니다.</b>
          </p>
          {plan.errors.map((one) => (
            <p key={one} className="text-destructive text-xs">
              {one}
            </p>
          ))}
        </div>
      )}

      {plan.warnings.length > 0 && (
        <div className="space-y-1 rounded border border-amber-500/40 bg-amber-500/5 p-3">
          <p className="flex items-center gap-1.5 text-sm font-medium text-amber-700 dark:text-amber-400">
            <AlertTriangle className="size-4" />
            적용은 되지만, 읽고 판단하세요
          </p>
          {plan.warnings.map((one) => (
            <p key={one} className="text-xs text-amber-700 dark:text-amber-400">
              {one}
            </p>
          ))}
        </div>
      )}

      {changed.length === 0 ? (
        <p className="text-muted-foreground text-sm">바뀌는 것이 없습니다.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>무엇</TableHead>
              <TableHead>이름</TableHead>
              <TableHead>어떻게</TableHead>
              <TableHead>바뀌는 칸</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {changed.map((one) => (
              <TableRow key={`${one.kind}:${one.slug}`}>
                <TableCell>{KIND_LABELS[one.kind] ?? one.kind}</TableCell>
                <TableCell className="font-mono text-xs">{one.slug}</TableCell>
                <TableCell>{ACTION_LABELS[one.action] ?? one.action}</TableCell>
                <TableCell className="text-muted-foreground text-xs">
                  {one.fields.join(', ') || '—'}
                  {/* **파일에 없던 타입이 왜 바뀌나** — 인터페이스를 따라서다. 안 적으면
                    보낸 사람은 자기가 그 타입을 건드린 줄 모른다. */}
                  {one.via && (
                    <span className="ml-2 rounded border border-sky-500/50 bg-sky-500/10 px-1.5">
                      인터페이스 {one.via} 를 따라
                    </span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </>
  )
}
