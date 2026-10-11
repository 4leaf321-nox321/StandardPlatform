/**
 * 검토함 — **사람이 판단할 일을 한 곳에서, 한꺼번에**(ADR 0027).
 *
 * 별칭 후보 · 검수 대기 별칭 · 병합 후보 · 지워진 것을 가리키는 칸은 각자의 화면에 흩어져 있었고,
 * 홈 「남은 일」 은 수만 말했다. 여기서는 원래 자리에서 읽어 한 줄(물음 · 근거 · 후보 · 추천 ·
 * 확신도)로 세우고, **한꺼번에 확인**하게 한다:
 *
 * - 「추천대로 모두」 — 지금 거른 것 중 확신도가 문턱 이상인 것을. 누르기 전에 몇 건이 무엇으로
 *   되는지 미리 보이고(막히는 것은 따로 세어 빼고), 적용한다.
 * - 선택(체크) 후 「추천대로」 · 「무시」 · 「다른 것으로」. 「이 쪽 전부」 · 「거른 전부」 선택.
 * - 「같은 것끼리 묶기」 — 같은 말 · 같은 지워진 객체를 한 줄로 접어 한 번에.
 * - 키보드 — j/k 이동 · x 선택 · a 추천대로 · i 무시 · 1~9 후보.
 *
 * 한 줄 결정은 복원할 수 있는 것이면 바로 적용하고(아래에 「복원」), 복원할 수 없는 것(병합) ·
 * 막히는 것이 있으면 미리 보기를 먼저 띄운다. 처리는 원래 기능이 한다 — 권한도 원래 화면의
 * 것이라, 못 고치는 줄은 「막힘」 배지에 까닭을 적는다.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ExternalLink, Inbox, Loader2 } from 'lucide-react'

import { jobsApi } from '@/modules/jobs/api'
import { ReviewPlanDialog } from '@/modules/review/ReviewPlanDialog'
import { ReviewRecent, ReviewUndoDialog } from '@/modules/review/ReviewRecent'
import { reviewApi, targetsOf } from '@/modules/review/api'
import type {
  ReviewAction,
  ReviewApplyMode,
  ReviewChoice,
  ReviewDecision,
  ReviewFilters,
  ReviewItem,
  ReviewKind,
  ReviewPlan,
  ReviewPlanRequest,
} from '@/modules/review/api'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Pagination } from '@/shared/components/Pagination'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import { Input } from '@/shared/components/ui/input'
import { useResource } from '@/shared/hooks/useResource'

const PAGE = 50
const DEFAULT_THRESHOLD = 0.9

/** 확신도 거르기 — 「추천대로 모두」 의 문턱과는 따로다(보는 것과 적용하는 것). */
const BANDS: { key: string; label: string; min?: number; max?: number }[] = [
  { key: '', label: '전체' },
  { key: '0.9', label: '0.9 이상', min: 0.9 },
  { key: '0.7', label: '0.7 이상', min: 0.7 },
  { key: 'low', label: '0.7 미만', max: 0.7 },
]

function rowKey(item: ReviewItem): string {
  return `${item.kind}:${item.id}`
}

function confidenceText(value: number | null): string {
  return value === null ? '추천 없음' : `확신도 ${value.toFixed(2)}`
}

/** 고른 줄 모두에게 있는 후보 — 「다른 것으로」 가 고를 수 있는 것. */
function commonChoices(rows: ReviewItem[]): ReviewChoice[] {
  if (rows.length === 0) return []
  return rows[0].choices.filter((choice) =>
    rows.slice(1).every((row) => row.choices.some((one) => one.value === choice.value)),
  )
}

function decisionsFor(rows: ReviewItem[], action: ReviewAction, value?: string): ReviewDecision[] {
  return rows.flatMap((row) =>
    targetsOf(row).map((itemId) => ({
      kind: row.kind,
      item_id: itemId,
      action,
      ...(value !== undefined ? { value } : {}),
    })),
  )
}

interface Pending {
  plan: ReviewPlan
  title: string
  mode: ReviewApplyMode
}

