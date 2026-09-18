"""FastAPI 앱 — API 와 SPA 를 한 프로세스가 서빙한다.

배포 산출물이 하나면 롤백도 하나다. 프론트를 따로 띄우는 구성은 개발에서는 편하지만
출하 형태가 성립하지 않는다 — "어느 쪽이 옛 버전인가" 를 물을 자리가 생긴다.

**조립은 여기 하나다.** 라우터도, 확장 지점 등록도, PAT 범위도 전부 이 파일을
거친다. 모듈이 서로를 import 하지 않게 하려면 조립 지점이 하나여야 하고, 그래야
"이게 왜 안 뜨지" 를 물을 자리가 생긴다.
"""

from __future__ import annotations

import logging
import re
from html import escape as html_escape

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from app import extensions as ext_loader
from app import version
from app.config import Settings, get_settings
from app.database import SessionLocal, engine
from app.logging_setup import setup_logging
from app.modules.accounts import routes as accounts_routes
from app.modules.accounts import services as accounts_services
from app.modules.audit import routes as audit_routes
from app.modules.auth import routes as auth_routes
from app.modules.bundles import routes as bundles_routes
from app.modules.datasources import routes as datasources_routes
from app.modules.datasources import services as datasources_services
from app.modules.files import routes as files_routes
from app.modules.files import services as files_services
from app.modules.graph import routes as graph_routes
from app.modules.notices import routes as notices_routes
from app.modules.notifications import routes as notifications_routes
from app.modules.objects import quality as objects_quality
from app.modules.objects import routes as objects_routes
from app.modules.objects import services as objects_services
from app.modules.objects import watches as objects_watches
from app.modules.ontology import routes as ontology_routes
from app.modules.rdf import routes as rdf_routes
from app.modules.search import routes as search_routes
from app.modules.server import routes as server_routes
from app.modules.webhooks import routes as webhooks_routes
from app.modules.webhooks import services as webhooks_services
from app.modules.workspaces import routes as workspaces_routes
from app.modules.workspaces import services as workspaces_services
from app.schema_version import warn_if_behind
from app.shared import events, extensions, ops, scopes, system_sources
from app.shared.access_log import AccessLogMiddleware
from app.shared.errors import NotFound, code, register_error_handlers
from app.shared.request_context import RequestIdMiddleware

logger = logging.getLogger(__name__)

API_PREFIX = "/api"


def _api_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix=API_PREFIX)

    @router.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        # **버전을 함께 준다.** 원격에서 "지금 서버에 뭐가 깔렸나" 를 물을 수 있는
        # 유일한 자리다. 배포 뒤 확인도, 나중의 점검 스크립트도 여기를 본다.
        return {
            "status": "ok",
            "version": version.current(),
            "app": settings.app_name,
            "slug": settings.app_slug,
        }

    # 모듈 라우터는 **여기서만** 모은다.
    router.include_router(auth_routes.router)
    router.include_router(accounts_routes.router)
    router.include_router(workspaces_routes.router)
    router.include_router(notices_routes.router)
    router.include_router(webhooks_routes.router)
    router.include_router(datasources_routes.router)
    router.include_router(notifications_routes.router)
    router.include_router(files_routes.router)
    router.include_router(audit_routes.router)
    # 메타모델과 그 객체. **도메인이 아니라 메커니즘이다**(ADR 0005) —
    # 도메인은 여전히 이 저장소에 없고, 여기 정의로 얹힌다.
    router.include_router(ontology_routes.router)
    router.include_router(objects_routes.router)
    # 정의 · 객체 · 관계를 한 묶음으로 — 로컬 정제 도구(pipeline/)가 부른다.
    router.include_router(bundles_routes.router)
    router.include_router(graph_routes.router)
    # 정의 · 데이터를 RDF/OWL 로 — 추론은 바깥(또는 owlrl)이 한다. 플랫폼이 정본, RDF 는 사본.
    router.include_router(rdf_routes.router)
    router.include_router(search_routes.router)
    router.include_router(server_routes.router)

    # --- 이 설치가 켠 확장의 라우터 --------------------------------------
    # 코어는 확장을 import 하지 않는다. `.env` 의 EXTENSIONS 에 적힌 이름만 찾아 붙인다.
    for name in settings.extension_names:
        ext_loader.load(name).register(router)

    # --- 여기에 도메인 라우터를 더한다 -----------------------------------
    #
    #   from app.modules.<name> import routes as <name>_routes
    #   router.include_router(<name>_routes.router)

    return router


