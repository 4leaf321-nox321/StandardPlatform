/**
 * 파일로 넣기 — **계획 먼저, 적용은 사람이 누른 뒤.**
 *
 * 정의 가져오기와 같은 무늬다. 파일을 올리면 서버가 행마다 무엇이 될지(새로/고침/
 * 그대로/오류)를 돌려주고, 그것을 표로 보여 준다. **한 행이라도 오류면 「적용」 이
 * 안 선다** — 반쯤 들어간 파일은 어디까지 들어갔는지를 사람이 챙겨야 하고, 아무도
 * 안 챙긴다.
 *
 * 객체 파일과 관계 파일은 탭으로 가른다. 열 모양이 다르고, 관계는 객체가 먼저
 * 있어야 하므로 순서도 다르다.
 */

import { useRef, useState } from 'react'
import { Download, FileUp, Loader2 } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import type { ImportPlan, ImportRow } from '@/modules/objects/api'
import type { ObjectType } from '@/modules/ontology/api'
import { useAuth } from '@/shared/auth/AuthContext'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger } from '@/shared/components/ui/tabs'

type Kind = 'objects' | 'relations'

interface ObjectImportDialogProps {
  type: ObjectType
  onClose: () => void
  /** 적용이 끝났을 때 — 목록을 다시 읽는다. */
  onApplied: () => void
}

const ACTION_LABEL: Record<ImportRow['action'], string> = {
  create: '새로',
  update: '고침',
  unchanged: '그대로',
  error: '오류',
}

const ACTION_VARIANT: Record<ImportRow['action'], 'default' | 'secondary' | 'outline' | 'destructive'> = {
  create: 'default',
  update: 'secondary',
  unchanged: 'outline',
  error: 'destructive',
}

/** 「그대로」 는 표에서 뺀다 — 300행 중 297행이 그대로면 나머지 셋이 안 보인다. */
const SHOW_UNCHANGED_BELOW = 20