export default function ReviewPage() {
  const [params, setParams] = useSearchParams()
  const kind = params.get('kind') || null
  const [type, setType] = useState<string | null>(null)
  const [workspace, setWorkspace] = useState<string | null>(null)
  const [band, setBand] = useState('')
  const [group, setGroup] = useState(false)
  const [actionable, setActionable] = useState(false)
  const [offset, setOffset] = useState(0)
  const [threshold, setThreshold] = useState(String(DEFAULT_THRESHOLD))

  const filters: ReviewFilters = useMemo(() => {
    const chosen = BANDS.find((one) => one.key === band)
    return {
      kind,
      type,
      workspace,
      min_confidence: chosen?.min ?? null,
      max_confidence: chosen?.max ?? null,
      actionable,
    }
  }, [kind, type, workspace, band, actionable])

  const page = useResource(
    () => reviewApi.items(filters, { group, offset, limit: PAGE }),
    [filters, group, offset],
  )
  const items = useMemo(() => page.data?.items ?? [], [page.data])

  /** 종류 칩 — 종류로 거르면 서버는 그 종류만 세므로, 전체를 본 마지막 답을 기억해 둔다. */
  const [allKinds, setAllKinds] = useState<ReviewKind[]>([])
  useEffect(() => {
    if (page.data && !kind) setAllKinds(page.data.kinds)
  }, [page.data, kind])
  const shownKinds = page.data?.kinds ?? []
  const kinds = kind && allKinds.length > 0 ? allKinds : shownKinds

  const [selected, setSelected] = useState<Set<string>>(new Set())
  /** 「거른 전부」 — 쪽을 넘어 지금 거르기에 맞는 전부. 결정은 서버가 다시 읽어 고른다. */
  const [everything, setEverything] = useState(false)
  const [focus, setFocus] = useState(0)
  const [pending, setPending] = useState<Pending | null>(null)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState('')
  const [error, setError] = useState<Error | null>(null)
  const [said, setSaid] = useState('')
  const [lastBatch, setLastBatch] = useState<string | null>(null)
  const [undoing, setUndoing] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const rowRefs = useRef<Map<string, HTMLLIElement>>(new Map())

  // 거르기가 바뀌면 고른 것 · 위치를 비운다 — 안 보이는 줄이 고른 채로 남으면 그것까지 적용된다.
  useEffect(() => {
    setSelected(new Set())
    setEverything(false)
    setFocus(0)
  }, [filters, group, offset])

  const chosenRows = useMemo(
    () => items.filter((one) => selected.has(rowKey(one))),
    [items, selected],
  )

  const reset = () => {
    setSelected(new Set())
    setEverything(false)
    page.reload()
    setVersion((value) => value + 1)
  }

  async function guard(work: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await work()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
      setProgress('')
    }
  }

  function finish(batchId: string | null, counts: Record<string, number>, undoable: number) {
    const done = counts.done ?? 0
    const refused = counts.refused ?? 0
    setSaid(
      `${done.toLocaleString()}건을 처리했습니다.` +
        (refused ? ` ${refused.toLocaleString()}건은 그 사이 막혀 처리하지 않았습니다.` : ''),
    )
    setLastBatch(batchId && undoable > 0 ? batchId : null)
    setPending(null)
    reset()
  }

  async function runApply(plan: ReviewPlan, mode: ReviewApplyMode) {
    const done = await reviewApi.apply(plan, mode)
    if (done.job_id) {
      // 큰 묶음 — 작업으로 돈다. 화면을 닫아도 계속되고, 여기서는 진행을 본다.
      setProgress('작업으로 실행하는 중…')
      const job = await jobsApi.waitFor(done.job_id, (tick) => {
        if (tick.progress.total) {
          setProgress(
            `작업으로 실행하는 중 — ${tick.progress.done.toLocaleString()} / ${tick.progress.total.toLocaleString()}`,
          )
        }
      })
      if (job.status !== 'done') throw new Error(job.error ?? '작업이 끝나지 않았습니다.')
      const result = (job.result ?? {}) as {
        batch_id?: string
        counts?: Record<string, number>
        rows?: { status: string; undoable: boolean }[]
      }
      const undoable = (result.rows ?? []).filter((one) => one.status === 'done' && one.undoable)
      finish(result.batch_id ?? null, result.counts ?? {}, undoable.length)
      return
    }
    finish(done.batch_id, done.counts, done.undoable)
  }

  /** 미리 보기 — 한 줄이고 복원할 수 있고 막힘이 없으면 바로 적용한다(아래 「복원」 이 선다). */
  const start = (request: ReviewPlanRequest, title: string, mode: ReviewApplyMode) =>
    guard(async () => {
      setSaid('')
      const plan = await reviewApi.plan(request)
      const quick =
        mode === 'one' &&
        plan.counts.blocked === 0 &&
        plan.counts.apply > 0 &&
        !plan.as_job &&
        plan.by_kind.every((one) => one.undoable)
      if (quick) {
        await runApply(plan, mode)
        return
      }
      setPending({ plan, title, mode })
    })

  const decide = (rows: ReviewItem[], action: ReviewAction, value?: string) => {
    const live = rows.filter((row) => row.actionable)
    if (live.length === 0) return
    const titles: Record<ReviewAction, string> = {
      accept: '추천대로',
      ignore: '무시',
      choose: '다른 것으로',
    }
    return start(
      { mode: 'decisions', decisions: decisionsFor(live, action, value) },
      `${titles[action]} — 미리 보기`,
      live.length === 1 && !live[0].members.length ? 'one' : 'selected',
    )
  }

  /** 고른 것이 있으면 그것, 없으면 지금 줄. */
  const targets = (): ReviewItem[] => {
    if (chosenRows.length > 0) return chosenRows
    const row = items[focus]
    return row ? [row] : []
  }

  const recommendAll = () => {
    const value = Number(threshold)
    if (!Number.isFinite(value) || value < 0 || value > 1) {
      setError(new Error('문턱은 0 ~ 1 사이의 수입니다.'))
      return
    }
    void start(
      { mode: 'recommended', filters, threshold: value },
      `추천대로 모두 — 확신도 ${value.toFixed(2)} 이상`,
      'recommended',
    )
  }

  const everythingAs = (action: 'accept' | 'ignore') =>
    void start(
      { mode: 'all', filters, action },
      `거른 전부 ${action === 'accept' ? '추천대로' : '무시'} — 미리 보기`,
      'all',
    )

  const toggle = (row: ReviewItem) => {
    if (!row.actionable) return
    setEverything(false)
    setSelected((current) => {
      const next = new Set(current)
      const key = rowKey(row)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  // --- 키보드 --------------------------------------------------------------------
  const keys = useRef<(event: KeyboardEvent) => void>(() => {})
  keys.current = (event: KeyboardEvent) => {
    if (pending || undoing || busy) return
    if (event.metaKey || event.ctrlKey || event.altKey) return
    const target = event.target as HTMLElement | null
    if (
      target &&
      (target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.tagName === 'SELECT' ||
        target.isContentEditable)
    ) {
      return
    }
    const row = items[focus]
    if (event.key === 'j') {
      setFocus((at) => Math.min(at + 1, Math.max(items.length - 1, 0)))
    } else if (event.key === 'k') {
      setFocus((at) => Math.max(at - 1, 0))
    } else if (event.key === 'x' && row) {
      toggle(row)
    } else if (event.key === 'a') {
      void decide(targets(), 'accept')
    } else if (event.key === 'i') {
      void decide(
        targets().filter((one) => one.ignorable),
        'ignore',
      )
    } else if (/^[1-9]$/.test(event.key) && row) {
      const choice = row.choices[Number(event.key) - 1]
      if (choice) void decide([row], 'choose', choice.value)
    } else {
      return
    }
    event.preventDefault()
  }
  useEffect(() => {
    const listener = (event: KeyboardEvent) => keys.current(event)
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [])
  useEffect(() => {
    const row = items[focus]
    if (row) rowRefs.current.get(rowKey(row))?.scrollIntoView?.({ block: 'nearest' })
  }, [focus, items])

  // --- 그리기 --------------------------------------------------------------------
  const sections = useMemo(() => {
    const out: { kind: string; rows: { row: ReviewItem; index: number }[] }[] = []
    items.forEach((row, index) => {
      const last = out[out.length - 1]
      if (last && last.kind === row.kind) last.rows.push({ row, index })
      else out.push({ kind: row.kind, rows: [{ row, index }] })
    })
    return out
  }, [items])
  const kindOf = (name: string) =>
    (page.data?.kinds ?? []).find((one) => one.kind === name) ??
    allKinds.find((one) => one.kind === name)
  const pageRows = items.filter((one) => one.actionable)
  const allOnPage = pageRows.length > 0 && pageRows.every((one) => selected.has(rowKey(one)))
  const common = commonChoices(chosenRows)
  const total = page.data?.total ?? 0

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <PageHeader
        title="검토함"
        description="사람이 판단할 일을 원래 자리에서 모았습니다 — 내가 볼 수 있는 것만. 처리는 원래 기능이 하고, 결정은 기록에 남습니다."
      />

      <div className="flex flex-wrap gap-1.5">
        <Button
          size="sm"
          variant={kind ? 'outline' : 'default'}
          onClick={() => {
            setOffset(0)
            setParams({})
          }}
        >
          전체
        </Button>
        {kinds.map((one) => (
          <Button
            key={one.kind}
            size="sm"
            variant={kind === one.kind ? 'default' : 'outline'}
            onClick={() => {
              setOffset(0)
              setParams({ kind: one.kind })
            }}
          >
            {one.label}
            <Badge variant="secondary" className="ml-1">
              {one.matched === one.total ? one.total : `${one.matched}/${one.total}`}
            </Badge>
          </Button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <span className="flex items-center gap-1">
          <span className="text-muted-foreground">확신도</span>
          {BANDS.map((one) => (
            <Button
              key={one.key}
              size="sm"
              variant={band === one.key ? 'secondary' : 'ghost'}
              onClick={() => {
                setOffset(0)
                setBand(one.key)
              }}
            >
              {one.label}
            </Button>
          ))}
        </span>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={group}
            onChange={(event) => {
              setOffset(0)
              setGroup(event.target.checked)
            }}
          />
          같은 것끼리 묶기
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={actionable}
            onChange={(event) => {
              setOffset(0)
              setActionable(event.target.checked)
            }}
          />
          처리할 수 있는 것만
        </label>
      </div>

      {((page.data?.types.length ?? 0) > 1 || type || (page.data?.workspaces.length ?? 0) > 1 || workspace) && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted-foreground">타입</span>
          {type ? (
            <Button size="sm" variant="secondary" onClick={() => setType(null)}>
              {page.data?.types.find((one) => one.value === type)?.label ?? type} ✕
            </Button>
          ) : (
            (page.data?.types ?? []).slice(0, 8).map((one) => (
              <Button key={one.value} size="sm" variant="ghost" onClick={() => setType(one.value)}>
                {one.label} {one.count}
              </Button>
            ))
          )}
          <span className="text-muted-foreground ml-3">부서</span>
          {workspace ? (
            <Button size="sm" variant="secondary" onClick={() => setWorkspace(null)}>
              {page.data?.workspaces.find((one) => one.value === workspace)?.label ?? workspace} ✕
            </Button>
          ) : (
            (page.data?.workspaces ?? []).slice(0, 8).map((one) => (
              <Button
                key={one.value}
                size="sm"
                variant="ghost"
                onClick={() => setWorkspace(one.value)}
              >
                {one.label} {one.count}
              </Button>
            ))
          )}
        </div>
      )}

      <div
        role="toolbar"
        aria-label="일괄 확인"
        className="bg-muted/40 flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm"
      >
        <Button size="sm" disabled={busy || total === 0} onClick={recommendAll}>
          추천대로 모두
        </Button>
        <label className="flex items-center gap-1">
          <span className="text-muted-foreground">문턱</span>
          <Input
            aria-label="문턱"
            className="h-8 w-16"
            value={threshold}
            onChange={(event) => setThreshold(event.target.value)}
          />
        </label>
        <span className="text-muted-foreground mx-1">|</span>
        <span className="text-muted-foreground">
          {everything
            ? `거른 전부 ${total.toLocaleString()}건 선택`
            : `${chosenRows.length}건 선택`}
        </span>
        {everything ? (
          <>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => everythingAs('accept')}>
              추천대로
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => everythingAs('ignore')}>
              무시
            </Button>
          </>
        ) : (
          <>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || chosenRows.length === 0}
              onClick={() => void decide(chosenRows, 'accept')}
            >
              추천대로
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || chosenRows.length === 0}
              onClick={() =>
                void decide(
                  chosenRows.filter((one) => one.ignorable),
                  'ignore',
                )
              }
            >
              무시
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || common.length === 0}
                  title={
                    chosenRows.length > 0 && common.length === 0
                      ? '선택한 줄 모두에게 있는 후보가 없습니다'
                      : undefined
                  }
                >
                  다른 것으로
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                {common.map((one) => (
                  <DropdownMenuItem
                    key={one.value}
                    onSelect={() => void decide(chosenRows, 'choose', one.value)}
                  >
                    {one.label}
                    {one.detail && <span className="text-muted-foreground ml-1">{one.detail}</span>}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        )}
        <span className="text-muted-foreground mx-1">|</span>
        <Button
          size="sm"
          variant="ghost"
          disabled={pageRows.length === 0}
          onClick={() => {
            setEverything(false)
            setSelected(allOnPage ? new Set() : new Set(pageRows.map(rowKey)))
          }}
        >
          {allOnPage ? '선택 해제' : '이 쪽 전부 선택'}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={total === 0}
          onClick={() => {
            setSelected(new Set())
            setEverything((value) => !value)
          }}
        >
          {everything ? '거른 전부 해제' : '거른 전부 선택'}
        </Button>
      </div>

      <p className="text-muted-foreground text-xs" aria-label="단축키">
        단축키 — <kbd>j</kbd>/<kbd>k</kbd> 이동 · <kbd>x</kbd> 선택 · <kbd>a</kbd> 추천대로 ·{' '}
        <kbd>i</kbd> 무시 · <kbd>1</kbd>~<kbd>9</kbd> 그 후보로. 선택한 것이 있으면 선택한 것에, 없으면
        지금 줄에 적용합니다. 복원할 수 있는 한 줄은 바로 적용하고, 그 밖에는 미리 보기를 먼저
        표시합니다.
      </p>

      {error && !pending && <ErrorNotice error={error} />}
      {progress && !pending && <p className="text-muted-foreground text-sm">{progress}</p>}
      {said && (
        <p className="flex items-center gap-2 text-sm">
          {said}
          {lastBatch && (
            <Button size="sm" variant="outline" onClick={() => setUndoing(lastBatch)}>
              복원
            </Button>
          )}
        </p>
      )}
      {page.error && <ErrorNotice error={page.error} />}
      {page.loading && !page.data && (
        <Loader2 className="text-muted-foreground size-5 animate-spin" />
      )}

      {page.data && items.length === 0 && (
        <EmptyState
          title="검토할 항목이 없습니다"
          hint={[...page.data.notes, ...page.data.kinds.flatMap((one) => one.notes)].join(' ')}
        />
      )}

      {sections.map((section) => {
        const info = kindOf(section.kind)
        return (
          <section key={section.kind} className="space-y-2">
            <h2 className="flex flex-wrap items-center gap-2 text-base font-medium">
              <Inbox className="text-muted-foreground size-4" />
              {info?.label ?? section.kind}
              <Badge variant="secondary">
                {info && info.matched !== info.total
                  ? `${info.matched}/${info.total}`
                  : (info?.total ?? section.rows.length)}
              </Badge>
              {info?.link && (
                <Link
                  to={info.link}
                  className="text-muted-foreground inline-flex items-center gap-1 text-xs font-normal hover:underline"
                >
                  원래 화면 <ExternalLink className="size-3" />
                </Link>
              )}
            </h2>
            {info?.description && (
              <p className="text-muted-foreground text-sm">{info.description}</p>
            )}
            {info?.undo_note && <p className="text-xs text-amber-700 dark:text-amber-400">{info.undo_note}</p>}
            {(info?.notes ?? []).map((line) => (
              <p key={line} className="text-muted-foreground text-xs">
                {line}
              </p>
            ))}
            <ul className="divide-y rounded-md border text-sm">
              {section.rows.map(({ row, index }) => {
                const key = rowKey(row)
                const focused = index === focus
                return (
                  <li
                    key={key}
                    ref={(node) => {
                      if (node) rowRefs.current.set(key, node)
                      else rowRefs.current.delete(key)
                    }}
                    data-focused={focused || undefined}
                    className={`flex gap-3 px-3 py-2 ${focused ? 'bg-muted/60 ring-primary/40 ring-1 ring-inset' : ''}`}
                    onClick={() => setFocus(index)}
                  >
                    <input
                      type="checkbox"
                      aria-label={`선택: ${row.question}`}
                      className="mt-1"
                      disabled={!row.actionable}
                      checked={everything ? row.actionable : selected.has(key)}
                      onChange={() => toggle(row)}
                    />
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <span className="font-medium">{row.question}</span>
                        <span className="flex shrink-0 items-center gap-1.5 text-xs">
                          {row.type_label && (
                            <span className="text-muted-foreground">{row.type_label}</span>
                          )}
                          {row.workspace_name && (
                            <span className="text-muted-foreground">· {row.workspace_name}</span>
                          )}
                          {row.actionable && (
                            <Badge
                              variant={
                                row.confidence !== null && row.confidence >= DEFAULT_THRESHOLD
                                  ? 'default'
                                  : 'secondary'
                              }
                            >
                              {confidenceText(row.confidence)}
                            </Badge>
                          )}
                        </span>
                      </div>
                      {row.evidence.length > 0 && (
                        <p className="text-muted-foreground text-xs">{row.evidence.join(' · ')}</p>
                      )}
                      {row.reason && row.recommended && (
                        <p className="text-muted-foreground text-xs">
                          추천: <strong className="text-foreground">{row.recommended_label}</strong>{' '}
                          — {row.reason}
                        </p>
                      )}
                      {row.blocked && (
                        <p className="text-destructive text-xs">
                          <Badge variant="destructive" className="mr-1">
                            막힘
                          </Badge>
                          {row.blocked}
                        </p>
                      )}
                      {row.actionable ? (
                        <div className="flex flex-wrap items-center gap-1.5">
                          {row.choices.map((choice, at) => (
                            <Button
                              key={choice.value}
                              size="sm"
                              variant={choice.value === row.recommended ? 'default' : 'secondary'}
                              disabled={busy || Boolean(row.blocked)}
                              title={choice.detail || undefined}
                              onClick={(event) => {
                                event.stopPropagation()
                                void decide([row], 'choose', choice.value)
                              }}
                            >
                              {at < 9 && (
                                <span className="text-muted-foreground mr-1 font-mono text-[10px]">
                                  {at + 1}
                                </span>
                              )}
                              {choice.label}
                              {choice.score !== null && (
                                <span className="ml-1 opacity-70">{choice.score.toFixed(2)}</span>
                              )}
                            </Button>
                          ))}
                          {row.ignorable && (
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={busy || Boolean(row.blocked)}
                              onClick={(event) => {
                                event.stopPropagation()
                                void decide([row], 'ignore')
                              }}
                            >
                              무시
                            </Button>
                          )}
                          {row.link && (
                            <Link
                              to={row.link}
                              className="text-muted-foreground ml-auto inline-flex items-center gap-1 text-xs hover:underline"
                            >
                              원래 화면 <ExternalLink className="size-3" />
                            </Link>
                          )}
                        </div>
                      ) : (
                        <Link
                          to={row.link}
                          className="text-primary inline-flex items-center gap-1 text-xs hover:underline"
                        >
                          원래 화면으로 <ExternalLink className="size-3" />
                        </Link>
                      )}
                    </div>
                  </li>
                )
              })}
            </ul>
          </section>
        )
      })}

      {page.data && total > PAGE && (
        <Pagination total={total} limit={PAGE} offset={offset} onChange={setOffset} unit="줄" />
      )}

      <ReviewRecent
        version={version}
        onChanged={(text) => {
          setSaid(text)
          setLastBatch(null)
          reset()
        }}
      />

      {pending && (
        <ReviewPlanDialog
          title={pending.title}
          plan={pending.plan}
          busy={busy}
          progress={progress}
          error={error}
          onApply={() => void guard(() => runApply(pending.plan, pending.mode))}
          onClose={() => {
            setPending(null)
            setError(null)
          }}
        />
      )}
      {undoing && (
        <ReviewUndoDialog
          batchId={undoing}
          onClose={() => setUndoing(null)}
          onDone={(text) => {
            setUndoing(null)
            setSaid(text)
            setLastBatch(null)
            reset()
          }}
        />
      )}
    </div>
  )
}
