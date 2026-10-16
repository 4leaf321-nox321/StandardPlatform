"""게시판 — 설치 전체가 함께 쓰는 글 목록(ADR 0033).

공지는 운영자가 위에서 아래로 전하는 말이고, 게시판은 **누구나 쓰는 자리**다. 사내 설치에는
메일 서버 · 메신저가 없는 경우가 많아 「이 파일 어디 있나요」 「이 서버 언제 내리나요」 같은
물음과 자료를 나눌 자리가 앱 안에밖에 없다.

**지우지 않는다** — 글 · 댓글의 삭제는 `deleted_at` 만 채운다. 첨부 · 알림 · 감사가 그 글을
가리키고, 무엇이 지워졌는지는 나중에 물어질 수 있다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

#: 첨부의 `owner_table` — 첨부는 외래키 없이 이 이름으로 글을 가리킨다.
POST_TABLE = "board_posts"


class BoardPost(Base):
    __tablename__ = POST_TABLE

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_name: Mapped[str] = mapped_column(String(100))
    """**그때의 이름을 박는다** — 계정이 지워져도 누가 쓴 글인지 남는다(감사와 같은 까닭)."""
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    """목록 맨 위 — 시스템 관리자만 정한다."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    """제목 · 본문을 마지막으로 수정한 때 — 첨부 · 고정은 수정이 아니다."""
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deleted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class BoardComment(Base):
    __tablename__ = "board_comments"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    post_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("board_posts.id", ondelete="CASCADE"), index=True
    )
    body: Mapped[str] = mapped_column(Text)
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
