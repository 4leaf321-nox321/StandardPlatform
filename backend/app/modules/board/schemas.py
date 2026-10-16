"""게시판 API 형태(ADR 0033)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

#: 본문의 상한 — 글은 길게(회의록 · 절차를 붙여 넣는다), 댓글은 짧게.
POST_BODY_MAX = 20_000
COMMENT_BODY_MAX = 2_000


class PostWriteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=POST_BODY_MAX)


class PostPatchIn(BaseModel):
    """부분 수정 — **안 보낸 칸은 그대로**(None). 비운 제목 · 본문은 받지 않는다."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=POST_BODY_MAX)


class PinIn(BaseModel):
    pinned: bool


class CommentWriteIn(BaseModel):
    body: str = Field(min_length=1, max_length=COMMENT_BODY_MAX)


class CommentOut(BaseModel):
    id: uuid.UUID
    body: str
    author_id: uuid.UUID | None
    author_name: str
    created_at: datetime
    edited_at: datetime | None
    can_edit: bool
    """작성자 · 시스템 관리자 — 화면의 수정 · 삭제 단추. **서버가 다시 판정한다.**"""


class PostSummaryOut(BaseModel):
    """목록의 한 줄 — 본문은 싣지 않는다."""

    id: uuid.UUID
    title: str
    author_id: uuid.UUID | None
    author_name: str
    pinned: bool
    created_at: datetime
    edited_at: datetime | None
    comment_count: int
    attachment_count: int


class PostOut(PostSummaryOut):
    body: str
    can_edit: bool
    """작성자 · 시스템 관리자 — 수정 · 삭제 · 첨부 업로드 · 첨부 삭제."""
    comments: list[CommentOut]
