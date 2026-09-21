"""작업 — **오래 걸리는 일은 요청이 아니라 표에 산다.**

파일을 올려 바꾸는 것이 이 플랫폼의 일반적인 쓰임이다. 그 일을 요청 안에서 하면
1만 행에서 분 단위가 되고, 브라우저·MCP 는 1-2분에서 끊고, 앱을 재시작하면 어디까지
됐는지 아무 데도 안 남는다. 그래서 일은 여기 한 행이 되고, 워커(`app/worker.py`)가
집어 돌리고, 요청은 그 행을 보기만 한다. 설계는 `docs/작업-워커-설계.md`.

## 파일도 표에 든다

서버가 A · B 두 대다. 올린 파일이 A 의 디스크에 놓이면 B 의 워커가 못 읽는다. 작업
파일은 7일 뒤 지우는 임시물이라 `job_files.data` 에 넣으면 복제로 따라가고 어느 대가
집어도 된다. 첨부(`attachments`)는 영구물이라 그대로 filestore 다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

JOB_STATUSES = ("queued", "running", "done", "failed", "cancelled")


class JobFile(Base):
    __tablename__ = "job_files"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255))
    """사람이 올린 이름 — 확장자로 CSV 인지 JSON 인지 가른다."""
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    """이 뒤로는 지운다 — 임시물이 영구물이 되면 표가 DB 의 대부분이 된다."""


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    kind: Mapped[str] = mapped_column(String(40), index=True)
    """`objects_import` · `relations_import` · … — `kinds.py` 의 등록 이름."""
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """종류가 정하는 인자 — 타입 · 부서 · apply · 지문. 값은 작아야 한다(파일은 따로)."""

    input_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("job_files.id", ondelete="SET NULL"), nullable=True
    )
    output_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("job_files.id", ondelete="SET NULL"), nullable=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    """적용 작업 → 그 계획 작업. 같은 파일 · 같은 지문을 여기서 잇는다."""

    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """`{"stage": "계획", "done": 12400, "total": 50000}` — 워커가 다른 연결로 쓴다."""
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    """끝난 뒤의 결과. 가져오기면 계획 표 그대로 — 화면이 같은 표를 그린다."""
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    """왜 실패했나 — 서버의 말 그대로. 스택은 로그에."""

    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    """어느 부서의 일인가 — 내 것과 내 부서 것만 보인다. NULL 은 전역."""

    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """워커가 살아 있다는 표시. `running` 인데 오래 멎어 있으면 다른 워커가 되돌린다."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    host: Mapped[str] = mapped_column(String(120))
    pid: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    current_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
