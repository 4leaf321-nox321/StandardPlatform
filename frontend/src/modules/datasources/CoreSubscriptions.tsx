/**
 * 코어 구독(ADR 0085) — 형제 Standard Platform 의 코어를 **통째로** 받는 등록.
 *
 * 묶음 구독은 그 순간의 타입마다 소스를 한 번 만들고 끝났다 — 허브에 공개 타입이 하나 늘 때마다
 * 정의 묶음을 다시 받고 토큰을 다시 넣어 구독을 다시 했다. 구독은 확인마다 허브의 정의를 맞추고
 * (저장값을 잃지 않는 변경만 자동), 공개 타입마다 소스를 두고 칸 대응을 맞추고, 공개가 해제된
 * 타입은 수신 중지한다(받은 자료는 남는다). 저장값을 잃을 수 있는 정의 변경은 「정의 검토 대기」 로
 * 남고, 여기서 줄을 읽고 「적용」 한다.
 */

import { Link2, RefreshCw, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { datasourceApi } from '@/modules/datasources/api'
import type {
  CoreSubscription,
  CoreSubscriptionPlan,
  CoreSubscriptionType,
} from '@/modules/datasources/api'
import type { Job } from '@/modules/jobs/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { StatusBadge } from '@/shared/components/StatusBadge'
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
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

const ACTION: Record<CoreSubscriptionType['action'], string> = {
  define: '정의 생성 후 수신',
  create: '소스 생성',
  adopt: '기존 소스 사용',
  skip: '받지 않음',
}

function interval(minutes: number): string {
  return minutes > 0 ? `${minutes}분마다 확인` : '「동기화」 를 클릭할 때만 확인'
}

/** 확인 작업의 결과 한 줄 — 무엇이 바뀌었나. */
export function checkSummary(job: Job): string {
  const result = (job.result ?? {}) as Record<string, unknown>
  const count = (key: string) => {
    const value = result[key]
    return Array.isArray(value) ? value.length : typeof value === 'number' ? value : 0
  }
  const parts = [
    ['definitions_applied', '정의 변경 적용'],
    ['created', '소스 생성'],
    ['adopted', '기존 소스 사용'],
    ['resumed', '수신 재개'],
    ['paused', '수신 중지'],
    ['restarted', '처음부터 다시 받음'],
    ['held', '정의 검토 대기'],
    ['failed', '실패한 소스'],
  ]
    .filter(([key]) => count(key) > 0)
    .map(([key, label]) => `${label} ${count(key)}`)
  return parts.length > 0 ? `확인 끝 — ${parts.join(' · ')}` : '확인 끝 — 바뀐 것이 없습니다.'
}

export function CoreSubscriptions({
  version,
  onChanged,
}: {
  /** 바뀌면 다시 읽는다 — 생성 창이 올린다. */
  version: number
  /** 구독이 소스를 만들거나 바꿨다 — 데이터 소스 목록이 다시 읽는다. */
  onChanged: () => void
}) {
  const list = useResource(() => datasourceApi.coreSubscriptions(), [version])
  const [editing, setEditing] = useState<CoreSubscription | null>(null)
  const [removing, setRemoving] = useState<CoreSubscription | null>(null)
  const [applying, setApplying] = useState<CoreSubscription | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [said, setSaid] = useState<{ id: string; text: string } | null>(null)

  const rows = list.data ?? []
  if (!list.error && rows.length === 0) return null

  async function check(row: CoreSubscription) {
    setBusy(row.id)
    setError(null)
    setSaid(null)
    try {
      const job = await datasourceApi.coreSubscriptionSync(row.id)
      setSaid({ id: row.id, text: checkSummary(job) })
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(null)
      list.reload()
      onChanged()
    }
  }

  return (
    <section className="space-y-2" aria-label="코어 구독">
      <h2 className="text-sm font-medium">코어 구독</h2>
      <ErrorNotice error={list.error ?? error} />
      <ul className="divide-y rounded-md border">
        {rows.map((row) => {
          const paused = row.sources.filter((one) => one.paused).length
          return (
            <li key={row.id} className="space-y-2 p-4">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-medium">{row.system || '형제 Standard Platform'}</span>
                <code className="text-muted-foreground text-xs">{row.base_url}</code>
                <StatusBadge kind="subscription" value={row.last_status ?? ''} />
                {!row.is_active && <span className="text-muted-foreground text-xs">비활성</span>}
                {row.checked_at && (
                  <span className="text-muted-foreground text-xs">
                    최종 확인 {shownDateTime(row.checked_at)}
                  </span>
                )}
                <span className="ml-auto flex gap-1">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy === row.id}
                    onClick={() => check(row)}
                  >
                    <RefreshCw className="mr-1 size-3.5" />
                    {busy === row.id ? '확인 중' : '동기화'}
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setEditing(row)}>
                    수정
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label="코어 구독 삭제"
                    onClick={() => setRemoving(row)}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </span>
              </div>
              <p className="text-muted-foreground text-xs">
                범위 {row.groups.length > 0 ? row.groups.join(', ') : '공개 타입 전부'} · 관리 이름{' '}
                <code>{row.source_name}</code> · {interval(row.interval_minutes)} · 소스{' '}
                {row.sources.length}개{paused > 0 && ` (수신 중지 ${paused}개)`}
              </p>
              {row.sources.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {row.sources.map((one) => (
                    <span
                      key={one.slug}
                      className="bg-muted inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs"
                      title={one.name}
                    >
                      <code>{one.type_slug}</code>
                      {one.paused ? (
                        <span className="text-amber-700 dark:text-amber-400">수신 중지</span>
                      ) : !one.is_active ? (
                        <span className="text-muted-foreground">비활성</span>
                      ) : one.last_status === 'failed' ? (
                        <span className="text-destructive">실패</span>
                      ) : null}
                    </span>
                  ))}
                </div>
              )}
              {said?.id === row.id && <p className="text-sm">{said.text}</p>}
              {row.last_status === 'failed' && row.last_error && (
                <p className="text-destructive text-sm">{row.last_error}</p>
              )}
              {row.held && (
                <div className="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
                  <p className="text-sm font-medium">
                    정의 검토 대기 — 저장된 값을 잃을 수 있어 자동으로 적용하지 않은 허브의 정의
                    변경입니다
                  </p>
                  <ul className="list-disc space-y-0.5 pl-5 text-sm">
                    {row.held.lines.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                  <Button size="sm" onClick={() => setApplying(row)}>
                    적용
                  </Button>
                </div>
              )}
              {row.notes.length > 0 && (
                <details className="text-xs">
                  <summary className="text-muted-foreground cursor-pointer">
                    알림 {row.notes.length}건
                  </summary>
                  <ul className="mt-1 list-disc space-y-0.5 pl-5">
                    {row.notes.map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                </details>
              )}
            </li>
          )
        })}
      </ul>

      {editing && (
        <CoreSubscriptionEdit
          row={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            list.reload()
            onChanged()
          }}
        />
      )}
      <ConfirmDialog
        open={applying !== null}
        title="정의 검토 대기 적용"
        description={
          <div className="space-y-2">
            <p>
              아래 변경을 이 설치의 정의에 적용합니다. 저장된 값이 변환되거나 비워질 수 있고, 지금
              값이 새 정의에 맞지 않는 객체는 수정할 때 거절됩니다. 적용은 묶음 기록에 남아 되돌릴
              수 있습니다.
            </p>
            <ul className="list-disc space-y-0.5 pl-5">
              {(applying?.held?.lines ?? []).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
        }
        confirmLabel="적용"
        onConfirm={async () => {
          if (!applying?.held) return
          const job = await datasourceApi.coreHeldApply(applying.id, applying.held.mark)
          setSaid({ id: applying.id, text: checkSummary(job) })
          list.reload()
          onChanged()
        }}
        onClose={() => setApplying(null)}
      />
      <ConfirmDialog
        open={removing !== null}
        title="코어 구독 삭제"
        description={`구독만 삭제합니다. 소스 ${removing?.sources.length ?? 0}개와 받은 자료는 남고, 소스는 하던 대로 받습니다 — 허브의 새 타입 · 새 칸 · 정의 변경은 더 이상 맞추지 않습니다.`}
        confirmLabel="삭제"
        destructive
        onConfirm={async () => {
          if (!removing) return
          await datasourceApi.coreSubscriptionRemove(removing.id)
          list.reload()
          onChanged()
        }}
        onClose={() => setRemoving(null)}
      />
    </section>
  )
}

