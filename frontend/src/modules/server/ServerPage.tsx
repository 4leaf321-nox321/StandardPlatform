/**
 * 서버 상태 — **한 화면이 답해야 하는 물음이 셋이다.**
 *
 *   지금 뭐가 깔렸나 · DB 는 맞춰져 있나 · 무엇이 얼마나 쌓였나
 *
 * 셋을 따로 두면 아무도 다 보지 않는다. 그리고 문제가 났을 때 첫 물음은 언제나
 * "지금 서버 버전이 뭐냐" 와 "어느 DB 를 보고 있냐" 다.
 */

import { api } from '@/shared/api/client'
import type { ServerStatus } from '@/shared/api/types'
import { missingExtensions } from '@/extensions'
import { APP_NAME, ENABLED_EXTENSIONS } from '@/shared/branding'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

function gib(bytes: number): string {
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`
}

export default function ServerPage() {
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
          서버에서 <span className="font-mono">alembic upgrade head</span> 를 돌리세요.
          그전까지는 새 칸을 읽는 화면이 오류를 냅니다.
        </div>
      )}

      {nameMismatch && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          화면은 <span className="font-mono">{APP_NAME}</span> 인데 서버는{' '}
          <span className="font-mono">{one.app_name}</span> 이라고 합니다. 화면이 옛 index.html
          을 들고 있거나(새로 고침), 개발 서버가 다른 백엔드에 붙어 있을 수 있습니다.
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
          <dt className="text-muted-foreground text-xs">켠 확장</dt>
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
              {gib(one.disk.free_bytes)} 남음 / {gib(one.disk.total_bytes)} (
              {one.disk.used_percent}% 사용)
            </dd>
          </div>
        )}
      </dl>

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
