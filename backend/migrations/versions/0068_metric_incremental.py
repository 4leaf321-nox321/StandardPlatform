"""지표 증분 재계산 — 셀이 든 실행 · 실행의 방식 · 정의 지문 · 다시 센 기간 · 까닭.

`metric_defs.cells_run_id` — 증분 계산은 새 실행 기록을 남기되 셀은 이 실행의 칸을 고친다.
`metric_runs.mode` · `spec_hash` · `periods` · `note`.

Revision ID: 0068_metric_incremental
Revises: 0067_home_metrics
Create Date: 2026-10-08 13:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0068_metric_incremental"
down_revision: Union[str, Sequence[str], None] = "0067_home_metrics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("metric_defs", sa.Column("cells_run_id", sa.UUID(), nullable=True))
    op.add_column(
        "metric_runs",
        sa.Column("mode", sa.String(length=12), server_default="full", nullable=False),
    )
    op.add_column(
        "metric_runs",
        sa.Column("spec_hash", sa.String(length=64), server_default="", nullable=False),
    )
    op.add_column(
        "metric_runs",
        sa.Column("periods", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "metric_runs",
        sa.Column("note", sa.String(length=300), server_default="", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("metric_runs", "note")
    op.drop_column("metric_runs", "periods")
    op.drop_column("metric_runs", "spec_hash")
    op.drop_column("metric_runs", "mode")
    op.drop_column("metric_defs", "cells_run_id")
