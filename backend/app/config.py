"""설정 — DB행 -> 환경변수 -> 기본값 3단 fallback.

세 번째 단(DB행)은 아직 비어 있지만 자리를 지금 만들어 둔다. 자주 바뀌는 값
(워커 수·타임아웃·임계값)을 나중에 관리 화면에서 고치려면 **읽는 지점이 한 곳**
이어야 하기 때문이다. os.getenv 가 코드에 흩어지면 값 하나를 바꾸는 데 재배포가
필요해진다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Protocol

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.branding import APP_SLUG

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    # utf-8-sig 로 읽는다. BOM 이 붙은 .env 는 **첫 줄 키만 조용히 무시된다** —
    # 그 키가 기본값으로 떨어지므로, 운영이 development 로 떠서 reload 가 켜진 채
    # 돈다. 서버에서 메모장으로 .env 를 고치기만 해도 같은 일이 나므로 읽는 쪽에서
    # 흡수한다.
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8-sig", extra="ignore"
    )

    app_env: str = "development"
    """development | production. 기동 방식과 로그 수준을 가른다."""

    database_url: str = f"postgresql+psycopg://postgres:postgres@localhost:5432/{APP_SLUG}"
    """**DB 이름은 `branding.APP_SLUG` 에서 나온다.** 포크해서 그 한 줄을 바꾸면
    여기가 따라온다 — 따로 적으면 언젠가 안 바뀌고, 그러면 두 플랫폼이 같은 DB 를
    보게 된다. 공통 틀에서 나온 표는 이름이 같아서(users·workspaces) **오류 없이
    남의 계정 표를 읽는다.**

    접속 정보(사용자·비밀번호·호스트)는 설치마다 다르므로 `.env` 가 이 값을
    통째로 덮는다. 여기 있는 것은 `.env` 가 없을 때의 기본값이다.

    시험은 이 이름에서 `_test` 를 파생해 쓴다(tests/conftest.py) — 접속 정보를
    두 곳에 적으면 한쪽만 고쳐지고, 그때 시험이 어느 DB 에서 도는지 모른다."""

    host: str = "0.0.0.0"
    port: int = 8030
    """**플랫폼마다 10씩 벌린다** — MatNexus 8010, TestScope 8020, 이 틀이 8030.

    **Apptainer 는 호스트 네트워크를 그대로 쓴다**(포트 매핑이 없다). 컨테이너가
    이 포트를 열면 그것이 곧 호스트의 포트다 — 한 서버에 여러 플랫폼을 얹을 때
    겹치면 나중에 뜬 쪽이 그냥 못 뜨고, 그 이유는 journal 에만 남는다.

    0.0.0.0 은 IPv4 전역 바인딩이라 localhost(::1) 로는 닿지 않는다 — 확인은
    127.0.0.1 로 한다. 개발 백엔드는 8031 을 쓴다(run.py)."""

    uvicorn_workers: int = 4
    """운영에서 띄울 워커 수.

    **개발(reload)에서는 무시된다** — uvicorn 은 reload 와 다중 워커를 같이 못 쓴다.
    둘을 함께 주면 한쪽이 조용히 버려지므로 run.py 가 갈라서 준다."""

    log_dir: Path = BACKEND_DIR / "logs"
    """개발에서는 저장소 안. **운영에서는 `.env` 가 `/data/logs` 로 덮는다** —
    컨테이너 루트는 읽기 전용이라 이미지 안에는 못 쓴다."""
    log_retention_days: int = 30

    filestore_dir: Path = BACKEND_DIR / "filestore"
    """첨부가 사는 곳. DB 에는 경로와 해시만 둔다.

    **운영에서는 반드시 bind-mount 된 경로여야 한다**(`/data/filestore`). SIF 루트는
    읽기 전용이라, 여기가 이미지 안을 가리키면 **첫 업로드에서** `[Errno 30]
    Read-only file system` 이 난다 — 그리고 그 오류는 파일을 올리는 사람에게만
    보인다. ReportArchive 가 임베드 번들 경로에서 정확히 그것을 겪었다."""

    backup_dir: Path | None = None
    """백업 스크립트(`deploy/backup.sh`)가 덤프를 남기는 폴더.

    **서버가 자기 백업을 볼 수 있어야 한다.** 백업은 작업 스케줄러가 돌리는데,
    아무도 안 보면 조용히 죽고 그 사실은 복구가 필요한 날에야 드러난다 — 그날은
    이미 늦다. 여기를 적어 두면 홈의 「남은 일」 이 오래된 백업을 말한다."""

    frontend_dist: Path = REPO_DIR / "frontend" / "dist"
    """존재하면 API 와 같은 프로세스가 SPA 를 서빙한다. 개발 중에는 없다."""

    jwt_secret: str = "dev-only-insecure-secret-change-me"
    """운영에서는 설치 스크립트가 난수로 만들어 .env 에 넣는다.

    기본값이 운영에 새어 나가면 아무나 토큰을 위조할 수 있으므로,
    app_env=production 이면서 이 값이 그대로면 **기동을 거부한다**(main.py)."""

    access_token_minutes: int = 720  # 12시간
    refresh_token_days: int = 30
    refresh_cookie_name: str = f"{APP_SLUG}_refresh"
    """**이름도 `branding.APP_SLUG` 에서 나온다.** 쿠키는 포트를 구분하지 않아서,
    같은 서버에 두 플랫폼을 띄웠을 때 이름이 같으면 한쪽 로그인이 다른 쪽 세션을
    덮어쓴다 — 번갈아 로그아웃되는 상태가 되고, 그 원인은 코드 어디에도 없다."""
    refresh_cookie_secure: bool = False
    """사내망 http 배포가 기본이라 False. https 로 서비스하면 True 로 올린다.
    (True 인데 http 로 접속하면 브라우저가 쿠키를 버려 로그인이 유지되지 않는다)"""

    login_delay_after: int = 5
    """같은 계정의 로그인 실패가 이 횟수부터 응답을 늦춘다. **잠그지 않는다** —
    관리자 복구가 서버 콘솔뿐인 시스템에서 잠금은 자해다."""
    login_delay_step_seconds: int = 2
    login_delay_max_seconds: int = 30
    login_failure_window_minutes: int = 15
    """이 시간 안의 실패만 센다. 지나면 처음부터."""

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5210", "http://127.0.0.1:5210"]
    )
    """개발 서버(Vite)용. 배포에서는 동일 출처라 필요 없다."""


class RuntimeSettingProvider(Protocol):
    """DB 기반 런타임 설정의 자리. runtime_settings 표가 생기면 여기에 끼운다."""

    def get(self, key: str) -> str | None: ...


class _NullProvider:
    def get(self, key: str) -> str | None:
        return None


_provider: RuntimeSettingProvider = _NullProvider()


def set_runtime_provider(provider: RuntimeSettingProvider) -> None:
    global _provider
    _provider = provider


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_setting(key: str) -> str | None:
    """3단 fallback 으로 값 하나를 읽는다. DB 행이 있으면 환경변수를 이긴다."""
    from_db = _provider.get(key)
    if from_db is not None:
        return from_db
    value = getattr(get_settings(), key, None)
    return None if value is None else str(value)
