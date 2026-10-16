"""게시판 라우터(ADR 0033) — 읽기 · 작성은 누구나, 수정 · 삭제는 작성자와 시스템 관리자, 고정은
시스템 관리자.

개인 토큰의 쓰기 범위에 **등록하지 않는다** — 토큰으로는 읽기만 된다(「모르는 것은 막는다」).
게시판의 글은 사람이 사람에게 쓰는 것이다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.board import services
from app.modules.board.models import POST_TABLE, BoardComment, BoardPost
from app.modules.board.schemas import (
    CommentOut,
    CommentWriteIn,
    PinIn,
    PostOut,
    PostPatchIn,
    PostSummaryOut,
    PostWriteIn,
)
from app.shared import audit
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import code
from app.shared.pagination import Page, clamp_limit
from app.shared.permissions import require_authored_edit

router = APIRouter(prefix="/board", tags=["board"])


@router.get("/posts", response_model=Page[PostSummaryOut])
def list_posts(
    q: str | None = Query(default=None, max_length=100, description="제목 · 본문에 든 말"),
    mine: bool = Query(default=False, description="내가 작성한 글만"),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Page[PostSummaryOut]:
    """**고정한 글이 먼저**, 그다음 새 글부터."""
    size = clamp_limit(limit)
    stmt = select(BoardPost).where(BoardPost.deleted_at.is_(None))
    needle = (q or "").strip()
    if needle:
        stmt = stmt.where(
            or_(
                BoardPost.title.icontains(needle, autoescape=True),
                BoardPost.body.icontains(needle, autoescape=True),
            )
        )
    if mine:
        stmt = stmt.where(BoardPost.author_id == user.id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    posts = list(
        db.scalars(
            stmt.order_by(BoardPost.pinned.desc(), BoardPost.created_at.desc(), BoardPost.id)
            .limit(size)
            .offset(offset)
        )
    )
    ids = [one.id for one in posts]
    comments = services.comment_counts(db, ids)
    attachments = services.attachment_counts(db, ids)
    return Page[PostSummaryOut](
        items=[
            services.summary(one, comments.get(one.id, 0), attachments.get(one.id, 0))
            for one in posts
        ],
        total=total,
        limit=size,
        offset=offset,
    )


@router.post("/posts", response_model=PostOut, status_code=201)
def create_post(
    payload: PostWriteIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PostOut:
    """글 작성 — 첨부는 글이 생긴 뒤 `/api/attachments`(`owner_table=board_posts`)로."""
    post = BoardPost(
        title=payload.title.strip(),
        body=payload.body,
        author_id=user.id,
        author_name=user.display_name,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return services.post_out(db, post, user)


@router.get("/posts/{post_id}", response_model=PostOut)
def get_post(
    post_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PostOut:
    return services.post_out(db, services.live_post(db, post_id), user)


@router.patch("/posts/{post_id}", response_model=PostOut)
def update_post(
    post_id: uuid.UUID,
    payload: PostPatchIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PostOut:
    post = services.live_post(db, post_id)
    require_authored_edit(user, post.author_id, what="게시글", code_value=code("BOARD", 4))
    changed = False
    if payload.title is not None and payload.title.strip() != post.title:
        post.title = payload.title.strip()
        changed = True
    if payload.body is not None and payload.body != post.body:
        post.body = payload.body
        changed = True
    if changed:
        post.edited_at = datetime.now(UTC)
        db.commit()
        db.refresh(post)
    return services.post_out(db, post, user)


@router.delete("/posts/{post_id}", status_code=204)
def delete_post(
    post_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """글 삭제 — 댓글 · 첨부와 함께 목록에서 내려간다(행은 남는다)."""
    post = services.live_post(db, post_id)
    require_authored_edit(user, post.author_id, what="게시글", code_value=code("BOARD", 4))
    services.delete_post(db, post, user)
    db.commit()
    return Response(status_code=204)


@router.put("/posts/{post_id}/pinned", response_model=PostOut)
def pin_post(
    post_id: uuid.UUID,
    payload: PinIn,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PostOut:
    """고정 · 고정 해제 — 시스템 관리자만. 목록 맨 위에 선다."""
    post = services.live_post(db, post_id)
    if post.pinned != payload.pinned:
        post.pinned = payload.pinned
        audit.record(
            db,
            action="board.post.pin" if payload.pinned else "board.post.unpin",
            actor=admin,
            target_table=POST_TABLE,
            target_id=post.id,
            target_label=post.title,
        )
        db.commit()
        db.refresh(post)
    return services.post_out(db, post, admin)


@router.post("/posts/{post_id}/comments", response_model=CommentOut, status_code=201)
def create_comment(
    post_id: uuid.UUID,
    payload: CommentWriteIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CommentOut:
    """댓글 작성 — 글의 작성자에게 알린다(내 글에 내가 단 것은 빼고)."""
    post = services.live_post(db, post_id)
    comment = BoardComment(
        post_id=post.id,
        body=payload.body,
        author_id=user.id,
        author_name=user.display_name,
    )
    db.add(comment)
    db.flush()
    services.notify_comment(db, post, comment, user)
    db.commit()
    db.refresh(comment)
    return services.comment_out(comment, user)


@router.patch("/comments/{comment_id}", response_model=CommentOut)
def update_comment(
    comment_id: uuid.UUID,
    payload: CommentWriteIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CommentOut:
    comment = services.live_comment(db, comment_id)
    require_authored_edit(user, comment.author_id, what="댓글", code_value=code("BOARD", 5))
    if payload.body != comment.body:
        comment.body = payload.body
        comment.edited_at = datetime.now(UTC)
        db.commit()
        db.refresh(comment)
    return services.comment_out(comment, user)


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(
    comment_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """댓글 삭제 — 행은 남는다. 남의 댓글을 시스템 관리자가 지운 것은 감사에 남긴다."""
    comment = services.live_comment(db, comment_id)
    require_authored_edit(user, comment.author_id, what="댓글", code_value=code("BOARD", 5))
    comment.deleted_at = datetime.now(UTC)
    comment.deleted_by_id = user.id
    if comment.author_id != user.id:
        audit.record(
            db,
            action="board.comment.delete",
            actor=user,
            target_table="board_comments",
            target_id=comment.id,
            target_label=comment.body[:80],
            changes={"author": comment.author_name, "post_id": str(comment.post_id)},
        )
    db.commit()
    return Response(status_code=204)
