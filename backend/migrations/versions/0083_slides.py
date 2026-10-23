"""장표(`slide_templates` · `slides`) — 여러 타입의 기록을 틀 한 장에 모아 본다(ADR 0037).

Revision ID: 0083_slides
Revises: 0082_drop_workspace_restricted
Create Date: 2026-10-23 23:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0083_slides"
down_revision: Union[str, Sequence[str], None] = "0082_drop_workspace_restricted"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "slide_templates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("subject_type_id", sa.UUID(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("cols", sa.Integer(), nullable=False),
        sa.Column(
            "areas",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subject_type_id"], ["object_types.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(
        op.f("ix_slide_templates_subject_type_id"),
        "slide_templates",
        ["subject_type_id"],
        unique=False,
    )
    op.create_table(
        "slides",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("author_id", sa.UUID(), nullable=True),
        sa.Column("author_name", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subject_id"], ["objects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["template_id"], ["slide_templates.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_slides_created_at"), "slides", ["created_at"], unique=False)
    op.create_index(op.f("ix_slides_deleted_at"), "slides", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_slides_subject_id"), "slides", ["subject_id"], unique=False)
    op.create_index(op.f("ix_slides_template_id"), "slides", ["template_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_slides_template_id"), table_name="slides")
    op.drop_index(op.f("ix_slides_subject_id"), table_name="slides")
    op.drop_index(op.f("ix_slides_deleted_at"), table_name="slides")
    op.drop_index(op.f("ix_slides_created_at"), table_name="slides")
    op.drop_table("slides")
    op.drop_index(op.f("ix_slide_templates_subject_type_id"), table_name="slide_templates")
    op.drop_table("slide_templates")
