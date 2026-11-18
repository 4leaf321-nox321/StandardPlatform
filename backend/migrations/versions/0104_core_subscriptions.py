"""코어 구독(ADR 0085).

형제 Standard Platform 의 코어를 통째로 받는 등록(`core_subscriptions`)과, 데이터 소스가 어느 구독의
것인가(`data_sources.subscription_id`). 구독이 동기화마다 허브의 정의를 맞추고, 공개 타입마다 소스를
두고 칸 대응을 맞춘다 — 허브에 공개 타입 · 칸이 늘 때마다 받는 쪽이 정의를 다시 받고 토큰을 다시 넣던
일이 없어진다. 데이터 소스 표는 작다(수십 줄) — 칸을 더해도 바로 끝난다.

Revision ID: 0104_core_subscriptions
Revises: 0103_type_scope
Create Date: 2026-11-18 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0104_core_subscriptions"
down_revision: Union[str, Sequence[str], None] = "0103_type_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "core_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("system", sa.String(length=100), server_default="", nullable=False),
        sa.Column(
            "auth",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("source_name", sa.String(length=40), nullable=False),
        sa.Column(
            "groups",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("interval_minutes", sa.Integer(), server_default="60", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("definitions_mark", sa.String(length=64), server_default="", nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=20), nullable=True),
        sa.Column("last_error", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "held",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_core_subscriptions_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_core_subscriptions")),
        sa.UniqueConstraint("base_url", name=op.f("uq_core_subscriptions_base_url")),
    )
    op.add_column(
        "data_sources",
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        op.f("ix_data_sources_subscription_id"),
        "data_sources",
        ["subscription_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_data_sources_subscription_id_core_subscriptions"),
        "data_sources",
        "core_subscriptions",
        ["subscription_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_data_sources_subscription_id_core_subscriptions"),
        "data_sources",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_data_sources_subscription_id"), table_name="data_sources")
    op.drop_column("data_sources", "subscription_id")
    op.drop_table("core_subscriptions")
