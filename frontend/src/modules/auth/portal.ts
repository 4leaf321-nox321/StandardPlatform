/**
 * HWAX 포털 로그인(ADR 0028) — 로그인 화면의 단추와 `?sso=` 사유.
 *
 * 포털이 그 사람으로 launch 토큰을 만들어 서버의 콜백(`/api/auth/portal-callback`)에 POST 한다.
 * 성공하면 서버가 refresh 쿠키를 심고 SPA 루트로 보내므로 화면은 할 일이 없다 — 기동 때 그
 * 쿠키로 세션을 되살린다(`AuthContext`). 실패 · 승인 대기 · 정지면 서버가 **로그인 화면에
 * `?sso=<사유>` 를 달아** 보낸다. 사유는 사람이 할 일이 갈리는 만큼만 온다(자세한 것은 서버 로그).
 */

import { api } from '@/shared/api/client'

/**
 * 단추가 보낼 곳 — 포털의 `/launch/<id>`. 서버가 `<meta name="app-portal-launch">` 로 심는다
 * (받을 준비 — `PORTAL_JWKS_URL` — 까지 된 설치만). **비면 단추가 없다.** 부를 때마다 읽는다 —
 * 시험이 meta 를 바꿔 끼운다.
 */
export function portalLaunchUrl(): string {
  if (typeof document === 'undefined') return ''
  return document.querySelector('meta[name="app-portal-launch"]')?.getAttribute('content') ?? ''
}

export type SsoTone = 'info' | 'error'

/**
 * `?sso=` 사유 → 사람 말. **모르는 사유도 말한다** — 서버가 새 사유를 더했는데 화면이 그 말을
 * 모르면 아무것도 안 뜨고, 사람은 단추가 고장 난 줄 안다.
 */
export function ssoMessage(reason: string): { tone: SsoTone; text: string } {
  switch (reason) {
    case 'pending':
      return {
        tone: 'info',
        text: '가입 승인을 기다립니다. HWAX 포털로 확인된 신원으로 가입 신청이 접수되어 있습니다 — 관리자가 승인하면 HWAX 포털에서 다시 열거나 「HWAX 포털로 로그인」 으로 로그인할 수 있습니다.',
      }
    case 'suspended':
      return { tone: 'error', text: '정지된 계정입니다. 관리자에게 문의하세요.' }
    case 'deleted':
      return { tone: 'error', text: '삭제된 계정입니다. 관리자에게 문의하세요.' }
    case 'expired':
      return {
        tone: 'error',
        text: 'HWAX 포털의 로그인 정보가 유효 시간을 벗어났습니다. HWAX 포털에서 다시 여세요 — 계속되면 서버 시각이 어긋났을 수 있으니 관리자에게 알려 주세요.',
      }
    case 'unavailable':
      return {
        tone: 'error',
        text: '이 서버가 HWAX 포털의 공개 키를 받지 못해 포털 로그인을 확인할 수 없습니다. 아이디 · 비밀번호로 로그인하거나, 관리자에게 알려 주세요.',
      }
    case 'admin_sub':
      return {
        tone: 'error',
        text: '이 계정은 아이디가 포털의 메일 주소와 달라 포털 로그인으로 연결하지 않습니다(시스템 관리자 계정). 아이디 · 비밀번호로 로그인하세요.',
      }
    case 'invalid':
      return {
        tone: 'error',
        text: 'HWAX 포털의 로그인 정보를 확인하지 못했습니다. HWAX 포털에서 다시 여세요 — 한 번 사용한 로그인 정보는 다시 받지 않습니다.',
      }
    default:
      return {
        tone: 'error',
        text: `HWAX 포털 로그인에 실패했습니다(${reason}). HWAX 포털에서 다시 열고, 계속되면 관리자에게 알려 주세요.`,
      }
  }
}

/** 게이트웨이 위임을 마지막으로 거절한 일 — 모든 서버 · 프로세스(접근 로그)에서. */
export interface GatewayRefusal {
  at: string
  /** 400 머리 없음 · 401 비밀 다름 · 403 계정 · 주소 · 404 꺼짐. */
  status: number
  /** 이 프로세스가 본 거절이면 자세한 사유, 아니면 숫자의 뜻. */
  reason: string
}

/**
 * HWAX 게이트웨이의 사람별 위임(`POST /api/auth/sso`, ADR 0028) — 포털 로그인과 따로 켜진다.
 * 게이트웨이는 위임이 200 이 아니면 그 사람의 도구 호출을 거절한다(서비스 계정으로 안 내려간다).
 */
export interface GatewayDelegation {
  /** `HEAX_SSO_SECRET` 이 있나. */
  enabled: boolean
  /** 게이트웨이가 부르는 길 — 앱 포트로 직접(접두어 없이). */
  sso_path: string
  allowed_ips: string[]
  /** 못 읽은 허용 주소 — 하나라도 있으면 모든 발급을 거절한다. */
  invalid_ips: string[]
  scopes: string[]
  token_seconds: number
  live_tokens: number
  last_issued_at: string | null
  last_refusal: GatewayRefusal | null
}

/** 서버 화면이 보는 포털 로그인 상태(`GET /api/auth/portal-status`, 시스템 관리자). */
export interface PortalStatus {
  enabled: boolean
  jwks_url: string | null
  /** 토큰의 `aud` 로 받는 값 — 포털 타일의 `audience` 와 같아야 한다. */
  audience: string
  issuer: string
  launch_url: string | null
  /** 포털 타일의 `url` 에 적을 길(이 설치의 접두어가 붙은 모양). */
  callback_path: string
  /** 공개 키를 지금 받았나. 꺼져 있으면 null. */
  reachable: boolean | null
  kids: string[]
  /** **이 프로세스가** 마지막으로 받은 때 — 워커마다 따로 든다. */
  fetched_at: string | null
  last_error: string | null
  last_error_at: string | null
  /** 설정이 서로 안 맞는 것 — 접두어 없음 · TRUST_PROXY 꺼짐 · 위임 비밀이 짧음 등. */
  problems: string[]
  delegation: GatewayDelegation
}

export const portalApi = {
  status: () => api.get<PortalStatus>('/auth/portal-status'),
}