def _register_extensions() -> None:
    """공통 화면에 무엇이 뜨는지 — **등록하지 않으면 안 뜬다.**

    홈의 「남은 일」, 서버 화면의 「쌓인 것」, 부서 삭제 확인의 참조 목록이
    여기서 정해진다. 도메인이 자기 표를 안 걸면 **부서를 지울 때 그 표는 아무 데도
    안 나타나고**, 사람은 아무것도 안 걸린 줄 안다.
    """
    extensions.register_maintenance(accounts_services.maintenance)
    extensions.register_stats(accounts_services.stats)
    extensions.register_stats(files_services.stats)
    # **백업이 오래되면 홈이 말한다.** 스케줄러가 죽어도 아무도 모르는 것이
    # 백업의 기본 실패 방식이고, 그 사실은 복구가 필요한 날에야 드러난다.
    extensions.register_maintenance(ops.maintenance)
    # **부서를 지울 때 첨부가 목록에 뜬다.** 안 걸면 사람은 아무것도 안 걸린 줄
    # 알고 지우려 하는데, FK 가 RESTRICT 라 서버가 500 을 낸다.
    extensions.register_workspace_reference(files_services.workspace_reference)
    # **부서를 지울 때 그 부서 소유의 객체가 목록에 뜬다.** 안 걸면 사람은
    # 아무것도 안 걸린 줄 알고 지우려 하는데, FK 가 RESTRICT 라 500 이 난다.
    extensions.register_workspace_reference(objects_services.workspace_reference)
    # **부서를 통폐합할 때 자료를 넘기는 길.** 안 걸면 그 표는 옮기기 화면에 안 뜨고,
    # 사람은 없어지는 부서를 비울 방법이 없어 삭제도 보관도 못 한다.
    extensions.register_workspace_content(objects_services.workspace_content)
    extensions.register_workspace_content(files_services.workspace_content)
    extensions.register_workspace_content(datasources_services.workspace_content)
    # 데이터 품질 — 필수값 빈 것·고아·깨진 참조·이름 같은 것을 홈 「남은 일」 에.
    extensions.register_maintenance(objects_quality.maintenance)
    extensions.register_stats(objects_quality.stats)

    # `system` 타입이 비추는 원 표. **등록하지 않으면 그 타입은 만들 수 없다** —
    # 스키마의 `system_sources` 가 여기서 나온다. 승격한 전용 표도 여기 더한다.
    system_sources.register_system_source(workspaces_services.SYSTEM_SOURCE)
    system_sources.register_system_source(accounts_services.SYSTEM_SOURCE)

    # 변경 이벤트(감사 기록이 커밋된 뒤)를 웹훅이 듣는다.
    events.register_listener(webhooks_services.on_events)
    # **지켜보는 사람에게도 같은 줄기로 간다.** 따로 심으면 두 벌이 되고, 두 벌은
    # 반드시 갈린다 — 감사에는 남는데 알림은 안 가는 변경이 생긴다.
    events.register_listener(objects_watches.on_events)
    extensions.register_stats(webhooks_services.stats)

    # **조용히 멎는 것들을 홈이 말한다.** 동기화 실패와 포기한 웹훅 전송은 지금까지
    # 각자의 표에만 적혔고, 그 화면을 여는 사람만 알았다 — 그리고 잘 도는 동안에는
    # 아무도 그 화면을 안 연다. 그것이 정기 작업의 기본 실패 방식이고, 백업을 같은
    # 이유로 이미 여기 걸어 두었다(ops.maintenance).
    extensions.register_maintenance(datasources_services.maintenance)
    extensions.register_stats(datasources_services.stats)
    extensions.register_maintenance(webhooks_services.maintenance)

    # **기계 자격으로 온톨로지를 채우는 길**(3-d). 안 열면 PAT 로는 못 고친다 —
    # 기본이 「막힘」 이고, 그것이 맞는 기본값이다(shared/scopes.py).
    #
    # 정의와 데이터를 **가른다.** 정의를 바꾸는 것은 화면의 모양을 바꾸는 일이라
    # 데이터를 넣는 것과 무게가 다르다 — 한 범위로 묶으면 「객체만 넣게」 하려던
    # 토큰이 타입까지 지울 수 있다.
    scopes.register_write_scope("/api/ontology", "ontology:write")
    scopes.register_write_scope("/api/objects", "objects:write")
    # 묶음은 객체를 쓴다. 정의가 들면 라우트가 ontology:write 를 더 묻는다.
    scopes.register_write_scope("/api/bundles", "objects:write")
    # 동기화는 객체를 넣는 일이다 — 같은 범위. 소스 정의 자체는 시스템 관리자만.
    scopes.register_write_scope("/api/datasources", "objects:write")
    # `import` 는 POST 지만 `dry_run` 이면 아무것도 안 바꾼다. 그래도 **읽기로
    # 열지 않는다** — 같은 경로가 적용도 하기 때문이다. 읽기 토큰은 `schema` 로
    # 본다.

    # --- 여기에 도메인 확장을 더한다 ---------------------------------------
    #
    #   extensions.register_stats(equipment_services.stats)
    #   extensions.register_maintenance(equipment_services.maintenance)
    #   extensions.register_workspace_reference(equipment_services.workspace_reference)
    #   extensions.register_workspace_content(equipment_services.workspace_content)
    #
    # PAT 범위도 같은 자리에서 연다. **안 열면 기계 자격으로 못 고친다** —
    # 그것이 맞는 기본값이다(shared/scopes.py).
    #
    #   scopes.register_write_scope("/api/equipment", "equipment:write")
    #   scopes.register_read_only_post("/api/search/")


