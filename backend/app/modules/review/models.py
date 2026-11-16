"""결정 기록 — **무엇을 누가 언제 어떻게 정했나, 추천을 따랐나.**

검토함은 항목을 복사해 두지 않는다(원래 자리에서 읽는다, `registry.py`). 남기는 것은 사람의
결정뿐이다 — 셋 때문에:

    되돌리기     적용한 묶음(`batch_id`)을 통째로 — 종류마다 원래 기능의 되돌리기에 넘길 값을
                 `undo` 에 둔다(일괄 수정의 묶음 번호 · 그때의 별칭 …)
    추천의 정확도 사람이 추천을 따랐나(`followed`) — 규칙 · AI 의 추천이 얼마나 맞는지 나중에
                 확신도 구간마다 잰다. 안 남기면 「0.9 이상은 믿어도 되나」 에 답할 근거가 없다
    무시의 기억   원래 자리에 「무시」 가 없는 종류(이름이 같은 객체 · 지워진 것을 가리키는
                 칸)는 여기 남은 무시(`signature` 가 같을 때만)로 다시 안 띄운다

데이터를 바꾼 기록은 여전히 원래 기능의 감사 기록(`audit_entries`)이다 — 여기는 그 위의
「검토함에서 정했다」 한 줄이다. 같은 일을 두 표에 적으면 어긋나므로, 값의 전 · 후는 여기 적지
않는다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

#: 결정을 어떻게 내렸나 — 한 줄 · 고른 것 · 거른 전부 · 추천대로 모두 · 자동 확인(ADR 0075).
REVIEW_MODES = ("one", "selected", "all", "recommended", "auto")


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        # 묶음 되돌리기 · 최근 묶음 목록.
        Index("ix_review_decisions_batch", "batch_id"),
        # 무시의 기억 — 그 항목에 살아 있는 무시가 있나.
        Index("ix_review_decisions_item", "kind", "item_id"),
        # 최근 결정 · 정확도(종류마다).
        Index("ix_review_decisions_kind_at", "kind", "decided_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    """한 번의 적용 — 「추천대로 모두」 한 번이면 그 전부가 한 묶음이다."""
    kind: Mapped[str] = mapped_column(String(40))
    item_id: Mapped[str] = mapped_column(String(300))
    question: Mapped[str] = mapped_column(String(500), default="", server_default="")
    """그때의 물음 — 원래 자리가 처리되어 사라져도 무엇을 정했는지 읽히게."""
    action: Mapped[str] = mapped_column(String(12))
    """`accept`(추천대로) · `choose`(다른 것으로) · `ignore`(무시)."""
    choice: Mapped[str] = mapped_column(String(200), default="", server_default="")
    """고른 값 — 객체 id · `approve` · `remove` · `clear` · `ignore`."""
    choice_label: Mapped[str] = mapped_column(String(300), default="", server_default="")
    recommended: Mapped[str | None] = mapped_column(String(200), nullable=True)
    """그때의 추천 — 없었으면 NULL."""
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    followed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    """추천을 따랐나 — 추천이 없었으면 NULL(셈에서 뺀다). 무시는 추천을 안 따른 것이다."""
    mode: Mapped[str] = mapped_column(String(12), default="one", server_default="one")
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    """「추천대로 모두」 의 문턱 — 그때 사람이 정한 값."""
    status: Mapped[str] = mapped_column(String(12))
    """`done`(처리함) · `refused`(그 사이 막혔다 — 까닭은 `message`)."""
    message: Mapped[str] = mapped_column(String(500), default="", server_default="")
    rows: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    signature: Mapped[str] = mapped_column(String(64), default="", server_default="")
    undo: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    undoable: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    type_slug: Mapped[str] = mapped_column(String(64), default="", server_default="")
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    """그 항목의 소유 부서 — 그 부서 관리자가 결정 기록을 본다."""
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    undone_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ReviewAutoRule(Base):
    """**자동 확인** — 종류 하나를 밤마다 「추천대로」 처리하는 규칙(ADR 0075). 시스템 관리자가
    켜고, **켠 사람의 이름으로** 처리한다(감사 기록 · 결정 기록에 그 사람이 남는다 — 사람 없는
    처리는 권한을 볼 수 없다).

    켜 둔다고 늘 도는 것이 아니다 — 그 종류에서 **사람이 하나씩 정한 결정**의 기록이 그 확신도
    구간에서 충분히 맞아야(`services.AUTO_MIN_DECIDED` · `AUTO_MIN_RATE`) 돈다. 자동으로 정한
    것을 누가 복원하면 멈춘다(다시 켜면 이어 한다). 밤마다 상한(`AUTO_MAX`)까지 — 결정은 묶음
    하나로 남아 「복원」 으로 되돌린다."""

    __tablename__ = "review_auto_rules"

    kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    threshold: Mapped[float] = mapped_column(Float, default=0.95, server_default="0.95")
    """이 확신도 이상인 것만 — 0.9 아래로는 못 둔다."""
    enabled_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    """켠 때 — 그 뒤에 자동으로 정한 것을 누가 복원했으면 멈춘다."""
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """마지막으로 작업을 넣은 때 — 하루에 한 번."""
    last_job_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    last_note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    """마지막 차례의 말 — 「312건 처리 · 막힘 4」 · 「조건 미달 — 결정 12건(30건 필요)」."""
