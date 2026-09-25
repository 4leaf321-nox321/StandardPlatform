/**
 * 확장 `caegroup` — CAE 그룹의 기능 묶음. 백엔드 `app/extensions/caegroup` 의 짝.
 *
 * **그룹을 제 이름(「디지털 트윈」)으로 낸다** — 켜고 끄는 일이 사용자에게는 「디지털 트윈이
 * 있다 / 없다」 이고, 「확장」 은 개발자의 사정이다.
 *
 * 경로에 `dt` 한 단을 둔다 — 이 확장에 CAE 그룹의 **다른** 기능이 붙는 날, 그때 경로를
 * 바꾸면 사람들이 즐겨찾기한 주소가 죽는다.
 */

import {
  Gauge,
  HardDrive,
  LayoutDashboard,
  SlidersHorizontal,
  TableProperties,
  Users,
} from 'lucide-react'
import { lazy } from 'react'

import type { ExtensionDef } from '@/extensions'

const DashboardPage = lazy(() => import('./DashboardPage'))
const PairsPage = lazy(() => import('./PairsPage'))
const BulkPage = lazy(() => import('./BulkPage'))
const StaffPage = lazy(() => import('./StaffPage'))
const InfraPage = lazy(() => import('./InfraPage'))
const SettingsPage = lazy(() => import('./SettingsPage'))

export const caegroupExtension: ExtensionDef = {
  name: 'caegroup',
  nav: [
    {
      title: '디지털 트윈',
      items: [
        { label: '대시보드', icon: LayoutDashboard, to: '/ext/caegroup/dt' },
        { label: '역량', icon: Gauge, to: '/ext/caegroup/dt/pairs' },
        { label: '일괄 입력', icon: TableProperties, to: '/ext/caegroup/dt/bulk' },
        { label: '인력', icon: Users, to: '/ext/caegroup/dt/staff' },
        { label: '인프라', icon: HardDrive, to: '/ext/caegroup/dt/infra' },
        // 고를 수 있는 값(S/W 단위 · 용도)을 고치는 자리 — **보이는 사람만 다르다.**
        // 막는 것은 서버다(`require_system_admin`).
        {
          label: '설정',
          icon: SlidersHorizontal,
          to: '/ext/caegroup/dt/settings',
          audience: 'system_admin',
        },
      ],
    },
  ],
  routes: [
    { path: 'ext/caegroup/dt', element: <DashboardPage /> },
    { path: 'ext/caegroup/dt/pairs', element: <PairsPage /> },
    { path: 'ext/caegroup/dt/bulk', element: <BulkPage /> },
    { path: 'ext/caegroup/dt/staff', element: <StaffPage /> },
    { path: 'ext/caegroup/dt/infra', element: <InfraPage /> },
    { path: 'ext/caegroup/dt/settings', element: <SettingsPage /> },
  ],
}
