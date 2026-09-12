"""지켜보기 — 이 객체가 바뀌면 나에게 알린다.

Revision ID: 0019_object_watches
Revises: 0018_view_summary_home
Create Date: 2026-09-12 23:46:01.645977

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0019_object_watches"
down_revision: Union[str, Sequence[str], None] = "0018_view_summary_home"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "object_watches",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["objects.id"],
            name=op.f("fk_object_watches_object_id_objects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_object_watches_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_object_watches")),
        sa.UniqueConstraint("object_id", "user_id", name="uq_object_watches_pair"),
    )
    op.create_index(
        op.f("ix_object_watches_object_id"), "object_watches", ["object_id"], unique=False
    )
    op.create_index(
        op.f("ix_object_watches_user_id"), "object_watches", ["user_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_object_watches_user_id"), table_name="object_watches")
    op.drop_index(op.f("ix_object_watches_object_id"), table_name="object_watches")
    op.drop_table("object_watches")