def _mount_spa(app: FastAPI, settings: Settings) -> None:
    dist = settings.frontend_dist
    index = dist / "index.html"
    if not index.exists():
        logger.info("frontend dist 없음 (%s) — API만 서빙합니다.", dist)
        return

    # 해시가 붙은 자산은 오래 캐시해도 안전하다.
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    # **접두어를 화면에 심는다.** 빌드는 접두어를 모르고(`base: "./"`), 브라우저는
    # `<base href>` 로 자산 주소를, `<meta name="app-base">` 로 API · 라우터 주소를 푼다.
    # 그래서 같은 이미지가 `/` 에서도 `/plm/` 에서도 뜬다 — 접두어는 배포 설정(.env)이지
    # 빌드가 아니다.
    # **이름도 심는다.** 번들 하나로 여러 플랫폼을 띄우므로 화면은 자기가 무슨 플랫폼인지
    # 빌드로는 모른다 — `<meta name="app-name">` 들이 말해 주고, 없을 때(개발 서버)만
    # 화면의 기본값이 쓰인다. `<title>` 도 여기서 바꾼다.
    base = settings.base_path
    metas = "\n    ".join(
        f'<meta name="{key}" content="{html_escape(value)}" />'
        for key, value in (
            ("app-base", base),
            ("app-name", settings.app_name),
            ("app-slug", settings.app_slug),
            ("app-tagline", settings.app_tagline),
            ("app-extensions", ",".join(settings.extension_names)),
        )
    )
    html = index.read_text(encoding="utf-8").replace(
        "<head>", f'<head>\n    <base href="{base}/" />\n    {metas}', 1
    )
    title = f"<title>{html_escape(settings.app_name)}</title>"
    html = re.sub(r"<title>.*?</title>", title, html, count=1)

    # response_model=None — 이 경로는 스키마에 나오지 않으므로 응답 모델이 필요
    # 없다. 반환 애노테이션에 Union 을 쓰면 FastAPI 가 모델을 만들려다 기동에
    # 실패하므로, 여기는 앞으로도 단일 Response 타입으로 둔다.
    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    def spa(full_path: str) -> HTMLResponse:
        # /api 아래는 위에서 이미 매칭됐어야 한다. 여기 닿았다면 없는 엔드포인트다.
        # index.html 을 돌려주면 프론트가 200 HTML 을 JSON 으로 파싱하려다 실패해
        # 원인이 흐려지므로, 명시적으로 404 를 준다. 응답 본문은 직접 만들지 않고
        # 오류 핸들러에 맡긴다 — 그래야 request_id 와 로그가 함께 남는다.
        if full_path.startswith("api/"):
            raise NotFound(
                code("COMMON", 404),
                "존재하지 않는 엔드포인트입니다.",
                details={"path": f"/{full_path}"},
            )
        # index.html 은 캐시하지 않는다. 배포 후 사용자가 옛 index 를 들고 있으면
        # 사라진 청크를 요청하게 된다.
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    logger.info("SPA 서빙: %s", dist)


def _guard_production_secrets(settings: Settings) -> None:
    """운영에서 기본 비밀키로 뜨는 것을 막는다.

    기본값이 그대로 배포되면 누구나 access 토큰을 위조할 수 있다. **경고 로그는
    아무도 읽지 않으므로 기동 자체를 거부한다.**
    """
    if settings.app_env != "production":
        return
    if settings.jwt_secret == Settings.model_fields["jwt_secret"].default:
        raise RuntimeError(
            "JWT_SECRET 이 기본값입니다. .env 에 난수 값을 넣고 다시 시작하세요."
        )


