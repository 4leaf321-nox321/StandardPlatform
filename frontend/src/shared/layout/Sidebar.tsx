/**
 * 사이드바 — 접으면 폭 0으로 줄어들고 본문이 전체 폭을 쓴다.
 *
 * 폭을 0으로 만들되 내부 래퍼는 고정폭을 유지한다. 그래야 접힐 때 글자가
 * 찌그러지지 않고 그대로 잘려 나간다.
 *
 * **묶음은 하나씩 접는다.** 타입이 늘면 사이드바가 길어지고, 그때 사람은 자기가 쓰는 묶음을
 * 찾으려고 매번 스크롤한다 — 안 쓰는 묶음을 접어 두면 그 일이 없어진다. 접은 것은 브라우저에
 * 기억한다(사람마다 쓰는 묶음이 다르니 서버가 알 일이 아니다).
 */

import { useCallback, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { NavLink, useLocation } from 'react-router-dom'

import { UNKNOWN_VERSION, systemApi } from '@/shared/api/system'
import { useAuth } from '@/shared/auth/AuthContext'
import { isAnyManager, isSystemAdmin } from '@/shared/auth/roles'
import { APP_NAME, APP_TAGLINE, STORAGE_PREFIX } from '@/shared/branding'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/shared/components/ui/sheet'
import { useResource } from '@/shared/hooks/useResource'
import { ontologyApi } from '@/modules/ontology/api'
import { extensionNavGroups, useEnabledExtensions } from '@/extensions'
import { itemHref, visibleGroups } from '@/shared/layout/navigation'
import { cn } from '@/shared/lib/utils'

/** 접어 둔 묶음. **한 서버에 두 플랫폼이 있을 수 있어** 키에 slug 를 붙인다. */
const STORAGE_KEY = `${STORAGE_PREFIX}.sidebar.folded`

function readFolded(): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed)
      ? parsed.filter((one): one is string => typeof one === 'string')
      : []
  } catch {
    // 사생활 보호 창이나 저장을 막은 브라우저 — 접힘은 편의일 뿐이라 그냥 다 펼친다.
    return []
  }
}

/** 묶음 접기 상태. 제목이 열쇠다 — 동적 묶음은 서버가 준 이름을 쓴다. */
function useFoldedGroups() {
  const [folded, setFolded] = useState<string[]>(readFolded)
  const toggle = useCallback((title: string) => {
    setFolded((current) => {
      const next = current.includes(title)
        ? current.filter((one) => one !== title)
        : [...current, title]
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      } catch {
        // 위와 같다 — 이번 화면에서만 기억한다.
      }
      return next
    })
  }, [])
  return { folded, toggle }
}

interface SidebarProps {
  collapsed: boolean
  workspaceSlug: string
  onNavigate?: () => void
}

