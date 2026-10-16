"""게시판의 규칙(ADR 0033) — 글 찾기 · 줄 만들기 · 첨부 자리 · 삭제 · 댓글 알림.

- **누구나 읽고 쓴다.** 설치 전체의 자리라 부서가 없다(첨부도 전역 — 모두가 본다).
- **수정 · 삭제는 작성자와 시스템 관리자**(`permissions.can_edit_authored`). 부서 관리자는
  아니다 — 게시판은 부서의 것이 아니다.
- **첨부는 글의 작성자가 붙이고 뗀다** — `extensions.register_attachment_owner` 로 자리를
  답한다. 안 걸면 첨부의 예전 규칙(전역은 시스템 관리자만)이 걸려, 작성자가 자기 글에 파일을
  못 붙인다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.board.models import POST_TABLE, BoardComment, BoardPost
from app.modules.board.schemas import CommentOut, PostOut, PostSummaryOut
from app.modules.files.models import Attachment
from app.modules.notifications import services as notifications
from app.shared import audit, extensions
from app.shared.errors import NotFound, code
from app.shared.permissions import can_edit_authored, require_authored_edit


def live_post(db: Session, post_id: uuid.UUID) -> BoardPost:
    """지우지 않은 글 — 지운 글은 없는 글과 같게 답한다."""
    post = db.get(BoardPost, post_id)
    if post is None or post.deleted_at is not None:
        raise NotFound(code("BOARD", 1), "게시글을 찾을 수 없습니다.")
    return post


def live_comment(db: Session, comment_id: uuid.UUID) -> BoardComment:
    comment = db.get(BoardComment, comment_id)
    if comment is None or comment.deleted_at is not None:
        raise NotFound(code("BOARD", 2), "댓글을 찾을 수 없습니다.")
    live_post(db, comment.post_id)  # 지운 글의 댓글도 없는 것
    return comment


def comment_counts(db: Session, post_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not post_ids:
        return {}
    rows = db.execute(
        select(BoardComment.post_id, func.count())
        .where(BoardComment.post_id.in_(post_ids), BoardComment.deleted_at.is_(None))
        .group_by(BoardComment.post_id)
    )
    return {post_id: int(count) for post_id, count in rows}


def attachment_counts(db: Session, post_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not post_ids:
        return {}
    rows = db.execute(
        select(Attachment.owner_id, func.count())
        .where(
            Attachment.owner_table == POST_TABLE,
            Attachment.owner_id.in_(post_ids),
            Attachment.deleted_at.is_(None),
        )
        .group_by(Attachment.owner_id)
    )
    return {owner_id: int(count) for owner_id, count in rows}


def summary(post: BoardPost, comments: int, attachments: int) -> PostSummaryOut:
    return PostSummaryOut(
        id=post.id,
        title=post.title,
        author_id=post.author_id,
        author_name=post.author_name,
        pinned=post.pinned,
        created_at=post.created_at,
        edited_at=post.edited_at,
        comment_count=comments,
        attachment_count=attachments,
    )


def comment_out(comment: BoardComment, viewer: User) -> CommentOut:
    return CommentOut(
        id=comment.id,
        body=comment.body,
        author_id=comment.author_id,
        author_name=comment.author_name,
        created_at=comment.created_at,
        edited_at=comment.edited_at,
        can_edit=can_edit_authored(viewer, comment.author_id),
    )


def post_out(db: Session, post: BoardPost, viewer: User) -> PostOut:
    comments = list(
        db.scalars(
            select(BoardComment)
            .where(BoardComment.post_id == post.id, BoardComment.deleted_at.is_(None))
            .order_by(BoardComment.created_at, BoardComment.id)
        )
    )
    attachments = attachment_counts(db, [post.id]).get(post.id, 0)
    head = summary(post, len(comments), attachments)
    return PostOut(
        **head.model_dump(),
        body=post.body,
        can_edit=can_edit_authored(viewer, post.author_id),
        comments=[comment_out(one, viewer) for one in comments],
    )


def delete_post(db: Session, post: BoardPost, user: User) -> None:
    """글을 지운다 — **행은 남기고**(`deleted_at`) 첨부도 함께 뗀다. 글이 없어졌는데 파일이
    id 로 계속 내려받아지면, 지운 사람(대개 잘못 올린 파일을 거둬들이려는 사람)의 뜻과 다르다.
    감사에 남긴다 — 남의 글을 시스템 관리자가 지운 일은 나중에 물어진다."""
    now = datetime.now(UTC)
    post.deleted_at = now
    post.deleted_by_id = user.id
    detached = list(
        db.scalars(
            update(Attachment)
            .where(
                Attachment.owner_table == POST_TABLE,
                Attachment.owner_id == post.id,
                Attachment.deleted_at.is_(None),
            )
            .values(deleted_at=now)
            .returning(Attachment.id)
        )
    )
    audit.record(
        db,
        action="board.post.delete",
        actor=user,
        target_table=POST_TABLE,
        target_id=post.id,
        target_label=post.title,
        changes={"author": post.author_name, "attachments": len(detached)},
    )


def notify_comment(db: Session, post: BoardPost, comment: BoardComment, user: User) -> None:
    """내 글에 남이 댓글을 달면 알린다 — 내가 단 것은 안 알린다. **부르는 쪽이 커밋한다.**"""
    if post.author_id is None or post.author_id == user.id:
        return
    author = db.get(User, post.author_id)
    if author is None or not author.can_sign_in:
        return
    notifications.notify(
        db,
        user_id=post.author_id,
        kind=notifications.BOARD_COMMENT,
        title=f"{comment.author_name} 님이 「{post.title}」 에 댓글을 작성했습니다",
        body=comment.body[:200],
        link=f"/board/{post.id}",
    )


# --- 공통 틀에 거는 것(`main._register_extensions`) ------------------------------------


def attachment_owner(
    db: Session, user: User, owner_id: uuid.UUID, owner_field: str | None
) -> extensions.AttachmentOwner:
    """첨부를 붙일 · 뗄 자리 — 지우지 않은 글, 작성자 · 시스템 관리자. 첨부는 전역(모두가
    본다 — 게시판이 그렇다)."""
    post = live_post(db, owner_id)
    require_authored_edit(
        user, post.author_id, what="게시글의 첨부", code_value=code("BOARD", 3)
    )
    return extensions.AttachmentOwner(workspace_id=None, label=post.title)


def stats(db: Session) -> list[extensions.StatItem]:
    total = (
        db.scalar(
            select(func.count()).select_from(BoardPost).where(BoardPost.deleted_at.is_(None))
        )
        or 0
    )
    return [extensions.StatItem(label="게시글", count=total)]