export function ObjectImportDialog({ type, onClose, onApplied }: ObjectImportDialogProps) {
  const { user } = useAuth()
  const myWorkspace = user?.home_workspace_slug ?? user?.memberships[0]?.slug ?? null
  const [kind, setKind] = useState<Kind>('objects')
  const [file, setFile] = useState<File | null>(null)
  const [plan, setPlan] = useState<ImportPlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const run = async (apply: boolean) => {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const result =
        kind === 'objects'
          ? await objectApi.import(type.slug, file, { apply, workspaceSlug: myWorkspace })
          : await objectApi.importRelations(type.slug, file, { apply })
      setPlan(result)
      if (result.applied) onApplied()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const pick = (next: File | null) => {
    setFile(next)
    setPlan(null)
    setError(null)
  }

  const canApply = Boolean(plan && !plan.applied && plan.errors.length === 0 && plan.counts.error === 0)
  const nothingToDo = Boolean(plan && plan.counts.create + plan.counts.update === 0 && plan.counts.error === 0)
  const shownRows = plan
    ? plan.rows.filter(
        (one) => one.action !== 'unchanged' || plan.rows.length <= SHOW_UNCHANGED_BELOW,
      )
    : []

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{type.label} 파일로 넣기</DialogTitle>
          <DialogDescription>
            올리면 먼저 <strong>무엇이 바뀔지</strong>를 보여 줍니다. 한 행이라도 틀리면 아무것도
            안 넣습니다 — 고쳐서 다시 올리세요.
          </DialogDescription>
        </DialogHeader>

        <Tabs
          value={kind}
          onValueChange={(value) => {
            setKind(value as Kind)
            pick(null)
            if (inputRef.current) inputRef.current.value = ''
          }}
        >
          <TabsList>
            <TabsTrigger value="objects">객체</TabsTrigger>
            <TabsTrigger value="relations">관계</TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="text-muted-foreground space-y-1 text-xs">
          {kind === 'objects' ? (
            <>
              <p>
                열은 <code>key, label</code> 과 속성 키(또는 라벨). 같은 <code>key</code> 면 만들지
                않고 고칩니다. <strong>파일에 없는 열은 안 건드리고, 빈 칸은 「안 보냄」</strong>
                입니다 — 비우려면 <code>\null</code> 을 적습니다.
              </p>
              <p>
                여러 값은 <code>;</code> 로, 참조는 상대의 식별자(없으면 이름)로. 엑셀에서는
                「CSV UTF-8」 로 저장하세요.
              </p>
            </>
          ) : (
            <p>
              열은 <code>src, relation, dst, evidence_note</code>. 출발점은 {type.label}, 끝점은
              식별자(없으면 이름). 이미 이어진 것은 「그대로」 라 두 번 올려도 두 겹이 안 됩니다.
            </p>
          )}
          <div className="flex flex-wrap gap-2 pt-1">
            {kind === 'objects' && (
              <Button size="xs" variant="outline" onClick={() => void objectApi.template(type.slug)}>
                <Download className="mr-1 size-3" />
                템플릿 받기
              </Button>
            )}
            <Button
              size="xs"
              variant="outline"
              onClick={() =>
                void (kind === 'objects'
                  ? objectApi.export(type.slug, 'csv')
                  : objectApi.exportRelations(type.slug, 'csv'))
              }
            >
              <Download className="mr-1 size-3" />
              지금 것 내려받기
            </Button>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <input
            ref={inputRef}
            type="file"
            accept=".csv,.json,text/csv,application/json"
            className="text-sm"
            onChange={(event) => pick(event.target.files?.[0] ?? null)}
          />
          <Button size="sm" disabled={!file || busy} onClick={() => void run(false)}>
            {busy && !plan?.applied ? (
              <Loader2 className="mr-1 size-3.5 animate-spin" />
            ) : (
              <FileUp className="mr-1 size-3.5" />
            )}
            미리 보기
          </Button>
        </div>

        {error && <ErrorNotice error={error} />}

        {plan && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge>새로 {plan.counts.create}</Badge>
              <Badge variant="secondary">고침 {plan.counts.update}</Badge>
              <Badge variant="outline">그대로 {plan.counts.unchanged}</Badge>
              {plan.counts.error > 0 && <Badge variant="destructive">오류 {plan.counts.error}</Badge>}
              {plan.applied && <span className="text-emerald-600 dark:text-emerald-400">적용했습니다.</span>}
            </div>
            {plan.errors.length > 0 && (
              <ul className="text-destructive space-y-0.5 text-sm">
                {plan.errors.map((one) => (
                  <li key={one}>{one}</li>
                ))}
              </ul>
            )}
            {shownRows.length > 0 && (
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
                    {shownRows.map((one) => (
                      <tr key={one.row} className="border-t">
                        <td className="text-muted-foreground px-2 py-1 text-right tabular-nums">{one.row}</td>
                        <td className="px-2 py-1">
                          <Badge variant={ACTION_VARIANT[one.action]}>{ACTION_LABEL[one.action]}</Badge>
                        </td>
                        <td className="max-w-48 truncate px-2 py-1">
                          {one.label}
                          {one.key && <span className="text-muted-foreground ml-1 font-mono">{one.key}</span>}
                        </td>
                        <td className="px-2 py-1">
                          {one.action === 'error' ? (
                            <span className="text-destructive">{one.message}</span>
                          ) : (
                            <span className="text-muted-foreground">{one.changes.join(', ')}</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {plan.rows.length > SHOW_UNCHANGED_BELOW && plan.counts.unchanged > 0 && (
              <p className="text-muted-foreground text-xs">「그대로」 {plan.counts.unchanged}행은 표에서 뺐습니다.</p>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {plan?.applied ? '닫기' : '취소'}
          </Button>
          {plan && !plan.applied && (
            <Button
              disabled={!canApply || nothingToDo || busy}
              onClick={() => void run(true)}
              title={
                !canApply
                  ? '오류가 있으면 아무것도 안 넣습니다'
                  : nothingToDo
                    ? '바뀌는 것이 없습니다'
                    : undefined
              }
            >
              {busy && <Loader2 className="mr-1 size-3.5 animate-spin" />}
              적용 — 새로 {plan.counts.create} · 고침 {plan.counts.update}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
