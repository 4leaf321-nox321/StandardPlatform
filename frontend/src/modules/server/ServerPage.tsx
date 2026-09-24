/**
 * 서버 상태 — **한 화면이 답해야 하는 물음이 셋이다.**
 *
 *   지금 뭐가 깔렸나 · DB 는 맞춰져 있나 · 무엇이 얼마나 쌓였나
 *
 * 셋을 따로 두면 아무도 다 보지 않는다. 그리고 문제가 났을 때 첫 물음은 언제나
 * "지금 서버 버전이 뭐냐" 와 "어느 DB 를 보고 있냐" 다.
 */

import { useState } from 'react'

import { api } from '@/shared/api/client'
import type { ExtensionState, ServerStatus } from '@/shared/api/types'
import { missingExtensions } from '@/extensions'
import { useAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin } from '@/shared/auth/roles'
import { APP_NAME, ENABLED_EXTENSIONS } from '@/shared/branding'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

function gib(bytes: number): string {
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`
}

/**
 * 확장 모듈 — **재배포 없이 켜고 끈다.**
 *
 * 고를 수 있는 것은 이 번들에 든 확장뿐이다(서버가 목록을 준다) — `.env` 에 이름을 적어
 * 보다가 오타로 기동이 막히던 길을 없앤 자리다. 메뉴와 경로는 `index.html` 이 들고 오므로
 * **새로 고침 뒤**에 바뀐다.
 */
function Extensions({ running }: { running: string[] }) {
  const list = useResource(() => api.get<ExtensionState[]>('/server/extensions'), [])
  const [busy, setBusy] = useState<string | null>(null)
  const [failed, setFailed] = useState<Error | null>(null)
  const [changed, setChanged] = useState(false)

  async function toggle(one: ExtensionState) {
    setBusy(one.name)
    setFailed(null)
    try {
      await api.patch<ExtensionState>(`/server/extensions/${one.name}`, { enabled: !one.enabled })
      setChanged(true)
      list.reload()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="space-y-3">
      <h2 className="text-base font-semibold">확장 모듈</h2>
      <p className="text-muted-foreground text-sm">
        이 번들에 들어 있는 확장입니다. 끄면 그 확장의 화면 · API 가 사라지고 <b>자료는 남습니다</b>{' '}
        — 다시 켜면 그대로입니다. 켜고 끈 기록은 관리 › 감사 기록에서{' '}
        <code>extension.toggle</code> 로 조회합니다.
      </p>
      <ErrorNotice error={failed ?? list.error} />
      {changed && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          메뉴와 경로는 새로 고침 뒤에 반영됩니다.
          <Button
            variant="outline"
            size="sm"
            className="ml-2"
            onClick={() => window.location.reload()}
          >
            새로 고침
          </Button>
        </div>
      )}
      {list.data?.length === 0 ? (
        <p className="text-muted-foreground text-sm">이 번들에 확장이 없습니다.</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {(list.data ?? []).map((one) => (
            <li key={one.name} className="flex items-center justify-between gap-4 p-3">
              <div className="min-w-0">
                <p className="font-mono text-sm">{one.name}</p>
                <p className="text-muted-foreground text-xs">
                  {one.enabled ? '사용 중' : '미사용'}
                  {one.pinned
                    ? one.updated_at
                      ? ` · 화면에서 지정 (${shownDateTime(one.updated_at)})`
                      : ' · 화면에서 지정'
                    : ' · .env 기본값'}
                  {one.enabled && !running.includes(one.name) && ' · 새로 고침 필요'}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                disabled={busy === one.name}
                onClick={() => void toggle(one)}
              >
                {one.enabled ? '끄기' : '켜기'}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export default function ServerPage() {
  const { user } = useAuth()
  const status = useResource(() => api.get<ServerStatus>('/server/status'), [])

  if (status.error) return <ErrorNotice error={status.error} />
  if (!status.data) return null

  const one = status.data
  // 화면과 서버가 서로 다른 이름을 든다면 **둘 중 하나가 안 고쳐진 것**이다.
  // 포크할 때 branding 을 한쪽만 바꾸면 정확히 이렇게 된다.
  const nameMismatch = one.app_name !== APP_NAME

  return (
    <div className="space-y-6">
      <PageHeader title="서버" description="이 설치의 상태입니다." />

      {/* **뒤처져 있으면 여기서 말한다.** 안 그러면 사람은 그 사실을 엉뚱한
          화면의 500 으로 만나고, 거기엔 원인이 안 적힌다. */}
      {one.schema_behind && (
        <div className="border-destructive/40 bg-destructive/5 text-destructive rounded-md border p-3 text-sm">
          데이터베이스가 코드보다 뒤처져 있습니다 ({one.schema_current} → {one.schema_head}).
          서버에서 <span className="font-mono">alembic upgrade head</span> 를 실행하세요. 그전까지는
          새 칸을 읽는 화면이 오류를 냅니다.
        </div>
      )}

      {nameMismatch && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          화면은 <span className="font-mono">{APP_NAME}</span> 인데 서버는{' '}
          <span className="font-mono">{one.app_name}</span> 이라고 합니다. 화면이 옛 index.html 을
          들고 있거나(새로 고침), 개발 서버가 다른 백엔드에 붙어 있을 수 있습니다.
        </div>
      )}

      {/* **켰는데 화면 쪽 짝이 없는 확장.** 서버에는 있고 메뉴에는 없는 상태 — 사람은
          「안 켜졌다」 로 읽는다. */}
      {missingExtensions(ENABLED_EXTENSIONS).length > 0 && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          확장 <span className="font-mono">{missingExtensions(ENABLED_EXTENSIONS).join(', ')}</span>{' '}
          이 켜져 있지만 화면 쪽(<span className="font-mono">src/extensions</span>)에 짝이 없습니다.
        </div>
      )}

      {/* **`.env` 에 적혔는데 이 번들에 없는 이름.** 예전에는 이것이 기동을 막았다 —
          운영 재시작 중이라면 오타 하나로 서비스가 안 뜬다. 지금은 뜨고, 여기서 말한다. */}
      {one.extensions_unknown.length > 0 && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          <span className="font-mono">{one.extensions_unknown.join(', ')}</span> 은 이 설치의{' '}
          <span className="font-mono">EXTENSIONS</span> 에 적혀 있지만 번들에 없는 확장입니다. 아래
          「확장 모듈」 목록에 있는 것만 켤 수 있습니다.
        </div>
      )}

      {/* **백업이 오래되면 여기서도 말한다.** 홈의 「남은 일」 에도 뜨지만,
          서버 화면은 「이 설치가 어떤 상태인가」 를 보러 오는 자리다. */}
      {one.backup.stale && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          {one.backup.problem ?? '백업이 오래됐습니다.'}
          {one.backup.path && (
            <span className="text-muted-foreground ml-1 font-mono text-xs">
              ({one.backup.path})
            </span>
          )}
        </div>
      )}

      <dl className="grid gap-4 rounded-md border p-4 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <dt className="text-muted-foreground text-xs">플랫폼</dt>
          <dd className="text-sm">
            {one.app_name} <span className="text-muted-foreground font-mono">({one.app_slug})</span>
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs">버전</dt>
          <dd className="font-mono text-sm">{one.version}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs">켜진 확장</dt>
          <dd className="text-sm">
            {one.extensions.length === 0 ? (
              <span className="text-muted-foreground">없음</span>
            ) : (
              <span className="font-mono">{one.extensions.join(', ')}</span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs">환경</dt>
          <dd className="text-sm">{one.app_env}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs">기동 시각</dt>
          <dd className="text-sm">{shownDateTime(one.started_at)}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-muted-foreground text-xs">데이터베이스</dt>
          <dd className="font-mono text-xs break-all">{one.database_url_safe}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs">스키마 리비전</dt>
          <dd className="font-mono text-xs">{one.schema_current ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs">마지막 백업</dt>
          <dd className="text-sm">
            {one.backup.last_at ? shownDateTime(one.backup.last_at) : '—'}
            {one.backup.age_hours !== null && (
              <span className="text-muted-foreground ml-1 text-xs tabular-nums">
                ({Math.round(one.backup.age_hours)}시간 전)
              </span>
            )}
          </dd>
        </div>
        {one.disk && (
          <div>
            <dt className="text-muted-foreground text-xs">디스크</dt>
            <dd className="text-sm">
              {gib(one.disk.free_bytes)} 남음 / {gib(one.disk.total_bytes)} ({one.disk.used_percent}
              % 사용)
            </dd>
          </div>
        )}
      </dl>

      {isSystemAdmin(user) && <Extensions running={one.extensions} />}

      <section className="space-y-3">
        <h2 className="text-base font-semibold">쌓인 것</h2>
        {/* 도메인이 `shared/extensions.py` 에 등록한 만큼 늘어난다 — 이 화면은
            무엇이 올지 모르고, 몰라도 된다. */}
        <ul className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {one.counts.map((count) => (
            <li key={count.label} className="rounded-md border p-4">
              <p className="text-2xl font-semibold">{count.count}</p>
              <p className="text-muted-foreground text-sm">{count.label}</p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
