/**
 * 일괄 입력 — **계획 먼저, 적용은 사람이 누른 뒤.**
 *
 * 이름이 「파일 가져오기」 였던 자리다. 기본 길이 표에 붙여넣기가 되면서 파일은 두 길 중
 * 하나가 됐고, 하는 일도 넣기만이 아니라 **같은 식별자면 수정**이다 — 그래서 방법(파일)이
 * 아니라 일(여러 건을 한 번에 입력)로 부른다.
 *
 * 정의 가져오기와 같은 무늬다. 표나 파일을 보내면 서버가 행마다 무엇이 될지(새로/고침/
 * 그대로/오류)를 돌려주고, 그것을 표로 보여 준다. **한 행이라도 오류면 「적용」 이
 * 안 선다** — 반쯤 들어간 파일은 어디까지 들어갔는지를 사람이 챙겨야 하고, 아무도
 * 안 챙긴다.
 *
 * 올리면 **작업**이 되고 워커가 뒤에서 돈다 — 파일은 한 번만 올리고, 계획도 적용도
 * 그 파일을 읽는다. 여기서는 작업을 기다리며 진행률을 그린다(`modules/jobs/api`).
 *
 * 객체 파일과 관계 파일은 탭으로 가른다. 열 모양이 다르고, 관계는 객체가 먼저
 * 있어야 하므로 순서도 다르다.
 */

import { useRef, useState } from 'react'
import { Download, FileUp, Loader2 } from 'lucide-react'

import { ontologyApi } from '@/modules/ontology/api'
import type { PropertyDef } from '@/modules/ontology/api'
import { PasteGrid, emptyRows, filledRows, toLines } from '@/shared/components/PasteGrid'
import type { GridColumn } from '@/shared/components/PasteGrid'
import { useResource } from '@/shared/hooks/useResource'
import { jobsApi, STUCK_MS } from '@/modules/jobs/api'
import type { Job } from '@/modules/jobs/api'
import { objectApi } from '@/modules/objects/api'
import type { ImportPlan } from '@/modules/objects/api'
import {
  ImportPlanTable,
  planChangesSomething,
  planIsClean,
} from '@/modules/objects/ImportPlanTable'
import type { ObjectType } from '@/modules/ontology/api'
import { useAuth } from '@/shared/auth/AuthContext'
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
import { Tabs, TabsList, TabsTrigger } from '@/shared/components/ui/tabs'

type Kind = 'objects' | 'relations'
/** 어디서 입력하나 — **표에 붙여넣기가 기본**이고, 파일 업로드는 그 옆이다. */
type Source = 'grid' | 'file'

/** 관계 표의 열 — 서버가 받는 네 개 그대로. */
const RELATION_COLUMNS: GridColumn[] = [
  { key: 'src', header: 'src', label: '출발', help: '식별자(없으면 이름)', required: true },
  { key: 'relation', header: 'relation', label: '관계', help: '관계 종류의 slug', required: true },
  { key: 'dst', header: 'dst', label: '도착', help: '식별자(없으면 이름)', required: true },
  { key: 'evidence_note', header: 'evidence_note', label: '근거', help: '어느 문서에서 나왔나' },
]

/**
 * 객체 표의 열 — **파일 템플릿과 같은 열**이어야 한다. 표와 파일이 다른 것을 받으면
 * 「표로는 되는데 파일로는 안 되는」 상태가 생긴다.
 */
function objectColumns(type: ObjectType, properties: PropertyDef[]): GridColumn[] {
  const columns: GridColumn[] = []
  if (type.key_policy !== 'none') {
    columns.push({
      key: 'key',
      header: 'key',
      label: '식별자',
      help: '같은 값이면 수정합니다',
      required: type.key_policy === 'required',
    })
  }
  columns.push({ key: 'label', header: 'label', label: '이름', required: true })
  for (const one of properties) {
    // 파일 칸은 표로 못 넣는다 — 첨부는 객체 상세에서 업로드한다.
    if (one.data_type === 'file') continue
    columns.push({
      key: one.key,
      header: one.key,
      // **속성의 이름을 크게.** 머리에 `plm_task` 만 있으면 그것이 무엇인지 표가 말해 주지
      // 못한다 — 정의에 적어 둔 이름이 여기 쓰인다.
      label: one.label,
      help: help(one),
      required: one.required,
    })
  }
  columns.push({ key: 'aliases', header: 'aliases', label: '다른 이름', help: '; 로 여럿' })
  return columns
}

