"""감사 기록의 묶음 번호 인덱스 — 여럿 골라 고친 것을 한 번에 되돌린다.

Revision ID: 0020_audit_batch_index
Revises: 0019_object_watches
Create Date: 2026-09-13 01:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0020_audit_batch_index"
down_revision: Union[str, Sequence[str], None] = "0019_object_watches"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_audit_entries_changes_batch",
        "audit_entries",
        [sa.literal_column("((changes -> '_batch') ->> 'id')")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_audit_entries_changes_batch", table_name="audit_entries")
