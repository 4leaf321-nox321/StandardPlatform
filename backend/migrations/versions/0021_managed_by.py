"""허브가 관리하는 정의 — 타입 · 관계 종류의 managed_by.

Revision ID: 0021_managed_by
Revises: 0020_audit_batch_index
Create Date: 2026-09-14 09:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0021_managed_by"
down_revision: Union[str, Sequence[str], None] = "0020_audit_batch_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "object_types",
        sa.Column("managed_by", sa.String(length=40), server_default="", nullable=False),
    )
    op.add_column(
        "relation_types",
        sa.Column("managed_by", sa.String(length=40), server_default="", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("relation_types", "managed_by")
    op.drop_column("object_types", "managed_by")
