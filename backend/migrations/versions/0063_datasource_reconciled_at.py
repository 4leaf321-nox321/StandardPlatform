"""데이터 소스 — 마지막으로 전량을 받아 대조한 때(`ra_reports`, ADR 0018).

RA 보고서 소스는 평소에 증분으로 쌓고, 하루 한 번 전량을 받아 태그만 바뀐 것을 메우고 안 온 보고서에
「원본에서 내려감」 을 적는다. 그 「하루」 를 재는 자리다.

Revision ID: 0063_datasource_reconciled_at
Revises: 0062_object_refs_statistics
Create Date: 2026-10-04 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0063_datasource_reconciled_at"
down_revision: Union[str, Sequence[str], None] = "0062_object_refs_statistics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "data_sources",
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("data_sources", "reconciled_at")
