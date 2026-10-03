"""지표 — 정의 · 계산 기록 · 셀(ADR 0013).

Revision ID: 0057_metrics
Revises: 0056_attachment_tickets
Create Date: 2026-10-04 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0057_metrics"
down_revision: Union[str, Sequence[str], None] = "0056_attachment_tickets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "metric_defs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("source_type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "spec",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("interval_hours", sa.Integer(), server_default="24", nullable=False),
        sa.Column("overlap", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("current_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=20), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("cells", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_type_id"], ["object_types.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(
        op.f("ix_metric_defs_source_type_id"), "metric_defs", ["source_type_id"], unique=False
    )
    op.create_table(
        "metric_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column("watermark", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rows", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("cells", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["metric_id"], ["metric_defs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_metric_runs_metric_id"), "metric_runs", ["metric_id"], unique=False
    )
    op.create_index(
        "ix_metric_runs_metric_started",
        "metric_runs",
        ["metric_id", "started_at"],
        unique=False,
    )
    op.create_table(
        "metric_values",
        sa.Column("metric_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cell_hash", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("period", sa.Date(), nullable=True),
        sa.Column("cohort", sa.Date(), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column(
            "dims",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("count", sa.BigInteger(), nullable=False),
        sa.Column("value_count", sa.BigInteger(), nullable=False),
        sa.Column("sum", sa.Float(), nullable=True),
        sa.Column("min", sa.Float(), nullable=True),
        sa.Column("max", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["metric_id"], ["metric_defs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["metric_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("metric_id", "run_id", "cell_hash"),
    )
    op.create_index(
        "ix_metric_values_cohort",
        "metric_values",
        ["metric_id", "run_id", "cohort"],
        unique=False,
    )
    op.create_index(
        "ix_metric_values_dims",
        "metric_values",
        ["dims"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"dims": "jsonb_path_ops"},
    )
    op.create_index(
        "ix_metric_values_period",
        "metric_values",
        ["metric_id", "run_id", "period"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_metric_values_period", table_name="metric_values")
    op.drop_index("ix_metric_values_dims", table_name="metric_values", postgresql_using="gin")
    op.drop_index("ix_metric_values_cohort", table_name="metric_values")
    op.drop_table("metric_values")
    op.drop_index("ix_metric_runs_metric_started", table_name="metric_runs")
    op.drop_index(op.f("ix_metric_runs_metric_id"), table_name="metric_runs")
    op.drop_table("metric_runs")
    op.drop_index(op.f("ix_metric_defs_source_type_id"), table_name="metric_defs")
    op.drop_table("metric_defs")