function CoreSubscriptionEdit({
  row,
  onClose,
  onSaved,
}: {
  row: CoreSubscription
  onClose: () => void
  onSaved: () => void
}) {
  const [secret, setSecret] = useState('')
  const [groups, setGroups] = useState(row.groups.join(', '))
  const [every, setEvery] = useState(String(row.interval_minutes))
  const [active, setActive] = useState(row.is_active)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const minutes = Number(every)

  async function save() {
    setBusy(true)
    setError(null)
    try {
      await datasourceApi.coreSubscriptionUpdate(row.id, {
        ...(secret !== '' ? { auth_secret: secret } : {}),
        groups: groups
          .split(',')
          .map((one) => one.trim())
          .filter(Boolean),
        interval_minutes: Math.max(0, minutes),
        is_active: active,
      })
      onSaved()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>코어 구독 수정</DialogTitle>
          <DialogDescription>
            토큰은 이 구독의 소스 {row.sources.length}개에 함께 적용합니다. 범위를 바꾸면 다음
            확인이 정의를 다시 받고, 범위에서 빠진 타입은 수신 중지합니다.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="space-y-1">
            <Label htmlFor="core-sub-token">액세스 토큰(core:read)</Label>
            <Input
              id="core-sub-token"
              type="password"
              autoComplete="off"
              placeholder={row.has_secret ? '비우면 그대로' : ''}
              value={secret}
              disabled={busy}
              onChange={(event) => setSecret(event.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="core-sub-groups">허브의 사이드바 묶음(쉼표로)</Label>
            <Input
              id="core-sub-groups"
              placeholder="비우면 공개 타입 전부"
              value={groups}
              disabled={busy}
              onChange={(event) => setGroups(event.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="core-sub-interval">확인 간격(분)</Label>
            <Input
              id="core-sub-interval"
              inputMode="numeric"
              value={every}
              disabled={busy}
              onChange={(event) => setEvery(event.target.value)}
            />
            <p className="text-muted-foreground text-xs">
              구독의 소스도 이 간격으로 받습니다. 0 이면 「동기화」 를 클릭할 때만.
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4"
              checked={active}
              disabled={busy}
              onChange={(event) => setActive(event.target.checked)}
            />
            활성
          </label>
        </div>
        <ErrorNotice error={error} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            취소
          </Button>
          <Button onClick={save} disabled={busy || !Number.isInteger(minutes)}>
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function CoreSubscribeDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  /** 만든 뒤 — 목록이 다시 읽는다. */
  onCreated: () => void
}) {
  const [baseUrl, setBaseUrl] = useState('')
  const [secret, setSecret] = useState('')
  const [sourceName, setSourceName] = useState('hub')
  const [picked, setPicked] = useState<string[]>([])
  const [every, setEvery] = useState('60')
  const [plan, setPlan] = useState<CoreSubscriptionPlan | null>(null)
  const [groupChoices, setGroupChoices] = useState<CoreSubscriptionPlan['groups']>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const minutes = Number(every)
  const ready =
    baseUrl.trim() !== '' &&
    secret !== '' &&
    sourceName.trim() !== '' &&
    Number.isInteger(minutes) &&
    minutes >= 0

  /** 입력을 바꾸면 미리 본 계획은 낡는다 — 그 계획으로 생성하지 않게 지운다. */
  function changed<T>(set: (value: T) => void) {
    return (value: T) => {
      set(value)
      setPlan(null)
    }
  }

  async function run(apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      const got = await datasourceApi.coreSubscribe({
        base_url: baseUrl.trim(),
        auth_kind: 'bearer',
        auth_secret: secret,
        source_name: sourceName.trim(),
        groups: picked,
        interval_minutes: minutes,
        apply,
      })
      setPlan(got)
      if (got.groups.length > 0) setGroupChoices(got.groups)
      if (apply) onCreated()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const receiving = plan ? plan.types.filter((one) => one.action !== 'skip') : []

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>코어 구독 — 형제 Standard Platform</DialogTitle>
          <DialogDescription>
            허브의 코어를 통째로 받습니다. 토큰은 한 번만 등록합니다 — 허브에 공개 타입 · 칸이 늘면
            다음 확인이 정의를 세우고 데이터 소스와 칸 대응을 맞춰 받습니다. 저장된 값을 잃을 수
            있는 정의 변경은 정의 검토 대기로 남고, 공개가 해제된 타입은 수신 중지합니다(받은 자료는
            남습니다).
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="core-subscribe-url">허브의 API 주소</Label>
            <Input
              id="core-subscribe-url"
              placeholder="https://hub.example.com/api"
              value={baseUrl}
              disabled={busy}
              onChange={(event) => changed(setBaseUrl)(event.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="core-subscribe-token">액세스 토큰(core:read)</Label>
            <Input
              id="core-subscribe-token"
              type="password"
              autoComplete="off"
              value={secret}
              disabled={busy}
              onChange={(event) => changed(setSecret)(event.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="core-subscribe-source">관리 이름</Label>
            <Input
              id="core-subscribe-source"
              value={sourceName}
              disabled={busy}
              onChange={(event) => changed(setSourceName)(event.target.value)}
            />
            <p className="text-muted-foreground text-xs">
              받은 정의를 관리하는 이름입니다 — 정의 묶음을 받을 때 적은 이름과 같게. 이 이름이
              관리하지 않는 같은 이름의 타입(이 설치가 만든 것)은 받지 않습니다.
            </p>
          </div>
          <div className="space-y-1">
            <Label htmlFor="core-subscribe-interval">확인 간격(분)</Label>
            <Input
              id="core-subscribe-interval"
              inputMode="numeric"
              value={every}
              disabled={busy}
              onChange={(event) => changed(setEvery)(event.target.value)}
            />
            <p className="text-muted-foreground text-xs">0 이면 「동기화」 를 클릭할 때만.</p>
          </div>
          {groupChoices.length > 0 && (
            <div className="space-y-1 sm:col-span-2">
              <p className="text-sm font-medium">
                범위 — 허브의 사이드바 묶음(선택하지 않으면 전부)
              </p>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {groupChoices.map((one) => (
                  <label key={one.slug} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="size-4"
                      disabled={busy}
                      checked={picked.includes(one.slug)}
                      onChange={(event) =>
                        changed(setPicked)(
                          event.target.checked
                            ? [...picked, one.slug]
                            : picked.filter((slug) => slug !== one.slug),
                        )
                      }
                    />
                    {one.label}
                    <span className="text-muted-foreground font-mono text-xs">{one.slug}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>

        <ErrorNotice error={error} />

        {plan && (
          <div className="space-y-3">
            <p className="text-sm">
              {plan.applied
                ? '코어 구독을 생성했습니다 — 첫 동기화 작업이 정의를 세우고 행을 끝까지 받습니다(작업 화면에서 진행을 봅니다).'
                : `「${plan.system}」 의 공개 타입 ${receiving.length}개를 받습니다.`}
              {!plan.definitions &&
                ' 허브에 정의 창구가 없어 이 설치에 정의가 있는 타입만 받습니다.'}
            </p>
            <div className="overflow-x-auto rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="px-3 py-1.5 text-left font-medium">타입</th>
                    <th className="px-3 py-1.5 text-left font-medium">처리</th>
                    <th className="px-3 py-1.5 text-right font-medium">허브 건수</th>
                    <th className="px-3 py-1.5 text-left font-medium">사유</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.types.map((one) => (
                    <tr key={one.type_slug} className="border-t align-top">
                      <td className="px-3 py-1.5">
                        {one.type_label}
                        <span className="text-muted-foreground block font-mono text-xs">
                          {one.source_slug || one.type_slug}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 whitespace-nowrap">{ACTION[one.action]}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {one.count.toLocaleString()}
                      </td>
                      <td className="text-muted-foreground px-3 py-1.5 text-xs">{one.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {plan.held.length > 0 && (
              <div className="space-y-1 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
                <p className="text-sm font-medium">정의 검토 대기로 남길 변경</p>
                <ul className="list-disc space-y-0.5 pl-5 text-sm">
                  {plan.held.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </div>
            )}
            {plan.notes.length > 0 && (
              <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-xs">
                {plan.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {plan?.applied ? '닫기' : '취소'}
          </Button>
          {!plan?.applied && (
            <Button variant="outline" onClick={() => run(false)} disabled={busy || !ready}>
              미리 보기
            </Button>
          )}
          {!plan?.applied && (
            <Button onClick={() => run(true)} disabled={busy || !ready || !plan}>
              <Link2 className="mr-1 size-4" />
              생성
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
