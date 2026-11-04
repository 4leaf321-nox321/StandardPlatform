"""자주검증 입력 — 확장 전용 표. **설정 한 줄뿐이다.**

문서 · 문제점 · 판정 · 지표는 허브의 온톨로지 객체다(`objects`) — 별칭 · 일괄 입력 · 내보내기 ·
부서 소유 · 감사 · 첨부 · MCP 가 거기 이미 있어서, 전용 표로 두면 그것을 다시 만들게 된다. 여기
있는 것은 **어느 타입 · 어느 칸이 어느 역할인가**뿐이다(ADR 0051).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SvInputSetting(Base):
    """이 설치의 설정 — **한 줄.** 행이 둘이면 어느 것이 참인지 물을 자리가 없어 DB 가
    막는다."""

    __tablename__ = "svinput_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_svinput_settings_single"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    roles: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """`{역할 key: {"type": 타입 slug, <칸 역할>: 속성 키, …}}` — 역할 목록은
    `definitions.ROLES`.

    비어 있으면 「설정 안 됨」 이다. 기본값은 심지 않는다 — 설정 화면이 후보를 제안한다."""
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
