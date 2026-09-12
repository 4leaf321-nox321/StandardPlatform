"""데이터 소스 — **바깥 시스템에서 읽어 와 온톨로지를 채운다.**

ENOVIA·Teamcenter·ERP 같은 시스템이 OData 로 내놓는 표를 주기적으로 읽어 한 타입의 객체로
넣는다. 규칙은 파일 가져오기와 같다 — 계획 먼저, 전부 아니면 무, 같은 것이면 고침, 빈 칸은
안 건드림. 다른 것은 **행이 어디서 오는가**와 **같은 객체를 어떻게 다시 찾는가** 뿐이다.

## 같은 객체를 다시 찾기

바깥 행의 식별자(`mapping.external_key`)를 그 객체의 별칭(`source:<slug>`)으로 남긴다. 다음
동기화는 그것으로 먼저 찾으므로 우리 쪽 이름·식별자를 고쳐도 안 끊긴다. 처음 만날 때는
식별자 → 별칭 → 이름 순으로 찾고, 겹치면 오류 행이다(사람이 고른다).

## 바깥에서 사라진 행

기본은 **건드리지 않는다** — 우리 것을 가리키는 관계가 있을 수 있다. `deprecate_missing`
을 켜면 이 소스가 남긴 객체 중 이번에 안 온 것을 「사용 중지」 로 표시한다. 진짜 지우기는
없다.
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
from app.modules.ontology.models import SLUG_MAX

#: 인증 방식.
AUTH_KINDS = ("none", "basic", "bearer")
#: 소스의 종류. 지금은 OData 뿐이지만 칸은 둔다 — REST/CSV-URL 이 올 자리.
SOURCE_KINDS = ("odata",)
#: 동기화 기록의 상태.
RUN_STATUSES = ("planned", "ok", "failed")


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(SLUG_MAX), unique=True)
    """별칭의 `source:<slug>` 에 박힌다 — **바뀌면 안 된다.** 바꾸면 그 소스가 남긴 외부
    식별자를 전부 못 찾고, 다음 동기화가 같은 것을 새로 만든다."""
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20), default="odata", server_default="odata")

    base_url: Mapped[str] = mapped_column(String(500))
    """OData 서비스 루트 — `https://plm.example.com/odata/v4`."""
    entity_set: Mapped[str] = mapped_column(String(200))
    """읽을 엔티티 셋 — `Suppliers`. 루트 뒤에 그대로 붙는다."""
    filter: Mapped[str] = mapped_column(Text, default="", server_default="")
    """`$filter` 그대로 — `Status eq 'Released'`."""
    select: Mapped[str] = mapped_column(Text, default="", server_default="")
    """`$select` — 비우면 칸 대응에 쓰인 열만 청한다."""
    auth: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """{kind: none|basic|bearer, user, secret}. **화면에 다시 보여 주지 않는다.**"""
    page_size: Mapped[int] = mapped_column(Integer, default=500, server_default="500")

    type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("object_types.id", ondelete="RESTRICT")
    )
    """어느 타입에 넣나. RESTRICT — 소스가 가리키는 타입을 지우면 동기화가 500 을 낸다."""
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=True
    )
    """새로 만드는 객체의 소유 부서. NULL 이면 전역."""
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """{external_key, columns: [{source, target, values, values_strict}]} — `services.py`."""

    deprecate_missing: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    interval_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    """0 이면 손으로만. 그 밖이면 타이머(`scripts/sync_datasources.py --due`)가 이 간격으로."""
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DataSourceRun(Base):
    """동기화 한 번의 기록 — 계획만 본 것도 남긴다(무엇이 막았는지 다음 사람이 본다)."""

    __tablename__ = "data_source_runs"
    __table_args__ = (Index("ix_data_source_runs_source_started", "source_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="planned", server_default="planned"
    )
    applied: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    actor_label: Mapped[str] = mapped_column(String(200), default="", server_default="")
    rows_seen: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """{create, update, unchanged, error, deprecated}."""
    errors: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    """행 오류(앞의 몇 개)와 연결 오류. 전부 담지 않는다 — 5,000줄을 표에 넣으면 아무도
    안 읽는다."""
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
