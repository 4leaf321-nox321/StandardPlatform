"""객체 별칭 — 사람이 붙인 다른 이름과 바깥 시스템의 식별자. 같은 타입 안에서 (kind, 값)은 하나.

Revision ID: 0015_object_aliases
Revises: 0014_webhooks
Create Date: 2026-09-12 10:01:08.315333

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0015_object_aliases"
down_revision: Union[str, Sequence[str], None] = "0014_webhooks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "object_aliases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("type_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=80), server_default="alias", nullable=False),
        sa.Column("value", sa.String(length=200), nullable=False),
        sa.Column("norm", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["objects.id"],
            name=op.f("fk_object_aliases_object_id_objects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["type_id"],
            ["object_types.id"],
            name=op.f("fk_object_aliases_type_id_object_types"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_object_aliases")),
        sa.UniqueConstraint("type_id", "kind", "norm", name="uq_object_aliases_value"),
    )
    op.create_index(
        "ix_object_aliases_lookup", "object_aliases", ["type_id", "norm"], unique=False
    )
    op.create_index("ix_object_aliases_object", "object_aliases", ["object_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_object_aliases_object", table_name="object_aliases")
    op.drop_index("ix_object_aliases_lookup", table_name="object_aliases")
    op.drop_table("object_aliases")
