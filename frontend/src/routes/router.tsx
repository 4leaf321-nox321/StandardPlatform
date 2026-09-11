/**
 * 라우트 표.
 *
 * 사이드바(navigation.ts)에 있는 항목은 여기에 대응 경로가 있어야 한다 — 시험이
 * 검사한다(`backend/tests/architecture/test_boundaries.py`). 아직 구현되지 않은
 * 화면은 `pending` 으로 두면 **여기서 자동으로 stub 이 생긴다.**
 *
 * /login · /signup · /force-password-change 만 가드 밖에 있고 나머지는 전부
 * ProtectedRoute 아래에 둔다 — **새 화면을 추가할 때 가드를 깜빡할 자리가 없도록.**
 */

import { lazy } from 'react'
import { Navigate, createBrowserRouter } from 'react-router-dom'

import ForcePasswordChangePage from '@/modules/auth/ForcePasswordChangePage'
import LoginPage from '@/modules/auth/LoginPage'
import { useAuth } from '@/shared/auth/AuthContext'
import { ProtectedRoute } from '@/shared/auth/ProtectedRoute'
import { Placeholder } from '@/shared/components/Placeholder'
import { AppShell } from '@/shared/layout/AppShell'
import { DEFAULT_WORKSPACE, pendingItems } from '@/shared/layout/navigation'

/**
 * **매일 밟는 길은 처음에 싣는다.** 로그인이 그것이다.
 *
 * 나머지는 나눠 싣는다 — 관리 화면은 대부분의 사람이 한 번도 안 열고, 그것을 위해
 * 첫 로드가 느려질 이유가 없다.
 */
const AccountsAdminPage = lazy(() => import('@/modules/accounts/AccountsAdminPage'))
const AuditPage = lazy(() => import('@/modules/audit/AuditPage'))
const GraphPage = lazy(() => import('@/modules/graph/GraphPage'))
const MembersPage = lazy(() => import('@/modules/workspaces/MembersPage'))
const NoticesPage = lazy(() => import('@/modules/notices/NoticesPage'))
const ObjectListPage = lazy(() => import('@/modules/objects/ObjectListPage'))
const ObjectProfilePage = lazy(() => import('@/modules/objects/ObjectProfilePage'))
const QualityPage = lazy(() => import('@/modules/objects/QualityPage'))
const OntologyLayout = lazy(() => import('@/modules/ontology/OntologyLayout'))
const OntologyGroupsPage = lazy(() => import('@/modules/ontology/OntologyGroupsPage'))
const OntologyTypesPage = lazy(() => import('@/modules/ontology/OntologyTypesPage'))
const OntologyRelationsPage = lazy(
  () => import('@/modules/ontology/OntologyRelationsPage'),
)
const OntologyImportPage = lazy(() => import('@/modules/ontology/OntologyImportPage'))
const NotificationsPage = lazy(() => import('@/modules/notifications/NotificationsPage'))
const ProfilePage = lazy(() => import('@/modules/auth/ProfilePage'))
const ServerPage = lazy(() => import('@/modules/server/ServerPage'))
const SignupPage = lazy(() => import('@/modules/auth/SignupPage'))
const WorkspaceHomePage = lazy(() => import('@/modules/workspaces/WorkspaceHomePage'))
const WorkspacesAdminPage = lazy(() => import('@/modules/workspaces/WorkspacesAdminPage'))

/**
 * 아직 화면이 없는 항목 — **사이드바가 정본이다.** 제목·단계·설명을 여기 다시
 * 적으면 메뉴와 화면이 다른 말을 하게 된다. 화면이 생기면 그 항목의 pending 을
 * 지우고 여기서 빠진다.
 */
const stubs = pendingItems().map((item) => ({
  path: item.to!.replace(/^\//, ''),
  element: (
    <Placeholder title={item.label} phase={item.phase ?? '—'} description={item.summary} />
  ),
}))

/**
 * 첫 화면 — **내 부서로 보낸다.**
 *
 * 고정된 slug 로 두면 다른 부서 사람이 로그인했을 때 자기 것이 아닌 부서를
 * 가리키고, 목록이 비어 보인다 — 그것은 데이터가 없는 것과 구별이 안 된다.
 */
function HomeRedirect() {
  const { user } = useAuth()
  const slug = user?.home_workspace_slug ?? user?.memberships[0]?.slug ?? DEFAULT_WORKSPACE
  return <Navigate to={`/w/${slug}`} replace />
}

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/signup', element: <SignupPage /> },
  {
    element: <ProtectedRoute />,
    children: [
      { path: '/force-password-change', element: <ForcePasswordChangePage /> },
      {
        path: '/',
        element: <AppShell />,
        children: [
          { index: true, element: <HomeRedirect /> },

          // --- 도메인 화면은 여기에 --------------------------------------
          //
          // 사이드바에 `pending: true` 로 적어 두면 아래 stubs 가 자리를 만든다.
          // 실제 화면이 생기면 그 표시를 지우고 여기에 한 줄을 적는다.
          ...stubs,

          // 내 활동
          { path: 'notifications', element: <NotificationsPage /> },
          { path: 'me', element: <ProfilePage /> },

          // 공통
          // **정의를 그림으로.** 타입이 늘어도 경로는 하나다 — 구조와 탐색이 한 화면이다.
          { path: 'graph', element: <GraphPage /> },
          { path: 'notices', element: <NoticesPage /> },
          { path: 'audit', element: <AuditPage /> },
          { path: 'quality', element: <QualityPage /> },

          // **정의가 만드는 화면.** 타입이 늘어도 라우트는 안 늘어난다 —
          // `navigation.ts` 가 정적 화면의 정본이라는 규칙이 그대로 선다.
          { path: 'o/:typeSlug', element: <ObjectListPage /> },
          { path: 'o/:typeSlug/:objectId', element: <ObjectProfilePage /> },

          // 관리 (전사)
          { path: 'admin/accounts', element: <AccountsAdminPage /> },
          // **두 번째 사이드바.** 묶음과 타입을 각각 제 화면에서 본다 —
          // 한 화면에 쌓으면 「지금 어디를 보고 있나」 를 화면이 말해 주지 못한다.
          {
            path: 'admin/ontology',
            element: <OntologyLayout />,
            children: [
              { index: true, element: <Navigate to="groups" replace /> },
              { path: 'groups', element: <OntologyGroupsPage /> },
              { path: 'types', element: <OntologyTypesPage /> },
              { path: 'relations', element: <OntologyRelationsPage /> },
              { path: 'import', element: <OntologyImportPage /> },
            ],
          },
          { path: 'admin/workspaces', element: <WorkspacesAdminPage /> },
          { path: 'admin/server', element: <ServerPage /> },

          // 부서 스코프
          {
            path: 'w/:slug',
            children: [
              { index: true, element: <WorkspaceHomePage /> },
              { path: 'members', element: <MembersPage /> },
            ],
          },

          {
            path: '*',
            element: (
              <Placeholder title="없는 페이지" phase="—" description="주소를 확인해 주세요." />
            ),
          },
        ],
      },
    ],
  },
])
