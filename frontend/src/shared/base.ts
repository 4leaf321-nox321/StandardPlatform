/**
 * 주소 접두어 — 여러 플랫폼이 한 호스트명에 경로로 붙을 때(`/plm`). 서버가 `index.html` 에
 * `<meta name="app-base">` 로 심어 준다(backend/app/main.py). 개발 서버와 루트 배포에서는 빈 값.
 *
 * **빌드에 굽지 않는다.** 같은 이미지가 `/` 에서도 `/plm/` 에서도 떠야 한다 — 접두어는 배포
 * 설정이지 빌드가 아니다. 자산 주소는 `<base href>` 가, API · 라우터 주소는 이 값이 푼다.
 */
export const PUBLIC_PATH: string =
  typeof document === 'undefined'
    ? ''
    : (document.querySelector('meta[name="app-base"]')?.getAttribute('content') ?? '')

/** 라우터의 basename — 접두어가 없으면 `/`. */
export const ROUTER_BASENAME = PUBLIC_PATH || '/'
