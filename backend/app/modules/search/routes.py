"""전역 찾기 라우터 — `/api/search`.

한 경로뿐이다. 타입을 가리지 않고 찾고, 타입으로 좁히는 것도 같은 경로가 한다 —
좁히는 순간 다른 API 로 넘어가면 화면이 두 모양을 다뤄야 한다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.board import services as board
from app.modules.objects import misses
from app.modules.search import services
from app.modules.search.schemas import SearchHitOut, SearchOut, SearchPostOut, SearchTypeOut
from app.shared.auth import current_user
from app.shared.pagination import clamp_limit

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=SearchOut)
def search(
    background: BackgroundTasks,
    q: str = Query(default="", description="이름·식별자·별칭의 일부"),
    type_slug: str | None = Query(default=None, alias="type"),
    limit: int | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SearchOut:
    """이름·식별자·별칭으로 타입을 가리지 않고 찾는다.

    **볼 수 있는 것만 나온다**(`visible_owner_clause`). 남의 부서 것이 수에만 잡혀도
    그것은 샌 것이다.
    """
    capped = clamp_limit(limit)
    found = services.search(db, user, q, type_slug=type_slug, limit=capped, offset=offset)
    if found.total == 0 and found.records == 0 and offset == 0:
        # **못 찾은 말은 별칭 후보로 남는다** — 응답 뒤에, 실패해도 이 답과 무관하게. 타입을
        # 안 골랐으면 범위는 「통합 검색」(빈 글자)이다.
        misses.note(background, user, type_slug or "", q, "search")
    # 게시판 글 — 좁히지 않은 첫 쪽에만. 타입을 골랐으면 그 타입을 찾는 것이고, 글은 쪽을
    # 넘기지 않는다(나머지는 「게시판에서 모두 보기」).
    posts: list[tuple[Any, int]] = []
    posts_total = 0
    if type_slug is None and offset == 0 and len(q.strip()) >= services.MIN_QUERY:
        posts, posts_total = board.search_posts(db, q)
    return SearchOut(
        q=q.strip(),
        total=found.total,
        types=[
            SearchTypeOut(
                type_slug=one.type_slug,
                type_label=one.type_label,
                icon=one.icon,
                count=one.count,
                usage=one.usage,
            )
            for one in found.types
        ],
        records=found.records,
        items=[
            SearchHitOut(
                id=one.id,
                type_slug=one.type_slug,
                type_label=one.type_label,
                icon=one.icon,
                label=one.label,
                key=one.key,
                matched=one.matched,
                matched_text=one.matched_text,
            )
            for one in found.hits
        ],
        limit=capped,
        offset=offset,
        min_query=services.MIN_QUERY,
        posts=[
            SearchPostOut(
                id=post.id,
                title=post.title,
                author_name=post.author_name,
                created_at=post.created_at,
                comment_count=comments,
                snippet=board.snippet(post.body, q),
            )
            for post, comments in posts
        ],
        posts_total=posts_total,
    )
