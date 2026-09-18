/**
 * 부서 홈 — **남은 일이 먼저 온다.**
 *
 * 관리 화면에 들어가야만 보이는 목록은 아무도 안 본다. 승인 대기가 며칠씩
 * 방치되고, 안 채워진 자료는 영영 안 채워진다.
 *
 * ## 도메인이 여기에 무엇을 더하나
 *
 * **화면을 고쳐서 더하지 않는다.** 두 길이 있고 둘 다 데이터다:
 *
 *     남은 일    서버의 레지스트리(`shared/extensions.py`)에 등록하면 저절로 뜬다
 *     위젯       부서 뷰에 「통계」 설정을 담고 **홈에 올리면** 그려진다
 *
 * 화면을 고치는 것과 서버를 고치는 것이 둘 다 필요하면, 언젠가 한쪽만 고쳐진다.
 *
 * ## 위젯은 그 부서가 함께 본다
 *
 * 개인 뷰는 못 올린다. 같은 화면을 보는 사람마다 다른 것이 뜨면 「내 홈에는 왜 그게
 * 없지」 를 아무도 설명하지 못한다.
 */

import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Plus } from 'lucide-react'

import { HomeWidget } from '@/modules/objects/HomeWidget'
import { AddWidgetDialog } from '@/modules/workspaces/AddWidgetDialog'
import { objectApi, viewApi } from '@/modules/objects/api'
import { api } from '@/shared/api/client'
import type { MaintenanceItem } from '@/shared/api/types'
import { useAuth } from '@/shared/auth/AuthContext'
import { isManagerOf } from '@/shared/auth/roles'
import { APP_NAME } from '@/shared/branding'
import { Button } from '@/shared/components/ui/button'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { TypeIcon } from '@/shared/components/TypeIcon'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

export default function WorkspaceHomePage() {
  const { slug } = useParams<{ slug?: string }>()
  const { user } = useAuth()
  const maintenance = useResource(() => api.get<MaintenanceItem[]>('/server/maintenance'), [])
  // **다른 부서 것도 함께** 볼 수 있다. 사람은 대개 여러 부서에 속하고, 그때 부서를
  // 갈아 가며 도는 일이 생긴다. 기본은 지금 부서만 — 홈은 「여기」 의 자리다.
  const [everywhere, setEverywhere] = useState(false)
  const widgets = useResource(
    () => viewApi.home(everywhere ? null : (slug ?? '')),
    [slug, everywhere],
  )
  const watched = useResource(() => objectApi.watching(8), [])
  const [adding, setAdding] = useState(false)
  // 올리는 것은 부서 관리자다. 못 올리는 사람에게 단추를 보이면 눌러 보고 나서 알게 된다.
  const canAdd = isManagerOf(user, slug)

  const workspace = user?.memberships.find((one) => one.slug === slug)

  return (
    <div className="space-y-8">
      <PageHeader
        title={workspace?.name ?? APP_NAME}
        description={workspace?.path ?? '소속된 부서가 없습니다. 관리자에게 배정을 요청하세요.'}
      />

      <ErrorNotice error={maintenance.error} />

      <section className="space-y-3">
        <h2 className="text-base font-semibold">남은 일</h2>
        {/* **0 건인 항목은 서버가 안 내보낸다.** 다 0 인 목록을 매일 보면 사람은
            그 자리를 아예 안 읽게 되고, 그때 진짜 하나가 떠도 눈에 안 들어온다. */}
        {maintenance.data && maintenance.data.length === 0 ? (
          <p className="text-muted-foreground text-sm">지금 처리할 일이 없습니다.</p>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {(maintenance.data ?? []).map((one) => (
              <li
                key={one.key}
                className={
                  one.severity === 'warning'
                    ? 'rounded-md border border-amber-500/40 bg-amber-500/5 p-4'
                    : 'rounded-md border p-4'
                }
              >
                <p className="text-2xl font-semibold">{one.count}</p>
                <p className="text-sm">{one.label}</p>
                {one.link && (
                  <Link
                    to={one.link}
                    className="text-muted-foreground mt-2 block text-xs underline"
                  >
                    보러 가기
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* --- 부서가 올린 위젯 -------------------------------------------------
          화면을 고쳐서 더하지 않는다. 목록에서 조건을 걸고 「통계」 기준을 고른 뒤
          부서 뷰로 저장하고, 그 뷰를 홈에 올리면 여기 그려진다. */}
      {/* --- 내가 지켜보는 것 -------------------------------------------------
          알림은 읽고 나면 사라진다. 모아 볼 자리가 없으면 알림 구독은 알림이 올 때만
          떠오르고, 그러면 「내가 보던 그게 지금 어떤가」 를 여전히 물을 데가 없다. */}
      {(watched.data ?? []).length > 0 && (
        <section className="space-y-3">
          <h2 className="text-base font-semibold">내가 지켜보는 것</h2>
          <ul className="divide-y rounded-md border">
            {(watched.data ?? []).map((one) => (
              <li key={one.id}>
                <Link
                  to={`/o/${one.type_slug}/${one.id}`}
                  className="hover:bg-muted/50 flex items-center gap-3 px-3 py-2 text-sm"
                >
                  <TypeIcon name={one.icon} className="shrink-0" />
                  <span className="min-w-0 flex-1 truncate">{one.label}</span>
                  <span className="text-muted-foreground shrink-0 text-xs">{one.type_label}</span>
                  {/* 언제 바뀌었는지 — **최근 바뀐 것부터** 서므로 이 값이 차례의 근거다. */}
                  <span className="text-muted-foreground shrink-0 text-xs">
                    {shownDateTime(one.updated_at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {(widgets.data ?? []).length > 0 ? (
        <section className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-semibold">부서가 보는 것</h2>
            <div className="flex items-center gap-2">
              <Button
                variant={everywhere ? 'secondary' : 'outline'}
                size="sm"
                aria-pressed={everywhere}
                onClick={() => setEverywhere((before) => !before)}
              >
                {everywhere ? '이 부서만' : '다른 부서 것도'}
              </Button>
              {canAdd && (
                <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
                  <Plus className="mr-1 size-4" />
                  위젯 추가
                </Button>
              )}
            </div>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            {(widgets.data ?? []).map((one, index) => (
              <HomeWidget
                key={one.view.id}
                widget={one}
                // 여러 부서를 한 화면에 놓으면 **어디 것인지** 적어야 한다.
                showWorkspace={everywhere}
                canEdit={canAdd}
                index={index}
                total={(widgets.data ?? []).length}
                onChanged={() => widgets.reload()}
              />
            ))}
          </div>
        </section>
      ) : (
        /* 빈 화면으로 두지 않는다 — 빈 화면은 「아직 안 만들었다」 와 「고장났다」 가
           구별되지 않는다. 무엇을 하면 채워지는지까지 적는다. */
        <section className="rounded-md border border-dashed p-6">
          <h2 className="text-sm font-medium">이 아래가 부서의 자리입니다</h2>
          <p className="text-muted-foreground mt-1 mb-3 text-sm">
            {canAdd
              ? '무엇을 표시할지 선택하면 그 목록이 열립니다. 거기서 조건과 기준을 정하고 「홈 게시」 를 클릭하면 여기에 표시됩니다.'
              : '부서 관리자가 위젯을 올리면 여기에 표시됩니다. 목록 화면의 「통계」 에서 올립니다.'}
          </p>
          {canAdd && (
            <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
              <Plus className="mr-1 size-4" />
              위젯 추가
            </Button>
          )}
        </section>
      )}

      {adding && <AddWidgetDialog onClose={() => setAdding(false)} />}
    </div>
  )
}
