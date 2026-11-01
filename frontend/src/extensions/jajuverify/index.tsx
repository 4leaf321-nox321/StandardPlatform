/**
 * 확장 `jajuverify` — 자주검증 회의(심사회 · 포스트모텀 · 설명회)의 한 장 보고. 백엔드
 * `app/extensions/jajuverify` 의 짝(ADR 0044).
 *
 * **그룹을 제 이름(「자주검증」)으로 낸다** — 켜고 끄는 일이 사용자에게는 「자주검증이 있다 / 없다」
 * 이고, 「확장」 은 개발자의 사정이다.
 *
 * 경로에 `meetings` 한 단을 둔다(`/ext/jajuverify/meetings/<종류>[/<회의>]`) — 이 확장에 다른
 * 기능(미결 조치 · 온톨로지 연결 설정 …)이 붙는 날 경로를 바꾸면 즐겨찾기한 주소가 죽는다.
 * **회의 종류마다 화면이 따로다**(심사회 · 포스트모텀 · 설명회) — 지금은 같은 표를 쓴다.
 */

import { Microscope, Presentation, Scale } from 'lucide-react'
import { lazy } from 'react'

import type { ExtensionDef } from '@/extensions'

const MeetingListPage = lazy(() => import('./MeetingListPage'))
const ReviewSheet = lazy(() => import('./ReviewSheet'))
const PostmortemSheet = lazy(() => import('./PostmortemSheet'))
const BriefingSheet = lazy(() => import('./BriefingSheet'))

const ROOT = 'ext/jajuverify/meetings'

export const jajuverifyExtension: ExtensionDef = {
  name: 'jajuverify',
  nav: [
    {
      title: '자주검증',
      items: [
        { label: '심사회', icon: Scale, to: `/${ROOT}/review` },
        { label: '포스트모텀', icon: Microscope, to: `/${ROOT}/postmortem` },
        { label: '설명회', icon: Presentation, to: `/${ROOT}/briefing` },
      ],
    },
  ],
  routes: [
    { path: `${ROOT}/review`, element: <MeetingListPage kind="review" /> },
    { path: `${ROOT}/review/:meetingId`, element: <ReviewSheet /> },
    { path: `${ROOT}/postmortem`, element: <MeetingListPage kind="postmortem" /> },
    { path: `${ROOT}/postmortem/:meetingId`, element: <PostmortemSheet /> },
    { path: `${ROOT}/briefing`, element: <MeetingListPage kind="briefing" /> },
    { path: `${ROOT}/briefing/:meetingId`, element: <BriefingSheet /> },
  ],
}
