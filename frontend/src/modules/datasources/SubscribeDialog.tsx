/**
 * 묶음 구독(ADR 0055) — 형제 Standard Platform 의 코어에서 **사이드바 묶음의 타입마다** 소스를 한
 * 번에 만든다.
 *
 * 기준정보가 커서 묶음 한 봉투(50만 줄)로 못 받을 때의 길이다. 정의는 「정의만」 묶음으로 먼저
 * 받아(사람이 미리 보고 적용 — 허브 관리가 선다) 묶음이 이 설치에 있어야 고를 수 있고, 행은 여기서
 * 만든 소스들이 바뀐 것만 쪽씩 받는다 — 기간이 다른 선 · 계보 고정 · 별칭의 출처까지(`shape=sp`).
 *
 * **미리 보기를 먼저** 한다. 상대가 코어로 공개하지 않은 타입 · 이미 소스가 있는 타입을 까닭과 함께
 * 보이고, 「생성」 은 그 뒤에만 누를 수 있다. 타입마다 손으로 만들면 묶음 하나에 열 번이고, 그중
 * 하나의 출처 이름 · 선 받기를 빠뜨리면 그 타입만 조용히 안 들어온다.
 */

import { useState } from 'react'

import { datasourceApi } from '@/modules/datasources/api'
import type { SubscribeItem, SubscribeResult } from '@/modules/datasources/api'
import type { NavGroupRow } from '@/modules/ontology/api'
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

const ACTION: Record<SubscribeItem['action'], string> = {
  create: '생성',
  exists: '이미 있음',
  skip: '건너뜀',
}

export function SubscribeDialog({
  groups,
  onClose,
  onCreated,
}: {
  groups: NavGroupRow[]
  onClose: () => void
  /** 만든 뒤 — 목록이 다시 읽는다. */
  onCreated: () => void
}) {
  const [baseUrl, setBaseUrl] = useState('')
  const [secret, setSecret] = useState('')
  const [picked, setPicked] = useState<string[]>([])
  const [every, setEvery] = useState('0')
  const [plan, setPlan] = useState<SubscribeResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const minutes = Number(every)
  const ready =
    baseUrl.trim() !== '' && secret !== '' && picked.length > 0 && Number.isInteger(minutes)

  async function run(apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      const got = await datasourceApi.subscribe({
        base_url: baseUrl.trim(),
        auth_kind: 'bearer',
        auth_secret: secret,
        groups: picked,
        interval_minutes: Math.max(0, minutes),
        apply,
      })
      setPlan(got)
      if (apply) onCreated()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  /** 입력을 바꾸면 미리 본 계획은 낡는다 — 그 계획으로 생성하지 않게 지운다. */
  function changed<T>(set: (value: T) => void) {
    return (value: T) => {
      set(value)
      setPlan(null)
    }
  }

  const creating = plan && !plan.applied ? plan.items.filter((one) => one.action === 'create') : []

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>묶음 구독 — 형제 Standard Platform</DialogTitle>
          <DialogDescription>
            형제 설치의 코어에서 사이드바 묶음의 타입마다 소스를 하나씩 만듭니다. 기준정보가 커서
            묶음 한 봉투로 받을 수 없을 때 씁니다 — 정의는 「정의만」 묶음으로 먼저 받아 두고, 행은
            이 소스들이 바뀐 것만 받습니다(기간이 다른 관계 · 계보 고정 · 별칭의 출처까지). 처음
            받기는 5만 줄씩 끊어 한 번의 동기화로 끝까지 잇습니다.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="subscribe-url">형제 설치의 API 주소</Label>
            <Input
              id="subscribe-url"
              placeholder="https://hub.example.com/api"
              value={baseUrl}
              disabled={busy}
              onChange={(event) => changed(setBaseUrl)(event.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="subscribe-token">액세스 토큰(core:read)</Label>
            <Input
              id="subscribe-token"
              type="password"
              autoComplete="off"
              value={secret}
              disabled={busy}
              onChange={(event) => changed(setSecret)(event.target.value)}
            />
          </div>
          <div className="space-y-1 sm:col-span-2">
            <p className="text-sm font-medium">사이드바 묶음</p>
            {groups.length === 0 ? (
              <p className="text-muted-foreground text-xs">
                묶음이 없습니다 — 형제 설치에서 「정의만」 묶음을 먼저 받으면 그 묶음이 여기
                표시됩니다.
              </p>
            ) : (
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {groups.map((one) => (
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
            )}
          </div>
          <div className="space-y-1">
            <Label htmlFor="subscribe-interval">동기화 간격(분)</Label>
            <Input
              id="subscribe-interval"
              inputMode="numeric"
              value={every}
              disabled={busy}
              onChange={(event) => changed(setEvery)(event.target.value)}
            />
            <p className="text-muted-foreground text-xs">0 이면 「동기화」 를 클릭할 때만.</p>
          </div>
        </div>

        <ErrorNotice error={error} />

        {plan && (
          <div className="space-y-2">
            <p className="text-sm">
              {plan.applied
                ? `소스 ${plan.created}개를 생성했습니다 — 목록에서 「동기화」 하면 처음 받기가 시작됩니다(참조되는 타입의 소스부터).`
                : `「${plan.system}」 에서 ${creating.length}개를 생성합니다.`}
            </p>
            <div className="overflow-x-auto rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="px-3 py-1.5 text-left font-medium">타입</th>
                    <th className="px-3 py-1.5 text-left font-medium">처리</th>
                    <th className="px-3 py-1.5 text-right font-medium">상대 건수</th>
                    <th className="px-3 py-1.5 text-left font-medium">관계</th>
                    <th className="px-3 py-1.5 text-left font-medium">출처 이름 · 사유</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.items.map((one) => (
                    <tr key={one.type_slug} className="border-t align-top">
                      <td className="px-3 py-1.5">
                        {one.type_label}
                        <span className="text-muted-foreground block font-mono text-xs">
                          {one.source_slug || one.type_slug}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 whitespace-nowrap">{ACTION[one.action]}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {one.action === 'skip' ? '' : one.count.toLocaleString()}
                      </td>
                      <td className="px-3 py-1.5">{one.relations ? '받음' : ''}</td>
                      <td className="text-muted-foreground px-3 py-1.5 text-xs">
                        {one.reason || (one.source_name ? `출처 「${one.source_name}」` : '')}
                        {one.notes.length > 0 && (
                          <span className="block">못 이은 칸: {one.notes.join(' · ')}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
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
            <Button onClick={() => run(true)} disabled={busy || !ready || creating.length === 0}>
              생성
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
