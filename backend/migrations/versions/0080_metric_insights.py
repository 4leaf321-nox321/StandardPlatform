"""눈여겨볼 것(`metric_insights`) — 플랫폼이 스스로 지표를 훑어 찾은 결론(ADR 0032).

지표마다 훑기를 끄는 칸 · 마지막 훑기 · 그때 셀이 든 부서(보이는 사람을 가르는 것)를 지표에 더한다.

Revision ID: 0080_metric_insights
Revises: 0079_cut_questions
Create Date: 2026-10-16 02:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0080_metric_insights"
down_revision: Union[str, Sequence[str], None] = "0079_cut_questions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "metric_defs",
        sa.Column("insights_off", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "metric_defs",
        sa.Column("insights_scanned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "metric_defs",
        sa.Column(
            "insights_owners",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.create_table(
        "metric_insights",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("metric_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=300), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("recipe", sa.String(length=16), nullable=False),
        sa.Column(
            "values",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("score", sa.Float(), server_default="0", nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_by_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["metric_id"], ["metric_defs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_insights")),
        sa.UniqueConstraint("metric_id", "key", name="uq_metric_insights_key"),
    )
    op.create_index(
        "ix_metric_insights_first_seen", "metric_insights", ["first_seen_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_metric_insights_first_seen", table_name="metric_insights")
    op.drop_table("metric_insights")
    op.drop_column("metric_defs", "insights_owners")
    op.drop_column("metric_defs", "insights_scanned_at")
    op.drop_column("metric_defs", "insights_off")
