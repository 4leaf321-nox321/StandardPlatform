"""사람이 브라우저로 여는 화면의 **바탕 주소** — 바깥에 줄 완전한 주소의 앞부분.

앱은 자기 바깥 주소를 모른다. 요청의 `Host` 는 프록시가 포트를 떼고 넘기고, HWAX 포털의
게이트웨이는 MCP 포트를 직접 부른다 — 그 요청의 주소로 만든 링크는 사람의 브라우저에서 안
열린다. 그래서 설정이 아는 것을 차례로 본다(`screen_base`). 바깥 AI 가 MCP 로 받은 답에 「같은
조건의 화면」 을 붙이는 데 쓴다(`/api/health` 의 `public_url`).

MCP 의 사진 업로드 주소(`files.services._public_upload_url`)도 같은 규칙의 한 갈래
(`mcp_app_origin`)를 쓴다 — 두 벌로 두면 한쪽만 고쳐진다. 화면이 사람에게 안내할 MCP
주소(`mcp_url`)도 여기서 정한다.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.config import Settings, get_settings

#: 포털이 MCP 로 넘기는 길의 꼬리 — `/<id>-mcp/`. 포털 nginx 는 경로 목록(`routes.local.env`)의
#: 줄마다 접두어를 떼고 넘긴다: `<id>` 는 앱 포트로, `<id>-mcp` 는 MCP 포트로. 그래서 사람의
#: Claude 가 붙는 주소는 `<포털>/<id>-mcp/mcp` 다 — `<포털>/<id>/mcp` 는 앱으로 가 404 다.
PORTAL_MCP_TAIL = "-mcp"


def portal_origin(settings: Settings) -> str | None:
    """`PORTAL_LAUNCH_URL` 의 `scheme://호스트` — 포털 뒤가 아니거나 읽을 수 없으면 None."""
    try:
        launch = urlsplit(settings.portal_launch_url.strip())
    except ValueError:  # 깨진 주소 — 모르는 것으로 둔다(아래 `mcp_app_origin` 과 같다)
        return None
    if launch.scheme and launch.netloc:
        return f"{launch.scheme}://{launch.netloc}"
    return None


def mcp_url(settings: Settings | None = None) -> str:
    """화면(토큰 발급 · 「내 정보」 의 Claude 연결 안내)이 사람에게 보여 줄 **MCP 주소** —
    모르면 빈 값(화면이 들어온 주소로 짐작한다, `McpSetupGuide.mcpUrlFrom`).

    1. `MCP_PUBLIC_URL` — 사람이 적은 정본.
    2. 포털 뒤(`PORTAL_LAUNCH_URL` 과 접두어가 있다) — `<포털>/<id>-mcp/mcp`
       (`PORTAL_MCP_TAIL`). 화면의 짐작(`<들어온 주소><접두어>/mcp`)은 포털 뒤에서 앱으로
       가는 길이 된다.
    3. 빈 값.
    """
    settings = settings or get_settings()
    public = settings.mcp_public_url.strip()
    if public:
        return public
    portal = portal_origin(settings)
    if portal and settings.base_path:
        return f"{portal}{settings.base_path}{PORTAL_MCP_TAIL}/mcp"
    return ""


def mcp_app_origin(settings: Settings) -> str | None:
    """`MCP_PUBLIC_URL` 이 `<앱 주소>/mcp` 꼴이면 그 앱 주소의 앞(`scheme://host[:port]`,
    접두어 뺌) — 표준이 아닌 포트의 프록시 뒤처럼 적어 둔 설치. 포털의 MCP 길
    (`<포털>/<id>-mcp/mcp`)을 적었으면 포털의 앞이다(앱은 그 포털의 `/<id>/`).

    MCP 포트를 바로 적은 값(`http://<IP>:<앱+2>/mcp` — ReportArchive 가 그렇게 쓴다)이면
    `/mcp` 를 떼도 앱이 아니라 MCP 포트다. 그때는 None — 그 주소로 만든 링크는 안 열린다."""
    configured = settings.mcp_public_url.strip().rstrip("/")
    if not configured.endswith("/mcp"):
        return None
    try:
        port = urlsplit(configured).port
    except ValueError:
        # 포트 오타(`:80443`) · 대괄호가 깨진 호스트 — 읽을 수 없으면 모르는 것으로 둔다.
        # 예외를 올리면 이것을 부르는 생존 확인(`/api/health`)까지 500 이 된다(2026-11-07
        # 검토).
        return None
    if port == settings.port + 2:
        return None
    base = configured[: -len("/mcp")]
    prefix = settings.base_path
    if prefix:
        for tail in (f"{prefix}{PORTAL_MCP_TAIL}", prefix):
            if base.endswith(tail):
                base = base[: -len(tail)]
                break
    return base


def screen_base(settings: Settings | None = None) -> str | None:
    """화면의 바탕 주소(접두어 포함, 끝 `/` 없음) — 모르면 None. `settings` 를 안 주면 지금
    설정.

    고르는 차례:
    1. `PUBLIC_URL` — 사람이 적은 정본.
    2. `PORTAL_LAUNCH_URL` 의 `scheme://호스트` + 접두어 — 포털 뒤면 사람은 포털의
       `/<id>/` 로 들어온다(포털이 접두어를 벗겨 넘긴다, ADR 0028).
    3. `MCP_PUBLIC_URL` 이 `<앱 주소>/mcp` 꼴이면 그 앞 + 접두어(`mcp_app_origin`).
    4. None — 부른 쪽(MCP)이 들어온 주소로 짐작한다.
    """
    settings = settings or get_settings()
    public = settings.public_url.strip().rstrip("/")
    if public:
        return public
    portal = portal_origin(settings)
    if portal:
        return f"{portal}{settings.base_path}"
    origin = mcp_app_origin(settings)
    if origin:
        return f"{origin}{settings.base_path}"
    return None
