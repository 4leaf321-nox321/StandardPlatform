"""참조 칸의 역방향 이름 — 참조 칸은 「칸에 저장한 많대일 관계」 다.

Revision ID: 0022_ref_inverse_label
Revises: 0021_managed_by
Create Date: 2026-09-14 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0022_ref_inverse_label"
down_revision: Union[str, Sequence[str], None] = "0021_managed_by"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "property_defs",
        sa.Column("inverse_label", sa.String(length=64), server_default="", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("property_defs", "inverse_label")
