"""묶음 한 판과 **되돌릴 기록.**

## 왜 있나

백필은 한 번에 수만 줄을 넣는다. 그 판이 틀렸을 때 되돌릴 길이 없었다 — 객체 하나씩
화면에서 고치는 것뿐이고, 수만 줄에는 그 길이 없다. 감사 기록으로도 안 된다: 백필은
`audit="summary"` 로 돌아서 줄마다의 기록이 아예 없다(그것이 켜진 이유다).

그래서 **넣으면서 되돌릴 것만 따로 적는다.** 감사 기록과 다른 것이다:

    감사 기록   누가 언제 무엇을 했나 — 사람이 읽는다, 지우지 않는다
    되돌릴 기록 그것을 되돌리려면 무엇을 써야 하나 — 기계가 읽는다, 보관 기간이 지나면 지운다

## 무엇을 적나

바뀐 것마다 한 줄이다(`before` · `after` 의 **바뀐 칸만**). `after` 를 함께 적는 이유:
되돌리기 전에 **그 사이 남이 고쳤는지** 봐야 한다. 지금 값이 우리가 넣은 값과 다르면 그
줄은 되돌리지 않고 이유를 적는다 — 남의 변경을 조용히 덮는 것이 되돌리기보다 나쁘다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

#: 되돌릴 기록이 가리키는 표들. 문자열로 두는 이유: 표가 늘어도 마이그레이션이 없다.
JOURNAL_TABLES = ("objects", "object_relations", "object_links", "object_aliases")


class BundleRun(Base):
    """묶음을 **적용한 한 판.** 미리 보기는 여기 안 남는다(아무것도 안 바뀐다)."""

    __tablename__ = "bundle_runs"
    __table_args__ = (Index("ix_bundle_runs_at", "at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
    """**`clock_timestamp()` 다** — `now()` 는 트랜잭션이 시작한 시각이라 한 트랜잭션에서
    판이 여럿이면 전부 같은 시각이 되고, 그러면 최근 순서가 질의마다 달라진다."""
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_label: Mapped[str] = mapped_column(String(200), default="")
    """그때의 이름을 박는다 — 계정이 지워져도 누가 넣었는지는 남는다."""
    source: Mapped[str] = mapped_column(String(100), default="")
    """허브에서 받은 묶음이면 그 출처. 빈 값이면 이 설치에서 만든 묶음이다."""
    label: Mapped[str] = mapped_column(String(300), default="")
    """무엇이 들어갔나 — 타입 slug 들. 목록에서 사람이 이 판을 알아보는 줄이다."""
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    undone_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    undo_note: Mapped[str] = mapped_column(String(500), default="")
    """되돌린 결과 한 줄 — 몇 건을 되돌렸고 몇 건은 왜 못 했나."""


class BundleUndoEntry(Base):
    """되돌리려면 무엇을 써야 하나 — **바뀐 것마다 한 줄.**

    판이 지워지면 함께 지워진다(`ondelete=CASCADE`). 보관 기간이 지난 판의 줄은 워커가
    비운다 — 안 비우면 백필 한 번이 표에 수만 줄을 영구히 남긴다.
    """

    __tablename__ = "bundle_undo_entries"
    __table_args__ = (Index("ix_bundle_undo_entries_run", "run_id", "seq"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("bundle_runs.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    """넣은 차례. **되돌릴 때는 거꾸로 간다** — 뒤 단계가 앞 단계의 것을 가리킨다."""
    table_name: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(16))
    """`create` 는 되돌리면 지우고, `update` 는 `before` 로, `delete` 는 되살린다."""
    before: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    """우리가 넣은 값. 지금 값이 이것과 다르면 **그 사이 남이 고친 것**이라 안 되돌린다."""
    label: Mapped[str] = mapped_column(String(300), default="")
    """사람이 읽는 줄 — 계획 표에 그대로 나간다."""
