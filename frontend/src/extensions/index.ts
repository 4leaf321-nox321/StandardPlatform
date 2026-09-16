/**
 * 확장 모듈의 화면 쪽 — **켠 것만 붙는다.**
 *
 * 백엔드 `app/extensions/<이름>` 의 짝이 `src/extensions/<이름>` 이다. 서버가 `.env` 의 EXTENSIONS 를
 * `<meta name="app-extensions">` 로 심어 주고(`shared/branding.ts` 의 ENABLED_EXTENSIONS), 여기서
 * 그 이름들의 메뉴 · 라우트만 사이드바와 라우터에 더한다. 안 켠 인스턴스에는 메뉴도 경로도 없다.
 *
 * 새 확장: `src/extensions/<이름>/index.tsx` 에서 `ExtensionDef` 를 내보내고 아래 REGISTRY 에
 * 한 줄 더한다. **코어 화면(`modules` · `shared`)은 확장을 import 하지 않는다.**
 */

import type { RouteObject } from 'react-router-dom'

import { ENABLED_EXTENSIONS } from '@/shared/branding'
import type { NavGroup } from '@/shared/layout/navigation'

import { sampleExtension } from './sample'

export interface ExtensionDef {
  /** 백엔드의 이름과 같다(EXTENSIONS 에 적는 값). */
  name: string
  /** 사이드바에 더할 그룹. 홈 바로 아래에 선다. */
  nav: NavGroup[]
  /** AppShell 아래 라우트. 경로는 `ext/<이름>/…` 로 시작한다 — 코어 경로와 안 겹치게. */
  routes: RouteObject[]
}

const REGISTRY: Record<string, ExtensionDef> = {
  [sampleExtension.name]: sampleExtension,
}

/** 켜져 있는데 화면 쪽 짝이 없는 이름 — 서버 화면이 말한다. */
export function missingExtensions(enabled: readonly string[] = ENABLED_EXTENSIONS): string[] {
  return enabled.filter((name) => !(name in REGISTRY))
}

export function enabledExtensions(enabled: readonly string[] = ENABLED_EXTENSIONS): ExtensionDef[] {
  return enabled.flatMap((name) => (name in REGISTRY ? [REGISTRY[name]] : []))
}

export function extensionNavGroups(enabled?: readonly string[]): NavGroup[] {
  return enabledExtensions(enabled).flatMap((ext) => ext.nav)
}

export function extensionRoutes(enabled?: readonly string[]): RouteObject[] {
  return enabledExtensions(enabled).flatMap((ext) => ext.routes)
}
