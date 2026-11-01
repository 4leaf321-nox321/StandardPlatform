"""자주검증 — 확장 전용 표(ADR 0044).

**먼저 독립된 회의 표다**(사용자 결정, 2026-10-30) — 표를 만들어 써 보고, 온톨로지 연결(열마다
받을 타입 · 고르기)은 그 안에서 시험해 본 뒤 다음 단계에서 붙인다. 그래서 여기 넷뿐이다:

    jv_meetings   회의 한 장 — 종류(심사회 · 포스트모텀 · 설명회) · 제목 · 일자 · 부서 · 확정
    jv_groups     묶음 — 표 중간의 머리줄(「설계 부문」)과 그 아래 줄들
    jv_rows       표의 줄 — 칸 글 넷(항목 · 문제점 · 개선대책 · 비고) · 일정(단계 목록) ·
                  작성 완료
    jv_presence   지금 수정하는 사람 — 회의마다 사람마다 한 줄(어느 칸 · 마지막으로 본 때)

## 여럿이 나눠 쓴다(2026-10-31 사용자 결정)

- **판 번호(`revision`)** — 회의의 쓰기마다 회의의 판 번호를 하나 올리고(같은 트랜잭션), 바뀐
  줄 · 묶음에 그 값을 적는다. 화면은 이 번호만 물어(`pulse`) 바뀌었을 때만 한 장을 다시 받는다.
  **시각(`updated_at`)을 커서로 쓰지 않는다** — 이 개발 기계(WSL)는 벽시계가 뒤로 튄다(ADR
  0024).
- **칸마다 마지막으로 고친 사람 · 때(`edits`)** — 덮어쓰기 충돌을 알릴 때 「누가 언제」 를 칸
  단위로 말한다. 줄 하나의 `updated_by_id` 만 보면 남이 비고를 고친 뒤 내 문제점이 부딪쳤을 때
  엉뚱한 사람을 댄다.

**사진은 여기 없다** — 공통 첨부(`files`)에 `owner_table="jv_rows"`, `owner_field` =
칸(`topic` · `problem` · `action` · `remark`)으로 붙는다. 이미지 판별 · 미리보기 · 파일 응답
머리 · 고아 정리 · MCP 업로드 표가 그대로 따라온다. 자리 확인은 확장이
등록한다(`services.attachment_owner`).

⚠️ 표 이름은 `jv_` 로 시작한다 — 어느 확장의 표인지 이름이 말한다.
⚠️ **지우지 않는다**(`deleted_at`) — 회의도 묶음도 줄도. 지운 줄의 사진(첨부 행)은 살아
   있어 파일도 남는다(객체의 휴지통과 같은 규칙 — `files/gc.py` 는 살아 있는 첨부가 가리키는
   파일을 지우지 않는다).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

#: 회의 종류 — 저장되는 키. 화면 이름은 `definitions.KINDS`.
MEETING_KINDS = ("review", "postmortem", "briefing")


class JvMeeting(Base):
    """회의 한 장 — 머리(제목 · 일자 · 부서)와 그 아래 표(`jv_rows`)."""

    __tablename__ = "jv_meetings"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('review', 'postmortem', 'briefing')", name="ck_jv_meetings_kind"
        ),
        # 목록 — 종류마다 일자가 새것부터(지운 것은 빼고).
        Index("ix_jv_meetings_kind_held", "kind", "held_on"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    kind: Mapped[str] = mapped_column(String(16))
    """`review`(심사회) · `postmortem`(포스트모텀) · `briefing`(설명회)."""
    title: Mapped[str] = mapped_column(String(200))
    held_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    """회의 일자 — 비워도 된다(목록에서 뒤로 선다)."""
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), index=True
    )
    """어느 부서의 회의인가 — 권한의 단위다(만들기는 구성원, 고치기는 만든 사람 · 부서 관리자).
    **RESTRICT** — 부서를 지우려 하면 확인 창에 이 회의가 뜨고(확장 지점), 통폐합이 옮긴다."""
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    """판 번호 — 이 회의의 쓰기(줄 · 묶음 · 사진 · 작성 완료 · 머리 · 배치 · 확정)마다 하나씩
    오른다. 올리는 `UPDATE` 가 행을 잠가 **같은 회의의 쓰기가 줄을 선다**(`services._lock`)."""
    edits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """칸마다 마지막으로 고친 사람 · 때 — `{"title": {"by": <user id>, "at": <iso>}}`."""
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """회의 확정 — 채워져 있으면 이 회의의 쓰기가 전부 막힌다(확정 해제까지)."""
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class JvGroup(Base):
    """묶음 — 표 중간의 머리줄과 그 아래 줄들. 묶음 없는 줄은 표 맨 위에 머리줄 없이 선다."""

    __tablename__ = "jv_groups"
    __table_args__ = (Index("ix_jv_groups_meeting_position", "meeting_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jv_meetings.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    """묶음의 차례 — 작은 것이 위. 배치가 1부터 다시 매긴다."""
    title: Mapped[str] = mapped_column(String(200), default="", server_default="")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    """이 묶음을 마지막으로 바꾼 쓰기의 판 번호(회의의 것)."""
    edits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class JvRow(Base):
    """표의 한 줄 — 글 칸 넷(사진은 첨부로 붙는다)과 일정. 열의 정의는 `cells.COLUMNS`."""

    __tablename__ = "jv_rows"
    __table_args__ = (Index("ix_jv_rows_meeting_position", "meeting_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jv_meetings.id", ondelete="CASCADE")
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jv_groups.id", ondelete="SET NULL"), nullable=True
    )
    """어느 묶음의 줄인가 — 비면 묶음 없음(표 맨 위)."""
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    """줄의 차례 — **묶음 안에서** 작은 것이 위. 배치가 1부터 다시 매긴다."""
    topic: Mapped[str] = mapped_column(Text, default="", server_default="")
    """항목."""
    problem: Mapped[str] = mapped_column(Text, default="", server_default="")
    """문제점."""
    action: Mapped[str] = mapped_column(Text, default="", server_default="")
    """개선대책."""
    remark: Mapped[str] = mapped_column(Text, default="", server_default="")
    """비고."""
    schedule: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    """일정 — 단계 목록 `[{"date": "YYYY-MM-DD", "label": "설계 변경"}]`, 날짜 차례로
    (`cells.schedule_of`). 글이 아니라 값이다."""
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    """완료 예정일 — **일정의 가장 늦은 날(파생값)**. 기한 지남 · 정렬에 쓴다. 직접 쓰지 않는다
    (일정을 고치면 따라온다)."""
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    """마지막으로 고친 사람 — 줄의 「마지막 수정」."""
    done: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    """작성 완료 — 그 줄을 맡아 쓰던 사람이 「다 썼다」 고 표시한 것. 고치기를 막지는
    않는다."""
    done_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    """이 줄을 마지막으로 바꾼 쓰기의 판 번호(회의의 것)."""
    edits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """칸마다 마지막으로 고친 사람 · 때 — 덮어쓰기 충돌이 「누가 언제」 를 칸 단위로 말한다."""


class JvPresence(Base):
    """지금 수정하는 사람 — 회의마다 사람마다 한 줄. **워커가 여럿이라 메모리에 두지 않는다.**

    칸에 들어가면 그 자리를 적고(`seen_at` = DB 의 now), 머무는 동안 10초마다 다시 적고, 나가면
    지운다. 30초 안의 것만 「수정 중」 이다. 감사는 남기지 않는다(쓰기가 아니라 눈짓이다).
    """

    __tablename__ = "jv_presence"
    __table_args__ = (Index("ix_jv_presence_seen_at", "seen_at"),)

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("jv_meetings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    row_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jv_rows.id", ondelete="CASCADE"), nullable=True
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jv_groups.id", ondelete="CASCADE"), nullable=True
    )
    field: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """칸 — 줄이면 `topic` · `problem` · `action` · `remark` · `schedule`, 묶음 · 머리면
    `title`."""
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