def _guard_writable_paths(settings: Settings) -> None:
    """쓸 수 있어야 하는 곳에 쓸 수 있나 — **로그를 열기 전에 본다.**

    컨테이너 루트는 읽기 전용이다. 이 경로들이 bind-mount 밖을 가리키면 앱은
    `[Errno 30] Read-only file system` 으로 죽는데, **그 메시지는 무엇을 고쳐야
    하는지 말해 주지 않는다** — 실측으로 겪었다: `.env` 에 `LOG_DIR` 이 없어서
    기본값인 이미지 안 경로로 떨어졌고, 트레이스백은 `pathlib.mkdir` 을 가리켰다.

    로그 설정보다 **먼저** 부른다. 로그를 여는 것 자체가 첫 번째 쓰기라서, 그
    뒤에 두면 이 검사가 영영 안 돈다.
    """
    targets = (
        ("LOG_DIR", settings.log_dir),
        ("FILESTORE_DIR", settings.filestore_dir),
    )
    for label, path in targets:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-probe"
            probe.touch()
            probe.unlink()
        except OSError as failure:
            raise RuntimeError(
                f"{label} 에 쓸 수 없습니다: {path} ({failure.strerror})\n"
                f"\n"
                f"컨테이너로 돌고 있다면 그 경로가 bind-mount 밖입니다. "
                f"systemd 유닛의 --bind 대상과 이 값이 같아야 합니다 "
                f"(이미지는 /data/logs · /data/filestore 를 기본으로 둡니다).\n"
                f"호스트에서 돌고 있다면 그 폴더의 소유자와 권한을 확인하세요."
            ) from failure


class PrefixMiddleware:
    """접두어(`/<slug>`)가 **없이** 온 요청에 접두어를 붙여 준다.

    Starlette 는 `root_path` 가 경로에 **붙어 있다**고 본다 — 라우트 매칭은 붙어 있으면
    떼고 없으면 그대로 두지만, 정적 파일 마운트는 붙어 있어야만 찾는다(없으면
    `/assets/…` 가 404 — 실측). 그런데 앞의 nginx 는 접두어를 떼고 넘기는 쪽이 흔하고,
    직접 붙는 확인은 붙인 채로 온다. 어느 쪽이든 되게 여기서 모양을 하나로 맞춘다.
    """

    def __init__(self, app: ASGIApp, base_path: str) -> None:
        self.app = app
        self.base = base_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.base and scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path != self.base and not path.startswith(self.base + "/"):
                scope = dict(scope)
                scope["path"] = self.base + path
                if scope.get("raw_path"):
                    scope["raw_path"] = self.base.encode() + scope["raw_path"]
        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    settings = get_settings()
    # **로그보다 먼저다.** 로그를 여는 것이 첫 번째 쓰기다.
    _guard_writable_paths(settings)
    setup_logging(settings)
    _guard_production_secrets(settings)

    app = FastAPI(
        title=f"{settings.app_name} API",
        version=version.current(),
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        # 앞의 nginx 가 `/<slug>` 를 떼고 넘기므로 문서 · 리다이렉트가 만드는 주소에 접두어를
        # 다시 붙여야 한다 — root_path 가 그 자리다.
        root_path=settings.base_path,
    )

    # 순서가 중요하다. add_middleware 는 **나중에 더한 것이 바깥**이므로 아래 두
    # 줄은 RequestId(바깥) -> AccessLog(안쪽) 이 된다. 접근 로그가 요청 id 를
    # 읽으려면 그 id 가 먼저 설정돼 있어야 한다.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)
    # 맨 바깥 — 접근 로그도 접두어가 붙은 한 가지 모양으로 본다.
    app.add_middleware(PrefixMiddleware, base_path=settings.base_path)

    # 요청 처리 밖에서 DB 를 쓰는 곳(접근 로그)이 참조한다. 테스트는 이 값을 자기
    # DB 로 바꿔 끼운다.
    app.state.session_factory = SessionLocal
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_error_handlers(app)
    _register_extensions()
    app.include_router(_api_router(settings))

    # SPA catch-all 은 반드시 API 라우터 뒤에 등록한다.
    _mount_spa(app, settings)

    # **DB 가 코드보다 뒤처져 있으면 여기서 말한다.** 안 그러면 사람은 화면의 500
    # 으로 먼저 만나는데, 거기엔 원인이 안 적힌다.
    warn_if_behind(engine)

    logger.info(
        "%s (%s) 기동 (env=%s, 확장=%s)",
        settings.app_name,
        settings.app_slug,
        settings.app_env,
        ",".join(settings.extension_names) or "없음",
    )
    return app


app = create_app()
