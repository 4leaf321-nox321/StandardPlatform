"""장표 — 여러 타입의 기록을 정형 배치(틀) 한 장에 모아 본다(ADR 0037).

화면의 말은 「장표 · 틀 · 영역」 이다(`docs/용어.md`). 코드 이름이 `slides` 인 까닭은
`app.shared.sheets` 가 이미 CSV · Excel 의 자리라서다 — 장표 한 장이 PPT 의 슬라이드 한 장이다.

**값을 담지 않는다.** 영역의 기록은 장표를 열 때 목록과 같은 거르기로 센다(살아 있는 값).
장표가
드는 것은 틀 · 주제 · 사람이 쓴 글 · 사람이 고른 기록뿐이다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SlideTemplate(Base):
    """틀 — 시스템 관리자가 만드는 정형 배치."""

    __tablename__ = "slide_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    subject_type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("object_types.id", ondelete="RESTRICT"), index=True
    )
    """주제 타입 — 장표 한 장이 무엇에 대한 것인가(예: 모델). 기록이 아니라 온톨로지
    항목이다."""
    rows: Mapped[int] = mapped_column(Integer)
    cols: Mapped[int] = mapped_column(Integer)
    areas: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    """영역들 — 모양은 `schemas.AreaIn`. 저장할 때 세워 보아 틀린 주소 · 칸을 거절한다."""
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Slide(Base):
    """장표 — 틀 하나 + 주제 객체 하나. 작성자와 시스템 관리자가 고친다(게시판과 같은 결)."""

    __tablename__ = "slides"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("slide_templates.id", ondelete="RESTRICT"), index=True
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("objects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    """주제 객체. 영구 삭제되면 비고, 장표는 「주제가 없습니다」 를 말하며 남는다."""
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """영역마다 사람이 채운 것 — `{영역 키: {"text": 글, "picks": [기록 id, …]}}`."""
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_name: Mapped[str] = mapped_column(String(100))
    """**그때의 이름을 박는다** — 계정이 지워져도 누가 만든 장표인지 남는다."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deleted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
