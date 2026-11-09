"""API 문서 화면(`/api/docs`) — **폐쇄망에서도 뜨게, 화면 파일을 서버가 직접 준다.**

FastAPI 의 기본 문서 화면은 스크립트 · 스타일을 `cdn.jsdelivr.net` 에서, 아이콘을
`fastapi.tiangolo.com` 에서 받는다. 폐쇄망에서는 그것을 못 받아 흰 화면만 남는다 — 그런데
연동 지침서는 다른 시스템 개발자에게 바로 이 화면을 열라고 안내한다. 여러 시스템을 잇는 일의
입구가 막혀 있던 것이다.

그래서 `app/static/swagger-ui/` 에 받아 둔 Swagger UI(판 · 지문 · 바꾸는 법은 그 폴더의
README)를 문서 주소 아래에서 주고, 화면이 그것을 가리키게 한다. 바깥을 부를 수 있는 남은 한
자리 — 명세를 `validator.swagger.io` 로 보내는 검증 배지 — 도 끈다(지금 배치에서는 안 뜨지만,
배치를 바꾸는 날 조용히 바깥을 부르지 않게).

ReDoc(`/redoc`)은 두지 않는다. 그것도 CDN 에서 받고, 주소가 `/api` 밖이라 앞의 nginx 뒤에서는
닿지도 않았다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

#: 받아 둔 Swagger UI. 이미지(SIF)에는 `backend` 가 통째로 들어가므로 번들에도 함께 간다.
SWAGGER_DIR = Path(__file__).parent / "static" / "swagger-ui"


def mount(app: FastAPI, docs_path: str) -> None:
    """`docs_path` 에 문서 화면을, `<docs_path>/assets` 에 그 파일을 붙인다.

    **화면의 「모든 주소 받기」(SPA) 보다 먼저 불러야 한다** — 뒤에 붙이면 그쪽이 먼저 받는다.
    """
    assets = f"{docs_path}/assets"
    app.mount(assets, StaticFiles(directory=SWAGGER_DIR), name="api-docs-assets")

    @app.get(docs_path, include_in_schema=False)
    def api_docs(request: Request) -> HTMLResponse:
        # 접두어(`/<slug>`) 아래 설치면 주소마다 그것을 붙인다 — FastAPI 의 기본 문서 화면이
        # 하던 것과 같다(`root_path` 는 설정의 접두어다).
        root = str(request.scope.get("root_path", "")).rstrip("/")
        return get_swagger_ui_html(
            openapi_url=root + (app.openapi_url or ""),
            title=f"{app.title} — 문서",
            swagger_js_url=f"{root}{assets}/swagger-ui-bundle.js",
            swagger_css_url=f"{root}{assets}/swagger-ui.css",
            swagger_favicon_url=f"{root}{assets}/favicon-32x32.png",
            swagger_ui_parameters={"validatorUrl": None},
        )
