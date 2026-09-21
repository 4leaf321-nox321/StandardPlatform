/**
 * 여러 속성 한 번에 — **하나씩 만드는 창을 열 번 여는 대신 표 한 장.**
 *
 * 타입을 새로 세울 때 속성은 대여섯 개씩 함께 온다(엑셀 열 이름이 대개 그대로다). 그때
 * 창을 열고 닫기를 되풀이하면 열 개째에서 사람은 무엇을 넣었는지 잊는다.
 *
 * ## 새 길을 안 만든다
 *
 * 보내는 곳은 **정의 가져오기**(`POST /api/ontology/import`)다. 표는 그 스키마를 만드는
 * 껍데기일 뿐이라, 검증 · 미리 보기 · 스냅샷이 전부 그쪽 규칙 그대로다 — 규칙을 두 벌로
 * 적으면 「표로는 되는데 파일로는 안 되는」 상태가 생긴다.
 *
 * **더하고 고치기만 한다.** 표에 없다고 지우지 않는다(가져오기가 그렇다).
 */

import { useState } from 'react'
import { Loader2 } from 'lucide-react'

import { ontologyApi } from '@/modules/ontology/api'
import type { DataType, ImportPlan, ObjectType, PropertyDef } from '@/modules/ontology/api'
import { DATA_TYPE_LABELS } from '@/modules/ontology/PropertyEditDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PasteGrid, emptyRows, filledRows } from '@/shared/components/PasteGrid'
import type { GridColumn } from '@/shared/components/PasteGrid'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'

/** 표의 열. `header` 는 사람이 읽는 말이 아니라 **이 창이 읽는 열쇠**다. */
const COLUMNS: GridColumn[] = [
  { key: 'key', header: 'key', label: '키', help: '영문·숫자·밑줄', required: true },
  { key: 'label', header: 'label', label: '이름', required: true },
  {
    key: 'data_type',
    header: 'data_type',
    label: '종류',
    help: 'text · number · date · bool · enum · object_ref …',
    required: true,
  },
  { key: 'unit', header: 'unit', label: '단위', help: 'kg · mm — 숫자 칸에만' },
  {
    key: 'enum_options',
    header: 'enum_options',
    label: '고를 값',
    help: 'enum 일 때, ; 로 여럿',
  },
  {
    key: 'ref_type_slug',
    header: 'ref_type_slug',
    label: '가리킬 타입',
    help: 'object_ref 일 때 그 타입의 slug',
  },
  { key: 'required', header: 'required', label: '필수', help: '예 / 아니오' },
  { key: 'multi', header: 'multi', label: '여러 값', help: '예 / 아니오' },
  { key: 'help', header: 'help', label: '안내', help: '칸 아래 한 줄' },
]

const TRUE_WORDS = new Set(['예', 'y', 'yes', 'true', '1', 'o'])

/** 사람이 적은 종류 → `data_type`. **이름으로 적어도 받는다** — 화면이 그 말로 보여 주니까. */
const BY_LABEL = new Map(
  Object.entries(DATA_TYPE_LABELS).map(([key, label]) => [label, key as DataType]),
)

function toProperty(row: string[], at: number): Record<string, unknown> {
  const [key, label, kind, unit, options, ref, required, multi, help] = row.map((one) =>
    (one ?? '').trim(),
  )
  const dataType = (BY_LABEL.get(kind) ?? kind) as DataType
  return {
    key,
    label: label || key,
    data_type: dataType,
    unit,
    help,
    required: TRUE_WORDS.has(required.toLowerCase()),
    multi: TRUE_WORDS.has(multi.toLowerCase()),
    sort_order: at,
    ...(options
      ? {
          enum_options: options
            .split(';')
            .map((one) => one.trim())
            .filter(Boolean),
        }
      : {}),
    ...(ref ? { ref_type_slug: ref } : {}),
  }
}

export function PropertyBulkDialog({
  type,
  onClose,
  onChanged,
}: {
  /** 이미 있는 속성 뒤에 이어 붙이려고 `properties` 까지 받는다. */
  type: ObjectType & { properties: PropertyDef[] }
  onClose: () => void
  onChanged: () => void
}) {
  const [rows, setRows] = useState<string[][]>(() => emptyRows(COLUMNS, 3))
  const [plan, setPlan] = useState<ImportPlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const filled = filledRows(rows)

  /** 정의 가져오기가 받는 모양 — 이 타입 하나에 속성만 얹는다. */
  const schema = () => ({
    types: [
      {
        slug: type.slug,
        label: type.label,
        properties: rows
          .filter((row) => row.some((cell) => cell.trim()))
          .map((row, at) => toProperty(row, type.properties.length + at + 1)),
      },
    ],
  })

  const run = async (apply: boolean) => {
    setBusy(true)
    setError(null)
    try {
      const result = await ontologyApi.importSchema(schema(), !apply)
      setPlan(result)
      if (result.applied) onChanged()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const clean = Boolean(plan && !plan.applied && plan.errors.length === 0)

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="h-[80vh] w-[80vw] sm:max-w-[80vw]">
        <DialogHeader>
          <DialogTitle>{type.label} · 여러 속성 추가</DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <p className="text-muted-foreground text-xs">
            엑셀에서 열 이름을 복사해 <b>아무 칸에나 붙여넣으면</b> 그 자리부터 채워집니다.{' '}
            <b>이미 있는 키는 그 속성을 수정</b>하고, 표에 없다고 지우지는 않습니다. 종류는{' '}
            <span className="font-mono">text</span> 같은 값이나{' '}
            <span className="font-mono">{DATA_TYPE_LABELS.number}</span> 처럼 화면의 말로 적어도
            됩니다.
          </p>

          {error && <ErrorNotice error={error} />}

          <PasteGrid columns={COLUMNS} rows={rows} onRows={setRows} />

          {plan && (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge>
                  {plan.changes.filter((one) => one.action !== 'unchanged').length} 건 변경
                </Badge>
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
              {plan.warnings.length > 0 && (
                <ul className="space-y-0.5 text-sm text-amber-600 dark:text-amber-400">
                  {plan.warnings.map((one) => (
                    <li key={one}>{one}</li>
                  ))}
                </ul>
              )}
              <div className="max-h-48 overflow-auto rounded-md border">
                <table className="w-full text-xs">
                  <tbody>
                    {plan.changes.map((one) => (
                      <tr key={`${one.kind}-${one.slug}`} className="border-t">
                        <td className="px-2 py-1">
                          <Badge variant={one.action === 'unchanged' ? 'outline' : 'default'}>
                            {one.action === 'create'
                              ? '새로'
                              : one.action === 'update'
                                ? '고침'
                                : '그대로'}
                          </Badge>
                        </td>
                        <td className="px-2 py-1 font-mono">{one.slug}</td>
                        <td className="text-muted-foreground px-2 py-1">{one.fields.join(', ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {plan?.applied ? '닫기' : '취소'}
          </Button>
          {!plan?.applied && (
            <>
              <Button
                variant="outline"
                disabled={filled === 0 || busy}
                onClick={() => void run(false)}
              >
                {busy && !clean && <Loader2 className="mr-1 size-3.5 animate-spin" />}
                미리 보기 — {filled}개
              </Button>
              <Button disabled={!clean || busy} onClick={() => void run(true)}>
                {busy && clean && <Loader2 className="mr-1 size-3.5 animate-spin" />}
                적용
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
