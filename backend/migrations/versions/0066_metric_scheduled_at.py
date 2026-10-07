"""지표 — 타이머가 마지막으로 넣은 때(`metric_defs.scheduled_at`).

「N일마다 밤」 의 차례를 계산이 끝난 때(`last_run_at`)로 세면, 오늘 밤 타이머가 어제 끝난
시각보다 몇 분 일찍 깨는 날 건너뛰어 이틀에 한 번꼴로 셀 수 있었다. 타이머가 넣은 때로 센다.

Revision ID: 0066_metric_scheduled_at
Revises: 0065_datasource_waiting
Create Date: 2026-10-08 02:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0066_metric_scheduled_at"
down_revision: Union[str, Sequence[str], None] = "0065_datasource_waiting"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "metric_defs",
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("metric_defs", "scheduled_at")
