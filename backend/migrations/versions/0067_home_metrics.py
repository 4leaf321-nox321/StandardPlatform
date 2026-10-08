"""부서 홈에 올린 지표(`home_metrics`) — 저장된 뷰와 같은 줄에 선다.

Revision ID: 0067_home_metrics
Revises: 0066_metric_scheduled_at
Create Date: 2026-10-08 12:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0067_home_metrics"
down_revision: Union[str, Sequence[str], None] = "0066_metric_scheduled_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "home_metrics",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("metric_id", sa.UUID(), nullable=False),
        sa.Column("home_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("split", sa.String(length=100), nullable=True),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_home_metrics_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["metric_id"],
            ["metric_defs.id"],
            name=op.f("fk_home_metrics_metric_id_metric_defs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_home_metrics_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_home_metrics")),
        sa.UniqueConstraint(
            "workspace_id", "metric_id", name="uq_home_metrics_workspace_metric"
        ),
    )
    op.create_index(
        op.f("ix_home_metrics_metric_id"), "home_metrics", ["metric_id"], unique=False
    )
    op.create_index(
        op.f("ix_home_metrics_workspace_id"), "home_metrics", ["workspace_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_home_metrics_workspace_id"), table_name="home_metrics")
    op.drop_index(op.f("ix_home_metrics_metric_id"), table_name="home_metrics")
    op.drop_table("home_metrics")
