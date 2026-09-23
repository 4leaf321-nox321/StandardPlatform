"""웹훅이 코어 타입을 따라가게 — webhooks.core_types_only.

코어를 새로 열 때마다 웹훅 설정에 그 slug 를 손으로 더해야 하면, 빠뜨린 타입은 **조용히**
알림이 안 간다. 켜 두면 「지금 코어인 것」 을 따라간다 — 목록이 아니라 규칙을 저장한다.

Revision ID: 0027_webhook_core
Revises: 0026_sp_core_source
Create Date: 2026-09-23 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0027_webhook_core"
down_revision: Union[str, Sequence[str], None] = "0026_sp_core_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "webhooks",
        sa.Column("core_types_only", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("webhooks", "core_types_only")