/** 칸 아래 한 줄 — 무엇을 적어야 하는지. 단위·고를 값이 여기서 갈린다. */
function help(one: PropertyDef): string {
  const parts: string[] = []
  if (one.data_type === 'enum' && one.enum_options?.length) {
    parts.push(one.enum_options.slice(0, 4).join(' · ') + (one.enum_options.length > 4 ? ' …' : ''))
  } else if (one.data_type === 'object_ref') {
    parts.push(`${one.ref_type_slug ?? ''} 의 식별자(없으면 이름)`)
  } else if (one.data_type === 'bool') {
    parts.push('예 / 아니오')
  } else if (one.unit) {
    parts.push(one.unit)
  }
  if (one.multi) parts.push('; 로 여럿')
  return parts.join(' · ')
}

interface ObjectImportDialogProps {
  type: ObjectType
  onClose: () => void
  /** 적용이 끝났을 때 — 목록을 다시 읽는다. */
  onApplied: () => void
}

export function ObjectImportDialog({ type, onClose, onApplied }: ObjectImportDialogProps) {
  const { user } = useAuth()
  const myWorkspace = user?.home_workspace_slug ?? user?.memberships[0]?.slug ?? null
  const [kind, setKind] = useState<Kind>('objects')
  const [file, setFile] = useState<File | null>(null)
  /** 「표에 붙여넣기」 — 파일을 못 만드는 곳(사내 DRM)에서 이 길이 유일하다. */
  const [grid, setGrid] = useState<string[][] | null>(null)
  const [source, setSource] = useState<Source>('grid')
  const [plan, setPlan] = useState<ImportPlan | null>(null)
  /** 계획을 세운 작업 — 적용은 이 작업의 파일 · 지문으로 간다. */
  const [planJob, setPlanJob] = useState<Job | null>(null)
  const [running, setRunning] = useState<Job | null>(null)
  /** 「대기」 가 길어졌나 — 워커가 안 떠 있다는 뜻일 수 있다. */
  const [stuck, setStuck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // **정의가 표의 열을 만든다.** 속성을 더하면 표에 그 칸이 저절로 선다 — 안내 문구를 따로
  // 고칠 자리가 없다.
  const properties = useResource(() => ontologyApi.properties(type.slug), [type.slug])
  const columns = kind === 'objects' ? objectColumns(type, properties.data ?? []) : RELATION_COLUMNS
  const rows = grid ?? emptyRows(columns)
  const filled = filledRows(rows)

  const download = (job: () => Promise<void>) => {
    setError(null)
    job().catch((caught: unknown) =>
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류')),
    )
  }

  /** 작업이 끝날 때까지 기다려 결과(계획 표)를 꺼낸다. 실패한 작업은 오류로 보인다. */
  const settle = async (started: Job): Promise<ImportPlan | null> => {
    setRunning(started)
    setStuck(false)
    const since = Date.now()
    const finished = await jobsApi.waitFor(started.id, (job) => {
      setRunning(job)
      // 도는 중이면 오래 걸려도 괜찮다 — **대기**가 길면 집을 워커가 없는 것이다.
      setStuck(job.status === 'queued' && Date.now() - since > STUCK_MS)
    })
    setRunning(null)
    setStuck(false)
    if (finished.status !== 'done' || !finished.result) {
      setError(
        new Error(
          finished.error ??
            (finished.status === 'cancelled' ? '취소됐습니다.' : '작업이 끝나지 않았습니다.'),
        ),
      )
      return null
    }
    return finished.result as unknown as ImportPlan
  }

  const run = async (apply: boolean) => {
    if (!apply && source === 'file' && !file) return
    if (!apply && source === 'grid' && filled === 0) return
    setBusy(true)
    setError(null)
    // 같은 경로를 다시 고를 수 있게 입력 칸을 비운다 — 안 비우면 change 가 안 나고,
    // 엑셀에서 고친 파일을 다시 골라도 옛 File 객체가 나가 「Failed to fetch」 로 끝난다.
    if (inputRef.current) inputRef.current.value = ''
    try {
      if (apply) {
        // **파일을 다시 올리지 않는다.** 계획을 세운 작업의 파일 · 지문으로 적용한다 —
        // 그 사이에 누가 바꿨으면 서버가 거절하고 다시 보게 한다.
        if (!planJob) return
        const result = await settle(await jobsApi.apply(planJob.id))
        if (result) {
          setPlan(result)
          if (result.applied) onApplied()
        }
        return
      }
      // **표도 파일이 된다.** 서버로는 한 길만 간다 — 형식이 둘이면 두 곳이 갈라지고,
      // 그때 「표로는 되는데 파일로는 안 되는」 상태가 생긴다.
      const sending =
        source === 'file'
          ? file
          : new File([toLines(columns, rows).join('\n')], 'pasted.csv', { type: 'text/plain' })
      if (!sending) return
      const started =
        kind === 'objects'
          ? await objectApi.import(type.slug, sending, {
              workspaceSlug: myWorkspace,
            })
          : await objectApi.importRelations(type.slug, sending)
      setPlanJob(started)
      const result = await settle(started)
      if (result) setPlan(result)
    } catch (caught) {
      setRunning(null)
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const pick = (next: File | null) => {
    setFile(next)
    setPlan(null)
    setPlanJob(null)
    setError(null)
  }

  /** 길을 바꾸면 앞의 계획은 버린다 — 그 계획은 다른 것을 보고 세운 것이다. */
  const pickSource = (next: Source) => {
    setSource(next)
    setPlan(null)
    setPlanJob(null)
    setError(null)
  }

  const canApply = Boolean(plan && !plan.applied && planIsClean(plan))
  const nothingToDo = Boolean(plan && !planChangesSomething(plan) && plan.counts.error === 0)

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      {/* **넓게 연다.** 표에 붙여넣는 자리라 열이 열 개를 넘을 수 있고, 좁은 창에서는 그
          전부가 가로 스크롤 뒤에 숨는다. */}
      <DialogContent className="max-h-[85vh] sm:max-w-[80vw]">
        <DialogHeader>
          <DialogTitle>{type.label} 일괄 입력</DialogTitle>
          <DialogDescription>
            보내면 먼저 <strong>무엇이 바뀔지</strong>를 보여 줍니다. 한 행이라도 틀리면 아무것도
            추가하지 않습니다 — 수정해서 다시 업로드하세요.
          </DialogDescription>
        </DialogHeader>

        <Tabs
          value={kind}
          onValueChange={(value) => {
            setKind(value as Kind)
            // 객체와 관계는 열이 다르다 — 표를 그대로 두면 엉뚱한 칸에 값이 남는다.
            setGrid(null)
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
                않고 수정합니다.{' '}
                <strong>파일에 없는 열은 변경하지 않고, 빈 칸은 「안 보냄」</strong>
                입니다 — 비우려면 <code>\null</code> 을 적습니다.
              </p>
              <p>
                여러 값은 <code>;</code> 로, 참조는 상대의 식별자(없으면 이름)로. 엑셀에서는 「CSV
                UTF-8」 로 저장하세요.
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
              <Button
                size="xs"
                variant="outline"
                onClick={() => download(() => objectApi.template(type.slug))}
              >
                <Download className="mr-1 size-3" />
                템플릿 다운로드
              </Button>
            )}
            <Button
              size="xs"
              variant="outline"
              onClick={() =>
                download(() =>
                  kind === 'objects'
                    ? objectApi.export(type.slug, 'csv')
                    : objectApi.exportRelations(type.slug, 'csv'),
                )
              }
            >
              <Download className="mr-1 size-3" />
              현재 목록 다운로드
            </Button>
          </div>
        </div>

        {/* **두 길을 나란히 둔다.** 파일이 있으면 올리고, 없으면 표에 붙인다 — 사내 DRM 이
            저장을 잠그는 곳에서는 표가 유일한 길이다. */}
        <Tabs value={source} onValueChange={(value) => pickSource(value as Source)}>
          <TabsList>
            <TabsTrigger value="grid">표에 붙여넣기</TabsTrigger>
            <TabsTrigger value="file">파일 업로드</TabsTrigger>
          </TabsList>
        </Tabs>

        {source === 'file' ? (
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
        ) : (
          <div className="space-y-2">
            <PasteGrid columns={columns} rows={rows} onRows={setGrid} />
            <Button size="sm" disabled={filled === 0 || busy} onClick={() => void run(false)}>
              {busy && !plan?.applied ? (
                <Loader2 className="mr-1 size-3.5 animate-spin" />
              ) : (
                <FileUp className="mr-1 size-3.5" />
              )}
              미리 보기 — {filled}줄
            </Button>
          </div>
        )}

        {running && (
          <p className="text-muted-foreground flex items-center gap-2 text-xs" role="status">
            <Loader2 className="size-3.5 animate-spin" />
            {running.status === 'queued'
              ? stuck
                ? '아직 아무도 집어 가지 않았습니다 — 작업 워커가 꺼져 있을 수 있습니다. 「내 활동 › 작업」 에서 확인하세요(파일은 이미 올라가 있습니다).'
                : '워커를 기다리는 중 — 작업 화면에서도 볼 수 있습니다.'
              : running.progress.total > 0
                ? `${running.progress.stage} ${running.progress.done.toLocaleString()} / ${running.progress.total.toLocaleString()}행`
                : `${running.progress.stage || '진행 중'}…`}
          </p>
        )}

        {error && <ErrorNotice error={error} />}

        {plan && <ImportPlanTable plan={plan} />}

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
                  ? '오류가 있으면 아무것도 추가하지 않습니다'
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
