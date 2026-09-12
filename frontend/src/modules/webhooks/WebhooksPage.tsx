/**
 * 웹훅 — **바뀐 것을 바깥에 알린다.**
 *
 * 감사 기록에 남는 변경이 곧 이벤트다. 웹훅마다 어떤 이벤트(`object.*`)·어떤 타입만 받을지
 * 정하고, 「보내 보기」 로 그 자리에서 확인한다. 보낸 기록이 표에 남아 실패한 것을 다시 보낼
 * 수 있다 — 「왜 안 오지」 를 바깥 시스템 로그에서 찾게 하지 않는다.
 */

import { useState } from 'react'
import { Plus, RefreshCw, Send, Trash2 } from 'lucide-react'

import { ontologyApi } from '@/modules/ontology/api'
import { webhookApi } from '@/modules/webhooks/api'
import type { Delivery, Webhook, WebhookWrite } from '@/modules/webhooks/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
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

/** 자주 쓰는 패턴. 직접 쳐도 된다 — 감사 기록의 action 이름이다. */
const EVENT_PRESETS: [string, string][] = [
  ['*', '전부'],
  ['object.*', '객체 전부 (만듦·고침·지움·합침·관계)'],
  ['object.create', '객체 만듦'],
  ['object.update', '객체 고침'],
  ['object.delete', '객체 지움'],
  ['object.relation.*', '관계 맺음·끊음'],
  ['ontology.*', '정의 변경'],
]

