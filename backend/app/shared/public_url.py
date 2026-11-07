"""사람이 브라우저로 여는 화면의 **바탕 주소** — 바깥에 줄 완전한 주소의 앞부분.

앱은 자기 바깥 주소를 모른다. 요청의 `Host` 는 프록시가 포트를 떼고 넘기고, HWAX 포털의
게이트웨이는 MCP 포트를 직접 부른다 — 그 요청의 주소로 만든 링크는 사람의 브라우저에서 안
열린다. 그래서 설정이 아는 것을 차례로 본다(`screen_base`). 바깥 AI 가 MCP 로 받은 답에 「같은
조건의 화면」 을 붙이는 데 쓴다(`/api/health` 의 `public_url`).

MCP 의 사진 업로드 주소(`files.services._public_upload_url`)도 같은 규칙의 한 갈래
(`mcp_app_origin`)를 쓴다 — 두 벌로 두면 한쪽만 고쳐진다.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.config import Settings, get_settings


def mcp_app_origin(settings: Settings) -> str | None:
    """`MCP_PUBLIC_URL` 이 `<앱 주소>/mcp` 꼴이면 그 앱 주소의 앞(`scheme://host[:port]`,
    접두어 뺌) — 표준이 아닌 포트의 프록시 뒤처럼 적어 둔 설치.

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
    if prefix and base.endswith(prefix):
        base = base[: -len(prefix)]
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
    try:
        launch = urlsplit(settings.portal_launch_url.strip())
    except ValueError:  # 깨진 주소 — 모르는 것으로 두고 다음 길로(위 `mcp_app_origin` 과 같다)
        launch = urlsplit("")
    if launch.scheme and launch.netloc:
        return f"{launch.scheme}://{launch.netloc}{settings.base_path}"
    origin = mcp_app_origin(settings)
    if origin:
        return f"{origin}{settings.base_path}"
    return None
