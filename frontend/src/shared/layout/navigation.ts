/**
 * 사이드바 메뉴 정의 — **화면 목록의 정본이다.**
 *
 * 메뉴를 컴포넌트에서 분리해 두는 이유: 어떤 화면이 있어야 하는지가 한 곳에
 * 적혀 있어야 라우터·사이드바·권한이 서로 어긋나지 않는다. 시험이 검사한다
 * (`backend/tests/architecture/test_boundaries.py`).
 *
 * ## 도메인을 얹을 때
 *
 * 아래 「도메인」 그룹에 항목을 더한다. 화면이 아직 없으면 `pending: true` 로 두면
 * 라우터가 stub 을 만든다 — **자리는 보이되 눌러 보고 알게 하지 않는다.** 제목·
 * 단계·설명을 라우터에 다시 적지 않는 것이 요점이다.
 */

import {
  Bell,
  Boxes,
  Building2,
  Home,
  LayoutGrid,
  Megaphone,
  ScrollText,
  ShieldCheck,
  Server,
  UserCog,
  Users,
  Waypoints,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

/** 소속 부서를 아직 모를 때 쓰는 임시 slug. 설치 스크립트가 만드는 뿌리 부서다. */
export const DEFAULT_WORKSPACE = 'hq'

/**
 * 누구에게 보이는가.
 *
 * **없으면 누르고 나서 403 을 본다.** 눌러야 권한이 없다는 것을 아는 화면은
 * "할 수 있는 일" 을 알려 주지 못한다.
 *
 * 이것은 **표시**일 뿐 권한이 아니다. 권한은 서버가 판정한다 — 사이드바를 고쳐
 * 우회할 수 있으면 그건 애초에 보안이 아니다.
 */
export type NavAudience = 'everyone' | 'manager' | 'system_admin'

export interface NavItem {
  label: string
  icon: LucideIcon
  /** 고정 경로 */
  to?: string
  /** 부서 스코프 경로 */
  resolve?: (slug: string) => string
  /** NavLink 의 end 옵션 (부모 경로가 자식에도 활성화되지 않게) */
  end?: boolean
  /** 기본은 everyone. */
  audience?: NavAudience
  /** 아직 화면이 없다. 사이드바가 「미구현」 표를 단다 — **자리는 보이되 눌러
   *  보고 알게 하지 않는다.** 화면이 생기면 이 표시를 지운다. */
  pending?: boolean
  /** pending 인 것이 로드맵의 어느 단계에서 들어오는가. */
  phase?: string
  /** 한 줄 설명. 개요 화면과 stub 화면이 같은 말을 하도록 여기 한 번만 적는다. */
  summary?: string
}

/** 서버가 주는 동적 묶음 (`modules/ontology/api` 의 NavGroupNode 와 짝). */
export interface DynamicGroup {
  slug: string
  label: string
  icon: string
  audience: string
  items: { label: string; icon: string; to: string; slug: string }[]
}

export interface NavGroup {
  /** 없으면 제목 없이 항목만 선다. **한 항목짜리 그룹에는 제목을 안 단다** —
   *  제목은 「여기 여럿이 있다」 는 신호라서, 하나뿐인데 달면 거짓말이 된다. */
  title?: string
  items: NavItem[]
  /** 그룹 전체가 안 보이는 조건. 항목이 하나도 안 보이면 제목도 지운다. */
  audience?: NavAudience
}

export const NAV_GROUPS: NavGroup[] = [
  {
    // **제목이 없다.** 홈 하나뿐인데 제목을 달면 「여기 더 있다」 로 읽힌다.
    items: [{ label: '홈', icon: Home, resolve: (s) => `/w/${s}`, end: true }],
  },
  {
    // --- 여기가 각 플랫폼이 채우는 자리다 --------------------------------
    //
    // **동선이 곧 순서여야 한다.** 사람이 밟는 차례대로 놓으면 「다음에 어디로」
    // 를 안 묻는다. 이 틀에는 도메인이 없으므로 자리만 하나 두고, 그것이
    // 무엇으로 바뀌어야 하는지 stub 화면이 말한다.
    title: '도메인',
    items: [
      {
        label: '(도메인 화면)',
        icon: LayoutGrid,
        to: '/domain',
        pending: true,
        phase: '이 플랫폼의 1단계',
        summary:
          '이 자리를 지우고 각 플랫폼의 화면을 넣습니다. ' +
          'navigation.ts 가 화면 목록의 정본이라, 여기 적으면 라우터와 사이드바가 함께 따라옵니다.',
      },
    ],
  },
  {
    title: '내 활동',
    items: [
      { label: '알림', icon: Bell, to: '/notifications' },
      { label: '내 정보', icon: UserCog, to: '/me' },
    ],
  },
  {
    // 도메인이 「기준정보」 같은 것을 여기 더하게 되어 있고, 그때 이름을 다시
    // 정하지 않아도 되도록 제목을 단다.
    title: '공통',
    items: [
      // **정의가 있는 설치라면 어디서나 있어야 하는 화면이다.** 타입이 늘수록
      // 「이게 저것과 어떻게 이어지지」 를 물을 자리가 목록만으로는 안 생긴다.
      { label: '지식 그래프', icon: Waypoints, to: '/graph' },
      { label: '공지', icon: Megaphone, to: '/notices' },
    ],
  },
  {
    // **사슬이 아닌 둘.** 부서 사람과 그 부서에서 무엇이 바뀌었나.
    title: '내 부서',
    audience: 'manager',
    items: [
      { label: '부서 멤버', icon: Users, resolve: (s) => `/w/${s}/members`, audience: 'manager' },
      {
        // **기록만 쌓이고 볼 자리가 없으면 자산이 아니다.** 여기에는 만들기·
        // 고치기·지우기가 없다 — 고칠 수 있으면 감사가 아니다.
        label: '변경 이력',
        icon: ScrollText,
        to: '/audit',
        audience: 'manager',
      },
      {
        // **나빠지고 있으면 어딘가에 떠야 한다.** 검증은 넣을 때만 걸리고, 그 뒤에
        // 필수가 생기고 가리키던 것이 지워지고 같은 것이 둘이 된다. 고칠 수 있는
        // 사람(관리자)에게만 — 못 고치는 사람에게 띄우면 못 지우는 숫자가 된다.
        label: '데이터 품질',
        icon: ShieldCheck,
        to: '/quality',
        audience: 'manager',
      },
    ],
  },
  {
    title: '관리',
    audience: 'system_admin',
    items: [
      { label: '계정', icon: UserCog, to: '/admin/accounts', audience: 'system_admin' },
      // **전사 부서 목록이다.** 위 '내 부서' 와 헷갈리지 않게 이름을 가른다 —
      // 이쪽은 부서를 만들고 고치는 자리고, 저쪽은 내 부서의 일이다.
      { label: '부서 정보', icon: Building2, to: '/admin/workspaces', audience: 'system_admin' },
      // **도메인을 정의하는 자리.** 타입을 만들면 위 묶음과 화면이 여기서 생긴다.
      {
        label: '온톨로지',
        icon: Boxes,
        to: '/admin/ontology',
        audience: 'system_admin',
      },
      { label: '서버', icon: Server, to: '/admin/server', audience: 'system_admin' },
    ],
  },
]

/**
 * 서버가 준 동적 묶음(`/api/ontology/nav`)을 정적 메뉴와 합친다.
 *
 * **정적 화면의 정본은 여전히 이 파일이다.** 동적 그룹은 그 아래 합류할 뿐이고,
 * 라우트는 `/o/:typeSlug` 둘로 받으므로 `router.test.tsx` 의 어긋남 검사가
 * 그대로 선다.
 *
 * 동적 묶음이 하나라도 있으면 **「도메인」 자리표시자를 감춘다** — 실제 도메인
 * 화면 옆에 「(도메인 화면)」 stub 이 함께 서면, 그것을 눌러야 하는지 아닌지를
 * 사람이 매번 판단하게 된다.
 */
export function mergeDynamic(groups: NavGroup[], dynamic: DynamicGroup[]): NavGroup[] {
  if (dynamic.length === 0) return groups

  const converted: NavGroup[] = dynamic.map((group) => ({
    title: group.label,
    audience: (group.audience as NavAudience) ?? 'everyone',
    items: group.items.map((item) => ({
      label: item.label,
      // 아이콘 이름은 데이터에서 온다. **모르는 이름이면 기본으로 떨어진다** —
      // 메뉴가 통째로 안 뜨는 것보다 낫다.
      icon: LayoutGrid,
      to: item.to,
    })),
  }))

  const withoutPlaceholder = groups.filter(
    (group) => !group.items.every((item) => item.pending && item.to === '/domain'),
  )
  // 홈 바로 아래에 세운다 — **동선이 곧 순서여야 한다.**
  return [withoutPlaceholder[0], ...converted, ...withoutPlaceholder.slice(1)]
}

export function itemHref(item: NavItem, slug: string): string {
  return item.to ?? item.resolve?.(slug) ?? '/'
}

/** 이 사람에게 보이는가. 서버가 최종 판정을 한다 — 여기는 표시일 뿐이다. */
export function canSee(
  audience: NavAudience | undefined,
  viewer: { isSystemAdmin: boolean; isAnyManager: boolean },
): boolean {
  if (audience === 'system_admin') return viewer.isSystemAdmin
  if (audience === 'manager') return viewer.isSystemAdmin || viewer.isAnyManager
  return true
}

/** 볼 수 있는 것만 남긴 메뉴. **빈 그룹은 제목까지 지운다.** */
export function visibleGroups(
  viewer: {
    isSystemAdmin: boolean
    isAnyManager: boolean
  },
  dynamic: DynamicGroup[] = [],
): NavGroup[] {
  return mergeDynamic(NAV_GROUPS, dynamic).map((group) => ({
    ...group,
    items: group.items.filter((item) => canSee(item.audience, viewer)),
  })).filter((group) => canSee(group.audience, viewer) && group.items.length > 0)
}

/** 아직 화면이 없는 항목들. 라우터가 이것으로 stub 경로를 만든다 — **사이드바가
 *  정본이라** 제목·단계·설명을 두 곳에 적지 않는다. */
export function pendingItems(): NavItem[] {
  return NAV_GROUPS.flatMap((group) => group.items).filter((item) => item.pending && item.to)
}