export default function WebhooksPage() {
  const list = useResource(() => webhookApi.list(), [])
  const schema = useResource(() => ontologyApi.schema(), [])
  const [editing, setEditing] = useState<Webhook | 'new' | null>(null)
  const [removing, setRemoving] = useState<Webhook | null>(null)
  const [opened, setOpened] = useState<string | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [lastTest, setLastTest] = useState<Delivery | null>(null)

  const hooks = list.data ?? []

  async function test(hook: Webhook) {
    setError(null)
    try {
      setLastTest(await webhookApi.test(hook.id))
      list.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeader
        title="웹훅"
        description="바뀐 것을 바깥 시스템에 알립니다. 감사 기록에 남는 변경이 곧 이벤트입니다."
        actions={
          <Button size="sm" onClick={() => setEditing('new')}>
            <Plus className="mr-1 size-4" />
            만들기
          </Button>
        }
      />

      {list.error && <ErrorNotice error={list.error} />}
      {error && <ErrorNotice error={error} />}
      {lastTest && (
        <p
          className={
            lastTest.status === 'ok'
              ? 'text-sm text-emerald-700 dark:text-emerald-400'
              : 'text-destructive text-sm'
          }
        >
          보내 보기: {lastTest.status === 'ok' ? '받았습니다' : '실패'}
          {lastTest.response_code !== null && ` (HTTP ${lastTest.response_code})`}
          {lastTest.last_error && ` — ${lastTest.last_error}`}
        </p>
      )}

      {list.data && hooks.length === 0 ? (
        <EmptyState
          title="웹훅이 없습니다"
          hint="만들면 객체가 바뀔 때마다 그 주소로 JSON 이 갑니다. 비밀을 두면 X-Signature-256 으로 서명합니다."
        />
      ) : (
        <ul className="divide-y rounded-md border">
          {hooks.map((hook) => (
            <li key={hook.id} className="space-y-2 p-4">
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  className="text-left font-medium hover:underline"
                  onClick={() => setOpened(opened === hook.id ? null : hook.id)}
                >
                  {hook.name}
                </button>
                {!hook.is_active && (
                  <span className="text-muted-foreground text-xs">사용 안 함</span>
                )}
                {/* 마지막 결과 — 죽어 있는 웹훅이 목록에서 보이게. */}
                {hook.last_status && (
                  <span
                    className={
                      hook.last_status === 'ok'
                        ? 'text-xs text-emerald-700 dark:text-emerald-400'
                        : 'text-destructive text-xs'
                    }
                  >
                    마지막 {hook.last_status === 'ok' ? '성공' : '실패'}
                    {hook.last_at && ` · ${shownDateTime(hook.last_at)}`}
                  </span>
                )}
                <span className="ml-auto flex gap-1">
                  <Button size="sm" variant="outline" onClick={() => test(hook)}>
                    <Send className="mr-1 size-3.5" />
                    보내 보기
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setEditing(hook)}>
                    고치기
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label="웹훅 지우기"
                    onClick={() => setRemoving(hook)}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </span>
              </div>
              <p className="text-muted-foreground font-mono text-xs break-all">{hook.url}</p>
              <p className="text-muted-foreground text-xs">
                이벤트 {hook.events.join(', ')}
                {hook.type_slugs && ` · 타입 ${hook.type_slugs.join(', ')}`}
                {hook.has_secret ? ' · 서명함' : ' · 서명 없음'}
              </p>
              {opened === hook.id && <Deliveries hook={hook} />}
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <EditDialog
          hook={editing === 'new' ? null : editing}
          typeSlugs={(schema.data?.types ?? []).map((one) => ({
            slug: one.slug,
            label: one.label,
          }))}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            list.reload()
          }}
        />
      )}

      <ConfirmDialog
        open={removing !== null}
        title={`「${removing?.name}」 을 지웁니다`}
        description="보낸 기록도 함께 사라집니다. 잠시 멈추려면 지우지 말고 「사용 안 함」 으로 두세요."
        confirmLabel="지우기"
        destructive
        onConfirm={async () => {
          if (removing) await webhookApi.remove(removing.id)
          setRemoving(null)
          list.reload()
        }}
        onClose={() => setRemoving(null)}
      />
    </div>
  )
}

/** 최근 보낸 기록 — 실패한 것은 다시 보낼 수 있다. */
function Deliveries({ hook }: { hook: Webhook }) {
  const rows = useResource(() => webhookApi.deliveries(hook.id), [hook.id])
  const [error, setError] = useState<Error | null>(null)
  const [shown, setShown] = useState<string | null>(null)

  async function retry(delivery: Delivery) {
    setError(null)
    try {
      await webhookApi.retry(hook.id, delivery.id)
      rows.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  const list = rows.data ?? []
  return (
    <div className="mt-2 space-y-2 rounded-md border p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">최근 보낸 것 {list.length}건</span>
        <Button size="sm" variant="ghost" onClick={rows.reload}>
          <RefreshCw className="size-3.5" />
        </Button>
      </div>
      {error && <ErrorNotice error={error} />}
      {rows.data && list.length === 0 && (
        <p className="text-muted-foreground text-xs">아직 보낸 것이 없습니다.</p>
      )}
      <ul className="space-y-1 text-xs">
        {list.map((one) => (
          <li key={one.id} className="flex flex-wrap items-center gap-2">
            <span
              className={
                one.status === 'ok'
                  ? 'text-emerald-700 dark:text-emerald-400'
                  : one.status === 'failed'
                    ? 'text-destructive'
                    : 'text-muted-foreground'
              }
            >
              {one.status === 'ok' ? '성공' : one.status === 'failed' ? '실패' : '대기'}
            </span>
            <span className="font-mono">{one.event}</span>
            <span className="text-muted-foreground">{shownDateTime(one.created_at)}</span>
            {one.response_code !== null && (
              <span className="text-muted-foreground">HTTP {one.response_code}</span>
            )}
            {one.attempts > 1 && (
              <span className="text-muted-foreground">{one.attempts}번 시도</span>
            )}
            {one.last_error && <span className="text-destructive truncate">{one.last_error}</span>}
            <button
              type="button"
              className="text-muted-foreground hover:underline"
              onClick={() => setShown(shown === one.id ? null : one.id)}
            >
              내용
            </button>
            {one.status === 'failed' && (
              <Button size="sm" variant="outline" className="h-6" onClick={() => retry(one)}>
                다시 보내기
              </Button>
            )}
            {shown === one.id && (
              <pre className="bg-muted mt-1 max-h-48 w-full overflow-auto rounded p-2 text-[11px]">
                {JSON.stringify(one.payload, null, 1)}
              </pre>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

function EditDialog({
  hook,
  typeSlugs,
  onClose,
  onSaved,
}: {
  hook: Webhook | null
  typeSlugs: { slug: string; label: string }[]
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(hook?.name ?? '')
  const [url, setUrl] = useState(hook?.url ?? '')
  const [secret, setSecret] = useState('')
  const [events, setEvents] = useState<string[]>(hook?.events ?? ['object.*'])
  const [custom, setCustom] = useState('')
  const [types, setTypes] = useState<string[]>(hook?.type_slugs ?? [])
  const [active, setActive] = useState(hook?.is_active ?? true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  function toggle(list: string[], value: string, on: boolean): string[] {
    return on ? [...new Set([...list, value])] : list.filter((one) => one !== value)
  }

  async function save() {
    setBusy(true)
    setError(null)
    try {
      const body: WebhookWrite = {
        name: name.trim(),
        url: url.trim(),
        events,
        type_slugs: types.length > 0 ? types : null,
        is_active: active,
      }
      // 비밀은 **적었을 때만** 보낸다 — 빈 값으로 보내면 있던 비밀이 지워진다.
      if (secret || !hook) body.secret = secret
      if (hook) await webhookApi.update(hook.id, body)
      else await webhookApi.create(body)
      onSaved()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{hook ? '웹훅 고치기' : '웹훅 만들기'}</DialogTitle>
          <DialogDescription>
            변경이 커밋된 뒤 이 주소로 JSON 을 POST 합니다. 실패하면 세 번까지 다시 보냅니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {error && <ErrorNotice error={error} />}
          <div className="space-y-1.5">
            <Label htmlFor="wh-name">이름</Label>
            <Input id="wh-name" value={name} onChange={(event) => setName(event.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="wh-url">주소</Label>
            <Input
              id="wh-url"
              value={url}
              placeholder="https://…/hook"
              onChange={(event) => setUrl(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="wh-secret">비밀 {hook?.has_secret && '(비우면 그대로)'}</Label>
            <Input
              id="wh-secret"
              value={secret}
              placeholder={hook?.has_secret ? '바꿀 때만 적기' : '비우면 서명하지 않음'}
              onChange={(event) => setSecret(event.target.value)}
            />
            <p className="text-muted-foreground text-xs">
              받는 쪽은 <code>X-Signature-256: sha256=HMAC(비밀, 본문)</code> 으로 확인합니다.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label>이벤트</Label>
            <ul className="space-y-1">
              {EVENT_PRESETS.map(([pattern, text]) => (
                <li key={pattern}>
                  <label className="flex cursor-pointer items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="size-4"
                      checked={events.includes(pattern)}
                      onChange={(event) => setEvents(toggle(events, pattern, event.target.checked))}
                    />
                    <code className="text-xs">{pattern}</code>
                    <span className="text-muted-foreground">{text}</span>
                  </label>
                </li>
              ))}
              {events
                .filter((one) => !EVENT_PRESETS.some(([pattern]) => pattern === one))
                .map((one) => (
                  <li key={one}>
                    <label className="flex cursor-pointer items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        className="size-4"
                        checked
                        onChange={() => setEvents(toggle(events, one, false))}
                      />
                      <code className="text-xs">{one}</code>
                    </label>
                  </li>
                ))}
            </ul>
            <div className="flex gap-2">
              <Input
                value={custom}
                placeholder="직접 적기 — 예: ontology.type.update"
                className="h-8 text-xs"
                onChange={(event) => setCustom(event.target.value)}
              />
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={!custom.trim()}
                onClick={() => {
                  setEvents(toggle(events, custom.trim(), true))
                  setCustom('')
                }}
              >
                더하기
              </Button>
            </div>
          </div>
          {typeSlugs.length > 0 && (
            <div className="space-y-1.5">
              <Label>타입 (비우면 전부)</Label>
              <ul className="grid grid-cols-2 gap-1">
                {typeSlugs.map((one) => (
                  <li key={one.slug}>
                    <label className="flex cursor-pointer items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        className="size-4"
                        checked={types.includes(one.slug)}
                        onChange={(event) =>
                          setTypes(toggle(types, one.slug, event.target.checked))
                        }
                      />
                      {one.label}
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4"
              checked={active}
              onChange={(event) => setActive(event.target.checked)}
            />
            사용
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            취소
          </Button>
          <Button
            onClick={save}
            disabled={busy || !name.trim() || !url.trim() || events.length === 0}
          >
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