function SidebarBody({ workspaceSlug, onNavigate }: Omit<SidebarProps, 'collapsed'>) {
  const { user } = useAuth()
  // **서버가 정본이다.** 번들에 박으면 그것은 빌드된 버전이지 지금 도는 서버가
  // 아니다 — 배포가 반쯤 끝난 상태에서 둘이 갈리고, 그때 화면이 거짓말을 한다.
  const health = useResource(() => systemApi.health(), [])
  const release = health.data?.version
  // 서버가 이 빌드와 다른 버전인가. **개발에서만 본다** — 배포에서는 백엔드 한
  // 프로세스가 SPA 까지 서빙하므로 둘이 다를 수가 없고, 그 자리에 경고가 뜨면
  // 그것 자체가 거짓말이다.
  const stale =
    import.meta.env.DEV && !!release && release !== UNKNOWN_VERSION && release !== __APP_VERSION__

  // **볼 수 있는 것만 보여 준다.** 눌러야 403 을 아는 메뉴는 "할 수 있는 일" 을
  // 알려 주지 못한다. 권한은 서버가 판정한다 — 여기는 표시일 뿐이다.
  // **정의가 만든 화면.** 못 불러와도 정적 메뉴는 그대로 선다 — 사이드바가
  // 통째로 비면 나갈 길까지 사라진다.
  const dynamic = useResource(() => ontologyApi.nav(), [])
  const enabled = useEnabledExtensions()

  const groups = visibleGroups(
    {
      isSystemAdmin: isSystemAdmin(user),
      isAnyManager: isAnyManager(user),
    },
    dynamic.data ?? [],
    // **켜진 목록은 서버가 답한다** — 메타만 믿으면 껐는데 메뉴가 남는다(실측).
    extensionNavGroups(enabled),
  )

  const { folded, toggle } = useFoldedGroups()
  const { pathname } = useLocation()

  return (
    <div className="flex h-full w-60 flex-col">
      <div className="flex h-14 shrink-0 flex-col justify-center border-b px-4">
        <span className="text-base leading-tight font-semibold tracking-tight">{APP_NAME}</span>
        <span className="text-muted-foreground text-xs leading-tight">
          {APP_TAGLINE}
          {/* **못 찾았으면 안 적는다.** unknown 을 그대로 띄우면 버전 자리가
              고장난 것처럼 보이는데, 실제로는 개발 경로에서 돈다는 뜻이다. */}
          {release && release !== UNKNOWN_VERSION && (
            <span
              className={cn(
                'font-mono',
                APP_TAGLINE && 'ml-1.5',
                stale && 'font-semibold text-amber-600',
              )}
              title={
                stale
                  ? `이 화면은 ${__APP_VERSION__} 인데 서버는 ${release} 입니다. ` +
                    '다른 서버에 붙어 있을 수 있습니다.'
                  : '지금 도는 서버의 버전입니다'
              }
            >
              {/* **버전 글자는 제 노드에 둔다.** 배지를 형제로 붙이면 글자가
                  이어져 버전만으로는 찾을 수 없게 된다. */}
              <span>{release}</span>
              {stale && <span className="ml-1">!= {__APP_VERSION__}</span>}
            </span>
          )}
        </span>
      </div>

      <nav className="flex-1 space-y-5 overflow-y-auto px-2 py-4">
        {groups.map((group) => {
          // **제목이 있어야 접을 수 있다.** 제목이 없는 묶음은 홈 하나뿐이라 접을 것도 없고,
          // 접는 단추를 둘 자리도 없다.
          const title = group.title
          const isFolded = Boolean(title && folded.includes(title))
          // 접힌 묶음 안에 **지금 보는 화면**이 있으면 점을 찍는다 — 접었다고 「어디 있는지」 를
          // 모르게 두면, 사람은 묶음을 하나씩 펴 가며 찾는다.
          const hasActive = group.items.some((item) => {
            const href = itemHref(item, workspaceSlug)
            return item.end ? pathname === href : pathname.startsWith(href)
          })
          return (
            <div key={title ?? group.items[0]?.label}>
              {/* **제목이 없으면 자리도 안 남긴다.** 빈 문단을 두면 홈 위에 설명
                없는 여백이 생겨 「뭔가 안 나온다」 로 읽힌다. */}
              {title && (
                <button
                  type="button"
                  aria-expanded={!isFolded}
                  onClick={() => toggle(title)}
                  className="text-muted-foreground hover:text-foreground flex w-full items-center gap-1 rounded px-2 pb-1 text-xs font-medium"
                >
                  <ChevronRight
                    className={cn('size-3 shrink-0 transition-transform', !isFolded && 'rotate-90')}
                  />
                  <span className="truncate">{title}</span>
                  {isFolded && hasActive && (
                    <span
                      className="bg-primary ml-auto size-1.5 shrink-0 rounded-full"
                      title="지금 보는 화면이 이 묶음 안에 있습니다"
                    />
                  )}
                </button>
              )}
              {/* **접으면 그리지 않는다.** CSS 로 숨기면 접힌 묶음의 링크가 탭 이동과 읽기
                  프로그램에는 그대로 남아, 접었는데도 거기로 갈 수 있다. */}
              {!isFolded && (
                <ul className="space-y-0.5">
                  {group.items.map((item) => (
                    <li key={item.label}>
                      <NavLink
                        to={itemHref(item, workspaceSlug)}
                        end={item.end}
                        onClick={onNavigate}
                        className={({ isActive }) =>
                          cn(
                            'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors',
                            isActive
                              ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                              : 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground',
                          )
                        }
                      >
                        <item.icon className="size-4 shrink-0" />
                        <span className="truncate">{item.label}</span>
                        {item.pending && (
                          <span className="text-muted-foreground/70 ml-auto shrink-0 rounded border px-1 text-[10px] leading-4">
                            미구현
                          </span>
                        )}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </nav>
    </div>
  )
}

/**
 * 좁은 화면의 메뉴 — **서랍으로 연다.**
 *
 * 옆의 사이드바는 md 미만에서 아예 안 그려진다. 상단의 접기 단추는 폭만 바꾸므로,
 * **좁은 화면에서는 눌러도 아무 일이 없고 메뉴로 가는 길이 하나도 없다** — 주소를
 * 직접 치지 않으면 다른 화면에 못 간다.
 *
 * 고르면 닫는다. 서랍이 덮은 채로 두면 방금 연 화면을 못 본다.
 */
export function SidebarDrawer({
  open,
  onOpenChange,
  workspaceSlug,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  workspaceSlug: string
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="left" className="bg-sidebar w-72 p-0 md:hidden">
        <SheetHeader className="sr-only">
          <SheetTitle>메뉴</SheetTitle>
        </SheetHeader>
        <SidebarBody workspaceSlug={workspaceSlug} onNavigate={() => onOpenChange(false)} />
      </SheetContent>
    </Sheet>
  )
}

export function Sidebar({ collapsed, workspaceSlug }: SidebarProps) {
  return (
    <aside
      data-collapsed={collapsed}
      aria-hidden={collapsed}
      className={cn(
        'bg-sidebar hidden h-full shrink-0 flex-col overflow-hidden md:flex',
        'transition-[width] duration-200 ease-in-out',
        collapsed ? 'w-0 border-r-0' : 'w-60 border-r',
      )}
    >
      <SidebarBody workspaceSlug={workspaceSlug} />
    </aside>
  )
}
