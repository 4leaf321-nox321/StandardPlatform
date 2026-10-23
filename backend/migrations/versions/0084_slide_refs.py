"""장표에 실린 기록(`slide_refs`) — 기록 상세의 「이 기록이 쓰인 장표」(ADR 0037 2단계).

Revision ID: 0084_slide_refs
Revises: 0083_slides
Create Date: 2026-10-24 00:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0084_slide_refs"
down_revision: Union[str, Sequence[str], None] = "0083_slides"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "slide_refs",
        sa.Column("slide_id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("area_key", sa.String(length=40), nullable=False),
        sa.Column(
            "seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["object_id"], ["objects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["slide_id"], ["slides.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("slide_id", "object_id", "area_key"),
    )
    op.create_index(op.f("ix_slide_refs_object_id"), "slide_refs", ["object_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_slide_refs_object_id"), table_name="slide_refs")
    op.drop_table("slide_refs")
