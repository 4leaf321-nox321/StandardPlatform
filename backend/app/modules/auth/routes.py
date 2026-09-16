"""인증 라우터.

**refresh 토큰은 httpOnly 쿠키로만 오간다.** 자바스크립트가 읽을 수 없으므로 XSS 로
새지 않고, 배포가 동일 출처(백엔드 한 프로세스가 SPA 까지 서빙)라 별도 설정도
필요 없다. access 토큰은 응답 본문으로만 주고 프론트는 메모리에 둔다 —
localStorage 에 두면 XSS 한 번에 탈취된다.

쿠키 path 를 /api/auth 로 제한해 일반 API 호출에는 실려 나가지 않는다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.modules.accounts.models import User
from app.modules.auth import services
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    PatCreateRequest,
    PatCreateResponse,
    PatOut,
    ProfileUpdateRequest,
    TokenScopesOut,
    UserOut,
)
from app.shared import scopes
from app.shared.auth import current_user
from app.shared.errors import AppError, code

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookie_path() -> str:
    """리프레시 쿠키의 path — 브라우저가 보는 주소 기준이라 접두어를 붙인다. 접두어 없이
    `/api/auth` 로 두면 `/plm/api/auth/refresh` 요청에 쿠키가 안 실려 로그인이 안 이어진다."""
    return f"{get_settings().base_path}/api/auth"


def _set_refresh_cookie(response: Response, raw: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.refresh_cookie_name,
        raw,
        max_age=settings.refresh_token_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.refresh_cookie_secure,
        path=_cookie_path(),
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(get_settings().refresh_cookie_name, path=_cookie_path())


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    user = services.authenticate(db, str(payload.email), payload.password)
    access, expires_in, refresh_raw = services.issue_session(
        db, user, request.headers.get("user-agent")
    )
    _set_refresh_cookie(response, refresh_raw)
    return LoginResponse(
        access_token=access, expires_in=expires_in, user=services.user_out(db, user)
    )


@router.post("/refresh", response_model=LoginResponse)
def refresh(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> LoginResponse:
    raw = request.cookies.get(get_settings().refresh_cookie_name)
    if not raw:
        raise AppError(code("AUTH", 3), "세션이 없습니다. 로그인해 주세요.", status=401)

    user, access, expires_in, new_raw = services.rotate_refresh(
        db, raw, request.headers.get("user-agent")
    )
    _set_refresh_cookie(response, new_raw)
    return LoginResponse(
        access_token=access, expires_in=expires_in, user=services.user_out(db, user)
    )


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    raw = request.cookies.get(get_settings().refresh_cookie_name)
    if raw:
        services.revoke_refresh(db, raw)
    _clear_refresh_cookie(response)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserOut:
    return services.user_out(db, user)


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: ProfileUpdateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> UserOut:
    """자기 표시 이름을 바꾼다.

    아이디는 여기서 못 바꾼다 — 로그인 식별자라 본인이 바꾸면 기록이 가리키는
    대상이 흔들린다. 그것은 관리자의 일이다.
    """
    user.display_name = payload.display_name.strip()
    db.commit()
    db.refresh(user)
    return services.user_out(db, user)


@router.post("/change-password", status_code=204)
def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    services.change_password(db, user, payload.current_password, payload.new_password)
    # 모든 세션을 끊었으므로 이 브라우저의 쿠키도 함께 버린다.
    _clear_refresh_cookie(response)


# --- PAT — 연계 스크립트용 자격 증명 ------------------------------------------


@router.get("/token-scopes", response_model=TokenScopesOut)
def token_scopes(_: User = Depends(current_user)) -> TokenScopesOut:
    """이 설치가 아는 범위.

    **화면이 목록을 손으로 들지 않는다.** 도메인이 범위를 더하면 발급 화면은
    아무것도 안 고쳐도 새 범위를 보여 준다 — 손 목록은 반드시 서버와 어긋나고,
    어긋난 날 사람은 있는 범위를 못 고른다.
    """
    return TokenScopesOut(scopes=list(scopes.known_scopes()))


@router.post("/tokens", response_model=PatCreateResponse, status_code=201)
def create_token(
    payload: PatCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PatCreateResponse:
    raw, pat = services.create_pat(
        db, user, payload.name, payload.expires_in_days, payload.scopes
    )
    return PatCreateResponse(token=raw, pat=pat)


@router.get("/tokens", response_model=list[PatOut])
def list_tokens(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[PatOut]:
    return services.list_pats(db, user)


@router.delete("/tokens/{pat_id}", status_code=204)
def revoke_token(
    pat_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    services.revoke_pat(db, user, pat_id)
