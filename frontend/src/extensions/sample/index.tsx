/**
 * 본보기 확장 — **새 확장을 만들 때 이 폴더를 복사한다.** 백엔드 `app/extensions/sample` 의 짝.
 * 메뉴 하나(「본보기」)와 페이지 하나(`/ext/sample`)가 전부다.
 */

import { Puzzle } from 'lucide-react'
import { lazy } from 'react'

import type { ExtensionDef } from '@/extensions'

const SamplePage = lazy(() => import('./SamplePage'))

export const sampleExtension: ExtensionDef = {
  name: 'sample',
  nav: [{ title: '확장', items: [{ label: '본보기', icon: Puzzle, to: '/ext/sample' }] }],
  routes: [{ path: 'ext/sample', element: <SamplePage /> }],
}
