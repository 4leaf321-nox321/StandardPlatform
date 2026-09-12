/**
 * 여럿 골라 한 칸 바꾸기 — **계획 먼저.**
 *
 * 「이 열 건의 담당 부서를 바꿔라」 는 자주 나오는 일인데, 지금까지 그 길은 CSV 로
 * 내려받아 고쳐 다시 올리는 것뿐이었다. 파일을 왕복하는 동안 **그 사이에 누가 고친
 * 것을 덮어쓴다.**
 *
 * ## 한 번에 한 칸
 *
 * 여러 칸을 동시에 바꾸게 하면 이 창은 곧 「폼」 이 되고, 그때 실수 한 번의 크기가
 * 수백 배가 된다.
 *
 * ## 못 고치는 것은 이유가 붙는다
 *
 * 남의 부서 것이 골라져 있을 수 있다(목록은 볼 수 있으니까). 조용히 빼면 「바꿨다」 고
 * 믿은 사람에게 나중에 다른 값으로 나타난다 — 행마다 이유를 적고, 나머지는 그대로 한다.
 */

import { useEffect, useState } from 'react'
import { Loader2, RotateCcw } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import type { BulkEditPlan } from '@/modules/objects/api'
import type { PropertyDef } from '@/modules/ontology/api'
import { PropertyFields } from '@/modules/objects/PropertyFields'
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

const ACTION: Record<string, string> = {
  change: '바뀜',
  unchanged: '그대로',
  error: '오류',
}

interface Props {
  typeSlug: string
  ids: string[]
  /** 속성 정의 — 고른 칸에 맞는 입력을 그리려고. */
  defs: PropertyDef[]
  /** 고를 수 있는 부서(소유 부서를 바꿀 때). */
  workspaces: { slug: string; name: string }[]
  onClose: () => void
  onApplied: () => void
  /** 적용한 뒤 「되돌리기」 — 방금 바꾼 묶음 번호를 넘긴다. */
  onUndo?: (batchId: string) => void
}

export function BulkEditDialog({
  typeSlug,
  ids,
  defs,
  workspaces,
  onClose,
  onApplied,
  onUndo,
}: Props) {
  const [field, setField] = useState('status')
  const [value, setValue] = useState<unknown>('active')
  const [plan, setPlan] = useState<BulkEditPlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  // 칸이 바뀌면 계획은 지난 것이 된다 — 남겨 두면 그 계획을 보고 적용을 누른다.
  useEffect(() => setPlan(null), [field, value])

  const def = defs.find((one) => `properties.${one.key}` === field) ?? null

  async function run(apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      const found = await objectApi.bulkEdit(typeSlug, { ids, field, value, apply })
      setPlan(found)
      if (found.applied) onApplied()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const shown = plan
    ? plan.rows.filter((one) => one.action !== 'unchanged' || plan.rows.length <= 20)
    : []

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>고른 {ids.length}건의 한 칸 바꾸기</DialogTitle>
          <DialogDescription>
            바꿀 칸과 값을 정하고 <strong>먼저 계획을 봅니다</strong>. 이미 그 값인 것은 「그대로」
            고, 고칠 수 없는 것은 이유가 붙습니다.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1.5">
            <Label htmlFor="bulk-field">바꿀 칸</Label>
            <Select
              value={field}
              onValueChange={(next) => {
                setField(next)
                setValue(next === 'status' ? 'active' : '')
              }}
            >
              <SelectTrigger id="bulk-field" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(plan?.fields ?? [{ field: 'status', label: '상태' }]).map((one) => (
                  <SelectItem key={one.field} value={one.field}>
                    {one.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="min-w-48 flex-1 space-y-1.5">
            <Label htmlFor="bulk-value">새 값</Label>
            {field === 'status' ? (
              <Select value={String(value)} onValueChange={setValue}>
                <SelectTrigger id="bulk-value">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="active">사용</SelectItem>
                  <SelectItem value="deprecated">안 씀</SelectItem>
                </SelectContent>
              </Select>
            ) : field === 'workspace' ? (
              <Select value={String(value)} onValueChange={setValue}>
                <SelectTrigger id="bulk-value">
                  <SelectValue placeholder="부서를 고르세요" />
                </SelectTrigger>
                <SelectContent>
                  {workspaces.map((one) => (
                    <SelectItem key={one.slug} value={one.slug}>
                      {one.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : def ? (
              /* 칸의 종류에 맞는 입력을 **폼과 같은 것**으로 그린다 — 고를 값은 고르개로,
                 참조는 찾아 고르기로. 여기서 따로 만들면 한 화면에서는 되는 값이 다른
                 화면에서는 안 된다. */
              <PropertyFields
                defs={[def]}
                values={{ [def.key]: value }}
                onChange={(next) => setValue(next[def.key])}
              />
            ) : (
              <Input
                id="bulk-value"
                value={String(value ?? '')}
                onChange={(event) => setValue(event.target.value)}
              />
            )}
          </div>
        </div>

        <ErrorNotice error={error} />

        {plan && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span>
                바뀜 <strong>{plan.counts.change ?? 0}</strong>
              </span>
              <span className="text-muted-foreground">그대로 {plan.counts.unchanged ?? 0}</span>
              {(plan.counts.error ?? 0) > 0 && (
                <span className="text-destructive">오류 {plan.counts.error}</span>
              )}
              {plan.applied && (
                <span className="text-emerald-600 dark:text-emerald-400">적용했습니다.</span>
              )}
            </div>
            {shown.length > 0 && (
              <div className="max-h-64 overflow-auto rounded-md border text-xs">
                <table className="w-full">
                  <tbody>
                    {shown.map((one) => (
                      <tr key={one.id} className="border-b">
                        <td className="px-2 py-1">{one.label}</td>
                        <td
                          className={
                            one.action === 'error' ? 'text-destructive px-2 py-1' : 'px-2 py-1'
                          }
                        >
                          {ACTION[one.action] ?? one.action}
                        </td>
                        <td className="text-muted-foreground px-2 py-1">
                          {one.message || `${one.before} → ${one.after}`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          {/* **실수를 알아채는 때는 대개 누른 직후다.** 그 자리에 되돌리는 길을 둔다 —
              이력에서 한 건씩 찾아 되돌리게 하면 수백 건은 사실상 못 돌린다. */}
          {plan?.applied && plan.batch_id && onUndo && (
            <Button variant="outline" onClick={() => onUndo(plan.batch_id as string)}>
              <RotateCcw className="mr-1 size-4" />
              되돌리기
            </Button>
          )}
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {plan?.applied ? '닫기' : '취소'}
          </Button>
          {!plan?.applied && (
            <>
              <Button variant="outline" onClick={() => run(false)} disabled={busy}>
                {busy && <Loader2 className="mr-1 size-4 animate-spin" />}
                계획 보기
              </Button>
              <Button
                onClick={() => run(true)}
                disabled={busy || !plan || (plan.counts.change ?? 0) === 0}
              >
                {plan ? `${plan.counts.change ?? 0}건 바꾸기` : '바꾸기'}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
