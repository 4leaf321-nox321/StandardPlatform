"""웹훅 — **바뀐 것을 바깥에 알린다.**

이 틀에는 작업 큐·워커가 없다(일부러 — 안 쓰는 곳에서는 그저 도는 프로세스다). 그래서
보내기는 앱 안의 스레드가 하고, **보낸 기록(`webhook_deliveries`)이 표에 남는다** — 스레드는
죽어도 기록은 남고, 남은 pending 은 다음 기동·다음 이벤트에 다시 보낸다.

## 무엇을 보내나

감사 기록에 남는 것 전부가 후보다(`object.create` · `object.relation.add` · `ontology.type.update`
…). 웹훅마다 `events`(패턴, `object.*` 처럼)와 `type_slugs`(객체 타입)로 거른다.
받는 쪽은 `X-Signature-256`(HMAC-SHA256, secret)으로 진짜인지 확인한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

#: 보낸 기록의 상태.
DELIVERY_STATUSES = ("pending", "ok", "failed")


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(500))
    secret: Mapped[str] = mapped_column(String(200), default="", server_default="")
    """비어 있으면 서명하지 않는다. **화면에는 다시 보여 주지 않는다** — 만들 때 한 번."""
    events: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    """`object.*` · `object.relation.add` · `*` 같은 패턴들. 비어 있으면 아무것도 안 보낸다 —
    「전부」 는 `*` 로 명시한다."""
    type_slugs: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    """객체 이벤트를 이 타입들로만. NULL 이면 전부."""
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    """마지막으로 보낸 결과 — 목록에서 「죽어 있는 웹훅」 이 보이게."""

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WebhookDelivery(Base):
    """보낸(보낼) 기록 하나. **표에 남기는 이유**: 스레드는 죽어도 이것은 남는다."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliveries_hook_created", "webhook_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    webhook_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("webhooks.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
