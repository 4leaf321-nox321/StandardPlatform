"""코어 API — **바깥 시스템이 당겨 가는 창구.**

주소를 `/api/core` 로 따로 낸 이유: 여기가 **약속의 경계**다. `/api/objects/...` 는 이
플랫폼의 화면이 쓰는 길이라 사정에 따라 바뀌지만, 이 아래는 남의 시스템 코드에 박히므로
함부로 못 바꾼다. 경계를 주소로 그어 두면 그 차이가 코드에서도 보인다.

    GET /api/core             무엇이 열려 있나 — 처음 붙는 쪽은 이것 하나만 읽는다
    GET /api/core/{type}      지난번 이후 바뀐 것(+ 사라진 것)

읽기다. 좁은 범위(`core:read`)로도 열리므로, 바깥에 주는 토큰에 사내 전부를 읽는 `read` 를
줄 필요가 없다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.coreapi import services
from app.modules.coreapi.schemas import CoreCatalogOut, CorePageOut
from app.shared.auth import current_user
from app.shared.errors import AppError, code

router = APIRouter(prefix="/core", tags=["core"])

#: 이 아래는 이 범위로도 읽는다. `main.py` 가 등록한다.
SCOPE = "core:read"


@router.get("", response_model=CoreCatalogOut)
def core_catalog(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CoreCatalogOut:
    """**무엇이 열려 있나** — 코어 타입 목록과 각 타입의 칸.

    받는 쪽은 이것을 읽고 필요한 칸만 집어 간다. 타입 slug 나 칸 이름을 문서에서 옮겨
    적게 하면 우리가 칸을 하나 더하는 날 그 문서가 틀린 것이 된다.

    `revision` 은 **열린 정의의 판**이다. 칸이 바뀌면 값이 바뀌므로, 받는 쪽이 「구조가
    달라졌네」 를 코드로 알아챌 수 있다.
    """
    return services.catalog(db, user, base=str(request.url).split("?")[0].rstrip("/"))


@router.get("/{type_slug}", response_model=CorePageOut)
def core_rows(
    type_slug: str,
    since: str | None = Query(
        default=None,
        description="지난 응답의 `as_of` 를 그대로. 비우면 처음부터(지워진 것은 빼고)",
    ),
    cursor: str | None = Query(default=None, description="지난 응답의 `next`"),
    limit: int = Query(default=services.DEFAULT_LIMIT, ge=1, le=services.MAX_LIMIT),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CorePageOut:
    """지난번 이후 **바뀐 것과 사라진 것.**

    받는 쪽의 반복문은 이렇다: `since` 없이 시작 → `next` 가 빌 때까지 이어 받기 →
    응답의 `as_of` 를 적어 두었다가 다음 주기에 `since` 로 넣기.

    **시각은 우리가 준다.** 받는 쪽 시계를 쓰면 몇 초 차이로 그 사이 행이 새고, 샌 줄은
    아무도 모른다. 쪽이 남아 있으면(`next` 가 있으면) `as_of` 를 옮기지 않는다 — 거기서
    멈춘 쪽이 남은 쪽을 영영 안 받게 되기 때문이다.

    `deleted: true` 인 행은 이 시스템에서 사라진 것이다. 합쳐져서 사라졌으면
    `merged_into` 에 이긴 쪽의 `key` 가 온다 — 받는 쪽이 제 참조를 옮길 수 있다.
    """
    object_type = services.find_core_type(db, type_slug)
    moment: datetime | None = None
    if since:
        try:
            moment = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise AppError(
                code("CORE", 3),
                f"`since` 는 지난 응답의 `as_of` 를 그대로 넣습니다: {since!r}",
                status=422,
            ) from None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
    return services.page(db, user, object_type, since=moment, cursor=cursor, limit=limit)
