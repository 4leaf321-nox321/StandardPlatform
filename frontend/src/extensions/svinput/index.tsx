/**
 * 확장 `svinput` — 자주검증 입력(ADR 0051). 백엔드 `app/extensions/svinput` 의 짝.
 *
 * **그룹을 제 이름(「자주검증 입력」)으로 낸다** — 켜고 끄는 일이 사용자에게는 「그 기능이 있다 /
 * 없다」 이고, 「확장」 은 개발자의 사정이다. 경로에 `postmortems` 한 단을 둔다 — 심사회 · 설명회가
 * 붙는 날 즐겨찾기한 주소가 죽지 않게.
 *
 * 문제점 목록(관리 단위) · 포스트모텀 목록(모델별) → 문제점 리스트 → 문제점 한 페이지 → 집계. 문서 ·
 * 문제점은 허브의 온톨로지 객체라 **만들기 · 고치기는 코어 화면 조각**(`ObjectCreateDialog` ·
 * `PropertyFields`)이 한다.
 */

import { BarChart3, FileText, ListChecks, SlidersHorizontal } from 'lucide-react'
import { lazy } from 'react'

import type { ExtensionDef } from '@/extensions'

const IssueListPage = lazy(() => import('./IssueListPage'))
const ReportListPage = lazy(() => import('./ReportListPage'))
const ReportPage = lazy(() => import('./ReportPage'))
const IssuePage = lazy(() => import('./IssuePage'))
const DashboardPage = lazy(() => import('./DashboardPage'))
const SetupPage = lazy(() => import('./SetupPage'))

const ROOT = 'ext/svinput'

export const svinputExtension: ExtensionDef = {
  name: 'svinput',
  nav: [
    {
      title: '자주검증 입력',
      items: [
        // **관리 단위는 문제점이다**(ADR 0051 7절) — 문서는 그것을 참조해 구성한다.
        { label: '문제점', icon: ListChecks, to: `/${ROOT}/issues` },
        { label: '포스트모텀', icon: FileText, to: `/${ROOT}/postmortems` },
        { label: '집계', icon: BarChart3, to: `/${ROOT}/dashboard` },
        // 역할 → 타입 · 칸을 고르는 자리 — **보이는 사람만 다르다.** 막는 것은 서버다.
        {
          label: '설정',
          icon: SlidersHorizontal,
          to: `/${ROOT}/setup`,
          audience: 'system_admin',
        },
      ],
    },
  ],
  routes: [
    { path: `${ROOT}/issues`, element: <IssueListPage /> },
    { path: `${ROOT}/postmortems`, element: <ReportListPage /> },
    { path: `${ROOT}/postmortems/:reportId`, element: <ReportPage /> },
    { path: `${ROOT}/issues/:issueId`, element: <IssuePage /> },
    { path: `${ROOT}/dashboard`, element: <DashboardPage /> },
    { path: `${ROOT}/setup`, element: <SetupPage /> },
  ],
}
