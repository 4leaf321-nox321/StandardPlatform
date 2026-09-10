/**
 * 이 화면이 무슨 플랫폼인가 — **이름을 한 곳에서만 정한다.**
 *
 * 이 저장소는 여러 플랫폼의 공통 틀이다. 포크해서 도메인을 얹는 순간 제품 이름이
 * 바뀌는데, 그 이름이 화면 곳곳에 흩어져 있으면 **한 벌만 바뀐다** — 로그인
 * 화면은 새 이름인데 사이드바는 옛 이름으로 남는다.
 *
 * 백엔드에도 짝이 있다(`backend/app/branding.py`). 두 벌인 이유는 화면이 이름을
 * 그리려고 서버를 한 번 더 물으면 첫 화면이 비었다 채워지기 때문이다 — 사람은
 * 그 깜빡임을 고장으로 읽는다. **서버 응답에도 이름이 실려 오므로**(`/api/health`)
 * 둘이 어긋나면 서버 화면에서 드러난다.
 */

/** 브라우저 탭·로그인 화면·사이드바 머리글. */
export const APP_NAME = 'StandardPlatform'

/**
 * 기계가 읽는 이름. **백엔드 `branding.py` 의 `APP_SLUG` 와 같은 값이어야 한다.**
 *
 * 저쪽에서는 이 값이 DB 이름·쿠키 이름·토큰 표식을 만든다. 이쪽에서는
 * localStorage 키만 만든다 — 같은 브라우저로 두 플랫폼을 열면 저장소는 보통
 * 출처로 갈리지만, 같은 서버의 다른 경로에 얹는 구성에서는 안 갈린다.
 */
export const APP_SLUG = 'standardplatform'

/** 한 줄 설명. 로그인 화면과 사이드바가 같은 말을 하도록 여기 한 번만 적는다. */
export const APP_TAGLINE = '사내 플랫폼 공통 틀'

/** localStorage 키의 앞머리. */
export const STORAGE_PREFIX = APP_SLUG

/** 클라이언트가 만드는 오류의 코드 앞머리. 서버의 ERROR_PREFIX 와 같아야 한다. */
export const ERROR_PREFIX = 'APP'
