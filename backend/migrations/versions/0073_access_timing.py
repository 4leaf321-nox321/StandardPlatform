"""요청마다 걸린 시간 · 응답 크기(`request_timings`) — 느린 요청 · 큰 응답(ADR 0026).

접근 로그는 상태를 바꾸는 요청만 남기고 걸린 시간 · 크기는 안 남겼다. 작업 목록 한 번이 93MB ·
4.4초였던 것을 사용자가 「화면이 느리다」 고 말하고 나서야 알았다(2026-10-09) — 그것은 조회였다.
조회까지 전부 남기는 가벼운 표를 따로 둔다(접근 로그에 섞으면 그 표가 조회로 찬다).

경로 틀(`route`)은 글자 정렬 규칙을 C 로 둔다 — 기간 집계가 이 칸으로 통째로 정렬한다(70만 줄
0.64 → 0.36초, 2026-10-10 실측).

Revision ID: 0073_access_timing
Revises: 0072_job_brief
Create Date: 2026-10-10 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0073_access_timing"
down_revision: Union[str, Sequence[str], None] = "0072_job_brief"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "request_timings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("route", sa.String(length=300, collation="C"), nullable=False),
        sa.Column("path", sa.String(length=300), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("response_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("request_id", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_request_timings")),
    )
    op.create_index(
        "ix_request_timings_created_at", "request_timings", ["created_at"], unique=False
    )
    op.create_index(
        "ix_request_timings_route",
        "request_timings",
        ["route", "method", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_request_timings_route", table_name="request_timings")
    op.drop_index("ix_request_timings_created_at", table_name="request_timings")
    op.drop_table("request_timings")
