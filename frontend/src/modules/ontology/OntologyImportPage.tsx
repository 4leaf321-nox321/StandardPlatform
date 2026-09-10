/**
 * 가져오기와 되돌리기 — **기계가 정의를 만들 때 필요한 자리.**
 *
 * 사람은 타입 하나를 5분에 만들고 에이전트는 200개를 5초에 만든다. 그러면
 * **적용 전에 무엇이 바뀌는지 보는 자리**와 **되돌릴 자리**가 반드시 있어야 한다 —
 * 감사 로그는 누가 뭘 했는지는 알려 주지만 되돌려 주지 않는다.
 */

import { useState } from 'react'
import { AlertTriangle, Download, History, Play } from 'lucide-react'

import { useOntology } from '@/modules/ontology/OntologyLayout'
import { ontologyApi } from '@/modules/ontology/api'
import type { ImportPlan } from '@/modules/ontology/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import { Textarea } from '@/shared/components/ui/textarea'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

const ACTION_LABELS: Record<string, string> = {
  create: '새로 만듦',
  update: '고침',
  unchanged: '그대로',
}

const KIND_LABELS: Record<string, string> = {
  group: '묶음',
  type: '타입',
  property: '속성',
  relation_type: '관계 종류',
}

export default function OntologyImportPage() {
  const { schema, reload } = useOntology()
  const snapshots = useResource(() => ontologyApi.snapshots(), [])
  const [text, setText] = useState('')
  const [plan, setPlan] = useState<ImportPlan | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)
  const [restoring, setRestoring] = useState<string | null>(null)

  async function run(dryRun: boolean) {
    setError(null)
    setBusy(true)
    try {
      const body = JSON.parse(text)
      const got = await ontologyApi.importSchema(body, dryRun)
      setPlan(got)
      if (got.applied) {
        reload()
        snapshots.reload()
      }
    } catch (caught) {
      setPlan(null)
      setError(
        caught instanceof SyntaxError
          ? new Error(`JSON 을 읽지 못했습니다: ${caught.message}`)
          : caught instanceof Error
            ? caught
            : new Error('알 수 없는 오류'),
      )
    } finally {
      setBusy(false)
    }
  }

  /** 지금 정의를 그대로 꺼내 준다 — **고쳐서 다시 넣는 것이 가장 흔한 일이다.** */
  function exportCurrent() {
    if (!schema) return
    const body = {
      groups: schema.groups.map(({ id: _id, ...rest }) => rest),
      types: schema.types.map(({ id: _id, object_count: _c, nav_group_id: _g, ...rest }) => ({
        ...rest,
        properties: rest.properties.map(({ id: _p, owner_id: _o, owner_kind: _k, ...one }) => one),
      })),
      relation_types: schema.relation_types.map(({ id: _id, ...rest }) => rest),
    }
    setText(JSON.stringify(body, null, 2))
  }

  const changed = (plan?.changes ?? []).filter((one) => one.action !== 'unchanged')

  return (
    <div className="space-y-5">
      <p className="text-muted-foreground text-sm">
        정의를 통째로 받아 <b>한 트랜잭션으로</b> 적용합니다. 중간에 실패하면 반쯤 만들어진
        온톨로지가 남지 않습니다. <b>더하고 고치기만 합니다</b> — 스키마에 없다고 지우지
        않습니다(부분 스키마를 한 번 보낸 날 그 타입의 객체가 갈 곳을 잃습니다).
      </p>

      {error && <ErrorNotice error={error} />}

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm font-medium">스키마 (JSON)</span>
          <Button variant="outline" size="sm" onClick={exportCurrent} disabled={!schema}>
            <Download className="mr-1 size-4" />
            지금 정의 꺼내기
          </Button>
        </div>
        <Textarea
          rows={12}
          value={text}
          placeholder={'{\n  "types": [\n    { "slug": "part", "label": "부품" }\n  ]\n}'}
          className="font-mono text-xs"
          onChange={(event) => setText(event.target.value)}
        />
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => run(true)} disabled={busy || !text.trim()}>
            <Play className="mr-1 size-4" />
            미리 보기
          </Button>
          <Button
            onClick={() => run(false)}
            // **미리 보기를 통과해야 적용한다.** 사람이 계획을 한 번은 읽게 한다.
            disabled={busy || !plan || plan.applied || plan.errors.length > 0}
          >
            적용
          </Button>
        </div>
      </div>

      {plan && (
        <section className="space-y-3 rounded-md border p-4">
          <h2 className="text-base font-semibold">
            {plan.applied ? '적용했습니다' : '적용하면 이렇게 됩니다'}
          </h2>
          {plan.applied && changed.length === 0 && (
            <p className="text-muted-foreground text-sm">
              <b>바뀐 것이 없습니다.</b> 그 시점의 정의가 지금과 같거나, 되돌리려던 것이
              그때는 아직 없던 것일 수 있습니다 — 가져오기는 지우지 않습니다.
            </p>
          )}

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
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </section>
      )}

      <section className="space-y-3">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <History className="size-4" />
          정의 이력
        </h2>
        <p className="text-muted-foreground text-xs">
          가져오기 <b>직전</b>의 정의를 남깁니다. 되돌리기는 그때의 정의를 다시 덮어씌우는
          일이고, <b>그 뒤에 새로 만든 것은 안 지웁니다</b> — 지우면 그 사이에 쌓인 객체가
          갈 곳을 잃습니다.
        </p>

        {(snapshots.data ?? []).length === 0 ? (
          <EmptyState title="아직 이력이 없습니다" hint="가져오기를 한 번 적용하면 남습니다." />
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>때</TableHead>
                  <TableHead>누가</TableHead>
                  <TableHead>무엇 직전</TableHead>
                  <TableHead className="text-right">타입·관계</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {(snapshots.data ?? []).map((one) => (
                  <TableRow key={one.id}>
                    <TableCell>{shownDateTime(one.taken_at)}</TableCell>
                    <TableCell>{one.actor_label}</TableCell>
                    <TableCell className="text-muted-foreground">{one.reason}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {one.type_count} · {one.relation_count}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" onClick={() => setRestoring(one.id)}>
                        되돌리기
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </section>

      {restoring && (
        <ConfirmDialog
          open
          destructive
          title="그때의 정의로 되돌립니다"
          description={
            <>
              그 시점의 묶음·타입·속성·관계를 <b>다시 덮어씌웁니다</b>. 그 뒤에 새로 만든
              것은 <b>안 지웁니다</b> — 지우면 그 사이에 쌓인 객체가 갈 곳을 잃습니다.
              되돌리기 직전의 모습도 이력에 남습니다.
            </>
          }
          confirmLabel="되돌리기"
          onConfirm={async () => {
            // **되돌린 결과를 보여 준다.** 그 스냅샷이 지금과 같으면 아무 일도
            // 안 일어나는데(예: 그때는 없던 타입), 화면이 조용하면 사람은
            // 되돌리기가 고장 났다고 읽는다.
            setPlan(await ontologyApi.restore(restoring))
            reload()
            snapshots.reload()
          }}
          onClose={() => setRestoring(null)}
        />
      )}
    </div>
  )
}
